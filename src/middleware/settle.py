"""On-chain settlement of accepted x402 payments (EIP-3009 transferWithAuthorization).

When X402_SETTLER_PRIVATE_KEY is configured, every verified payment is executed
on-chain: USDC actually moves from the payer to the configured wallet within
seconds, automatically.

The settler key does NOT need to be the receiving wallet's key — EIP-3009 lets
any address execute an authorization, so a dedicated gas-only burner key can
submit the transactions while the USDC lands in the main wallet. Without a
settler key the server behaves as before: payments are verified and counted,
but no funds move (the USDC stays with the payer).
"""
import asyncio
import json
import logging
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import httpx
from eth_account import Account
from eth_utils import keccak, to_checksum_address

logger = logging.getLogger("x402.settle")

# transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,bytes)
_TRANSFER_WITH_AUTH_SELECTOR = keccak(
    b"transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,bytes)"
)[:4]

_DEFAULT_GAS = 100_000  # an EIP-3009 transfer is ~50-65k gas; leave headroom


def _uint(value: Any) -> int:
    """Parse an unsigned integer that may be int, decimal string, or 0x-hex."""
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    return int(s, 10)


def _word(value: int) -> bytes:
    return value.to_bytes(32, "big")


def encode_transfer_with_authorization(authorization: Dict[str, Any], signature: bytes) -> bytes:
    """ABI-encode a USDC transferWithAuthorization() call from an authorization."""
    from_addr = _word(_uint(authorization["from"]))
    to_addr = _word(_uint(authorization["to"]))
    value = _word(_uint(authorization["value"]))
    valid_after = _word(_uint(authorization["validAfter"]))
    valid_before = _word(_uint(authorization["validBefore"]))
    nonce_hex = str(authorization["nonce"])
    nonce = bytes.fromhex(nonce_hex[2:] if nonce_hex.startswith("0x") else nonce_hex)
    if len(nonce) != 32:
        nonce = nonce.ljust(32, b"\x00")
    # signature is the dynamic `bytes` arg: offset points past the 7 head words
    offset = _word(7 * 32)
    tail = _word(len(signature)) + signature
    tail += b"\x00" * (-len(tail) % 32)
    return (
        _TRANSFER_WITH_AUTH_SELECTOR
        + from_addr + to_addr + value + valid_after + valid_before + nonce
        + offset + tail
    )


class SettlementService:
    """Executes verified EIP-3009 authorizations on-chain (USDC on Base)."""

    def __init__(
        self,
        private_key: Optional[str],
        wallet_address: Optional[str],
        usdc_contract: str,
        rpc_url: str,
        chain_id: int,
        settle_min_units: int = 0,
        max_attempts: int = 5,
    ):
        self.enabled = private_key is not None
        self.wallet_address = wallet_address
        self.usdc_contract = to_checksum_address(usdc_contract)
        self.rpc_url = rpc_url
        self.chain_id = chain_id
        self.settle_min_units = settle_min_units
        self.max_attempts = max_attempts
        self._account = Account.from_key(private_key) if private_key else None
        # pending items: [authorization, signature bytes, value units, attempts]
        self.pending: Deque[List[Any]] = deque()
        self._task: Optional[asyncio.Task] = None

    @property
    def executor_address(self) -> Optional[str]:
        return self._account.address if self._account else None

    # ------------------------------------------------------------------
    def enqueue(self, authorization: Dict[str, Any], signature_hex: str, value: int) -> None:
        """Queue a verified payment for on-chain settlement."""
        if not self.enabled:
            return
        try:
            sig = bytes.fromhex(
                signature_hex[2:] if signature_hex.startswith("0x") else signature_hex
            )
        except ValueError:
            logger.error("settlement: unparseable payment signature, dropping %s micro-USDC", value)
            return
        self.pending.append([authorization, sig, value, 0])
        self._ensure_drain()

    def _ensure_drain(self) -> None:
        """Start the drain task from whatever event loop is running, if any."""
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._drain())
        except RuntimeError:
            pass  # no running loop; retried on the next enqueue

    # ------------------------------------------------------------------
    async def _drain(self) -> None:
        while True:
            try:
                if not self.pending:
                    await asyncio.sleep(2)
                    continue
                # Optionally batch: wait until pending value reaches the threshold
                if self.settle_min_units:
                    total = sum(item[2] for item in self.pending)
                    if total < self.settle_min_units:
                        await asyncio.sleep(5)
                        continue
                item = self.pending[0]
                authorization, sig, value, attempts = item
                try:
                    await self._settle_one(authorization, sig, value)
                except Exception as exc:
                    item[3] = attempts + 1
                    if item[3] >= self.max_attempts:
                        self.pending.popleft()
                        logger.critical(
                            "settlement FAILED after %d attempts (%s micro-USDC not collected). "
                            "Authorization for manual redemption: %s",
                            item[3], value, json.dumps(authorization, sort_keys=True),
                        )
                    else:
                        logger.warning("settlement attempt %d/%d failed (%s: %s); retrying",
                                       item[3], self.max_attempts, type(exc).__name__, exc)
                        await asyncio.sleep(min(2 ** item[3], 60))
                    continue
                self.pending.popleft()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("settlement drain error")
                await asyncio.sleep(5)

    async def _settle_one(self, authorization: Dict[str, Any], sig: bytes, value: int) -> str:
        data = encode_transfer_with_authorization(authorization, sig)
        async with httpx.AsyncClient(timeout=30) as client:
            nonce = int(await self._rpc(client, "eth_getTransactionCount",
                                        [self.executor_address, "pending"]), 16)
            gas_price = int(await self._rpc(client, "eth_gasPrice", []), 16)
            tx = {
                "nonce": nonce,
                "gasPrice": gas_price,
                "gas": _DEFAULT_GAS,
                "to": self.usdc_contract,
                "value": 0,
                "chainId": self.chain_id,
                "data": "0x" + data.hex(),
            }
            try:
                gas = int(await self._rpc(client, "eth_estimateGas",
                                          [dict(tx, **{"from": self.executor_address})]), 16)
                tx["gas"] = gas
            except Exception:
                pass  # keep the conservative default gas
            signed = Account.sign_transaction(tx, self._account.key)
            tx_hash = await self._rpc(client, "eth_sendRawTransaction",
                                      ["0x" + signed.raw_transaction.hex()])
        logger.info("payment settled: %s micro-USDC -> %s (tx %s)",
                    value, self.wallet_address, tx_hash)
        return tx_hash

    async def _rpc(self, client: httpx.AsyncClient, method: str, params: list) -> Any:
        resp = await client.post(self.rpc_url, json={
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
        })
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"RPC {method}: {body['error']}")
        return body.get("result")


__all__: List[str] = ["SettlementService", "encode_transfer_with_authorization"]
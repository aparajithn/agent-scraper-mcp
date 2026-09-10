"""x402 (HTTP 402) payment middleware — x402 v1 over USDC (EIP-3009).

How it works:
- When the free tier is exhausted the server replies with HTTP 402 carrying
  machine-readable payment requirements (x402 v1 "accepts" format), so any
  x402-aware client (x402-fetch, Coinbase AgentKit, ...) can pay and retry
  automatically.
- The retried request carries an ``X-Payment`` header: a base64-encoded JSON
  envelope wrapping an EIP-3009 ``TransferWithAuthorization`` and its EIP-712
  signature. The signature must recover to the payer's address and authorize
  transferring at least the required price, in USDC, to the configured wallet,
  before the deadline, with an unused nonce (replay-protected).

Amounts are expressed in USDC atomic units (6 decimals), i.e. micro-dollars:
$0.001 == 1000 units.
"""
import base64
import json
import os
import time
from typing import Dict, List, Mapping, Optional, Tuple

from eth_account.messages import encode_typed_data
from eth_keys import keys
from eth_utils import keccak, to_checksum_address
from fastapi.responses import JSONResponse

from .settle import SettlementService

# USDC on Base mainnet
USDC_BASE_MAINNET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

_EIP712_DOMAIN = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]

_TRANSFER_WITH_AUTHORIZATION = [
    {"name": "from", "type": "address"},
    {"name": "to", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "validAfter", "type": "uint256"},
    {"name": "validBefore", "type": "uint256"},
    {"name": "nonce", "type": "bytes32"},
]


def _int_or_none(value) -> Optional[int]:
    """Parse a decimal or 0x-hex integer."""
    if value is None:
        return None
    try:
        s = str(value).strip()
        if s.startswith("0x") or s.startswith("0X"):
            return int(s, 16)
        return int(s, 10)
    except (ValueError, TypeError):
        return None


def _epoch_ms(value_ms: int) -> float:
    """Interpret a timestamp as seconds (handles both ms and s precision)."""
    return value_ms / 1000.0 if value_ms > 10**12 else float(value_ms)


def _pad_b64(raw: str) -> str:
    return raw + "=" * (-len(raw) % 4)


def _recover_signer_candidates(
    authorization: Mapping,
    signature_hex: str,
    chain_id: int,
    verifying_contract: str,
    asset_name: str,
    asset_version: str,
) -> List[str]:
    """Recover possible signer addresses of an EIP-3009 TransferWithAuthorization.

    The typed data is reconstructed from the authorization fields and the
    server's own trusted domain constants (never from client-supplied
    types/domain, which could be spoofed). Both signature parity candidates
    are returned so the caller can match either one (v normalization varies
    across signing libraries).
    """
    try:
        sig = bytes.fromhex(signature_hex[2:] if signature_hex.startswith("0x") else signature_hex)
        if len(sig) != 65:
            return []
        r = int.from_bytes(sig[:32], "big")
        s = int.from_bytes(sig[32:64], "big")
        v = sig[64]
        if v >= 27:
            v -= 27

        value = _int_or_none(authorization.get("value"))
        valid_after = _int_or_none(authorization.get("validAfter"))
        valid_before = _int_or_none(authorization.get("validBefore"))
        if value is None or valid_after is None or valid_before is None:
            return []

        typed_data = {
            "types": {
                "EIP712Domain": _EIP712_DOMAIN,
                "TransferWithAuthorization": _TRANSFER_WITH_AUTHORIZATION,
            },
            "primaryType": "TransferWithAuthorization",
            "domain": {
                "name": asset_name,
                "version": asset_version,
                "chainId": chain_id,
                "verifyingContract": verifying_contract,
            },
            "message": {
                "from": to_checksum_address(authorization["from"]),
                "to": to_checksum_address(authorization["to"]),
                "value": value,
                "validAfter": valid_after,
                "validBefore": valid_before,
                "nonce": authorization["nonce"],
            },
        }
        signable = encode_typed_data(full_message=typed_data)
        # EIP-191 digest: keccak(0x19 || version byte || domainSeparator || structHash)
        digest = keccak(b"\x19" + signable.version + signable.header + signable.body)

        candidates: List[str] = []
        for parity in (v, v ^ 1):
            try:
                # eth_keys expects v in {0, 1}
                pub = keys.Signature(vrs=(parity, r, s)).recover_public_key_from_msg_hash(digest)
                candidates.append(pub.to_checksum_address())
            except Exception:
                continue
        return candidates
    except Exception:
        return []


class X402Middleware:
    """HTTP 402 payment middleware (x402 v1)."""

    def __init__(
        self,
        wallet_address: Optional[str] = None,
        network: str = "base",
        chain_id: int = 8453,
        usdc_contract: str = USDC_BASE_MAINNET,
        default_price_units: int = 1000,
        tool_prices: Optional[Dict[str, int]] = None,
        max_timeout_seconds: int = 60,
        description: str = "Payment for API access",
        asset_name: str = "USD Coin",
        asset_version: str = "2",
        settlement: Optional[SettlementService] = None,
    ):
        """
        Args:
            wallet_address: Wallet that payments must be addressed to.
                If None, the paywall is disabled.
            network / chain_id / usdc_contract: The USDC payment network.
            default_price_units: Default price in USDC atomic units (micro-$).
            tool_prices: Per-tool price overrides (tool name -> units).
            max_timeout_seconds: Payment validity window advertised in 402s.
        """
        self.wallet_address = wallet_address
        self.network = network
        self.chain_id = chain_id
        self.usdc_contract = usdc_contract
        self.default_price_units = default_price_units
        self.tool_prices = tool_prices or {}
        self.max_timeout_seconds = max_timeout_seconds
        self.description = description
        self.asset_name = asset_name
        self.asset_version = asset_version
        self.settlement = settlement
        self.enabled = wallet_address is not None
        # nonce -> expiry (epoch seconds) for replay protection
        self._seen_nonces: Dict[str, float] = {}

    def price_for(self, tool_name: Optional[str] = None) -> int:
        """Price in USDC atomic units for the given tool (or the default)."""
        if tool_name and tool_name in self.tool_prices:
            return self.tool_prices[tool_name]
        return self.default_price_units

    # ------------------------------------------------------------------
    # Payment verification
    # ------------------------------------------------------------------
    def verify_payment(self, headers: Mapping[str, str], price_units: int) -> Tuple[bool, Optional[str]]:
        """Verify the X-Payment header against the required price.

        Returns:
            (ok, error) — error is a human-readable reason when ok is False.
        """
        if not self.enabled:
            return True, None

        raw = headers.get("x-payment")
        if not raw:
            return False, "X-PAYMENT header is required"
        try:
            envelope = json.loads(base64.b64decode(_pad_b64(raw)).decode("utf-8"))
        except Exception:
            return False, "X-PAYMENT is not a base64-encoded JSON envelope"
        if not isinstance(envelope, dict):
            return False, "X-PAYMENT envelope must be a JSON object"
        if envelope.get("x402Version") != 1:
            return False, "unsupported x402 version"
        if envelope.get("scheme") != "https":
            return False, f"unsupported payment scheme {envelope.get('scheme')!r}"
        network = envelope.get("network")
        if network and network != self.network:
            return False, f"unsupported payment network {network!r}"

        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return False, "missing payment payload"
        authorization = payload.get("authorization")
        if not isinstance(authorization, dict):
            return False, "missing EIP-3009 authorization"
        signature = payload.get("signature") or envelope.get("signature")
        if not signature:
            return False, "missing payment signature"

        # Recipient must be our wallet
        pay_to = str(authorization.get("to") or "")
        if pay_to.lower() != (self.wallet_address or "").lower():
            return False, "payment addressed to a different wallet"

        # Amount must cover the price
        value = _int_or_none(authorization.get("value"))
        if value is None:
            return False, "invalid payment value"
        if value < price_units:
            return False, "payment amount is below the required price"

        # Time window (accepts seconds or milliseconds precision)
        valid_after_raw = _int_or_none(authorization.get("validAfter"))
        valid_before_raw = _int_or_none(authorization.get("validBefore"))
        if valid_after_raw is None or valid_before_raw is None:
            return False, "invalid payment validity window"
        now = time.time()
        if not (_epoch_ms(valid_after_raw) <= now < _epoch_ms(valid_before_raw)):
            return False, "payment authorization expired or not yet valid"

        # Nonce replay protection
        nonce = authorization.get("nonce")
        if not nonce or not isinstance(nonce, str):
            return False, "invalid payment nonce"
        self._purge_nonces(now)
        if nonce.lower() in self._seen_nonces:
            return False, "payment nonce was already used"

        # EIP-712 signature must recover to the payer's address
        payer = str(authorization.get("from") or "")
        candidates = _recover_signer_candidates(
            authorization,
            signature,
            self.chain_id,
            self.usdc_contract,
            self.asset_name,
            self.asset_version,
        )
        if not candidates:
            return False, "invalid payment signature"
        if payer.lower() not in {c.lower() for c in candidates}:
            return False, "signature does not match payer address"

        # Valid: remember the nonce so it cannot be replayed
        self._seen_nonces[nonce.lower()] = _epoch_ms(valid_before_raw)
        # Settle on-chain (moves the USDC to our wallet) when a settler key is configured
        if self.settlement is not None and self.settlement.enabled:
            self.settlement.enqueue(authorization, signature, value)
        return True, None

    def _purge_nonces(self, now: float) -> None:
        expired = [n for n, exp in self._seen_nonces.items() if exp < now]
        for n in expired:
            del self._seen_nonces[n]

    # ------------------------------------------------------------------
    # 402 responses
    # ------------------------------------------------------------------
    def payment_requirements(
        self,
        resource: str,
        price_units: int,
        description: Optional[str] = None,
    ) -> dict:
        """x402 v1 payment requirements block (one entry of "accepts")."""
        return {
            "scheme": "https",
            "network": self.network,
            "maxAmountRequired": str(price_units),
            "resource": resource,
            "description": description or self.description,
            "mimeType": "application/json",
            "payTo": self.wallet_address,
            "maxTimeoutSeconds": self.max_timeout_seconds,
            "extra": {"name": self.asset_name, "version": self.asset_version},
        }

    def payment_required_response(
        self,
        resource: str,
        price_units: int,
        reason: str = "X-PAYMENT header is required",
        description: Optional[str] = None,
    ) -> JSONResponse:
        """Spec-compliant HTTP 402 response carrying payment requirements."""
        return JSONResponse(
            status_code=402,
            content={
                "x402Version": 1,
                "error": reason,
                "accepts": [self.payment_requirements(resource, price_units, description)],
            },
            headers={"X402-VERSION": "1"},
        )


def get_x402_middleware() -> X402Middleware:
    """Build the x402 middleware from environment configuration."""
    scrape_price = int(os.getenv("X402_SCRAPE_PRICE_MICROUSD", "5000"))
    screenshot_price = int(os.getenv("X402_SCREENSHOT_PRICE_MICROUSD", "10000"))
    settlement = SettlementService(
        private_key=os.getenv("X402_SETTLER_PRIVATE_KEY") or None,
        wallet_address=os.getenv("X402_WALLET_ADDRESS") or None,
        usdc_contract=os.getenv("X402_USDC_CONTRACT", USDC_BASE_MAINNET),
        rpc_url=os.getenv("X402_RPC_URL", "https://mainnet.base.org"),
        chain_id=int(os.getenv("X402_CHAIN_ID", "8453")),
        settle_min_units=int(os.getenv("X402_SETTLE_MIN_MICROUSD", "0")),
    )
    return X402Middleware(
        wallet_address=os.getenv("X402_WALLET_ADDRESS") or None,
        network=os.getenv("X402_NETWORK", "base"),
        chain_id=int(os.getenv("X402_CHAIN_ID", "8453")),
        usdc_contract=os.getenv("X402_USDC_CONTRACT", USDC_BASE_MAINNET),
        default_price_units=scrape_price,
        tool_prices={
            "screenshot_url": screenshot_price,
            "tool_screenshot_url": screenshot_price,
        },
        max_timeout_seconds=int(os.getenv("X402_MAX_TIMEOUT_SECONDS", "60")),
        description="Payment for Agent Scraper MCP tool access",
        settlement=settlement,
    )


# Tool names in the X-Payment requirement payloads are plain (no tool_ prefix).
__all__: List[str] = ["X402Middleware", "get_x402_middleware", "USDC_BASE_MAINNET"]
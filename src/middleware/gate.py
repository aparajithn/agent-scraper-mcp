"""ASGI gate: apply the free-tier rate limit + x402 paywall to MCP tool calls.

The MCP endpoint (/mcp) would otherwise bypass the REST API's rate limiting
and paywall entirely. This pure-ASGI middleware inspects JSON-RPC requests
and only meters ``tools/call`` methods (initialize / tools/list stay free),
sharing the same per-IP free-tier counter as the REST API.
"""
import json
from typing import List, Mapping, Optional

from .rate_limit import client_ip
from .x402 import X402Middleware
from .rate_limit import RateLimiter


class PaymentGateMiddleware:
    """Wrap the MCP ASGI app with rate limiting + x402 payment enforcement."""

    def __init__(self, app, rate_limiter: RateLimiter, x402: X402Middleware, public_host: str):
        self.app = app
        self.rate_limiter = rate_limiter
        self.x402 = x402
        self.public_host = public_host

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and str(scope.get("path", "")).rstrip("/").startswith("/mcp")
        ):
            body = b""
            while True:
                message = await receive()
                if message["type"] == "http.request":
                    body += message.get("body", b"")
                    if not message.get("more_body", False):
                        break
                elif message["type"] == "http.disconnect":
                    return
                else:
                    break

            response = self._gate(scope, body)
            if response is not None:
                await response(scope, receive, send)
                return

            replayed = False

            async def replay():
                nonlocal replayed
                if replayed:
                    return {"type": "http.disconnect"}
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}

            await self.app(scope, replay, send)
            return

        await self.app(scope, receive, send)

    # ------------------------------------------------------------------
    def _gate(self, scope, body: bytes):
        """Return a 402 JSONResponse to block the request, or None to allow."""
        called_tools = self._tool_calls(body)
        if not called_tools:
            return None
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        ip = client_ip(headers, (scope.get("client") or ("unknown",))[0])
        allowed, _remaining, _reset_at = self.rate_limiter.check_limit_ip(ip)
        if allowed:
            return None
        if not self.x402.enabled:
            return None  # paywall not configured

        price = max(self.x402.price_for(name) for name in called_tools)
        ok, error = self.x402.verify_payment(headers, price)
        if ok:
            return None
        resource = f"https://{self.public_host}{scope.get('path', '')}"
        return self.x402.payment_required_response(
            resource,
            price,
            reason=error or "X-PAYMENT header is required",
            description=f"Payment for MCP tool call ({', '.join(called_tools)})",
        )

    @staticmethod
    def _tool_calls(body: bytes) -> List[str]:
        """Tool names targeted by tools/call methods in a JSON-RPC body."""
        try:
            data = json.loads(body)
        except Exception:
            return []
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
        names: List[str] = []
        for entry in data:
            if not isinstance(entry, dict) or entry.get("method") != "tools/call":
                continue
            params = entry.get("params") or {}
            name = params.get("name") if isinstance(params, dict) else None
            if name:
                names.append(str(name))
        return names
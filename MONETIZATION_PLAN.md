# Monetization Fix Plan — make the x402 paywall actually collect money

> Status: **ready to implement**. This document is written for an implementing
> agent (human or AI). Read it top to bottom, then execute Phase 1. A
> copy-paste prompt for an AI implementing agent is in the last section.

Applies to **both** repos:

- `aparajithn/agent-scraper-mcp` (service `agent-scraper-mcp.onrender.com`, Render id `srv-d6kq1n6a2pns7395ef90`)
- `aparajithn/agent-utils-mcp` (service `agent-utils-mcp.onrender.com`, Render id `srv-d6ko6v15pdvs7385to0g`)

Both share the same middleware layout: `src/middleware/{gate.py, rate_limit.py, x402.py, settle.py}`.

## 1. Background — where the money is (and why we're not collecting it)

Both services run a working x402 (HTTP 402) paywall: verified EIP-3009 USDC
payments on Base, on-chain settlement, per-IP free tier before the 402 fires.
The payment machinery is done and live-verified. The problem is that **almost
no client ever receives a 402**, so revenue is $0.

Observed traffic (Render metrics + request-log sampling, Sep 10–11 2026):

| | Scraper | Utils |
|---|---|---|
| Successful requests/day | ~1,300–1,600 | ~950–1,100 |
| Real clients per hour | 2–3 logical users | ~9 (identifiable via `?api_key=` on `/mcp`) |
| Dominant clients | `python-httpx2` poller at ~50 req/hr (fixed IP); "Google" fetcher at ~46 req/hr spread over **13 rotating `192.179.0.x` IPs** | `python-requests` clients arriving entirely via **rotating Cloudflare edge IPs** (172.69.x, 104.22.x, 162.158.x, …) |
| Price | $0.005/req (scrape), $0.01 (screenshot) | $0.001/req |
| Revenue ceiling if every over-quota request were paid | ~$6/day (~$180/mo) | ~$0.85/day (~$25/mo) |
| Actual revenue today | $0 | $0 |

### Root causes (in priority order)

1. **Rate-limit identity keying is defeated by rotating front IPs.**
   `client_ip()` in `src/middleware/rate_limit.py` uses the first hop of
   `X-Forwarded-For`. The heaviest users arrive through fronts whose edge IP
   changes per request (Cloudflare egress, Google fetcher, likely MCP-registry
   proxies), so each request gets a fresh counter and the free tier is never
   exhausted. Zero 402s have ever fired.
2. **Counters are in-memory and reset on every deploy.** Render redeploys
   (including env-var changes) wipe the rate-limit counters, so even
   fixed-IP users that should trip the cap get a fresh 24h allowance.
3. **Nothing tells well-behaved clients when their quota resets.** The 402/429
   responses should carry reset timing so pollers back off instead of
   hammering.

### Honest expectation setting

A 402 only converts to revenue if the client is x402-aware **and** holds USDC
on Base. The dominant traffic today is plain `python-httpx`/`python-requests`
pollers which will most likely stop calling rather than pay. Realistic
capture is 10–30% of the ceiling. The goal of this plan is to make the cap
*enforceable*; converting capped clients into paying ones is a separate,
later problem (e.g. listing the services in x402-aware registries).

## 2. Phase 1 — required changes

### 2.1 Stable client identity for rate limiting (both repos)

Rewrite the key derivation so the limiter tracks *logical* clients, not
whatever hop the front happens to use. New precedence in
`rate_limit.py` (extend `client_ip()` or add a `rate_limit_key(headers, query_params, fallback)` function):

1. `CF-Connecting-IP` header if present (Cloudflare sets the true client IP here).
2. `api_key` query parameter if present (utils traffic is full of MCP-registry
   proxy URLs like `/mcp?api_key=18e35413-…`; this is the most stable identity
   utils has). Key it as `key:<api_key>` rather than an IP, so a registry
   user's whole session counts against one bucket.
3. `X-Forwarded-For` **first** hop (unchanged current behavior).
4. `X-Real-IP`, then the direct connection host (current fallbacks).

Additionally, add a **subnet guard** for rotating-IP traffic that carries none
of the above: bucket by `/24` of the client IP (`ip24:192.179.0.x`) with the
same limit, so 13 rotating IPs inside one or two /24s can no longer dodge the
cap. (This specifically catches the Google fetcher pattern.)

Keep it simple: one key per request, computed once, passed to the existing
`check_limit_ip()`. Do not track multiple counters per request.

### 2.2 Rate-limit counters that survive redeploys (both repos)

Minimum viable: keep the in-memory `RateLimiter`, but **persist counters to
disk** — a small JSON file (e.g. `/tmp/rate_limits.json`) written on every
increment and loaded on startup, with the same TTL semantics. Render's disk is
ephemeral per instance but *survives within an instance's lifetime*, which is
what matters between redeploys triggered by config changes.

If you want the durable version: support an optional `REDIS_URL` env var
(free Upstash tier works) with the JSON file as fallback. Do not make Redis
required.

Do **not** move the x402 `_seen_nonces` replay cache to disk — nonces are
valid for ≤60s; a redeploy losing them is acceptable and already documented.

### 2.3 Tighter free tier

- Scraper: `RATE_LIMIT_FREE=10` (was 50)
- Utils: `RATE_LIMIT_FREE=20` (was 100)

Set via Render environment variables on each service (an env change triggers a
redeploy — do it after the code above is deployed so the deploy resets are
irrelevant). The code defaults may stay as they are; the env vars win.

### 2.4 Quota-reset transparency

When the free tier is exhausted, the 402 response should include:

- `Retry-After: <seconds until the client's counter resets>` header
- in the `error` string: the reset time in UTC

so well-behaved pollers back off instead of retrying in a tight loop. The
`reset_at` value already exists in `RateLimiter.check_limit_ip()`'s return.

### 2.5 What NOT to touch

- `x402.py` payment verification and `settle.py` on-chain settlement — verified
  working, live-tested, 12/12 test pass. No changes.
- Never log or accept the operator's private keys anywhere new.
- Don't add new dependencies beyond `redis` (optional, only if you implement
  the Redis variant) and stdlib.

## 3. Phase 2 — optional, later

- Per-tool or per-client pricing tiers (e.g. cheaper `search_google`, higher `screenshot_url`).
- `X402_SETTLE_MIN_MICROUSD` batching once payment volume exists (saves gas per tx).
- Listing the services in x402-aware MCP registries so 402s actually convert
  to payments rather than dropped traffic.
- Revisit pricing once real payment data exists.

## 4. Testing & acceptance criteria

1. **Unit tests** (pytest, extend the existing test suite):
   - `CF-Connecting-IP` takes precedence over `X-Forwarded-For`.
   - `api_key` produces a `key:` bucket independent of the source IP.
   - Two different XFF IPs inside the same /24 with no better headers land in
     one `ip24:` bucket.
   - `Retry-After` and reset-time appear in the exhausted-quota response.
   - Counter persistence: create limiter, increment, write state, new limiter
     instance loads state and keeps the count.
2. **Integration test against the live app** (pattern exists in the repo
   history — spin up `uvicorn src.main:app` locally with `X402_WALLET_ADDRESS`
   set and `RATE_LIMIT_FREE` small, then hammer with rotating fake XFF IPs
   sharing one `CF-Connecting-IP` and assert the 402 fires on request
   N+1). MCP path needs `Accept: application/json`.
3. **Live verification after deploy**: send `RATE_LIMIT_FREE+1` requests from
   one IP to the production URL, confirm a 402 with a correct
   `accepts` block, and confirm the count resets only after TTL.
4. Both services deploy green on Render; watch logs for `payment settled`
   lines (none expected immediately — there is currently no x402-paying
   traffic; that's Phase 2's problem).

## 5. Rollout & monitoring

1. Deploy code to both services.
2. Set the `RATE_LIMIT_FREE` env vars (2.3) on Render.
3. For 3–4 days watch: total request volume (expect some loss as pollers get
   capped — that's intended), 402 counts per service, and any
   `settlement`/`payment settled` log lines.
4. Report: requests/day before vs after, distinct capped clients, payments
   received.

---

## 6. Prompt for the implementing agent

Copy everything inside the fence below and hand it to the agent that will do
the work. It is self-contained.

```text
You are implementing a monetization fix for two FastAPI MCP servers:
aparajithn/agent-scraper-mcp and aparajithn/agent-utils-mcp. They are
deployed on Render as agent-scraper-mcp.onrender.com and
agent-utils-mcp.onrender.com. Both have a working x402 (HTTP 402) USDC
paywall: a per-IP free tier (RATE_LIMIT_FREE requests/day), then HTTP 402
with machine-readable payment requirements, then verified EIP-3009
payments that settle on-chain (Base network) to the operator's wallet.
The payment machinery is DONE and live-verified — do not touch
src/middleware/x402.py or src/middleware/settle.py.

FIRST: read MONETIZATION_PLAN.md in both repos. It contains the full
background, traffic data, root-cause analysis, and this same task list.
Implement Phase 1 exactly as specified there. Summary of the work:

1. Stable rate-limit identity (both repos, src/middleware/rate_limit.py
   and the gate middleware that uses it, src/middleware/gate.py):
   the current limiter keys on the first X-Forwarded-For hop, but the
   heaviest users arrive via rotating front IPs (Cloudflare egress,
   Google fetcher, MCP-registry proxies) and never get capped — zero
   402s have ever fired. Derive one rate-limit key per request with
   this precedence:
     a. CF-Connecting-IP header, if present
     b. api_key query parameter on the request URL (utils traffic is
        full of /mcp?api_key=<uuid> proxy URLs) -> key "key:<api_key>"
     c. first X-Forwarded-For hop
     d. X-Real-IP, else direct connection host
   Additionally, when none of (a)/(b) identify the client, fall back to
   a /24 subnet bucket ("ip24:<a.b.c>.0") so a client rotating through
   IPs inside one /24 cannot dodge the cap.

2. Persist the rate-limit counters across restarts: snapshot the
   RateLimiter counters to a small JSON file (e.g. /tmp/rate_limits.json)
   on each increment and load it on startup, preserving the existing TTL
   semantics. Optional extension: REDIS_URL env var support with the
   file as fallback. Do not touch the x402 nonce replay cache.

3. Quota-reset transparency: when the free tier is exhausted, add a
   Retry-After header (seconds until that client's counter resets) and
   include the reset time in UTC in the 402 error message. reset_at is
   already returned by RateLimiter.check_limit_ip().

4. Free tier env vars (code defaults unchanged):
     agent-scraper-mcp: RATE_LIMIT_FREE=10
     agent-utils-mcp:   RATE_LIMIT_FREE=20
   If you have the Render MCP tools, set them with
   update_environment_variables (workspaceId tea-d3lvn8emcj7s73aa1ds0,
   service ids srv-d6kq1n6a2pns7395ef90 and srv-d6ko6v15pdvs7385to0g);
   otherwise tell the user to set them in the Render dashboard.

Requirements:
- Apply the SAME changes to both repos (the middleware is duplicated,
  not shared; keep the two copies identical except where the plan says
  otherwise). Do not introduce a shared package.
- Python 3.11, no new dependencies (stdlib only; redis optional).
- Match the existing code style (docstrings, type hints, small pure
  functions).
- Write pytest unit tests for the key-derivation precedence (a-d), the
  /24 bucket fallback, Retry-After/reset-time in the 402, and counter
  persistence across a reload. Also add an integration test that boots
  uvicorn src.main:app locally with X402_WALLET_ADDRESS set,
  RATE_LIMIT_FREE=1, sends enough requests with rotating
  X-Forwarded-For values but one shared CF-Connecting-IP, and asserts
  the 402 fires. (MCP requests need "Accept: application/json".)
- Run the full test suite in both repos; everything must pass.
- Commit to master with a clear message and push. Do not push to any
  other branch, do not force-push. If you have Render MCP access,
  confirm both services redeploy green; otherwise verify with
  curl https://<service>.onrender.com/health.
- Security: never echo, log, or accept any private keys. Only
  X402_WALLET_ADDRESS (public) appears in code/config.
- When done, report: files changed per repo, test results, deploy
  status, and the exact list of env vars the user still needs to set
  (if you could not set them yourself).
```
# Agent Scraper MCP Server

**The #1 most requested utility for AI agents** — professional web scraping, screenshots, and content extraction via MCP + REST API.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Smithery](https://smithery.ai/badge/@aparajithn/agent-scraper-mcp)](https://smithery.ai/server/@aparajithn/agent-scraper-mcp)
[![Glama](https://glama.ai/badge/mcp/agent-scraper-mcp)](https://glama.ai/mcp/servers/agent-scraper-mcp)

## Features

🌐 **Clean Content Extraction** — Extract readable text/markdown from any webpage (like Readability)  
🎯 **Structured Scraping** — Extract specific data using CSS selectors  
📸 **Screenshots** — Capture full-page or viewport screenshots with Playwright  
🔗 **Link Extraction** — Get all links from a page with optional regex filtering  
📋 **Metadata Extraction** — Extract title, description, Open Graph tags, favicon, etc  
🔍 **Google Search** — Search Google and get results programmatically  

## Quick Start

### MCP Configuration

Add to your MCP settings file (`cline_mcp_settings.json` or similar):

```json
{
  "mcpServers": {
    "agent-scraper": {
      "url": "https://agent-scraper-mcp.onrender.com/mcp"
    }
  }
}
```

### REST API

Base URL: `https://agent-scraper-mcp.onrender.com`

#### Scrape URL (Clean Content)

```bash
curl -X POST https://agent-scraper-mcp.onrender.com/api/v1/scrape_url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/article",
    "format": "markdown"
  }'
```

Response:
```json
{
  "success": true,
  "url": "https://example.com/article",
  "title": "Article Title",
  "content": "# Article Title\n\nClean markdown content...",
  "format": "markdown"
}
```

#### Scrape Structured Data

```bash
curl -X POST https://agent-scraper-mcp.onrender.com/api/v1/scrape_structured \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/product",
    "selectors": {
      "title": "h1.product-title",
      "price": ".price",
      "reviews": ".review-text"
    }
  }'
```

Response:
```json
{
  "success": true,
  "url": "https://example.com/product",
  "data": {
    "title": "Product Name",
    "price": "$29.99",
    "reviews": ["Great product!", "Worth the money"]
  }
}
```

#### Screenshot URL

```bash
curl -X POST https://agent-scraper-mcp.onrender.com/api/v1/screenshot_url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "width": 1280,
    "height": 720,
    "full_page": false
  }'
```

Response:
```json
{
  "success": true,
  "url": "https://example.com",
  "image": "iVBORw0KGgoAAAANSUhEUgAA...",
  "width": 1280,
  "height": 720,
  "full_page": false
}
```

#### Extract Links

```bash
curl -X POST https://agent-scraper-mcp.onrender.com/api/v1/extract_links \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "filter": "https://example.com/blog/.*"
  }'
```

#### Extract Metadata

```bash
curl -X POST https://agent-scraper-mcp.onrender.com/api/v1/extract_meta \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

#### Search Google

```bash
curl -X POST https://agent-scraper-mcp.onrender.com/api/v1/search_google \
  -H "Content-Type: application/json" \
  -d '{
    "query": "python web scraping",
    "num_results": 10
  }'
```

## Pricing

### Free Tier
- **50 requests per IP per day**
- All tools included
- No credit card required

### Paid Tier (HTTP 402 / x402 Payment)
After free tier exhausted:
- **Scraping tools**: $0.005/request (scrape_url, scrape_structured, extract_links, extract_meta, search_google)
- **Screenshot tool**: $0.01/request (higher due to compute cost)

Payment via the x402 protocol (HTTP 402) in USDC on Base:
- Wallet address (`payTo`): `0x8E844a7De89d7CfBFe9B4453E65935A22F146aBB`

When you exceed the free tier, the server responds with HTTP 402 carrying
machine-readable payment requirements (x402 v1), so x402-aware clients pay and
retry automatically:

```python
from x402_fetch import x402_fetch  # pip install x402-fetch
response = x402_fetch("https://agent-scraper-mcp.onrender.com/api/v1/scrape_url", method="POST",
                      body={"url": "https://example.com", "format": "markdown"})
```

To pay manually, attach an `X-Payment` header containing a base64-encoded
JSON envelope with an EIP-3009 `TransferWithAuthorization` (USDC on Base)
signed with the payer's EIP-712 key for the exact price, addressed to the
wallet above, with an unused nonce. Payments are verified cryptographically
(signature recovery + amount/recipient/deadline/replay checks) before the
request is served — applies to both the REST API and MCP `tools/call`.

### Collecting revenue (server operator)

With `X402_SETTLER_PRIVATE_KEY` set, every verified payment is executed
on-chain within seconds: the EIP-3009 `transferWithAuthorization` is submitted
to the USDC contract on Base and the USDC moves from the payer to
`X402_WALLET_ADDRESS`. The settler key does **not** need to be the receiving
wallet's key — any address may execute the authorization, so a dedicated
gas-only burner key can submit the transactions while funds land in the main
wallet. It only needs a small ETH balance on Base for gas. Additional env vars:
`X402_RPC_URL` (default `https://mainnet.base.org`) and
`X402_SETTLE_MIN_MICROUSD` (default `0` — settle each payment immediately;
raise it to batch small payments). Failed settlements are retried with
backoff and, if all retries fail, the full authorization is written to the
logs (CRITICAL) for manual redemption — no payment is silently lost.

## Tools Reference

### 1. `scrape_url`
Extract clean, readable content from any webpage (like Readability).

**Parameters:**
- `url` (string, required): URL to scrape
- `format` (string, optional): Output format — `text`, `markdown`, or `html` (default: `markdown`)

**Returns:** `{success, url, title, content, format}`

---

### 2. `scrape_structured`
Extract specific data using CSS selectors.

**Parameters:**
- `url` (string, required): URL to scrape
- `selectors` (object, required): Dict of `name → CSS selector`

**Returns:** `{success, url, data}`

**Example selectors:**
```json
{
  "title": "h1.post-title",
  "author": ".author-name",
  "price": "span.price",
  "images": "img.product-image"
}
```

---

### 3. `screenshot_url`
Capture a screenshot of any webpage.

**Parameters:**
- `url` (string, required): URL to screenshot
- `width` (int, optional): Viewport width (default: 1280)
- `height` (int, optional): Viewport height (default: 720)
- `full_page` (bool, optional): Capture full scrollable page (default: false)

**Returns:** `{success, url, image, width, height, full_page}`

Image is base64-encoded PNG.

---

### 4. `extract_links`
Extract all links from a webpage.

**Parameters:**
- `url` (string, required): URL to scrape
- `filter` (string, optional): Regex pattern to filter URLs

**Returns:** `{success, url, links, count}`

Links array contains `{text, href}` objects.

---

### 5. `extract_meta`
Extract metadata from a webpage.

**Parameters:**
- `url` (string, required): URL to scrape

**Returns:** `{success, url, meta}`

Meta object includes:
- `title`: Page title
- `description`: Meta description
- `canonical`: Canonical URL
- `favicon`: Favicon URL
- `og`: Open Graph tags
- `twitter`: Twitter Card tags

---

### 6. `search_google`
Search Google and get results.

**Parameters:**
- `query` (string, required): Search query
- `num_results` (int, optional): Number of results (default: 10)

**Returns:** `{success, query, results, count}`

Results array contains `{title, url, snippet}` objects.

---

## Development

### Local Setup

```bash
# Clone repo
git clone https://github.com/aparajithn/agent-scraper-mcp.git
cd agent-scraper-mcp

# Install dependencies
pip install -e ".[dev]"

# Install Playwright browsers
playwright install chromium --with-deps

# Run server
uvicorn src.main:app --reload --port 8080
```

### Test MCP Protocol

```bash
# Initialize
curl -X POST http://localhost:8080/mcp -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0.0"}}}'

# List tools
curl -X POST http://localhost:8080/mcp -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# Call scrape_url
curl -X POST http://localhost:8080/mcp -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"scrape_url","arguments":{"url":"https://example.com","format":"markdown"}}}'
```

### Docker

```bash
# Build
docker build -t agent-scraper-mcp .

# Run
docker run -p 8080:8080 -e PUBLIC_HOST=localhost agent-scraper-mcp
```

## Deployment

Deployed on **Render** (free tier):
- Service: `agent-scraper-mcp`
- Runtime: Docker
- Region: Ohio
- Auto-deploy from GitHub: `aparajithn/agent-scraper-mcp`

Environment variables:
- `PUBLIC_HOST`: `agent-scraper-mcp.onrender.com`
- `X402_WALLET_ADDRESS`: `0x8E844a7De89d7CfBFe9B4453E65935A22F146aBB`

## Tech Stack

- **Python 3.11** — Modern async/await
- **FastAPI** — REST API framework
- **FastMCP** — MCP protocol implementation (Streamable HTTP)
- **Playwright** — Browser automation for screenshots
- **httpx** — Fast async HTTP client
- **BeautifulSoup4** — HTML parsing
- **readability-lxml** — Content extraction (like Firefox Reader View)

## License

MIT License — see [LICENSE](LICENSE) for details.

## Support

- Issues: [GitHub Issues](https://github.com/aparajithn/agent-scraper-mcp/issues)
- Email: support@agent-scraper.com
- Docs: [https://agent-scraper-mcp.onrender.com/docs](https://agent-scraper-mcp.onrender.com/docs)

---

**Built for AI agents by AI engineers** 🤖

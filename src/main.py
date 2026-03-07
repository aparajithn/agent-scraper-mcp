"""Agent Scraper MCP Server — Web scraping, screenshots, and content extraction for AI agents.

Dual interface: MCP (Streamable HTTP) at /mcp + REST API at /api/v1/*.
"""
import os
import json
from typing import Dict

from fastapi import FastAPI, Request, Depends
from pydantic import BaseModel

from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import TransportSecuritySettings

from .tools import (
    scrape_url,
    scrape_structured,
    screenshot_url,
    extract_links,
    extract_meta,
    search_google,
)
from .middleware import RateLimiter, get_x402_middleware

# ---------------------------------------------------------------------------
# MCP Server (FastMCP — stateless Streamable HTTP)
# ---------------------------------------------------------------------------
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "agent-scraper-mcp.onrender.com")

mcp = FastMCP(
    "agent-scraper",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

@mcp.tool()
async def tool_scrape_url(url: str, format: str = "markdown") -> str:
    """
    Fetch a URL and extract clean text/markdown content (like readability).
    
    Args:
        url: URL to scrape
        format: Output format (text|markdown|html)
    
    Returns:
        Extracted content with title and metadata
    """
    result = await scrape_url(url, format)
    return json.dumps(result)

@mcp.tool()
async def tool_scrape_structured(url: str, selectors: Dict[str, str]) -> str:
    """
    Extract structured data from a URL using CSS selectors.
    
    Args:
        url: URL to scrape
        selectors: Dict of name → CSS selector (e.g. {"title": "h1.title", "price": ".price"})
    
    Returns:
        JSON object with extracted data for each selector
    """
    result = await scrape_structured(url, selectors)
    return json.dumps(result)

@mcp.tool()
async def tool_screenshot_url(
    url: str,
    width: int = 1280,
    height: int = 720,
    full_page: bool = False
) -> str:
    """
    Take a screenshot of a URL.
    
    Args:
        url: URL to screenshot
        width: Viewport width (default 1280)
        height: Viewport height (default 720)
        full_page: Capture full scrollable page (default false)
    
    Returns:
        Base64-encoded PNG image
    """
    result = await screenshot_url(url, width, height, full_page)
    return json.dumps(result)

@mcp.tool()
async def tool_extract_links(url: str, filter: str = None) -> str:
    """
    Extract all links from a URL with their text.
    
    Args:
        url: URL to scrape
        filter: Optional regex pattern to filter URLs
    
    Returns:
        Array of {text, href} objects
    """
    result = await extract_links(url, filter)
    return json.dumps(result)

@mcp.tool()
async def tool_extract_meta(url: str) -> str:
    """
    Extract metadata from a URL (title, description, OG tags, favicon, etc).
    
    Args:
        url: URL to scrape
    
    Returns:
        Metadata object with title, description, Open Graph tags, etc
    """
    result = await extract_meta(url)
    return json.dumps(result)

@mcp.tool()
async def tool_search_google(query: str, num_results: int = 10) -> str:
    """
    Search Google and return results.
    
    Args:
        query: Search query
        num_results: Number of results to return (default 10)
    
    Returns:
        Array of {title, url, snippet} objects
    """
    result = await search_google(query, num_results)
    return json.dumps(result)


# ---------------------------------------------------------------------------
# REST-only FastAPI app — mounted UNDER the MCP Starlette app
# ---------------------------------------------------------------------------
rate_limiter = RateLimiter(free_limit=50, ttl_seconds=86400)
x402 = get_x402_middleware()

rest_app = FastAPI(
    title="Agent Scraper MCP Server",
    description="Web scraping server for AI agents — screenshots, content extraction, structured scraping.",
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

async def check_access(request: Request, is_screenshot: bool = False):
    """Check rate limit and payment for requests."""
    allowed, remaining, reset_at = rate_limiter.check_limit(request)
    if not allowed:
        if x402.check_payment(request):
            return
        return x402.create_payment_required_response(is_screenshot=is_screenshot)

# --- Health & discovery ---
@rest_app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "0.1.0",
        "tools": 6,
        "free_tier": "50 requests/IP/day",
    }

@rest_app.get("/.well-known/agent-card.json")
async def agent_card():
    return {
        "name": "Agent Scraper",
        "description": "Web scraping server for AI agents — screenshots, content extraction, structured scraping",
        "version": "0.1.0",
        "url": os.getenv("PUBLIC_URL", "https://agent-scraper-mcp.onrender.com"),
        "capabilities": {"tools": 6, "screenshots": True, "google_search": True},
        "endpoints": {"mcp": "/mcp", "rest": "/api/v1", "openapi": "/docs"},
        "pricing": {
            "scraping": "$0.005/request",
            "screenshot": "$0.01/request",
            "free_tier": "50 requests/IP/day"
        }
    }

@rest_app.get("/.well-known/mcp/server-card.json")
async def mcp_server_card():
    return {
        "serverInfo": {"name": "agent-scraper", "version": "0.1.0"},
        "tools": [
            {
                "name": "scrape_url",
                "description": "Fetch a URL and extract clean text/markdown content (like readability).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to scrape"},
                        "format": {"type": "string", "description": "Output format", "enum": ["text", "markdown", "html"], "default": "markdown"}
                    },
                    "required": ["url"]
                }
            },
            {
                "name": "scrape_structured",
                "description": "Extract structured data from a URL using CSS selectors.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to scrape"},
                        "selectors": {
                            "type": "object",
                            "description": "Dict of name → CSS selector (e.g. {\"title\": \"h1.title\", \"price\": \".price\"})",
                            "additionalProperties": {"type": "string"}
                        }
                    },
                    "required": ["url", "selectors"]
                }
            },
            {
                "name": "screenshot_url",
                "description": "Take a screenshot of a URL.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to screenshot"},
                        "width": {"type": "integer", "description": "Viewport width", "default": 1280},
                        "height": {"type": "integer", "description": "Viewport height", "default": 720},
                        "full_page": {"type": "boolean", "description": "Capture full scrollable page", "default": False}
                    },
                    "required": ["url"]
                }
            },
            {
                "name": "extract_links",
                "description": "Extract all links from a URL with their text.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to scrape"},
                        "filter": {"type": "string", "description": "Optional regex pattern to filter URLs"}
                    },
                    "required": ["url"]
                }
            },
            {
                "name": "extract_meta",
                "description": "Extract metadata from a URL (title, description, OG tags, favicon, etc).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to scrape"}
                    },
                    "required": ["url"]
                }
            },
            {
                "name": "search_google",
                "description": "Search Google and return results.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "num_results": {"type": "integer", "description": "Number of results to return", "default": 10}
                    },
                    "required": ["query"]
                }
            }
        ]
    }

# --- REST endpoints ---
class ScrapeUrlIn(BaseModel):
    url: str
    format: str = "markdown"

class ScrapeStructuredIn(BaseModel):
    url: str
    selectors: Dict[str, str]

class ScreenshotIn(BaseModel):
    url: str
    width: int = 1280
    height: int = 720
    full_page: bool = False

class ExtractLinksIn(BaseModel):
    url: str
    filter: str = None

class ExtractMetaIn(BaseModel):
    url: str

class SearchGoogleIn(BaseModel):
    query: str
    num_results: int = 10

@rest_app.post("/api/v1/scrape_url", dependencies=[Depends(lambda r: check_access(r, False))])
async def r_scrape_url(req: ScrapeUrlIn):
    return await scrape_url(req.url, req.format)

@rest_app.post("/api/v1/scrape_structured", dependencies=[Depends(lambda r: check_access(r, False))])
async def r_scrape_structured(req: ScrapeStructuredIn):
    return await scrape_structured(req.url, req.selectors)

@rest_app.post("/api/v1/screenshot_url", dependencies=[Depends(lambda r: check_access(r, True))])
async def r_screenshot_url(req: ScreenshotIn):
    return await screenshot_url(req.url, req.width, req.height, req.full_page)

@rest_app.post("/api/v1/extract_links", dependencies=[Depends(lambda r: check_access(r, False))])
async def r_extract_links(req: ExtractLinksIn):
    return await extract_links(req.url, req.filter)

@rest_app.post("/api/v1/extract_meta", dependencies=[Depends(lambda r: check_access(r, False))])
async def r_extract_meta(req: ExtractMetaIn):
    return await extract_meta(req.url)

@rest_app.post("/api/v1/search_google", dependencies=[Depends(lambda r: check_access(r, False))])
async def r_search_google(req: SearchGoogleIn):
    return await search_google(req.query, req.num_results)


# ---------------------------------------------------------------------------
# Compose: MCP Starlette app is primary, REST FastAPI mounted inside it
# ---------------------------------------------------------------------------
from starlette.routing import Mount as StarletteMount
mcp._custom_starlette_routes.append(StarletteMount("/", app=rest_app))

# The final ASGI app
app = mcp.streamable_http_app()

"""Web scraping tool implementations."""
import re
import base64
import asyncio
from typing import Dict, List, Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from readability import Document
from playwright.async_api import async_playwright, Browser, Page


# Global browser instance for Playwright (initialized on first screenshot)
_browser: Browser | None = None
_browser_lock = asyncio.Lock()


async def get_browser() -> Browser:
    """Get or initialize the shared browser instance."""
    global _browser
    async with _browser_lock:
        if _browser is None:
            playwright = await async_playwright().start()
            _browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                ]
            )
        return _browser


async def fetch_html(url: str, timeout: int = 30) -> str:
    """Fetch HTML content from a URL using httpx."""
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def scrape_url(url: str, format: str = "markdown") -> Dict[str, Any]:
    """
    Fetch a URL and extract clean text/markdown content (like readability).
    
    Args:
        url: URL to scrape
        format: Output format (text|markdown|html)
    
    Returns:
        Dict with title, content, url
    """
    try:
        # Fetch HTML
        html = await fetch_html(url)
        
        # Use readability to extract main content
        doc = Document(html)
        title = doc.title()
        content_html = doc.summary()
        
        # Parse with BeautifulSoup for further processing
        soup = BeautifulSoup(content_html, 'lxml')
        
        if format == "text":
            content = soup.get_text(separator="\n", strip=True)
        elif format == "markdown":
            # Simple markdown conversion
            content = _html_to_markdown(soup)
        else:  # html
            content = str(soup)
        
        return {
            "success": True,
            "url": url,
            "title": title,
            "content": content,
            "format": format,
        }
    
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "url": url,
        }


def _html_to_markdown(soup: BeautifulSoup) -> str:
    """Convert BeautifulSoup HTML to basic markdown."""
    lines = []
    
    for elem in soup.descendants:
        if elem.name == 'h1':
            lines.append(f"\n# {elem.get_text(strip=True)}\n")
        elif elem.name == 'h2':
            lines.append(f"\n## {elem.get_text(strip=True)}\n")
        elif elem.name == 'h3':
            lines.append(f"\n### {elem.get_text(strip=True)}\n")
        elif elem.name == 'p':
            lines.append(f"{elem.get_text(strip=True)}\n")
        elif elem.name == 'a' and elem.get('href'):
            lines.append(f"[{elem.get_text(strip=True)}]({elem.get('href')})")
        elif elem.name == 'li':
            lines.append(f"- {elem.get_text(strip=True)}")
        elif elem.name == 'code':
            lines.append(f"`{elem.get_text(strip=True)}`")
        elif elem.name == 'pre':
            lines.append(f"\n```\n{elem.get_text(strip=True)}\n```\n")
    
    return "\n".join(lines)


async def scrape_structured(url: str, selectors: Dict[str, str]) -> Dict[str, Any]:
    """
    Extract structured data from a URL using CSS selectors.
    
    Args:
        url: URL to scrape
        selectors: Dict of name → CSS selector
    
    Returns:
        Dict with extracted data for each selector
    """
    try:
        html = await fetch_html(url)
        soup = BeautifulSoup(html, 'lxml')
        
        data = {}
        for name, selector in selectors.items():
            elements = soup.select(selector)
            
            if len(elements) == 1:
                # Single element: return text
                data[name] = elements[0].get_text(strip=True)
            elif len(elements) > 1:
                # Multiple elements: return list
                data[name] = [el.get_text(strip=True) for el in elements]
            else:
                # No match
                data[name] = None
        
        return {
            "success": True,
            "url": url,
            "data": data,
        }
    
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "url": url,
        }


async def screenshot_url(
    url: str,
    width: int = 1280,
    height: int = 720,
    full_page: bool = False
) -> Dict[str, Any]:
    """
    Take a screenshot of a URL.
    
    Args:
        url: URL to screenshot
        width: Viewport width
        height: Viewport height
        full_page: Capture full scrollable page
    
    Returns:
        Dict with base64 PNG image
    """
    try:
        browser = await get_browser()
        page = await browser.new_page(
            viewport={"width": width, "height": height}
        )
        
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            screenshot_bytes = await page.screenshot(
                full_page=full_page,
                type="png"
            )
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
        finally:
            await page.close()
        
        return {
            "success": True,
            "url": url,
            "image": screenshot_b64,
            "width": width,
            "height": height,
            "full_page": full_page,
        }
    
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "url": url,
        }


async def extract_links(url: str, filter: str = None) -> Dict[str, Any]:
    """
    Extract all links from a URL with their text.
    
    Args:
        url: URL to scrape
        filter: Optional regex pattern to filter URLs
    
    Returns:
        Array of {text, href}
    """
    try:
        html = await fetch_html(url)
        soup = BeautifulSoup(html, 'lxml')
        
        links = []
        for a in soup.find_all('a', href=True):
            href = urljoin(url, a['href'])
            text = a.get_text(strip=True) or "(no text)"
            
            # Apply filter if provided
            if filter:
                if not re.search(filter, href):
                    continue
            
            links.append({
                "text": text,
                "href": href,
            })
        
        return {
            "success": True,
            "url": url,
            "links": links,
            "count": len(links),
        }
    
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "url": url,
        }


async def extract_meta(url: str) -> Dict[str, Any]:
    """
    Extract metadata from a URL (title, description, OG tags, favicon, etc).
    
    Args:
        url: URL to scrape
    
    Returns:
        Metadata object
    """
    try:
        html = await fetch_html(url)
        soup = BeautifulSoup(html, 'lxml')
        
        # Extract title
        title = None
        if soup.title:
            title = soup.title.string
        
        # Extract description
        description = None
        desc_tag = soup.find('meta', attrs={'name': 'description'})
        if desc_tag:
            description = desc_tag.get('content')
        
        # Extract Open Graph tags
        og_tags = {}
        for meta in soup.find_all('meta', property=re.compile(r'^og:')):
            prop = meta.get('property')
            content = meta.get('content')
            if prop and content:
                og_tags[prop] = content
        
        # Extract Twitter Card tags
        twitter_tags = {}
        for meta in soup.find_all('meta', attrs={'name': re.compile(r'^twitter:')}):
            name = meta.get('name')
            content = meta.get('content')
            if name and content:
                twitter_tags[name] = content
        
        # Extract favicon
        favicon = None
        icon_link = soup.find('link', rel=re.compile(r'icon', re.I))
        if icon_link and icon_link.get('href'):
            favicon = urljoin(url, icon_link['href'])
        
        # Extract canonical URL
        canonical = None
        canonical_link = soup.find('link', rel='canonical')
        if canonical_link and canonical_link.get('href'):
            canonical = canonical_link['href']
        
        return {
            "success": True,
            "url": url,
            "meta": {
                "title": title,
                "description": description,
                "canonical": canonical,
                "favicon": favicon,
                "og": og_tags,
                "twitter": twitter_tags,
            }
        }
    
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "url": url,
        }


async def search_google(query: str, num_results: int = 10) -> Dict[str, Any]:
    """
    Search Google and return results.
    
    Args:
        query: Search query
        num_results: Number of results to return (default 10)
    
    Returns:
        Array of {title, url, snippet}
    """
    try:
        from googlesearch import search
        
        results = []
        for url in search(query, num=num_results, stop=num_results, pause=2):
            # Fetch title and snippet from the page
            try:
                html = await fetch_html(url)
                soup = BeautifulSoup(html, 'lxml')
                
                title = soup.title.string if soup.title else url
                
                # Try to get meta description as snippet
                snippet = ""
                desc_tag = soup.find('meta', attrs={'name': 'description'})
                if desc_tag:
                    snippet = desc_tag.get('content', "")
                
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                })
            except:
                # If fetching fails, just add the URL
                results.append({
                    "title": url,
                    "url": url,
                    "snippet": "",
                })
        
        return {
            "success": True,
            "query": query,
            "results": results,
            "count": len(results),
        }
    
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "query": query,
        }

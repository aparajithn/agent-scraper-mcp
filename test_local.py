#!/usr/bin/env python3
"""Quick local test of scraping functions."""
import sys
sys.path.insert(0, '/home/node/.openclaw/workspace/projects/agent-scraper-mcp')

from src.tools.scraper import scrape_url, extract_links, extract_meta

# Test scrape_url
print("Testing scrape_url...")
result = scrape_url("https://example.com", format="text")
print(f"Success: {result['success']}")
if result['success']:
    print(f"Title: {result['title']}")
    print(f"Content length: {len(result['content'])} chars")
else:
    print(f"Error: {result.get('error', 'Unknown error')}")
print()

# Test extract_links
print("Testing extract_links...")
result = extract_links("https://example.com")
print(f"Success: {result['success']}")
if result['success']:
    print(f"Found {result['count']} links")
print()

# Test extract_meta
print("Testing extract_meta...")
result = extract_meta("https://example.com")
print(f"Success: {result['success']}")
if result['success']:
    print(f"Title: {result['meta']['title']}")
    print(f"Description: {result['meta']['description']}")
print()

print("All basic tests passed!")

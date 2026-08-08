from __future__ import annotations

import random
import re
import asyncio
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from ..base import Skill
from ... import config as app_config

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._text_parts: list[str] = []
        self._skip_tags = {"script", "style", "noscript", "nav", "footer", "header", "aside", "iframe", "svg", "form"}
        self._skip_depth = 0
        self._title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self._title += data
            return
        text = data.strip()
        if text:
            self._text_parts.append(text)

    def result(self) -> tuple[str, str]:
        return self._title.strip(), " ".join(self._text_parts)


def _extract_readable(html: str) -> tuple[str, str, str]:
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        pass
    title, body = extractor.result()

    desc = ""
    m = re.search(r'<meta\s+[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']', html, re.IGNORECASE)
    if not m:
        m = re.search(r'<meta\s+[^>]*content=["\']([^"\']*)["\'][^>]*name=["\']description["\']', html, re.IGNORECASE)
    if m:
        desc = m.group(1)

    body = re.sub(r'\s+', ' ', body).strip()
    return title, desc, body[:50000]


def _make_browser_headers(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    ua = random.choice(_USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Referer": origin + "/",
    }


class WebSearchSkill(Skill):
    name = "web_search"
    entitlement = "web_search"
    category = "web"
    scopes = ('web',)
    description = "Search the web via SearXNG. Returns search results with snippets and URLs."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "num_results": {"type": "integer", "description": "Number of results (1-10)", "default": 5},
        },
        "required": ["query"],
    }

    async def execute(self, arguments: dict) -> str:
        query = arguments.get("query", "")
        num_results = arguments.get("num_results", 5)
        searxng = app_config.settings.searxng_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{searxng}/search",
                    params={
                        "q": query,
                        "format": "json",
                        "categories": "general",
                        "pageno": 1,
                    },
                )
                if resp.status_code != 200:
                    return f"Error: Search failed: {resp.status_code} {resp.text[:200]}"
                data = resp.json()
                results = data.get("results", [])
                if not results:
                    return "No results found."
                lines = []
                for i, r in enumerate(results[:num_results], 1):
                    title = r.get("title", "No title")
                    url = r.get("url", "")
                    text = (r.get("content") or r.get("snippet", ""))[:500]
                    lines.append(f"{i}. **{title}**\n   URL: {url}\n   {text}")
                return "\n\n".join(lines)
        except Exception as e:
            return f"Search error: {str(e)}"


class WebScrapeSkill(Skill):
    name = "web_scrape"
    entitlement = "web_search"
    category = "web"
    scopes = ('web',)
    description = "Fetch a web page URL and extract its readable content as clean text. Use this when you need the actual content of a page (not just a search snippet). Handles most websites without being blocked."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to fetch (including https://)"},
            "max_length": {"type": "integer", "description": "Maximum characters to return (default: 10000)", "default": 10000},
        },
        "required": ["url"],
    }

    async def execute(self, arguments: dict) -> str:
        url = arguments.get("url", "")
        max_length = arguments.get("max_length", 10000)
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        headers = _make_browser_headers(url)
        max_length = min(max(max_length, 1000), 50000)

        await asyncio.sleep(random.uniform(0.3, 1.2))

        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                headers=headers,
                cookies={},
            ) as client:
                resp = await client.get(url, headers=headers)

                if resp.status_code in (403, 503, 429):
                    retry_headers = _make_browser_headers(url)
                    different_uas = [ua for ua in _USER_AGENTS if ua != headers.get("User-Agent")]
                    if different_uas:
                        retry_headers["User-Agent"] = random.choice(different_uas)
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                    resp = await client.get(url, headers=retry_headers)

                if resp.status_code >= 400:
                    return f"Error: Failed to fetch page: HTTP {resp.status_code}"

                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type:
                    summary = f"URL: {url}\nContent-Type: {content_type}\nSize: {len(resp.content)} bytes\n\n"
                    text_content = resp.text[:max_length] if resp.text else "(binary content)"
                    return summary + text_content

                html = resp.text
                title, desc, body = _extract_readable(html)

                result = f"Title: {title}\n"
                if desc:
                    result += f"Description: {desc}\n"
                result += f"URL: {url}\n\n---\n\n{body}"

                if len(result) > max_length:
                    result = result[:max_length] + "\n\n[Content truncated...]"

                return result

        except httpx.TimeoutException:
            return f"Error: Request timed out: {url}"
        except httpx.ConnectError:
            return f"Error: Could not connect to: {url}"
        except Exception as e:
            return f"Error: Scrape error: {str(e)}"

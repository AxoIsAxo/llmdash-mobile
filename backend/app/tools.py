import json
import re
import random
import asyncio
import inspect
from typing import Any, Optional
from datetime import datetime
import os
import base64
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

from sqlalchemy import select

import httpx
from docx import Document
from fpdf import FPDF
from odf.opendocument import OpenDocumentText
from odf.text import P

from . import config as app_config
from .sandbox import run_in_alpine


def _normalize_ws_collapse(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


def _trim_per_line(s: str) -> str:
    return '\n'.join(line.strip() for line in s.split('\n'))


def _count_and_replace(haystack: str, needle: str, replacement: str) -> tuple[int, str]:
    """Returns (count, replaced) where replaced is haystack with the first match swapped in (only if count==1)."""
    count = haystack.count(needle)
    if count != 1:
        return count, haystack
    return count, haystack.replace(needle, replacement, 1)


def _try_patch_strategies(current: str, old_str: str, new_str: str) -> tuple[str, str]:
    """Try matching strategies in order: exact, trim-per-line, whitespace-collapse.
    Returns (outcome, new_css). outcome is one of: 'ok', 'no_match', 'ambiguous'.
    On 'ok', new_css holds the patched string. On other outcomes, new_css is the original current."""
    if not old_str:
        return "no_match", current

    ambiguous = False

    count, replaced = _count_and_replace(current, old_str, new_str)
    if count == 1:
        return "ok", replaced
    if count > 1:
        ambiguous = True

    cur_trimmed = _trim_per_line(current)
    old_trimmed = _trim_per_line(old_str)
    count, replaced = _count_and_replace(cur_trimmed, old_trimmed, new_str)
    if count == 1:
        return "ok", replaced
    if count > 1:
        ambiguous = True

    cur_collapsed = _normalize_ws_collapse(current)
    old_collapsed = _normalize_ws_collapse(old_str)
    count, replaced = _count_and_replace(cur_collapsed, old_collapsed, new_str)
    if count == 1:
        return "ok", replaced
    if count > 1:
        ambiguous = True

    return ("ambiguous" if ambiguous else "no_match"), current


def _apply_patch(current: str, old_str: str, new_str: str) -> tuple[bool, str, str]:
    """Returns (success, error_message, new_css). error_message set on failure."""
    if not old_str:
        return False, "old_str is empty — cannot patch", current
    if current == "":
        return False, "no match found — user CSS is empty. Did you call get_user_css first?", current

    outcome, new_css = _try_patch_strategies(current, old_str, new_str)
    if outcome == "ok":
        return True, "", new_css
    if outcome == "ambiguous":
        return False, "ambiguous match — add more context lines to old_str", current

    preview_lines = old_str.split('\n')[:3]
    preview = '\n'.join(preview_lines)
    return False, f"no match found. First lines of old_str:\n{preview}", current


def _encode_new_css_marker(new_css: str) -> str:
    return "NEW_CSS:" + base64.b64encode(new_css.encode("utf-8")).decode("ascii")


available_tools: dict[str, dict] = {}


def _escape_and_format(text: str) -> str:
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    return text


def _content_to_html(content: str) -> str:
    lines = content.split('\n')
    result = []
    in_list = False
    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            if in_list:
                result.append('</ul>')
                in_list = False
            result.append('')
            continue
        if stripped.startswith('### '):
            if in_list: result.append('</ul>'); in_list = False
            result.append(f'<h3>{_escape_and_format(stripped[4:])}</h3>')
        elif stripped.startswith('## '):
            if in_list: result.append('</ul>'); in_list = False
            result.append(f'<h2>{_escape_and_format(stripped[3:])}</h2>')
        elif stripped.startswith('# '):
            if in_list: result.append('</ul>'); in_list = False
            result.append(f'<h1>{_escape_and_format(stripped[2:])}</h1>')
        elif stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list: result.append('<ul>'); in_list = True
            result.append(f'<li>{_escape_and_format(stripped[2:])}</li>')
        else:
            if in_list: result.append('</ul>'); in_list = False
            result.append(f'<p>{_escape_and_format(stripped)}</p>')
    if in_list:
        result.append('</ul>')
    return '\n'.join(result)


def _preview_html(format: str, filename: str, content: str) -> str:
    body = _content_to_html(content)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:Georgia,'Times New Roman',serif; background:#f0f0f0; padding:20px; }}
  .page {{ max-width:800px; margin:0 auto; background:#fff; padding:50px 60px; box-shadow:0 1px 3px rgba(0,0,0,.12),0 1px 2px rgba(0,0,0,.24); min-height:90vh; }}
  h1 {{ font-size:24px; margin:0 0 12px 0; border-bottom:1px solid #ddd; padding-bottom:8px; }}
  h2 {{ font-size:20px; margin:18px 0 8px 0; }}
  h3 {{ font-size:16px; margin:14px 0 6px 0; }}
  p {{ margin:0 0 10px 0; line-height:1.7; }}
  ul {{ margin:0 0 10px 20px; }}
  li {{ line-height:1.6; }}
  strong {{ font-weight:bold; }}
  em {{ font-style:italic; }}
  code {{ background:#f5f5f5; padding:1px 4px; border-radius:3px; font-family:monospace; font-size:0.9em; }}
  .file-label {{ color:#888; font-size:12px; margin-bottom:24px; }}
</style></head><body><div class="page">
  <div class="file-label">{filename}.{format}</div>
  {body}
</div></body></html>"""


def tool(name: str, description: str, input_schema: dict):
    def decorator(func):
        available_tools[name] = {
            "name": name,
            "description": description,
            "input_schema": input_schema,
            "fn": func,
        }
        return func
    return decorator


@tool(
    name="web_search",
    description="Search the web via SearXNG. Returns search results with snippets and URLs.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "num_results": {"type": "integer", "description": "Number of results (1-10)", "default": 5},
        },
        "required": ["query"],
    },
)
async def web_search(query: str, num_results: int = 5) -> str:
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
                return f"Search failed: {resp.status_code} {resp.text[:200]}"
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


@tool(
    name="web_scrape",
    description="Fetch a web page URL and extract its readable content as clean text. Use this when you need the actual content of a page (not just a search snippet). Handles most websites without being blocked.",
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to fetch (including https://)"},
            "max_length": {"type": "integer", "description": "Maximum characters to return (default: 10000)", "default": 10000},
        },
        "required": ["url"],
    },
)
async def web_scrape(url: str, max_length: int = 10000) -> str:
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
                return f"Failed to fetch page: HTTP {resp.status_code}"

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
        return f"Request timed out: {url}"
    except httpx.ConnectError:
        return f"Could not connect to: {url}"
    except Exception as e:
        return f"Scrape error: {str(e)}"


@tool(
    name="edit_document",
    description="Create or edit a text or document file. Supports rich document formats (docx, pdf, odt) and any text-based file type (py, js, ts, html, css, json, xml, yaml, toml, md, txt, csv, sh, rs, go, java, c, cpp, sql, r, rb, php, lua, swift, kt, tf, ini, cfg, env, gitignore, Dockerfile, Makefile, etc.). Returns file path and download link.",
    input_schema={
        "type": "object",
        "properties": {
            "format": {"type": "string", "description": "File extension (e.g. py, js, html, txt, md, json, docx, pdf, odt). For rich documents use docx/pdf/odt; everything else is saved as a plain text file."},
            "filename": {"type": "string", "description": "Desired filename (without extension)"},
            "content": {"type": "string", "description": "Full file content. For docx/odt: use markdown-like formatting with # headings, **bold**, bullet lists. For pdf: plain text."},
        },
        "required": ["format", "filename", "content"],
    },
)
async def edit_document(format: str, filename: str, content: str) -> str:
    docs_dir = "data/documents"
    os.makedirs(docs_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in filename)
    ext = format.lower()

    filepath = os.path.join(docs_dir, f"{safe_name}_{timestamp}.{ext}")

    try:
        if ext == "docx":
            doc = Document()
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("# "):
                    doc.add_heading(line[2:], level=1)
                elif line.startswith("## "):
                    doc.add_heading(line[3:], level=2)
                elif line.startswith("### "):
                    doc.add_heading(line[4:], level=3)
                elif line.startswith("- ") or line.startswith("* "):
                    doc.add_paragraph(line[2:], style="List Bullet")
                else:
                    doc.add_paragraph(line)
            doc.save(filepath)

        elif ext == "pdf":
            pdf = FPDF()
            pdf.add_page()
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.add_font("DejaVu", "", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", uni=True)
            pdf.add_font("DejaVu", "B", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", uni=True)
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("# "):
                    pdf.set_font("DejaVu", "B", 16)
                    pdf.cell(0, 10, line[2:], new_x="LMARGIN", new_y="NEXT")
                elif line.startswith("## "):
                    pdf.set_font("DejaVu", "B", 14)
                    pdf.cell(0, 8, line[3:], new_x="LMARGIN", new_y="NEXT")
                else:
                    pdf.set_font("DejaVu", "", 11)
                    pdf.multi_cell(0, 6, line)
            pdf.output(filepath)

        elif ext == "odt":
            doc = OpenDocumentText()
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                p = P(text=line)
                doc.text.addElement(p)
            doc.save(filepath)

        else:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

        if ext in ('docx', 'pdf', 'odt'):
            preview_html = _preview_html(ext, safe_name, content)
            encoded = base64.b64encode(preview_html.encode()).decode()
            return f"Document created: {filepath}\nDownload: /api/files/{os.path.basename(filepath)}\nHTML_RENDER:{encoded}"
        return f"Document created: {filepath}\nDownload: /api/files/{os.path.basename(filepath)}"
    except Exception as e:
        return f"Document creation error: {str(e)}"


@tool(
    name="render_html",
    description="Render HTML with CSS and JavaScript in a sandboxed iframe preview. Returns HTML that the chat UI will display visually.",
    input_schema={
        "type": "object",
        "properties": {
            "html": {"type": "string", "description": "HTML content"},
            "css": {"type": "string", "description": "CSS styles (optional)", "default": ""},
            "js": {"type": "string", "description": "JavaScript code (optional, sandboxed)", "default": ""},
        },
        "required": ["html"],
    },
)
async def render_html(html: str, css: str = "", js: str = "") -> str:
    full_html = "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
    if css:
        full_html += f"<style>{css}</style>"
    full_html += "</head><body>"
    full_html += html
    if js:
        full_html += f"<script>{js}</script>"
    full_html += "</body></html>"
    encoded = base64.b64encode(full_html.encode()).decode()
    return f"HTML_RENDER:{encoded}"


@tool(
    name="run_command",
    description="Execute Linux commands in a secure Alpine Linux sandbox via Docker. Has network access and common tools (curl, wget, python3, node, git).",
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command(s) to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
        },
        "required": ["command"],
    },
)
async def run_command(command: str, timeout: int = 30) -> str:
    result = await run_in_alpine(command, timeout=timeout)
    return result


@tool(
    name="get_user_css",
    description="Get the current user's custom stylesheet. Returns the FULL CSS string the user has saved (or empty string if none). This stylesheet controls the entire LLMDash UI — not just colors. It can contain rules for colors, backgrounds, borders, border-radius, shadows, spacing, font family/size/weight, line-height, opacity, transitions, animations, layout widths, z-index, and any other CSS property. ALWAYS call this first before any styling edit so you operate on the real current state, not a stale memory.",
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def get_user_css(_current_user: dict = None) -> str:
    if not _current_user:
        return "Error: Not authenticated"
    from .database import async_session, User
    async with async_session() as sess:
        result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
        user = result.scalar_one_or_none()
        if user and user.custom_css:
            return user.custom_css
        return ""


@tool(
    name="patch_user_css",
    description="Make a targeted edit to the user's custom stylesheet by finding an exact block of text and replacing it. This is the PRIMARY edit tool and is NOT limited to colors — use it for any CSS change (fonts, spacing, borders, radius, shadows, layout, animations, etc.). Always call get_user_css first so old_str matches the real current state. Provide enough surrounding lines in old_str to make it unique. Matching tries (in order): 1) exact match, 2) trim leading/trailing whitespace per line, 3) collapse all whitespace runs to single space. On success, saves server-side and applies immediately.",
    input_schema={
        "type": "object",
        "properties": {
            "old_str": {"type": "string", "description": "The exact block of CSS to find. Include enough surrounding lines (a few lines before and after) to make it unique in the current stylesheet."},
            "new_str": {"type": "string", "description": "The replacement CSS block. Pass an empty string to delete the matched block."},
            "description": {"type": "string", "description": "Optional one-line description of what this patch changes (for your own reasoning; not displayed to the user)."},
        },
        "required": ["old_str", "new_str"],
    },
)
async def patch_user_css(old_str: str, new_str: str, description: str = None, _current_user: dict = None) -> str:
    if not _current_user:
        return "Error: Not authenticated"
    from .database import async_session, User
    async with async_session() as sess:
        result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
        user = result.scalar_one_or_none()
        if not user:
            return "Error: User not found"

        current = user.custom_css or ""
        success, err, new_css = _apply_patch(current, old_str, new_str)
        if not success:
            return f"Error: {err}"

        user.custom_css = new_css or None
        await sess.commit()
        return f"patched successfully\n{_encode_new_css_marker(new_css)}"


@tool(
    name="append_user_css",
    description="Append new CSS rules to the END of the user's custom stylesheet. Use this when adding entirely new rules that don't exist yet. Works for any kind of style — colors, fonts, spacing, borders, radius, shadows, layout, animations, etc. Saves server-side and applies immediately.",
    input_schema={
        "type": "object",
        "properties": {
            "css": {"type": "string", "description": "The CSS rules to append. Do not include existing rules — only the new ones."},
        },
        "required": ["css"],
    },
)
async def append_user_css(css: str, _current_user: dict = None) -> str:
    if not _current_user:
        return "Error: Not authenticated"
    from .database import async_session, User
    async with async_session() as sess:
        result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
        user = result.scalar_one_or_none()
        if not user:
            return "Error: User not found"

        current = user.custom_css or ""
        if current and not current.endswith("\n"):
            current += "\n"
        if current and not current.endswith("\n\n"):
            current += "\n"
        new_css = current + css
        if not new_css.endswith("\n"):
            new_css += "\n"

        user.custom_css = new_css or None
        await sess.commit()
        return f"appended successfully\n{_encode_new_css_marker(new_css)}"


@tool(
    name="set_user_css",
    description="FULL REPLACEMENT of the user's custom stylesheet. Do NOT use this for targeted edits — use patch_user_css instead. Only call this when the user explicitly asks to 'reset', 'completely redo', or 'overwrite' all styles. The replacement can target any CSS property (colors, fonts, spacing, borders, layout, animations, etc.), not just colors. Pass the COMPLETE CSS string (including any existing styles you want to keep).",
    input_schema={
        "type": "object",
        "properties": {
            "css": {"type": "string", "description": "The complete CSS string to set as the user's custom CSS."},
        },
        "required": ["css"],
    },
)
async def set_user_css(css: str, _current_user: dict = None) -> str:
    if not _current_user:
        return "Error: Not authenticated"
    from .database import async_session, User
    async with async_session() as sess:
        result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
        user = result.scalar_one_or_none()
        if not user:
            return "Error: User not found"
        user.custom_css = css or None
        await sess.commit()
        return f"CSS saved successfully ({len(css)} characters)\n{_encode_new_css_marker(css or '')}"


def get_tool_definitions():
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        for t in available_tools.values()
    ]


async def execute_tool(name: str, arguments: dict, **context) -> str:
    tool_def = available_tools.get(name)
    if not tool_def:
        return f"Error: Unknown tool '{name}'"
    fn = tool_def["fn"]
    try:
        sig = inspect.signature(fn)
        filtered_context = {k: v for k, v in context.items() if k in sig.parameters}
        if asyncio.iscoroutinefunction(fn):
            result = await fn(**arguments, **filtered_context)
        else:
            result = fn(**arguments, **filtered_context)
        return str(result)
    except TypeError as e:
        try:
            type_hints = {k: v for k, v in fn.__annotations__.items() if not k.startswith("_")}
        except Exception:
            type_hints = {}
        params = ", ".join(
            f"{p.name}: {_format_type(type_hints.get(p.name, p.annotation))}"
            for p in sig.parameters.values()
            if not p.name.startswith("_")
        )
        provided = ", ".join(arguments.keys()) or "(none)"
        return (
            f"Tool execution error: {e}\n"
            f"Function signature: {name}({params})\n"
            f"You provided arguments: {{{provided}}}\n"
            f"If the argument you intended to send is large or contains special "
            f"characters, try sending it as a shorter, properly-escaped JSON string.\n"
            f"Please retry with the correct arguments matching the signature above."
        )
    except Exception as e:
        return f"Tool execution error: {e}"


def _format_type(annotation) -> str:
    """Render a type annotation in a model-friendly form ('str', 'int', etc).

    Falls back to 'any' for empty annotations, complex generic forms, or
    anything that doesn't have a clean __name__.
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return "any"
    if isinstance(annotation, str):
        return annotation
    name = getattr(annotation, "__name__", None)
    if name:
        return name
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        origin_name = getattr(origin, "__name__", None) or str(origin)
        args = getattr(annotation, "__args__", ())
        if args:
            inner = ", ".join(_format_type(a) for a in args)
            return f"{origin_name}[{inner}]"
        return origin_name
    return "any"

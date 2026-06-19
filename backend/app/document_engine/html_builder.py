from __future__ import annotations
from typing import Optional
from .ast_types import (
    BlockNode, InlineNode, Text, Bold, Italic, Strikethrough, Underline,
    InlineCode, Link, Image, MathInline, Subscript, Superscript,
    Heading, Paragraph, BulletList, ListItem, Table, CodeBlock,
    BlockQuote, HorizontalRule, ChartDirective, MathBlock,
)
from .chart_generator import generate_chart_svg
import base64
import os
import re


def build_html(
    nodes: list[BlockNode],
    title: str = "Document",
    charts_dir: str = "data/documents/charts",
) -> str:
    body_parts: list[str] = []
    for node in nodes:
        body_parts.append(_render_block(node, charts_dir))
    body = "\n".join(body_parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape_html(title)}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:Georgia,'Times New Roman',serif; background:#f0f0f0; padding:20px; }}
  .page {{ max-width:800px; margin:0 auto; background:#fff; padding:50px 60px; box-shadow:0 1px 3px rgba(0,0,0,.12),0 1px 2px rgba(0,0,0,.24); min-height:90vh; }}
  .file-label {{ color:#888; font-size:12px; margin-bottom:24px; }}
  h1 {{ font-size:24px; margin:0 0 12px 0; border-bottom:1px solid #ddd; padding-bottom:8px; font-family:'Helvetica Neue',Arial,sans-serif; }}
  h2 {{ font-size:20px; margin:18px 0 8px 0; font-family:'Helvetica Neue',Arial,sans-serif; }}
  h3 {{ font-size:17px; margin:14px 0 6px 0; font-family:'Helvetica Neue',Arial,sans-serif; }}
  h4 {{ font-size:15px; margin:12px 0 4px 0; font-family:'Helvetica Neue',Arial,sans-serif; }}
  h5 {{ font-size:13px; margin:10px 0 3px 0; font-family:'Helvetica Neue',Arial,sans-serif; }}
  h6 {{ font-size:12px; margin:8px 0 2px 0; font-family:'Helvetica Neue',Arial,sans-serif; }}
  p {{ margin:0 0 10px 0; line-height:1.7; }}
  ul, ol {{ margin:0 0 10px 20px; }}
  li {{ line-height:1.6; }}
  strong {{ font-weight:bold; }}
  em {{ font-style:italic; }}
  s {{ text-decoration:line-through; }}
  u {{ text-decoration:underline; }}
  code {{ background:#f5f5f5; padding:1px 4px; border-radius:3px; font-family:'Consolas','Courier New',monospace; font-size:0.9em; }}
  pre {{ background:#f8f8f8; border:1px solid #e0e0e0; border-radius:4px; padding:12px 16px; overflow-x:auto; margin:0 0 10px 0; }}
  pre code {{ background:none; padding:0; border-radius:0; font-size:0.85em; }}
  blockquote {{ margin:0 0 10px 0; padding:0 0 0 16px; border-left:4px solid #ccc; color:#555; font-style:italic; }}
  table {{ border-collapse:collapse; width:100%; margin:0 0 10px 0; }}
  th, td {{ border:1px solid #ccc; padding:8px 12px; text-align:left; }}
  th {{ background:#f0f0f0; font-weight:bold; }}
  hr {{ border:none; border-top:1px solid #ddd; margin:20px 0; }}
  img {{ max-width:100%; height:auto; }}
  .chart-container {{ margin:10px 0; text-align:center; }}
  .chart-container svg {{ max-width:100%; }}
  .math {{ font-style:italic; }}
  sub {{ font-size:0.8em; vertical-align:sub; }}
  sup {{ font-size:0.8em; vertical-align:super; }}
</style>
</head>
<body>
<div class="page">
  <div class="file-label">{_escape_html(title)}</div>
  {body}
</div>
</body>
</html>"""


def _render_block(node: BlockNode, charts_dir: str) -> str:
    if isinstance(node, Heading):
        tag = f"h{node.level}"
        inner = "".join(_render_inline(c) for c in node.children)
        return f"<{tag}>{inner}</{tag}>"
    elif isinstance(node, Paragraph):
        inner = "".join(_render_inline(c) for c in node.children)
        return f"<p>{inner}</p>"
    elif isinstance(node, BulletList):
        tag = "ol" if node.ordered else "ul"
        items = "".join(f"<li>{_render_block_content(item, charts_dir)}</li>" for item in node.items)
        return f"<{tag}>{items}</{tag}>"
    elif isinstance(node, ListItem):
        return _render_block_content(node, charts_dir)
    elif isinstance(node, Table):
        return _render_table(node)
    elif isinstance(node, CodeBlock):
        lang = node.language or ""
        code = _escape_html(node.code)
        if lang:
            return f'<pre><code class="language-{_escape_html(lang)}">{code}</code></pre>'
        return f"<pre><code>{code}</code></pre>"
    elif isinstance(node, BlockQuote):
        inner = "".join(_render_block(c, charts_dir) for c in node.children)
        return f"<blockquote>{inner}</blockquote>"
    elif isinstance(node, HorizontalRule):
        return "<hr>"
    elif isinstance(node, ChartDirective):
        return _render_chart(node, charts_dir)
    elif isinstance(node, MathBlock):
        return f'<div class="math">\\[{_escape_html(node.latex)}\\]</div>'
    return ""


def _render_block_content(item: ListItem, charts_dir: str) -> str:
    parts: list[str] = []
    for child in item.children:
        if isinstance(child, (Paragraph, Heading, BulletList, Table, CodeBlock, BlockQuote)):
            parts.append(_render_block(child, charts_dir))
        else:
            parts.append(_render_block(child, charts_dir))
    return "".join(parts)


def _render_table(node: Table) -> str:
    parts = ["<table>"]
    if node.headers:
        parts.append("<thead><tr>")
        for i, cell in enumerate(node.headers):
            align = node.aligns[i] if i < len(node.aligns) else None
            style = f' style="text-align:{align}"' if align else ""
            parts.append(f"<th{style}>{''.join(_render_inline(c) for c in cell)}</th>")
        parts.append("</tr></thead>")
    if node.rows:
        parts.append("<tbody>")
        for row in node.rows:
            parts.append("<tr>")
            for i, cell in enumerate(row):
                align = node.aligns[i] if i < len(node.aligns) else None
                style = f' style="text-align:{align}"' if align else ""
                parts.append(f"<td{style}>{''.join(_render_inline(c) for c in cell)}</td>")
            parts.append("</tr>")
        parts.append("</tbody>")
    parts.append("</table>")
    return "".join(parts)


def _render_chart(node: ChartDirective, charts_dir: str) -> str:
    try:
        svg = generate_chart_svg(node)
        if svg:
            return f'<div class="chart-container">{svg}</div>'
    except Exception:
        pass
    return f'<p class="chart-error">[Chart: {_escape_html(node.chart_type)} — {_escape_html(node.title)}]</p>'


def _render_inline(node: InlineNode) -> str:
    if isinstance(node, Text):
        return _escape_html(node.text)
    elif isinstance(node, Bold):
        return f"<strong>{''.join(_render_inline(c) for c in node.children)}</strong>"
    elif isinstance(node, Italic):
        return f"<em>{''.join(_render_inline(c) for c in node.children)}</em>"
    elif isinstance(node, Strikethrough):
        return f"<s>{''.join(_render_inline(c) for c in node.children)}</s>"
    elif isinstance(node, Underline):
        return f"<u>{''.join(_render_inline(c) for c in node.children)}</u>"
    elif isinstance(node, InlineCode):
        return f"<code>{_escape_html(node.text)}</code>"
    elif isinstance(node, Link):
        return f'<a href="{_escape_attr(node.url)}">{_escape_html(node.text)}</a>'
    elif isinstance(node, Image):
        src = _escape_attr(node.url)
        alt = _escape_attr(node.alt)
        style = ""
        if node.width:
            style += f" width:{node.width}px;"
        if node.height:
            style += f" height:{node.height}px;"
        return f'<img src="{src}" alt="{alt}" style="{style}" />'
    elif isinstance(node, MathInline):
        return f'<span class="math">\\({_escape_html(node.latex)}\\)</span>'
    elif isinstance(node, Subscript):
        return f"<sub>{''.join(_render_inline(c) for c in node.children)}</sub>"
    elif isinstance(node, Superscript):
        return f"<sup>{''.join(_render_inline(c) for c in node.children)}</sup>"
    return ""


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

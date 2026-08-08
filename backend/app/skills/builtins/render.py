from __future__ import annotations

import base64

from ..base import Skill


class RenderHtmlSkill(Skill):
    name = "render_html"
    entitlement = "render"
    description = "Render HTML with CSS and JavaScript in a sandboxed iframe preview. Returns HTML that the chat UI will display visually."
    input_schema = {
        "type": "object",
        "properties": {
            "html": {"type": "string", "description": "HTML content"},
            "css": {"type": "string", "description": "CSS styles (optional)", "default": ""},
            "js": {"type": "string", "description": "JavaScript code (optional, sandboxed)", "default": ""},
        },
        "required": ["html"],
    }

    async def execute(self, arguments: dict) -> str:
        html = arguments.get("html", "")
        css = arguments.get("css", "")
        js = arguments.get("js", "")
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


class RenderSvgSkill(Skill):
    name = "render_svg"
    entitlement = "render"
    description = "Create an SVG vector graphic and display it in the chat. Use this for diagrams, charts, icons, illustrations, flowcharts, network graphs, architectural diagrams, or any vector graphics that should render inline as proper SVG (scalable, interactive, stylable). Pass the complete <svg>...</svg> markup including XML namespace. The SVG will be rendered inline in the conversation."
    input_schema = {
        "type": "object",
        "properties": {
            "svg": {
                "type": "string",
                "description": "The complete SVG markup including <svg> tag with xmlns attribute.",
            },
            "title": {
                "type": "string",
                "description": "Optional title/description for the SVG graphic.",
            },
        },
        "required": ["svg"],
    }

    async def execute(self, arguments: dict) -> str:
        svg = arguments.get("svg", "")
        title = arguments.get("title", "")
        if "<svg" not in svg:
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">{svg}</svg>'
        encoded = base64.b64encode(svg.encode()).decode()
        label = f" ({title})" if title else ""
        return f"SVG_RENDER:{encoded}{label}"

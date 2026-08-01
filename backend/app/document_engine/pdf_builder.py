from __future__ import annotations
from fpdf import FPDF
import os

from .ast_types import (
    BlockNode, InlineNode, Text, Bold, Italic, Strikethrough, Underline,
    InlineCode, Link, Image, MathInline, Subscript, Superscript,
    Heading, Paragraph, BulletList, ListItem, Table as TableNode, CodeBlock,
    BlockQuote, HorizontalRule, ChartDirective, MathBlock,
)
from .chart_generator import generate_chart_png


_DEJAVU_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/TTF",
    "/usr/share/fonts/dejavu",
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
]
_DEJAVU_DIR: str = "/usr/share/fonts/TTF"
for _d in _DEJAVU_DIRS:
    if os.path.isfile(os.path.join(_d, "DejaVuSans.ttf")):
        _DEJAVU_DIR = _d
        break


class DocFPDF(FPDF):
    def __init__(self, font_family: str, heading_font: str, font_size: int):
        super().__init__()
        self.doc_font_family = font_family
        self.doc_heading_font = heading_font
        self.doc_font_size = font_size
        self._register_fonts()
        self.set_auto_page_break(auto=True, margin=20)
        self.add_page()
        self._list_depth = 0

    def _register_fonts(self):
        dv = _DEJAVU_DIR
        missing = [f for f in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf", "DejaVuSerif.ttf", "DejaVuSansMono.ttf") if not os.path.isfile(os.path.join(dv, f))]
        if missing:
            raise RuntimeError(
                f"DejaVu fonts not found in {dv} (missing: {', '.join(missing)}). "
                "Install the 'fonts-dejavu' package (Debian/Ubuntu) or the "
                "'dejavu' Homebrew package (macOS)."
            )
        self.add_font("DejaVu", "", f"{dv}/DejaVuSans.ttf", uni=True)
        self.add_font("DejaVu", "B", f"{dv}/DejaVuSans-Bold.ttf", uni=True)
        self.add_font("DejaVu", "I", f"{dv}/DejaVuSans-Oblique.ttf", uni=True)
        self.add_font("DejaVu", "BI", f"{dv}/DejaVuSans-BoldOblique.ttf", uni=True)
        self.add_font("DejaVuSerif", "", f"{dv}/DejaVuSerif.ttf", uni=True)
        self.add_font("DejaVuSerif", "B", f"{dv}/DejaVuSerif-Bold.ttf", uni=True)
        self.add_font("DejaVuSerif", "I", f"{dv}/DejaVuSerif-Italic.ttf", uni=True)
        self.add_font("DejaVuMono", "", f"{dv}/DejaVuSansMono.ttf", uni=True)
        self.add_font("DejaVuMono", "B", f"{dv}/DejaVuSansMono-Bold.ttf", uni=True)

    def _font_name(self, family: str) -> str:
        return {"serif": "DejaVuSerif", "sans": "DejaVu", "mono": "DejaVuMono"}.get(family, "DejaVuSerif")


def _inline_to_text(children: list[InlineNode]) -> str:
    parts: list[str] = []
    for c in children:
        if isinstance(c, Text):
            parts.append(c.text)
        elif isinstance(c, InlineCode):
            parts.append(c.text)
        elif isinstance(c, (Bold, Italic, Strikethrough, Underline)):
            parts.append(_inline_to_text(c.children))
        elif isinstance(c, Link):
            parts.append(c.text)
        elif isinstance(c, MathInline):
            parts.append(f"${c.latex}$")
        elif isinstance(c, (Subscript, Superscript)):
            parts.append(_inline_to_text(c.children))
        elif isinstance(c, Image):
            parts.append(c.alt or "")
    return "".join(parts)


def build_pdf(
    nodes: list[BlockNode],
    filepath: str,
    font_family: str = "serif",
    heading_font: str = "sans",
    font_size: int = 11,
    charts_dir: str = "data/documents/charts",
):
    pdf = DocFPDF(font_family, heading_font, font_size)
    pdf.set_margins(20, 15, 20)

    for node in nodes:
        _render_node(pdf, node, font_family, heading_font, font_size, charts_dir)

    pdf.output(filepath)


def _render_node(pdf: DocFPDF, node: BlockNode, font_family: str, heading_font: str, font_size: int, charts_dir: str):
    pdf.set_x(pdf.l_margin)
    if isinstance(node, Heading):
        size = max(24 - (node.level * 2), 12)
        style = "B"
        font_name = pdf._font_name(heading_font)
        pdf.set_font(font_name, style, size)
        pdf.set_text_color(26, 26, 26)
        text = _inline_to_text(node.children)
        if text:
            pdf.cell(0, size / 2 + 2, text, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    elif isinstance(node, Paragraph):
        text = _inline_to_text(node.children)
        if not text:
            return
        font_name = pdf._font_name(font_family)
        pdf.set_font(font_name, "", font_size)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(0, font_size / 2 + 2, text)
        pdf.ln(1)

    elif isinstance(node, BulletList):
        _render_list(pdf, node, font_family, font_size, charts_dir, level=0)

    elif isinstance(node, TableNode):
        _render_table(pdf, node, font_family, font_size)

    elif isinstance(node, CodeBlock):
        font_name = pdf._font_name("mono")
        pdf.set_font(font_name, "", font_size - 2)
        pdf.set_fill_color(245, 245, 245)
        pdf.set_draw_color(220, 220, 220)
        for line in node.code.split("\n"):
            y_before = pdf.get_y()
            if y_before > 270:
                pdf.add_page()
            pdf.set_x(25)
            pdf.cell(0, font_size - 2, " " + line[:100], new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.ln(2)

    elif isinstance(node, BlockQuote):
        pdf.set_left_margin(30)
        pdf.set_x(pdf.l_margin)
        font_name = pdf._font_name(font_family)
        pdf.set_font(font_name, "I", font_size)
        pdf.set_text_color(85, 85, 85)
        text = _inline_to_text(_collect_quote_text(node))
        pdf.multi_cell(0, font_size / 2 + 2, text)
        pdf.set_left_margin(20)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

    elif isinstance(node, HorizontalRule):
        y = pdf.get_y()
        if y > 270:
            pdf.add_page()
        pdf.set_draw_color(200, 200, 200)
        pdf.set_line_width(0.3)
        pdf.line(20, y + 5, pdf.w - 20, y + 5)
        pdf.ln(8)

    elif isinstance(node, ChartDirective):
        chart_path = generate_chart_png(node, charts_dir)
        if chart_path and os.path.exists(chart_path):
            pdf.image(chart_path, x=pdf.get_x() + 10, w=150)

    elif isinstance(node, MathBlock):
        font_name = pdf._font_name(font_family)
        pdf.set_font(font_name, "I", font_size + 1)
        pdf.set_text_color(60, 60, 60)
        pdf.cell(0, font_size / 2 + 2, node.latex, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)


def _render_list(pdf: DocFPDF, node: BulletList, font_family: str, font_size: int, charts_dir: str, level: int):
    indent = 10 + level * 6
    for idx, item in enumerate(node.items):
        text = _list_item_text(item)
        if not text:
            continue
        if pdf.get_y() > 270:
            pdf.add_page()
        font_name = pdf._font_name(font_family)
        pdf.set_font(font_name, "", font_size)
        x0 = pdf.l_margin + indent
        pdf.set_x(x0)
        marker = f"{idx + 1}. " if node.ordered else "- "
        pdf.cell(5, font_size / 2 + 2, marker)
        avail = pdf.w - pdf.r_margin - pdf.x
        pdf.multi_cell(avail, font_size / 2 + 2, text)
        _render_nested_lists(pdf, item, font_family, font_size, charts_dir, level + 1)
    pdf.set_x(pdf.l_margin)


def _render_nested_lists(pdf: DocFPDF, item: ListItem, font_family: str, font_size: int, charts_dir: str, level: int):
    for child in item.children:
        if isinstance(child, BulletList):
            _render_list(pdf, child, font_family, font_size, charts_dir, level)


def _render_table(pdf: DocFPDF, node: TableNode, font_family: str, font_size: int):
    num_cols = max(len(node.headers) if node.headers else 0, max((len(r) for r in node.rows), default=0))
    if num_cols == 0:
        return
    col_w = (pdf.w - 40) / num_cols
    font_name = pdf._font_name(font_family)

    pdf.set_draw_color(200, 200, 200)
    pdf.set_line_width(0.2)

    if node.headers:
        pdf.set_font(font_name, "B", font_size - 1)
        pdf.set_fill_color(235, 235, 235)
        for i, cell in enumerate(node.headers):
            text = _inline_to_text(cell)
            pdf.cell(col_w, 7, text, border=1, fill=True)
        pdf.ln()

    pdf.set_font(font_name, "", font_size - 1)
    pdf.set_fill_color(255, 255, 255)
    for row in node.rows:
        if pdf.get_y() > 270:
            pdf.add_page()
        for i, cell in enumerate(row):
            text = _inline_to_text(cell)
            pdf.cell(col_w, 7, text, border=1)
        pdf.ln()


def _list_item_text(item: ListItem) -> str:
    parts: list[str] = []
    for child in item.children:
        if isinstance(child, Paragraph):
            parts.append(_inline_to_text(child.children))
        elif isinstance(child, CodeBlock):
            parts.append(child.code)
    return " ".join(parts)


def _collect_quote_text(node: BlockQuote) -> list[InlineNode]:
    result: list[InlineNode] = []
    for child in node.children:
        if isinstance(child, Paragraph):
            result.extend(child.children)
            result.append(Text(text="\n"))
        elif isinstance(child, Heading):
            result.extend(child.children)
            result.append(Text(text="\n"))
    return result

from __future__ import annotations
from typing import Optional
from odf.opendocument import OpenDocumentText
from odf.style import (
    Style, ParagraphProperties, TextProperties, TableProperties,
    TableColumnProperties, TableRowProperties, TableCellProperties,
    GraphicProperties, DrawingPageProperties,
)
from odf.text import H, P, Span, A, List, ListItem as OdfListItem, ListStyle, ListLevelStyleBullet, ListLevelStyleNumber, NoteCitation, NoteBody
from odf.table import Table as OdfTable, TableRow, TableColumn, TableCell
from odf.draw import Frame, Image as OdfImage
from odf import draw, text as odftext
from odf.namespaces import TEXTNS, STYLENS, TABLENS, DRAWNS
from odf.office import AutomaticStyles
import os
import base64

from .ast_types import (
    BlockNode, InlineNode, Text, Bold, Italic, Strikethrough, Underline,
    InlineCode, Link, Image, MathInline, Subscript, Superscript,
    Heading, Paragraph, BulletList, ListItem as ListItemNode, Table as TableNode, CodeBlock,
    BlockQuote, HorizontalRule, ChartDirective, MathBlock,
)
from .chart_generator import generate_chart_png


def build_odt(
    nodes: list[BlockNode],
    filepath: str,
    font_family: str = "serif",
    heading_font: str = "sans",
    font_size: int = 11,
    charts_dir: str = "data/documents/charts",
):
    doc = OpenDocumentText()
    _setup_styles(doc, font_family, heading_font, font_size)

    for node in nodes:
        _add_block(doc, node, font_family, heading_font, font_size, charts_dir)

    doc.save(filepath)


def _style_name(prefix: str, family: str) -> str:
    return f"{prefix}_{family}"


def _setup_styles(doc: OpenDocumentText, font_family: str, heading_font: str, font_size: int):
    font_map = {"serif": "Georgia", "sans": "Calibri", "mono": "Consolas"}
    body_font = font_map.get(font_family, font_family)
    h_font = font_map.get(heading_font, heading_font)

    default_style = Style(name="Standard", family="paragraph")
    default_style.addElement(ParagraphProperties())
    default_style.addElement(TextProperties(
        fontsize=f"{font_size}pt", fontfamily=body_font
    ))
    doc.styles.addElement(default_style)

    for level in range(1, 7):
        hsize = max(24 - (level * 2), 12)
        hstyle = Style(name=f"Heading{level}", family="paragraph", parentstylename="Standard")
        hstyle.addElement(ParagraphProperties(
            marginbottom="0.15in", margintop="0.2in"
        ))
        hstyle.addElement(TextProperties(
            fontsize=f"{hsize}pt", fontweight="bold", fontfamily=h_font,
        ))
        doc.styles.addElement(hstyle)

    code_style = Style(name="CodeBlock", family="paragraph", parentstylename="Standard")
    code_style.addElement(ParagraphProperties(
        backgroundcolor="#f5f5f5",
        padding="0.1in", marginleft="0.2in",
    ))
    code_style.addElement(TextProperties(
        fontsize=f"{font_size - 2}pt", fontfamily="Consolas",
    ))
    doc.styles.addElement(code_style)

    quote_style = Style(name="BlockQuote", family="paragraph", parentstylename="Standard")
    quote_style.addElement(ParagraphProperties(
        marginleft="0.5in", marginright="0.2in",
    ))
    quote_style.addElement(TextProperties(
        fontstyle="italic", color="#555555",
    ))
    doc.styles.addElement(quote_style)

    code_inline_style = Style(name="InlineCode", family="text")
    code_inline_style.addElement(TextProperties(
        fontfamily="Consolas", fontsize=f"{font_size - 2}pt",
    ))
    doc.styles.addElement(code_inline_style)

    bold_style = Style(name="Bold", family="text")
    bold_style.addElement(TextProperties(fontweight="bold"))
    doc.styles.addElement(bold_style)

    italic_style = Style(name="Italic", family="text")
    italic_style.addElement(TextProperties(fontstyle="italic"))
    doc.styles.addElement(italic_style)

    strike_style = Style(name="Strikethrough", family="text")
    strike_style.addElement(TextProperties(textlinethroughtype="single", textlinethroughstyle="solid"))
    doc.styles.addElement(strike_style)

    underline_style = Style(name="Underline", family="text")
    underline_style.addElement(TextProperties(textunderlinetype="single", textunderlinestyle="solid"))
    doc.styles.addElement(underline_style)

    link_style = Style(name="Link", family="text")
    link_style.addElement(TextProperties(
        color="#0563C1",
    ))
    doc.styles.addElement(link_style)


def _add_block(doc, node: BlockNode, font_family: str, heading_font: str, font_size: int, charts_dir: str):
    if isinstance(node, Heading):
        h = H(outlinelevel=node.level)
        h.setAttribute("stylename", f"Heading{node.level}")
        _add_inline(h, node.children, doc)
        doc.text.addElement(h)

    elif isinstance(node, Paragraph):
        p = P()
        _add_inline(p, node.children, doc)
        doc.text.addElement(p)

    elif isinstance(node, BulletList):
        _add_odt_list(doc, node, font_family, font_size, charts_dir, level=0)

    elif isinstance(node, TableNode):
        _add_odt_table(doc, node, font_family, font_size)

    elif isinstance(node, CodeBlock):
        p = P(stylename="CodeBlock")
        span = Span(stylename="InlineCode", text=node.code)
        p.addElement(span)
        doc.text.addElement(p)

    elif isinstance(node, BlockQuote):
        for child in node.children:
            if isinstance(child, Paragraph):
                p = P(stylename="BlockQuote")
                _add_inline(p, child.children, doc)
                doc.text.addElement(p)

    elif isinstance(node, HorizontalRule):
        p = P()
        p.addElement(Span(text="─" * 60))
        doc.text.addElement(p)

    elif isinstance(node, ChartDirective):
        chart_path = generate_chart_png(node, charts_dir)
        if chart_path and os.path.exists(chart_path):
            with open(chart_path, "rb") as f:
                img_data = f.read()
            b64 = base64.b64encode(img_data).decode("ascii")
            href = doc.addPicture(chart_path, b64)
            image_frame = Frame(
                width="5in",
                height="3in",
                anchortype="paragraph",
                zindex="0",
            )
            image_elem = OdfImage(href=href, type="simple")
            image_frame.addElement(image_elem)
            doc.text.addElement(image_frame)

    elif isinstance(node, MathBlock):
        p = P()
        span = Span(text=node.latex)
        doc.text.addElement(p)


def _add_inline(parent, children: list[InlineNode], doc):
    for child in children:
        if isinstance(child, Text):
            parent.addElement(Span(text=child.text))
        elif isinstance(child, Bold):
            s = Span(stylename="Bold")
            _add_inline(s, child.children, doc)
            parent.addElement(s)
        elif isinstance(child, Italic):
            s = Span(stylename="Italic")
            _add_inline(s, child.children, doc)
            parent.addElement(s)
        elif isinstance(child, Strikethrough):
            s = Span(stylename="Strikethrough")
            _add_inline(s, child.children, doc)
            parent.addElement(s)
        elif isinstance(child, Underline):
            s = Span(stylename="Underline")
            _add_inline(s, child.children, doc)
            parent.addElement(s)
        elif isinstance(child, InlineCode):
            parent.addElement(Span(stylename="InlineCode", text=child.text))
        elif isinstance(child, Link):
            a = A(href=child.url, text=child.text)
            a.setAttribute("stylename", "Link")
            parent.addElement(a)
        elif isinstance(child, Image):
            if os.path.exists(child.url):
                with open(child.url, "rb") as f:
                    img_data = f.read()
                b64 = base64.b64encode(img_data).decode("ascii")
                href = doc.addPicture(child.url, b64)
                w = min(child.width or 400, 600)
                h = min(child.height or 300, 400)
                image_frame = Frame(
                    width=f"{w}px",
                    height=f"{h}px",
                    anchortype="paragraph",
                )
                image_elem = OdfImage(href=href, type="simple")
                image_frame.addElement(image_elem)
                parent.addElement(image_frame)
        elif isinstance(child, MathInline):
            parent.addElement(Span(text=f"${child.latex}$"))
        elif isinstance(child, Subscript):
            s = Span()
            _add_inline(s, child.children, doc)
            parent.addElement(s)
        elif isinstance(child, Superscript):
            s = Span()
            _add_inline(s, child.children, doc)
            parent.addElement(s)


def _add_odt_list(doc, node: BulletList, font_family: str, font_size: int, charts_dir: str, level: int):
    lst = List()
    for item in node.items:
        li = OdfListItem()
        for child in item.children:
            if isinstance(child, Paragraph):
                p = P()
                _add_inline(p, child.children, doc)
                li.addElement(p)
            elif isinstance(child, BulletList):
                _add_odt_list(doc, child, font_family, font_size, charts_dir, level + 1)
        lst.addElement(li)
    doc.text.addElement(lst)


def _add_odt_table(doc, node: TableNode, font_family: str, font_size: int):
    num_cols = max(len(node.headers) if node.headers else 0, max((len(r) for r in node.rows), default=0))
    if num_cols == 0:
        return

    table = OdfTable()
    table.setAttribute("stylename", "Standard")

    if node.headers:
        tr = TableRow()
        for i, cell_content in enumerate(node.headers):
            tc = TableCell()
            p = P()
            _add_inline(p, cell_content, doc)
            tc.addElement(p)
            tr.addElement(tc)
        table.addElement(tr)

    for row in node.rows:
        tr = TableRow()
        for cell_content in row:
            tc = TableCell()
            p = P()
            _add_inline(p, cell_content, doc)
            tc.addElement(p)
            tr.addElement(tc)
        table.addElement(tr)

    doc.text.addElement(table)


def _inline_children_to_text(children: list[InlineNode]) -> str:
    parts: list[str] = []
    for c in children:
        if isinstance(c, Text):
            parts.append(c.text)
        elif isinstance(c, InlineCode):
            parts.append(c.text)
        elif isinstance(c, (Bold, Italic, Strikethrough, Underline)):
            parts.append(_inline_children_to_text(c.children))
        elif isinstance(c, Link):
            parts.append(c.text)
        elif isinstance(c, MathInline):
            parts.append(f"${c.latex}$")
        elif isinstance(c, (Subscript, Superscript)):
            parts.append(_inline_children_to_text(c.children))
        elif isinstance(c, Image):
            parts.append(c.alt or "")
    return "".join(parts)

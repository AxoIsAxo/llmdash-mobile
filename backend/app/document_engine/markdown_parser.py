from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.container import container_plugin


from .ast_types import (
    BlockNode, InlineNode, Text, Bold, Italic, Strikethrough, Underline,
    InlineCode, Link, Image, MathInline, Subscript, Superscript,
    Heading, Paragraph, BulletList, ListItem, Table, CodeBlock,
    BlockQuote, HorizontalRule, ChartDirective, MathBlock,
)

import re


_parser = None


def _get_parser() -> MarkdownIt:
    global _parser
    if _parser is None:
        md = MarkdownIt("commonmark", {"maxNesting": 30})
        md.enable(["table", "strikethrough"])
        md.use(container_plugin, name="chart", marker=":", validate=lambda p, m: p.strip().startswith("chart"))
        md.inline.ruler.after("escape", "math_inline", _math_inline_rule)
        md.block.ruler.after("fence", "math_block", _math_block_rule)
        _parser = md
    return _parser


def _math_inline_rule(state, silent):
    pos = state.pos
    max_pos = state.posMax
    if pos + 2 > max_pos or state.src[pos] != "$":
        return False
    end = state.src.find("$", pos + 1)
    if end == -1 or end == pos + 1:
        return False
    if not silent:
        token = state.push("math_inline", "math", 0)
        token.content = state.src[pos + 1:end]
        token.markup = "$"
        token.map = [pos, end + 1]
    state.pos = end + 1
    return True


def _math_block_rule(state, start_line, end_line, silent):
    lines = state.src.split("\n")
    if start_line >= len(lines):
        return False
    line = lines[start_line]
    if not line.strip().startswith("$$"):
        return False
    if silent:
        return True
    end = start_line + 1
    while end < len(lines) and not lines[end].strip().startswith("$$"):
        end += 1
    if end >= len(lines):
        end = len(lines) - 1
    content = "\n".join(lines[start_line + 1:end])
    token = state.push("math_block", "math", 0)
    token.content = content
    token.map = [start_line, end + 1]
    state.line = end + 1
    return True


def parse_markdown(text: str) -> list[BlockNode]:
    tokens = _get_parser().parse(text.strip())
    return _process_tokens(tokens)


def _process_tokens(tokens: list[Token]) -> list[BlockNode]:
    nodes: list[BlockNode] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "heading_open":
            level = int(t.tag[1])
            i += 1
            inline_tok = tokens[i] if i < len(tokens) else None
            children: list[InlineNode] = []
            if inline_tok and inline_tok.type == "inline":
                children = _parse_inline(inline_tok)
                i += 1
            nodes.append(Heading(level=level, children=children))
            if i < len(tokens) and tokens[i].type == "heading_close":
                i += 1
        elif t.type == "paragraph_open":
            i += 1
            inline_tok = tokens[i] if i < len(tokens) else None
            children = []
            if inline_tok and inline_tok.type == "inline":
                children = _parse_inline(inline_tok)
                i += 1
            if children:
                nodes.append(Paragraph(children=children))
            if i < len(tokens) and tokens[i].type == "paragraph_close":
                i += 1
        elif t.type == "bullet_list_open" or t.type == "ordered_list_open":
            ordered = t.type == "ordered_list_open"
            start = None
            if ordered and t.attrs:
                start = t.attrs.get("start", None)
            items, i = _parse_list_items(tokens, i + 1)
            nodes.append(BulletList(items=items, ordered=ordered, start=start))
        elif t.type == "blockquote_open":
            depth = 1
            inner_tokens = []
            i += 1
            while i < len(tokens) and depth > 0:
                if tokens[i].type == "blockquote_open":
                    depth += 1
                elif tokens[i].type == "blockquote_close":
                    depth -= 1
                if depth > 0:
                    inner_tokens.append(tokens[i])
                i += 1
            children = _process_tokens(inner_tokens)
            if children:
                nodes.append(BlockQuote(children=children))
        elif t.type == "fence":
            lang = t.info.strip() if t.info else None
            nodes.append(CodeBlock(language=lang, code=t.content))
            i += 1
        elif t.type == "code_block":
            nodes.append(CodeBlock(code=t.content))
            i += 1
        elif t.type == "hr":
            nodes.append(HorizontalRule())
            i += 1
        elif t.type == "table_open":
            table, i = _parse_table(tokens, i)
            if table:
                nodes.append(table)
        elif t.type == "container_chart_open":
            chart, i = _parse_chart(tokens, i)
            if chart:
                nodes.append(chart)
        elif t.type == "math_block":
            nodes.append(MathBlock(latex=t.content))
            i += 1
        elif t.type == "inline":
            children = _parse_inline(t)
            if children:
                nodes.append(Paragraph(children=children))
            i += 1
        else:
            i += 1
    return nodes


def _parse_inline(tok: Token) -> list[InlineNode]:
    if not tok.children:
        text = tok.content
        return _parse_inline_text(text)
    return _parse_inline_tokens(tok.children)


_INLINE_MD = None


def _parse_inline_text(text: str) -> list[InlineNode]:
    global _INLINE_MD
    if _INLINE_MD is None:
        _INLINE_MD = MarkdownIt("commonmark", {"maxNesting": 10})
        _INLINE_MD.enable(["strikethrough"])
    tokens = _INLINE_MD.parse(text)
    result: list[InlineNode] = []
    for t in tokens:
        if t.type == "inline" and t.children:
            result.extend(_parse_inline_tokens(t.children))
        elif t.type == "inline":
            result.extend(_split_text_with_math(t.content))
        elif t.type in ("paragraph_open", "paragraph_close"):
            continue
    return result


_INLINE_MATH_RE = re.compile(r'\$(.+?)\$')


def _split_text_with_math(text: str) -> list[InlineNode]:
    parts = _INLINE_MATH_RE.split(text)
    nodes: list[InlineNode] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 1:
            nodes.append(MathInline(latex=part))
        else:
            nodes.append(Text(text=part))
    return nodes or [Text(text=text)]


def _parse_inline_tokens(children: list) -> list[InlineNode]:
    result: list[InlineNode] = []
    i = 0
    while i < len(children):
        c = children[i]
        t = getattr(c, "type", getattr(c, "_type", None)) or ""
        if t == "text":
            result.extend(_split_text_with_math(c.content))
        elif t == "hardbreak":
            result.append(Text(text="\n"))
        elif t == "softbreak":
            result.append(Text(text=" "))
        elif t == "strong_open":
            inner, i = _collect_inline_until(children, i + 1, "strong_close")
            result.append(Bold(children=inner))
        elif t == "em_open":
            inner, i = _collect_inline_until(children, i + 1, "em_close")
            result.append(Italic(children=inner))
        elif t == "s_open":
            inner, i = _collect_inline_until(children, i + 1, "s_close")
            result.append(Strikethrough(children=inner))
        elif t == "code_inline":
            result.append(InlineCode(text=c.content))
        elif t == "link_open":
            url = c.attrs.get("href", "") if c.attrs else ""
            inner, i = _collect_inline_until(children, i + 1, "link_close")
            result.append(Link(text=_inline_to_plain(inner), url=url))
        elif t == "image":
            url = c.attrs.get("src", "") if c.attrs else ""
            alt = c.attrs.get("alt", "") if c.attrs else ""
            width = None
            height = None
            if c.attrs:
                w = c.attrs.get("width")
                h = c.attrs.get("height")
                if w:
                    try:
                        width = int(w)
                    except (ValueError, TypeError):
                        pass
                if h:
                    try:
                        height = int(h)
                    except (ValueError, TypeError):
                        pass
            result.append(Image(alt=alt, url=url, width=width, height=height))
        elif t in ("math_inline",):
            result.append(MathInline(latex=c.content))
        elif t in ("sub",):
            result.append(Subscript(children=[Text(text=c.content)]))
        elif t in ("sup",):
            result.append(Superscript(children=[Text(text=c.content)]))
        elif t in ("underline_open", "u_open"):
            inner, i = _collect_inline_until(children, i + 1, "underline_close")
            result.append(Underline(children=inner))
        i += 1
    return result


def _collect_inline_until(children: list, start: int, close_type: str) -> tuple[list[InlineNode], int]:
    result: list[InlineNode] = []
    i = start
    while i < len(children):
        c = children[i]
        t = getattr(c, "type", getattr(c, "_type", None)) or ""
        if t == close_type:
            return result, i
        if t == "text":
            result.extend(_split_text_with_math(c.content))
        elif t == "code_inline":
            result.append(InlineCode(text=c.content))
        elif t == "strong_open":
            inner, i = _collect_inline_until(children, i + 1, "strong_close")
            result.append(Bold(children=inner))
        elif t == "em_open":
            inner, i = _collect_inline_until(children, i + 1, "em_close")
            result.append(Italic(children=inner))
        elif t == "s_open":
            inner, i = _collect_inline_until(children, i + 1, "s_close")
            result.append(Strikethrough(children=inner))
        elif t == "link_open":
            url = c.attrs.get("href", "") if c.attrs else ""
            inner, i = _collect_inline_until(children, i + 1, "link_close")
            result.append(Link(text=_inline_to_plain(inner), url=url))
        elif t == "image":
            url = c.attrs.get("src", "") if c.attrs else ""
            alt = c.attrs.get("alt", "") if c.attrs else ""
            result.append(Image(alt=alt, url=url))
        i += 1
    return result, i


def _inline_to_plain(nodes: list[InlineNode]) -> str:
    parts: list[str] = []
    for n in nodes:
        if isinstance(n, Text):
            parts.append(n.text)
        elif isinstance(n, InlineCode):
            parts.append(n.text)
        elif isinstance(n, (Bold, Italic, Strikethrough, Underline)):
            parts.append(_inline_to_plain(n.children))
        elif isinstance(n, Link):
            parts.append(n.text)
        elif isinstance(n, Image):
            parts.append(n.alt)
        elif isinstance(n, MathInline):
            parts.append(f"${n.latex}$")
        elif isinstance(n, (Subscript, Superscript)):
            parts.append(_inline_to_plain(n.children))
    return "".join(parts)


def _parse_list_items(tokens: list[Token], start: int) -> tuple[list[ListItem], int]:
    items: list[ListItem] = []
    i = start
    while i < len(tokens):
        t = tokens[i]
        if t.type in ("bullet_list_close", "ordered_list_close"):
            i += 1
            break
        if t.type == "list_item_open":
            i += 1
            depth = 1
            item_tokens: list[Token] = []
            while i < len(tokens) and depth > 0:
                tt = tokens[i]
                if tt.type in ("list_item_open",):
                    depth += 1
                elif tt.type == "list_item_close":
                    depth -= 1
                if depth > 0:
                    item_tokens.append(tt)
                i += 1
            children = _process_tokens(item_tokens)
            items.append(ListItem(children=children))
        else:
            i += 1
    return items, i


def _parse_table(tokens: list[Token], start: int) -> tuple[Table | None, int]:
    i = start + 1
    header_tokens: list[Token] = []
    body_tokens: list[Token] = []
    in_header = False
    in_body = False
    aligns: list = []
    while i < len(tokens):
        t = tokens[i]
        if t.type == "thead_open":
            in_header = True
        elif t.type == "thead_close":
            in_header = False
        elif t.type == "tbody_open":
            in_body = True
        elif t.type == "tbody_close":
            in_body = False
        elif t.type == "tr_open":
            if in_header:
                header_tokens.append(t)
            elif in_body:
                body_tokens.append(t)
        elif t.type == "tr":
            if in_header:
                header_tokens.append(t)
            elif in_body:
                body_tokens.append(t)
        elif t.type == "table_close":
            i += 1
            break
        elif t.type == "inline":
            if in_header:
                header_tokens.append(t)
            elif in_body:
                body_tokens.append(t)
        elif t.type in ("th_open", "td_open", "th_close", "td_close"):
            pass
        i += 1

    if header_tokens:
        aligns = _extract_aligns(header_tokens)
    elif body_tokens:
        aligns = _extract_aligns(body_tokens)

    headers = _parse_table_row(header_tokens)
    rows = []
    if body_tokens:
        current_row: list[Token] = []
        for bt in body_tokens:
            if bt.type == "tr_open" or bt.type == "tr":
                if current_row:
                    rows.append(_parse_table_row(current_row))
                current_row = [bt]
            elif bt.type == "inline":
                current_row.append(bt)
        if current_row:
            rows.append(_parse_table_row(current_row))

    return Table(headers=headers, rows=rows, aligns=aligns), i


def _extract_aligns(tokens: list[Token]) -> list:
    aligns = []
    for t in tokens:
        if t.type in ("th_open", "td_open") and t.attrs:
            style = t.attrs.get("style", "")
            if "text-align:center" in style:
                aligns.append("center")
            elif "text-align:right" in style:
                aligns.append("right")
            elif "text-align:left" in style:
                aligns.append("left")
            else:
                aligns.append(None)
    return aligns


def _parse_table_row(tokens: list[Token]) -> list[list[InlineNode]]:
    cells: list[list[InlineNode]] = []
    for t in tokens:
        if t.type == "inline":
            children = _parse_inline(t)
            cells.append(children)
    return cells


def _parse_chart(tokens: list[Token], start: int) -> tuple[ChartDirective | None, int]:
    i = start + 1
    lines: list[str] = []
    list_depth = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "container_chart_close":
            i += 1
            break
        if t.type == "inline":
            prefix = "  " * (list_depth - 1) + "- " if list_depth > 0 else ""
            for line in t.content.split("\n"):
                if list_depth > 0:
                    lines.append(prefix + line)
                else:
                    lines.append(line)
        elif t.type == "bullet_list_open":
            list_depth += 1
        elif t.type == "bullet_list_close":
            list_depth -= 1
        i += 1

    text = "\n".join(lines)
    chart_data = _parse_chart_yaml(text)
    if chart_data:
        return ChartDirective(**chart_data), i
    return None, i


def _parse_chart_yaml(text: str) -> dict:
    import yaml
    try:
        parsed = yaml.safe_load(text)
        if not isinstance(parsed, dict):
            return {"chart_type": "bar", "title": "", "data": [], "options": {}}
        return {
            "chart_type": parsed.get("type", "bar"),
            "title": parsed.get("title", ""),
            "data": parsed.get("data", []),
            "options": parsed.get("options", {}),
        }
    except Exception:
        return {"chart_type": "bar", "title": "", "data": [], "options": {}}

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InlineNode:
    pass


@dataclass
class Text(InlineNode):
    text: str


@dataclass
class Bold(InlineNode):
    children: list[InlineNode] = field(default_factory=list)


@dataclass
class Italic(InlineNode):
    children: list[InlineNode] = field(default_factory=list)


@dataclass
class Strikethrough(InlineNode):
    children: list[InlineNode] = field(default_factory=list)


@dataclass
class Underline(InlineNode):
    children: list[InlineNode] = field(default_factory=list)


@dataclass
class InlineCode(InlineNode):
    text: str


@dataclass
class Link(InlineNode):
    text: str
    url: str


@dataclass
class Image(InlineNode):
    alt: str
    url: str
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class MathInline(InlineNode):
    latex: str


@dataclass
class Subscript(InlineNode):
    children: list[InlineNode] = field(default_factory=list)


@dataclass
class Superscript(InlineNode):
    children: list[InlineNode] = field(default_factory=list)


@dataclass
class BlockNode:
    pass


@dataclass
class Heading(BlockNode):
    level: int
    children: list[InlineNode] = field(default_factory=list)


@dataclass
class Paragraph(BlockNode):
    children: list[InlineNode] = field(default_factory=list)


@dataclass
class BulletList(BlockNode):
    items: list[ListItem] = field(default_factory=list)
    ordered: bool = False
    start: Optional[int] = None


@dataclass
class ListItem(BlockNode):
    children: list[BlockNode] = field(default_factory=list)


TableAlign = Optional[str]  # None (default), "left", "center", "right"


@dataclass
class Table(BlockNode):
    headers: list[list[InlineNode]] = field(default_factory=list)
    rows: list[list[list[InlineNode]]] = field(default_factory=list)
    aligns: list[TableAlign] = field(default_factory=list)


@dataclass
class CodeBlock(BlockNode):
    language: Optional[str] = None
    code: str = ""


@dataclass
class BlockQuote(BlockNode):
    children: list[BlockNode] = field(default_factory=list)


@dataclass
class HorizontalRule(BlockNode):
    pass


@dataclass
class ChartDirective(BlockNode):
    chart_type: str = "bar"
    title: str = ""
    data: list[dict] = field(default_factory=list)
    options: dict = field(default_factory=dict)


@dataclass
class MathBlock(BlockNode):
    latex: str = ""


DocumentMeta = dict[str, Optional[str | int]]

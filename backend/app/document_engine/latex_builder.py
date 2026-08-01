from __future__ import annotations
from typing import Optional
from jinja2 import Template
import os
import shutil
import subprocess
import tempfile

from .ast_types import (
    BlockNode, InlineNode, Text, Bold, Italic, Strikethrough, Underline,
    InlineCode, Link, Image, MathInline, Subscript, Superscript,
    Heading, Paragraph, BulletList, ListItem, Table as TableNode, CodeBlock,
    BlockQuote, HorizontalRule, ChartDirective, MathBlock,
)
from .chart_generator import generate_chart_png


_LATEX_TEMPLATE = Template(r"""\documentclass[{{ font_size }}pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{geometry}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{xcolor}
\usepackage{listings}
\usepackage[normalem]{ulem}
\usepackage{hyperref}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{longtable}
\usepackage{parskip}

\geometry{margin=1in}

\lstset{
  basicstyle=\small\ttfamily,
  backgroundcolor=\color{gray!10},
  frame=single,
  breaklines=true,
  postbreak=\mbox{\textcolor{red}{$\hookrightarrow$}\space},
}

\definecolor{linkcolor}{HTML}{0563C1}
\hypersetup{
  colorlinks=true,
  linkcolor=black,
  urlcolor=linkcolor,
}

\title{{ title }}
\author{LLMDash}
\date{\today}

\begin{document}
\maketitle
\thispagestyle{empty}

{{ body }}

\end{document}
""")


def _escape_latex(text: str) -> str:
    replacements = {
        "\\": "\\textbackslash{}",
        "{": "\\{",
        "}": "\\}",
        "$": "\\$",
        "&": "\\&",
        "#": "\\#",
        "^": "\\textasciicircum{}",
        "_": "\\_",
        "~": "\\textasciitilde{}",
        "%": "\\%",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


def _inline_to_latex(children: list[InlineNode]) -> str:
    parts: list[str] = []
    for c in children:
        if isinstance(c, Text):
            parts.append(_escape_latex(c.text))
        elif isinstance(c, Bold):
            parts.append(f"\\textbf{{{_inline_to_latex(c.children)}}}")
        elif isinstance(c, Italic):
            parts.append(f"\\textit{{{_inline_to_latex(c.children)}}}")
        elif isinstance(c, Strikethrough):
            parts.append(f"\\sout{{{_inline_to_latex(c.children)}}}")
        elif isinstance(c, Underline):
            parts.append(f"\\underline{{{_inline_to_latex(c.children)}}}")
        elif isinstance(c, InlineCode):
            parts.append(f"\\texttt{{{_escape_latex(c.text)}}}")
        elif isinstance(c, Link):
            parts.append(f"\\href{{{_escape_latex(c.url)}}}{{{_escape_latex(c.text)}}}")
        elif isinstance(c, Image):
            p = c.url
            opts = f"width={min(c.width or 400, 500)}px" if c.width else "width=0.8\\textwidth"
            parts.append(f"\\includegraphics[{opts}]{{{_escape_latex(p)}}}")
        elif isinstance(c, MathInline):
            parts.append(f"${c.latex}$")
        elif isinstance(c, Subscript):
            parts.append(f"\\textsubscript{{{_inline_to_latex(c.children)}}}")
        elif isinstance(c, Superscript):
            parts.append(f"\\textsuperscript{{{_inline_to_latex(c.children)}}}")
    return "".join(parts)


def _block_to_latex(node: BlockNode, charts_dir: str, image_dir: str) -> str:
    if isinstance(node, Heading):
        cmd = "\\section" if node.level == 1 else "\\subsection" if node.level == 2 else "\\subsubsection" if node.level == 3 else "\\paragraph" if node.level == 4 else "\\subparagraph"
        return f"{cmd}{{{_inline_to_latex(node.children)}}}"

    elif isinstance(node, Paragraph):
        text = _inline_to_latex(node.children)
        return text + "\n\n"

    elif isinstance(node, BulletList):
        env = "enumerate" if node.ordered else "itemize"
        items = "\n".join(f"\\item {_list_item_to_latex(item)}" for item in node.items)
        return f"\\begin{{{env}}}\n{items}\n\\end{{{env}}}\n\n"

    elif isinstance(node, TableNode):
        return _table_to_latex(node)

    elif isinstance(node, CodeBlock):
        lang = node.language or "text"
        return f"\\begin{{lstlisting}}[language={lang}]\n{node.code}\n\\end{{lstlisting}}\n\n"

    elif isinstance(node, BlockQuote):
        text = _collect_quote_text(node)
        return f"\\begin{{quote}}\n{_inline_to_latex(text)}\n\\end{{quote}}\n\n"

    elif isinstance(node, HorizontalRule):
        return "\\bigskip\n\\hrule\n\\bigskip\n\n"

    elif isinstance(node, ChartDirective):
        chart_path = generate_chart_png(node, charts_dir)
        if chart_path and os.path.exists(chart_path):
            dest = os.path.join(image_dir, os.path.basename(chart_path))
            os.makedirs(image_dir, exist_ok=True)
            try:
                shutil.copy2(chart_path, dest)
            except OSError:
                pass
            return f"\\begin{{center}}\n\\includegraphics[width=0.8\\textwidth]{{{os.path.basename(chart_path)}}}\n\\end{{center}}\n\n"
        return ""

    elif isinstance(node, MathBlock):
        return f"\\[ {node.latex} \\]\n\n"

    return ""


def _list_item_to_latex(item: ListItem) -> str:
    parts: list[str] = []
    for child in item.children:
        if isinstance(child, Paragraph):
            parts.append(_inline_to_latex(child.children))
        elif isinstance(child, CodeBlock):
            parts.append(f"\\begin{{lstlisting}}\n{child.code}\n\\end{{lstlisting}}")
        elif isinstance(child, BulletList):
            env = "enumerate" if child.ordered else "itemize"
            items = "\n".join(f"\\item {_list_item_to_latex(sub)}" for sub in child.items)
            parts.append(f"\\begin{{{env}}}\n{items}\n\\end{{{env}}}")
    return "\n".join(parts)


def _table_to_latex(node: TableNode) -> str:
    num_cols = max(len(node.headers) if node.headers else 0, max((len(r) for r in node.rows), default=0))
    if num_cols == 0:
        return ""

    aligns_str = "".join(
        {"center": "c", "right": "r", "left": "l"}.get(a, "l") if a else "l"
        for a in node.aligns[:num_cols]
    )
    if not aligns_str:
        aligns_str = "l" * num_cols

    lines: list[str] = []
    lines.append(f"\\begin{{longtable}}{{{aligns_str}}}")
    lines.append("\\toprule")

    if node.headers:
        cells = " & ".join(_inline_to_latex(c) for c in node.headers)
        lines.append(cells + " \\\\")
        lines.append("\\midrule")
    else:
        lines.append("\\midrule")

    for row in node.rows:
        cells = " & ".join(_inline_to_latex(c) for c in row)
        lines.append(cells + " \\\\")

    lines.append("\\bottomrule")
    lines.append("\\end{longtable}")
    lines.append("")
    return "\n".join(lines)


def _collect_quote_text(node: BlockQuote) -> list[InlineNode]:
    result: list[InlineNode] = []
    for child in node.children:
        if isinstance(child, Paragraph):
            result.extend(child.children)
    return result


def build_latex(
    nodes: list[BlockNode],
    filepath: str,
    title: str = "Document",
    charts_dir: str = "data/documents/charts",
) -> str:
    image_dir = os.path.join(os.path.dirname(filepath), "images")
    os.makedirs(image_dir, exist_ok=True)

    body_parts: list[str] = []
    for node in nodes:
        part = _block_to_latex(node, charts_dir, image_dir)
        if part:
            body_parts.append(part)
    body = "\n".join(body_parts)

    result = _LATEX_TEMPLATE.render(title=_escape_latex(title), body=body, font_size=11)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(result)
    return filepath


def compile_latex_to_pdf(tex_path: str, output_dir: Optional[str] = None) -> Optional[str]:
    if not shutil.which("pdflatex"):
        return None

    if output_dir is None:
        output_dir = os.path.dirname(tex_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path],
                capture_output=True,
                text=True,
                timeout=60,
            )
            pdf_name = os.path.splitext(os.path.basename(tex_path))[0] + ".pdf"
            pdf_tmp = os.path.join(tmpdir, pdf_name)
            if os.path.exists(pdf_tmp):
                pdf_out = os.path.join(output_dir, pdf_name)
                shutil.copy2(pdf_tmp, pdf_out)
                return pdf_out
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    return None

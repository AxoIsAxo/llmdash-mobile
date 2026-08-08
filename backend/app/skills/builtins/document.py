from __future__ import annotations

import base64
import os

from sqlalchemy import select

from ..base import Skill
from ... import config as app_config
from ...database import async_session, Document as DocModel
from ...document_engine.markdown_parser import parse_markdown
from ...document_engine.html_builder import build_html
from ...document_engine.docx_builder import build_docx
from ...document_engine.pdf_builder import build_pdf
from ...document_engine.odt_builder import build_odt
from ...document_engine.latex_builder import build_latex, compile_latex_to_pdf
from ...patch_utils import apply_patch


def _generate_preview(content: str, filename: str, fmt: str) -> str:
    try:
        nodes = parse_markdown(content)
        return build_html(nodes, title=f"{filename}.{fmt}")
    except Exception:
        return f"<html><body><p>Preview unavailable for {filename}.{fmt}</p></body></html>"


class EditDocumentSkill(Skill):
    name = "edit_document"
    entitlement = "document_editor"
    description = "Create a downloadable file artifact. Supports rich document formats (docx, pdf, odt, tex) and any text-based file type (py, js, ts, html, css, json, xml, yaml, toml, txt, csv, sh, rs, go, java, etc.). Returns a file path and download link. Only use when the user explicitly asks to save, download, or export a file."
    input_schema = {
        "type": "object",
        "properties": {
            "format": {"type": "string", "description": "File extension (e.g. docx, pdf, odt, tex, md, py, js, html). For rich documents use docx/pdf/odt/tex; everything else is saved as plain text."},
            "filename": {"type": "string", "description": "Desired filename (without extension)"},
            "content": {"type": "string", "description": "Full file content in markdown format. Supports full markdown: headings, bold, italic, strikethrough, inline code, fenced code blocks, tables, lists (ordered and unordered), blockquotes, images, links, horizontal rules, LaTeX math ($...$ and $$...$$), and :::chart directives."},
            "existing_doc_id": {"type": "integer", "description": "Optional. ID of an existing document to edit. If provided, creates a new version preserving the old file."},
            "old_str": {"type": "string", "description": "Optional. When editing an existing document, find this text in the content and replace it with new_str (patch mode). Requires existing_doc_id."},
            "new_str": {"type": "string", "description": "Optional. Replacement text for patch mode. Requires old_str."},
            "font_family": {"type": "string", "description": "Base font family: serif, sans, or mono (default: serif)"},
            "heading_font": {"type": "string", "description": "Heading font family: serif, sans, or mono (default: sans)"},
            "font_size": {"type": "integer", "description": "Base font size in pt (default: 11)"},
        },
        "required": ["format", "filename", "content"],
    }

    async def execute(self, arguments: dict, _current_user: dict = None) -> str:
        fmt = arguments.get("format", "")
        filename = arguments.get("filename", "")
        content = arguments.get("content", "")
        existing_doc_id = arguments.get("existing_doc_id")
        old_str = arguments.get("old_str")
        new_str = arguments.get("new_str")
        font_family = arguments.get("font_family", "serif")
        heading_font = arguments.get("heading_font", "sans")
        font_size = arguments.get("font_size", 11)

        docs_dir = app_config.settings.documents_dir
        charts_dir = os.path.join(docs_dir, "charts")
        os.makedirs(docs_dir, exist_ok=True)
        os.makedirs(charts_dir, exist_ok=True)

        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in filename)
        ext = fmt.lower()
        ff = font_family or "serif"
        hf = heading_font or "sans"
        fs = font_size or 11

        existing_version = 0
        existing_content_md = None

        if existing_doc_id is not None and _current_user:
            async with async_session() as sess:
                result = await sess.execute(
                    select(DocModel).where(
                        DocModel.id == existing_doc_id,
                        DocModel.user_id == _current_user["user_id"],
                    )
                )
                doc = result.scalar_one_or_none()
                if doc:
                    existing_version = doc.version
                    existing_content_md = doc.content_md or ""

                    if old_str is not None and new_str is not None and existing_content_md:
                        success, err, patched = apply_patch(existing_content_md, old_str, new_str)
                        if not success:
                            return f"Error: {err}. The existing content has {len(existing_content_md)} characters."
                        content = patched
                else:
                    return f"Error: Document with id {existing_doc_id} not found."

        new_version = existing_version + 1
        version_suffix = f"_v{new_version}"
        filepath = os.path.join(docs_dir, f"{safe_name}{version_suffix}.{ext}")

        try:
            if ext in ("docx", "pdf", "odt", "tex"):
                nodes = parse_markdown(content)

            if ext == "docx":
                build_docx(nodes, filepath, font_family=ff, heading_font=hf, font_size=fs, charts_dir=charts_dir)
            elif ext == "pdf":
                build_pdf(nodes, filepath, font_family=ff, heading_font=hf, font_size=fs, charts_dir=charts_dir)
            elif ext == "odt":
                build_odt(nodes, filepath, font_family=ff, heading_font=hf, font_size=fs, charts_dir=charts_dir)
            pdf_path = None
            if ext == "tex":
                build_latex(nodes, filepath, title=safe_name, charts_dir=charts_dir)
                pdf_path = compile_latex_to_pdf(filepath)
            else:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)

            if _current_user:
                async with async_session() as sess:
                    doc = DocModel(
                        user_id=_current_user["user_id"],
                        filename=safe_name,
                        format=ext,
                        version=new_version,
                        file_path=filepath,
                        content_md=content,
                    )
                    sess.add(doc)
                    await sess.commit()
                    await sess.refresh(doc)
                    doc_id = doc.id
            else:
                doc_id = 0

            preview_html_str = ""
            if ext in ("docx", "pdf", "odt", "tex"):
                try:
                    preview_html_str = _generate_preview(content, safe_name, ext)
                except Exception:
                    preview_html_str = ""

            action = "updated" if existing_doc_id else "created"
            result_lines = [
                f"Document {action}: {filepath}",
                f"Download: /api/files/{os.path.basename(filepath)}",
            ]
            if doc_id:
                result_lines.append(f"Document ID: {doc_id}")
            if preview_html_str:
                encoded = base64.b64encode(preview_html_str.encode()).decode()
                result_lines.append(f"HTML_RENDER:{encoded}")
            if ext == "tex" and pdf_path:
                pdf_basename = os.path.basename(pdf_path)
                result_lines.append(f"PDF compiled: /api/files/{pdf_basename}")

            return "\n".join(result_lines)
        except Exception as e:
            return f"Document creation error: {type(e).__name__}: {str(e)}"

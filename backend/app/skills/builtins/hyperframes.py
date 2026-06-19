from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile

from ..base import Skill
from ... import config as app_config
from ...database import async_session, Document as DocModel


class RenderVideoSkill(Skill):
    name = "render_video"
    description = (
        "Create an MP4 video from an HTML composition using HyperFrames. "
        "Provide the complete HTML with HyperFrames data-* attributes for timing and tracks. "
        "Supports GSAP, CSS animations, Lottie, Three.js, video/audio media tracks. "
        "Returns a downloadable MP4 file."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "html": {
                "type": "string",
                "description": (
                    "Complete HTML composition content for HyperFrames. "
                    "Must contain a root <div> with data-composition-id, data-start=\"0\", data-width, data-height, data-duration. "
                    "Use child elements with data-start (seconds), data-duration (seconds), data-track-index for timeline placement. "
                    "Wrap GSAP/CSS/WAAPI animations in <script> tags with a paused timeline assigned to window.__timelines[compositionId]. "
                    "Example:\n"
                    '<div data-composition-id="demo" data-start="0" data-width="1920" data-height="1080" data-duration="5">\n'
                    '  <h1 data-start="0.5" data-duration="3" data-track-index="0">Hello</h1>\n'
                    '  <script src="https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js"></script>\n'
                    '  <script>const tl = gsap.timeline({paused:true}); tl.from("h1",{opacity:0,y:40,duration:0.8},0); window.__timelines=window.__timelines||{}; window.__timelines.demo=tl;</script>\n'
                    "</div>"
                ),
            },
            "filename": {
                "type": "string",
                "description": "Output filename (without extension). Default: 'hyperframes_video'",
            },
            "width": {
                "type": "integer",
                "description": "Composition width in pixels. Default: 1920",
                "default": 1920,
            },
            "height": {
                "type": "integer",
                "description": "Composition height in pixels. Default: 1080",
                "default": 1080,
            },
            "fps": {
                "type": "integer",
                "description": "Frame rate. Default: 30",
                "default": 30,
            },
            "duration": {
                "type": "number",
                "description": "Total duration in seconds. Default: 5",
                "default": 5,
            },
        },
        "required": ["html"],
    }

    async def execute(self, arguments: dict, _current_user: dict = None) -> str:
        html = arguments.get("html", "")
        filename = arguments.get("filename", "hyperframes_video")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in filename)
        docs_dir = app_config.settings.documents_dir
        os.makedirs(docs_dir, exist_ok=True)

        width = arguments.get("width", 1920)
        height = arguments.get("height", 1080)
        fps = arguments.get("fps", 30)
        duration = arguments.get("duration", 5)

        html = self._ensure_full_document(html)
        html = self._inject_timeline_script(html)

        project_dir = tempfile.mkdtemp(prefix="hyperframes_")
        try:
            index_path = os.path.join(project_dir, "index.html")
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(html)

            composition = {
                "width": width,
                "height": height,
                "fps": fps,
                "duration": duration,
            }
            config_path = os.path.join(project_dir, "composition.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(composition, f)

            has_hyperframes = await self._check_hyperframes()
            if not has_hyperframes:
                html_path = os.path.join(docs_dir, f"{safe_name}.html")
                shutil.copy2(index_path, html_path)
                if _current_user:
                    await self._add_document(_current_user, safe_name, "html", html_path, html)
                return (
                    f"HyperFrames composition saved: /api/files/{safe_name}.html\n\n"
                    "To render this to MP4, install Node.js 22+ and run:\n"
                    "  npm install -g hyperframes\n"
                    "  hyperframes render /path/to/dir --output video.mp4"
                )

            result = await self._render(project_dir, safe_name, docs_dir, duration)
            if result["success"]:
                if _current_user:
                    await self._add_document(
                        _current_user, safe_name, "mp4", result["filepath"],
                        f"HyperFrames MP4 video rendered from HTML composition. Duration: {duration}s, {width}x{height}, {fps}fps."
                    )
                return (
                    f"Video rendered: /api/files/{result['filename']}\n"
                    f"Duration: {result['duration']}s"
                )

            html_path = os.path.join(docs_dir, f"{safe_name}.html")
            shutil.copy2(index_path, html_path)
            if _current_user:
                await self._add_document(_current_user, safe_name, "html", html_path, html)
            return (
                f"HyperFrames composition saved: /api/files/{safe_name}.html\n"
                f"Render error: {result.get('error', 'unknown')}"
            )

        finally:
            shutil.rmtree(project_dir, ignore_errors=True)

    @staticmethod
    async def _add_document(user: dict, safe_name: str, fmt: str, file_path: str, content: str):
        async with async_session() as sess:
            doc = DocModel(
                user_id=user["user_id"],
                filename=safe_name,
                format=fmt,
                version=1,
                file_path=file_path,
                content_md=content,
            )
            sess.add(doc)
            await sess.commit()

    def _ensure_full_document(self, html: str) -> str:
        stripped = html.strip()
        if stripped.startswith("<!DOCTYPE html") or stripped.startswith("<html"):
            return html
        return (
            "<!DOCTYPE html><html><head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            "</head><body>\n"
            f"{html}\n"
            "</body></html>"
        )

    def _inject_timeline_script(self, html: str) -> str:
        m = re.search(r'data-composition-id\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
        comp_id = m.group(1) if m else "main"
        script = (
            f"<script>window.__timelines=window.__timelines||{{}};"
            f"window.__timelines[\"{comp_id}\"]=window.__timelines[\"{comp_id}\"]||"
            f"{{pause:function(){{}},play:function(){{}},progress:function(){{return 1;}},"
            f"totalDuration:function(){{return 0;}},time:function(){{return 0;}},"
            f"duration:function(){{return 0;}},paused:!0}};"
            f"</script>"
        )
        if "</body>" in html:
            return html.replace("</body>", script + "</body>", 1)
        return html + script

    async def _check_hyperframes(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "hyperframes", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return proc.returncode == 0 and bool(stdout)
        except (FileNotFoundError, asyncio.TimeoutError):
            pass
        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "--yes", "hyperframes", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            return proc.returncode == 0 and bool(stdout)
        except (FileNotFoundError, asyncio.TimeoutError):
            return False

    async def _render(self, project_dir: str, safe_name: str, docs_dir: str, duration: float) -> dict:
        output_path = os.path.join(docs_dir, f"{safe_name}.mp4")
        cmd = ["hyperframes", "render", project_dir, "--output", output_path]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except FileNotFoundError:
            cmd = ["npx", "--yes", "hyperframes", "render", project_dir, "--output", output_path]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except FileNotFoundError:
                return {"success": False, "error": "hyperframes CLI not found"}
            except asyncio.TimeoutError:
                return {"success": False, "error": "render timed out after 300s"}

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace")[:1000] if stderr else "unknown error"
            return {"success": False, "error": err_text}

        if not os.path.exists(output_path):
            return {"success": False, "error": "output file not produced"}

        return {
            "success": True,
            "filepath": output_path,
            "filename": f"{safe_name}.mp4",
            "duration": str(duration),
        }

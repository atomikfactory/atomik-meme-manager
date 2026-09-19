"""Serve `web/dist` with SPA fallback. Handles a missing `web/dist` gracefully."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse

_MISSING_UI_HTML = """\
<!doctype html>
<html>
<head><meta charset="utf-8"><title>atomik-meme-web</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; \
line-height: 1.5;">
<h1>atomik-meme-web</h1>
<p>The frontend hasn't been built yet (<code>web/dist</code> is missing).</p>
<p>Build it with:</p>
<pre>scripts\\build-web.ps1</pre>
<p>or:</p>
<pre>cd web &amp;&amp; npm ci &amp;&amp; npm run build</pre>
<p>The API is fully available in the meantime:
<a href="/docs">/docs</a> &middot; <a href="/api/health">/api/health</a></p>
</body>
</html>
"""


def mount_spa(app: FastAPI, dist_dir: Path) -> None:
    """Register the catch-all route serving `dist_dir`, or a friendly placeholder at `/`."""
    index_file = dist_dir / "index.html"
    if dist_dir.is_dir() and index_file.is_file():

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str, request: Request) -> FileResponse:
            candidate = (dist_dir / full_path).resolve()
            try:
                candidate.relative_to(dist_dir.resolve())
            except ValueError:
                candidate = index_file
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_file)
    else:

        @app.get("/", include_in_schema=False)
        async def missing_ui() -> HTMLResponse:
            return HTMLResponse(_MISSING_UI_HTML)

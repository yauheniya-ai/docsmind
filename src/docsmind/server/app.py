"""FastAPI application for the `docsmind` UI (`frontend/`).

Thin by design: routes delegate to `PostgresRepository` / `AsyncDocsmind`
exactly like the CLI does. This module is only imported when the `server`
extra is installed (`pip install "docsmind[server]"`) — see `cli/serve.py`,
which is the only place that imports it eagerly.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from docsmind.config import DocsmindConfig


def create_app(config: DocsmindConfig | None = None) -> FastAPI:
    config = config or DocsmindConfig()

    app = FastAPI(title="docsmind", description="Chat with your documents.")

    # Dev-friendly default: the Vite dev server runs on a different port.
    # Tighten this (or drive it from config) before exposing a deployment
    # beyond localhost.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.config = config

    from docsmind.server.routes import chat, documents

    app.include_router(documents.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")

    # Extracted images/tables are rendered to disk under `blob_root` (see
    # `FilesystemBlobStore`) — serve them directly so the UI can <img src=...>
    # them instead of round-tripping bytes through a JSON API.
    blob_root = Path(config.blob_root)
    blob_root.mkdir(parents=True, exist_ok=True)
    app.mount("/blobs", StaticFiles(directory=str(blob_root)), name="blobs")

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    # Bundled frontend UI (copied from ../frontend/dist into
    # src/docsmind/server/ui at release time). If absent, the API still works
    # exactly as before.
    ui_root = Path(__file__).resolve().parent / "ui"
    ui_index = ui_root / "index.html"

    @app.get("/", include_in_schema=False)
    async def ui_index_route():
        if not ui_index.exists():
            return {
                "name": "docsmind",
                "status": "ok",
                "message": "Bundled UI not found. Build frontend and copy it into docsmind/server/ui.",
            }
        return FileResponse(ui_index)

    @app.get("/{path:path}", include_in_schema=False)
    async def ui_spa_fallback(path: str):
        # `/api/*` and `/blobs/*` are mounted above; this handles the UI's
        # client-side routes (`/documents/...`, `/activity`, etc.).
        if path in {"api", "blobs"} or path.startswith(("api/", "blobs/")):
            raise HTTPException(status_code=404, detail="Not found")

        if not ui_index.exists():
            raise HTTPException(status_code=404, detail="Bundled UI not found")

        requested = ui_root / path
        if requested.is_file():
            return FileResponse(requested)
        return FileResponse(ui_index)

    return app


app = create_app()

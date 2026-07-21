import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.startup import ensure_database_ready
from app.workers.document_tasks import DocumentTasks

DOCS_ASSETS_DIR = Path(__file__).resolve().parent / "static" / "docs"
DOCS_ASSETS_URL = "/docs-assets"
PRECOMPRESSED_DOCS_ASSETS = {
    "redoc.standalone.js": "text/javascript; charset=utf-8",
    "swagger-ui-bundle.js": "text/javascript; charset=utf-8",
    "swagger-ui.css": "text/css; charset=utf-8",
}


class LocalDocsStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        media_type = PRECOMPRESSED_DOCS_ASSETS.get(path)
        response = await super().get_response(f"{path}.gz" if media_type else path, scope)
        if media_type and response.status_code == 200:
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Content-Type"] = media_type
            response.headers["Vary"] = "Accept-Encoding"
        return response


def create_app() -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        ensure_database_ready()
        stop_event = asyncio.Event()
        worker_task: asyncio.Task[None] | None = None
        if settings.embedded_document_worker_enabled:
            worker_task = asyncio.create_task(
                DocumentTasks().run_forever(stop_event),
                name="embedded-document-worker",
            )
        try:
            yield
        finally:
            if worker_task is not None:
                stop_event.set()
                try:
                    await asyncio.wait_for(
                        worker_task,
                        timeout=settings.worker_poll_interval_seconds + 1.0,
                    )
                except TimeoutError:
                    worker_task.cancel()
                    try:
                        await worker_task
                    except asyncio.CancelledError:
                        pass

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.mount(
        DOCS_ASSETS_URL,
        LocalDocsStaticFiles(directory=DOCS_ASSETS_DIR),
        name="docs-assets",
    )

    @app.get("/docs", include_in_schema=False)
    async def local_swagger_ui():
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{settings.app_name} - Swagger UI",
            oauth2_redirect_url="/docs/oauth2-redirect",
            swagger_js_url=f"{DOCS_ASSETS_URL}/swagger-ui-bundle.js",
            swagger_css_url=f"{DOCS_ASSETS_URL}/swagger-ui.css",
            swagger_favicon_url=f"{DOCS_ASSETS_URL}/favicon-32x32.png",
        )

    @app.get("/docs/oauth2-redirect", include_in_schema=False)
    async def swagger_ui_redirect():
        return get_swagger_ui_oauth2_redirect_html()

    @app.get("/redoc", include_in_schema=False)
    async def local_redoc():
        return get_redoc_html(
            openapi_url=app.openapi_url,
            title=f"{settings.app_name} - ReDoc",
            redoc_js_url=f"{DOCS_ASSETS_URL}/redoc.standalone.js",
            redoc_favicon_url=f"{DOCS_ASSETS_URL}/favicon-32x32.png",
            with_google_fonts=False,
        )

    register_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()

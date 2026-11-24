"""The web application: API under /api, the built interface everywhere else.

Start it with:  python -m app.serve   (from the backend folder; scripts/start.ps1 does this for you).
That listens on the address and port in the settings. Running uvicorn directly also works, but then the
address is whatever its --host says, so keep that on 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, db
from .api import clips, discovery, jobs, publishing, system, videos
from .config import check_bind, get_settings
from .errors import ClipForgeError
from .logging_config import quiet_dropped_connections, setup_logging
from .security.secrets import SecretError
from .services.supervisor import WorkerSupervisor

log = logging.getLogger("clipforge.api")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _host_of(header: str) -> str:
    header = header.strip().lower()
    if header.startswith("["):            # [::1]:3000
        return header.split("]")[0].lstrip("[")
    return header.rsplit(":", 1)[0] if ":" in header else header


def create_app() -> FastAPI:
    settings = get_settings()
    check_bind(settings)
    settings.ensure_dirs()
    setup_logging(settings.logs_dir, "api", settings.log_level)
    db.configure(settings.db_path)
    db.run_migrations(settings.db_path, settings.data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        quiet_dropped_connections(asyncio.get_running_loop())
        if settings.spawn_worker:
            app.state.worker = WorkerSupervisor()
            app.state.worker.start()
        log.info("ClipForge %s ready on http://%s:%s", __version__, settings.host, settings.port)
        yield
        if getattr(app.state, "worker", None) is not None:
            app.state.worker.stop()

    app = FastAPI(title="ClipForge AI", version=__version__, lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.state.worker = None
    allowed_hosts = {"127.0.0.1", "localhost", "::1", settings.host.lower()}
    allowed_origins = settings.allowed_origins()

    @app.middleware("http")
    async def guard(request: Request, call_next):  # noqa: ANN001, ANN202
        """Two cheap checks that keep other websites away from this local API:
        the request must be addressed to this PC by name (stops DNS-rebinding tricks), and anything that
        changes data must not come from a page on another origin (stops cross-site form posts)."""
        if not settings.allow_remote and _host_of(request.headers.get("host", "")) not in allowed_hosts:
            return JSONResponse({"error": "This address is not served here."}, status_code=400)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") not in allowed_origins:
                log.warning("blocked cross-site request", extra={"origin": origin, "path": request.url.path})
                return JSONResponse({"error": "Requests from other websites are not allowed."}, status_code=403)
        started = time.monotonic()
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        elapsed = time.monotonic() - started
        if response.status_code >= 500 or elapsed > 2.0:
            log.info("%s %s -> %s in %.2fs", request.method, request.url.path, response.status_code, elapsed)
        return response

    @app.exception_handler(ClipForgeError)
    async def known_error(request: Request, exc: ClipForgeError) -> JSONResponse:
        body = {"error": exc.message}
        if exc.hint:
            body["hint"] = exc.hint
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(SecretError)
    async def unreadable_credential(request: Request, exc: SecretError) -> JSONResponse:
        """A stored key or sign-in that this PC cannot decrypt: the data folder was copied from another PC or
        Windows account, or Windows was installed again. Not a fault of the server, and it says what to do."""
        log.warning("a stored credential could not be read on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse({"error": "A stored key or sign-in cannot be read on this PC.",
                             "hint": "That happens when the data folder comes from another PC or Windows account. "
                                     "Enter the key again, or connect the account again."}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query"))
        message = f"{where}: {first.get('msg', 'invalid value')}" if where else first.get("msg", "Invalid request")
        return JSONResponse({"error": f"Invalid request ({message})"}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        reference = uuid.uuid4().hex[:8]
        log.error("unhandled error %s on %s %s", reference, request.method, request.url.path, exc_info=exc)
        return JSONResponse({"error": "Something went wrong on the server.",
                             "hint": f"Details are in data/logs/api.log (reference {reference})."}, status_code=500)

    for module in (videos, clips, jobs, system, publishing, discovery):
        app.include_router(module.router, prefix="/api")

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse({"error": f"No such API endpoint: /api/{rest}"}, status_code=404)

    dist = settings.frontend_dist

    @app.get("/{path:path}", include_in_schema=False)
    async def interface(path: str, request: Request):  # noqa: ANN202
        """The built interface. Unknown paths get index.html so page links and refresh work."""
        query = request.query_params
        if not path and "state" in query and ("code" in query or "error" in query):
            # Google sends you back to the bare address of this PC: pass the sign-in on to its callback
            return RedirectResponse(f"/api/publishing/oauth/callback?{request.url.query}", status_code=302)
        if not (dist / "index.html").exists():
            return JSONResponse({"error": "The interface has not been built yet.",
                                 "hint": "Run scripts\\setup.ps1, or `npm run build` in the frontend folder."},
                                status_code=503)
        candidate = (dist / path).resolve() if path else dist / "index.html"
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {}
            return FileResponse(candidate, headers=headers)
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app

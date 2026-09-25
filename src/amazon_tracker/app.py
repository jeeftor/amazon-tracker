"""Local control plane for the persistent-browser acceptance gate."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from amazon_tracker.browser import BrowserBusy, BrowserManager, BrowserUnavailable
from amazon_tracker.config import Settings
from amazon_tracker.session import SessionResult
from amazon_tracker.storage import Store


class Runtime:
    """Keep browser operations asynchronous and expose only sanitized state."""

    def __init__(self, settings: Settings) -> None:
        """Create private state and a single browser owner."""
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings.data_dir.chmod(0o700)
        self.browser = BrowserManager(settings)
        self.store = Store(settings.data_dir)
        self.session = SessionResult("unknown", "not_verified_since_start")
        self.last_verified_at = self.store.last_verified_at()
        self.operation: dict[str, str | None] = {"action": None, "state": "idle", "error": None}
        self.task: asyncio.Task[None] | None = None

    def submit(self, action: str) -> None:
        """Coalesce identical requests and reject conflicting browser work."""
        if self.task and not self.task.done():
            if self.operation["action"] == action:
                return
            raise BrowserBusy("another_operation_running")
        self.browser.expire_interactive()
        if action == "restart" and self.browser.mode == "interactive":
            raise BrowserBusy("end_interactive_before_restart")
        self.operation = {"action": action, "state": "running", "error": None}
        self.task = asyncio.create_task(self._run(action))

    async def _run(self, action: str) -> None:
        """Persist verification before reporting success; never expose raw exceptions."""
        try:
            if action == "open-login":
                await self.browser.open_login()
            elif action == "verify":
                self.session = SessionResult("checking", "verification_in_progress")
                result = await self.browser.verify()
                verified_at = datetime.now(UTC).isoformat()
                self.store.record(result.state, result.reason, verified_at)
                self.session = result
                self.last_verified_at = verified_at
            elif action == "end-interactive":
                await self.browser.end_interactive()
            elif action == "restart":
                self.session = SessionResult("unknown", "browser_restarted_verify_required")
                await self.browser.restart()
            self.operation["state"] = "complete"
        except BrowserBusy:
            self.operation.update(state="failed", error="browser_busy")
        except Exception:
            self.session = SessionResult("unknown", "operation_failed")
            self.operation.update(state="failed", error="operation_failed_check_browser")

    def status(self) -> dict[str, Any]:
        """Return separate service, browser, and Amazon session health."""
        self.browser.expire_interactive()
        if not self.browser.running and self.session.state == "authenticated":
            self.session = SessionResult("unknown", "browser_disconnected")
        return {
            "service": {"state": "online" if self.browser.running else "degraded"},
            "session": {
                "state": self.session.state,
                "reason": self.session.reason,
                "last_verified_at": self.last_verified_at,
            },
            "browser": {
                "running": self.browser.running,
                "mode": self.browser.mode,
                "pages": [k for k, p in self.browser.pages.items() if not p.is_closed()],
                "interactive_seconds_remaining": max(
                    0, int(self.browser.interactive_until - time.monotonic())
                ),
                "error": self.browser.error,
            },
            "operation": self.operation,
            "tracker": {"state": "pending_live_acceptance"},
            "mqtt": {"state": "not_implemented"},
        }

    async def close(self) -> None:
        """Cancel in-flight navigation before stopping browser and database."""
        if self.task and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        await self.browser.close()
        self.store.close()


def create_app(settings: Settings | None = None, *, start_browser: bool = True) -> FastAPI:
    """Build the local API; tests can omit the browser startup side effect."""
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Keep the control plane available if Chromium cannot start."""
        runtime = Runtime(config)
        app.state.runtime = runtime
        try:
            if start_browser:
                with suppress(BrowserUnavailable):
                    await runtime.browser.start()
            yield
        finally:
            await runtime.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.middleware("http")
    async def local_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Reject cross-site mutations and keep browser/account state out of caches."""
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if (
                request.headers.get("x-tracker-request") != "1"
                or request.headers.get("sec-fetch-site") == "cross-site"
                or (origin is not None and origin != str(request.base_url).rstrip("/"))
            ):
                return JSONResponse({"error": "same_origin_request_required"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            f"frame-src http://127.0.0.1:{config.novnc_port}; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Report process liveness without querying Amazon or the browser."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        """Report browser readiness independently of Amazon authentication."""
        runtime: Runtime = request.app.state.runtime
        running = runtime.browser.running
        return JSONResponse(
            {"ready": running, "database": "ok", "browser": "ok" if running else "unavailable"},
            status_code=200 if running else 503,
        )

    @app.get("/api/v1/status")
    async def status(request: Request) -> dict[str, Any]:
        """Expose sanitized state and the latest asynchronous operation."""
        runtime: Runtime = request.app.state.runtime
        return runtime.status()

    @app.get("/api/v1/session")
    async def session(request: Request) -> dict[str, Any]:
        """Return the Amazon session domain only."""
        runtime: Runtime = request.app.state.runtime
        return dict(runtime.status()["session"])

    @app.post("/api/v1/session/{action}", status_code=202)
    async def session_action(action: str, request: Request) -> JSONResponse:
        """Queue a known login action; arbitrary navigation is not accepted."""
        if action not in {"open-login", "verify", "end-interactive"}:
            return JSONResponse({"error": "unknown_action"}, status_code=404)
        return enqueue(request, action)

    @app.post("/api/v1/browser/restart", status_code=202)
    async def restart(request: Request) -> JSONResponse:
        """Queue a browser restart after explicit confirmation."""
        if request.headers.get("x-confirm-restart") != "yes":
            return JSONResponse({"error": "restart_confirmation_required"}, status_code=400)
        return enqueue(request, "restart")

    def enqueue(request: Request, action: str) -> JSONResponse:
        """Apply the same conflict policy to every control operation."""
        runtime: Runtime = request.app.state.runtime
        try:
            runtime.submit(action)
        except BrowserBusy as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse({"accepted": True, "operation": runtime.operation}, status_code=202)

    panel = Path(__file__).parent / "panel"
    app.mount("/static", StaticFiles(directory=panel), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        """Render the local login panel without a frontend framework."""
        return (panel / "index.html").read_text().replace("__NOVNC_PORT__", str(config.novnc_port))

    return app

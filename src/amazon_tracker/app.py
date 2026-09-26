"""Local control plane for the persistent-browser acceptance gate."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from amazon_tracker.browser import BrowserBusy, BrowserManager, BrowserUnavailable, SessionRequired
from amazon_tracker.build_info import build_info
from amazon_tracker.config import Settings
from amazon_tracker.live_state import LiveState
from amazon_tracker.notification_config import OUTPUTS, NotificationConfig
from amazon_tracker.notifications import NotificationTests
from amazon_tracker.session import SessionResult
from amazon_tracker.shipments import shipment_candidate
from amazon_tracker.storage import Store


class Runtime:
    """Keep browser operations asynchronous and expose only sanitized state."""

    def __init__(self, settings: Settings) -> None:
        """Create private state and a single browser owner."""
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings.data_dir.chmod(0o700)
        self.browser = BrowserManager(settings)
        self.store = Store(settings.data_dir)
        self.notification_config = NotificationConfig(settings.data_dir, settings)
        self.notification_tests = NotificationTests(self.notification_config)
        self.session = SessionResult("unknown", "not_verified_since_start")
        self.last_verified_at = self.store.last_verified_at()
        self.operation: dict[str, str | None] = {"action": None, "state": "idle", "error": None}
        self.task: asyncio.Task[None] | None = None
        self.discovery_revalidated = False
        self.build = build_info()
        self.live_states: dict[str, tuple[LiveState, datetime, str]] = {}
        self.live_context = self.browser.context
        self.browser.on_live_state = self.observe_live_state

    def observe_live_state(self, url: str, state: LiveState, observed: datetime) -> None:
        """Accept response evidence only for a discovered, unfinished shipment."""
        candidate = shipment_candidate(url, self.store.secret)
        if candidate is None:
            return
        known = next(
            (row for row in self.store.shipments() if row["shipment_id"] == candidate.shipment_id),
            None,
        )
        if known is None or known["status"] == "delivered":
            return
        if self.live_context is not self.browser.context:
            self.live_states.clear()
            self.live_context = self.browser.context
        previous = self.live_states.get(candidate.shipment_id)
        if previous is not None and observed <= previous[1]:
            return
        if state.status == "delivered":
            self.store.confirm_delivery(candidate.shipment_id, observed.isoformat())
            self.live_states.pop(candidate.shipment_id, None)
        else:
            self.live_states[candidate.shipment_id] = (state, observed, url)

    def shipments(self) -> list[dict[str, Any]]:
        """Overlay short-lived counts; confirmed delivery remains durable and final."""
        rows = self.store.shipments(
            revalidated=self.discovery_revalidated
            and self.browser.running
            and self.session.state == "authenticated"
        )
        context = self.browser.context
        open_urls = (
            {page.url for page in context.pages if not page.is_closed()} if context else set()
        )
        for row in rows:
            row["arrival_phase"] = "unknown"
            live = self.live_states.get(row["shipment_id"])
            if row["status"] == "delivered" or live is None:
                continue
            state, observed, url = live
            fresh = (
                context is not None
                and context is self.live_context
                and url in open_urls
                and (datetime.now(UTC) - observed).total_seconds() <= 120
            )
            row.update(
                status=state.status if fresh else "unknown",
                status_observed_at=observed.isoformat(),
                status_checked_at=observed.isoformat(),
                observed_at=observed.isoformat(),
                stops_remaining=state.stops_remaining if fresh else None,
                arrival_phase=state.arrival_phase if fresh else "unknown",
                tracking_visibility=(
                    "stops_available" if fresh and state.stops_remaining is not None else "unknown"
                ),
                stale_after_seconds=120,
                is_stale=not fresh or state.status == "unknown",
            )
        return rows

    def submit(self, action: str) -> None:
        """Coalesce identical requests and reject conflicting browser work."""
        if self.task and not self.task.done():
            if self.operation["action"] == action:
                return
            if self.operation["action"] == "refresh" and action == "open-login":
                previous = self.task
                previous.cancel()
                self.operation = {"action": action, "state": "running", "error": None}
                self.task = asyncio.create_task(self._takeover(previous))
                return
            raise BrowserBusy("another_operation_running")
        self.browser.expire_interactive()
        if action == "restart" and self.browser.mode == "interactive":
            raise BrowserBusy("end_interactive_before_restart")
        if action == "refresh" and self.browser.mode == "interactive":
            raise BrowserBusy("end_interactive_before_refresh")
        self.operation = {"action": action, "state": "running", "error": None}
        self.task = asyncio.create_task(self._run(action))

    async def _takeover(self, previous: asyncio.Task[None]) -> None:
        """Cancel discovery before handing browser ownership to your login action."""
        with suppress(asyncio.CancelledError):
            await previous
        await self._run("open-login")

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
                self.discovery_revalidated = False
                await self.browser.restart()
            elif action == "refresh":
                result_discovery = await self.browser.discover()
                observed_at = datetime.now(UTC).isoformat()
                self.store.save_discovery(result_discovery, observed_at)
                self.session = SessionResult("authenticated", "orders_discovery_verified")
                self.store.record(self.session.state, self.session.reason, observed_at)
                self.last_verified_at = observed_at
                self.discovery_revalidated = True
            self.operation["state"] = "complete"
        except SessionRequired as exc:
            self.session = exc.result
            self.discovery_revalidated = False
            self.operation.update(state="failed", error="amazon_session_requires_attention")
        except BrowserBusy as exc:
            self.operation.update(state="failed", error=str(exc))
        except Exception:
            self.session = SessionResult("unknown", "operation_failed")
            self.operation.update(state="failed", error="operation_failed_check_browser")

    def status(self) -> dict[str, Any]:
        """Return separate service, browser, and Amazon session health."""
        self.browser.expire_interactive()
        if not self.browser.running and self.session.state == "authenticated":
            self.session = SessionResult("unknown", "browser_disconnected")
        return {
            "build": self.build,
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
            "tracker": {
                "state": "orders_status",
                "last_discovery": self.store.last_discovery(),
                "discovered_shipments": len(self.store.shipments()),
            },
            "notifications": {
                "automatic_announcements": False,
                "tests": self.notification_tests.status(),
            },
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

    @app.get("/api/v1/shipments")
    async def shipments(request: Request) -> list[dict[str, Any]]:
        """List discovered packages without leaking order numbers or signed links."""
        runtime: Runtime = request.app.state.runtime
        return runtime.shipments()

    @app.post("/api/v1/refresh", status_code=202)
    async def refresh(request: Request) -> JSONResponse:
        """Queue one bounded scan; repeated requests share the in-flight operation."""
        return enqueue(request, "refresh")

    @app.get("/api/v1/settings/notifications")
    async def notification_settings(request: Request) -> dict[str, Any]:
        """Return effective settings with environment ownership and secret-presence flags."""
        runtime: Runtime = request.app.state.runtime
        return runtime.notification_config.public()

    @app.patch("/api/v1/settings/notifications")
    async def save_notification_settings(request: Request) -> JSONResponse:
        """Save a bounded JSON patch without reflecting invalid secrets in errors."""
        runtime: Runtime = request.app.state.runtime
        if runtime.notification_tests.lock.locked():
            return JSONResponse({"error": "notification_test_running"}, status_code=409)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                return JSONResponse({"error": "settings_request_too_large"}, status_code=413)
        if runtime.notification_tests.lock.locked():
            return JSONResponse({"error": "notification_test_running"}, status_code=409)
        try:
            patch = json.loads(body)
            if not isinstance(patch, dict):
                raise ValueError("invalid_settings")
            runtime.notification_config.update(patch)
        except ValidationError:
            return JSONResponse({"error": "invalid_settings_values"}, status_code=422)
        except (ValueError, UnicodeError) as exc:
            code = str(exc)
            if code not in {
                "unknown_settings_field",
                "environment_managed_field",
                "enabled_output_requires_configuration",
            }:
                code = "invalid_settings"
            return JSONResponse({"error": code}, status_code=422)
        except OSError:
            return JSONResponse({"error": "settings_save_failed"}, status_code=500)
        runtime.notification_tests.results.clear()
        return JSONResponse(runtime.notification_config.public())

    @app.post("/api/v1/notifications/{output}/test")
    async def test_notification(output: str, request: Request) -> JSONResponse:
        """Send a clearly labeled test only when you explicitly request one."""
        if output not in OUTPUTS:
            return JSONResponse({"error": "unknown_output"}, status_code=404)
        runtime: Runtime = request.app.state.runtime
        try:
            result = await runtime.notification_tests.run(output)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(result, status_code=200 if result["state"] == "sent" else 502)

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

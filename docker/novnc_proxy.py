"""Restrict the local browser viewer against hostile websites and DNS rebinding."""

import os
from http.client import HTTPMessage

from websockify.auth_plugins import AuthenticationError, ExpectOrigin
from websockify.websocketproxy import ProxyRequestHandler, WebSocketProxy

VIEWER_PORT = int(os.environ.get("NOVNC_PORT", "6080"))
PANEL_PORT = int(os.environ.get("PANEL_PORT", "8080"))
VIEWER_HOSTS = {f"127.0.0.1:{VIEWER_PORT}", f"localhost:{VIEWER_PORT}"}
VIEWER_ORIGINS = " ".join(f"http://{host}" for host in sorted(VIEWER_HOSTS))


class LocalViewerAuth(ExpectOrigin):
    """Validate Host as well as Origin before the WebSocket upgrade."""

    def authenticate(self, headers: HTTPMessage, target_host: str, target_port: int) -> None:
        """Reject rebinding even when a client sends an allowed Origin."""
        if headers.get("Host") not in VIEWER_HOSTS:
            raise AuthenticationError(response_code=403, response_msg="Local viewer host required")
        super().authenticate(headers, target_host, target_port)


class LocalViewerHandler(ProxyRequestHandler):
    """Permit local Host headers and embedding only by your local panel."""

    def do_GET(self) -> None:
        """Reject rebinding before serving files or upgrading a WebSocket."""
        if self.headers.get("Host") not in VIEWER_HOSTS:
            self.send_error(403, "Local viewer host required")
            return
        super().do_GET()

    def end_headers(self) -> None:
        """Prevent other websites from framing your signed-in browser."""
        self.send_header(
            "Content-Security-Policy",
            f"frame-ancestors 'self' http://127.0.0.1:{PANEL_PORT} http://localhost:{PANEL_PORT}",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


if __name__ == "__main__":
    WebSocketProxy(
        RequestHandlerClass=LocalViewerHandler,
        listen_host="0.0.0.0",
        listen_port=6080,
        target_host="127.0.0.1",
        target_port=5900,
        web="/usr/share/novnc",
        auth_plugin=LocalViewerAuth(VIEWER_ORIGINS),
    ).start_server()

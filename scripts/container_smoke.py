"""Exercise a disposable container without your Amazon account or persistent volume."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def docker(*arguments: str) -> str:
    """Run Docker without a shell and return its output."""
    return subprocess.check_output(["docker", *arguments], text=True).strip()


def request(port: int, path: str, **headers: str) -> tuple[int, dict[str, str], bytes]:
    """Read an HTTP response, including expected rejection statuses."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.code, dict(response.headers), response.read()


def websocket(port: int, origin: str | None, host: str = "127.0.0.1:6080") -> int:
    """Check only the handshake; never read or send desktop frames."""
    headers = [
        "GET /websockify HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version: 13",
    ]
    if origin is not None:
        headers.append(f"Origin: {origin}")
    with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
        connection.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
        result = b""
        while b"\r\n" not in result:
            chunk = connection.recv(1024)
            if not chunk:
                raise RuntimeError("Viewer closed without an HTTP response")
            result += chunk
    return int(result.split(b" ")[1])


def main() -> None:
    """Verify sandbox startup, permissions, local API, and viewer security boundaries."""
    image = sys.argv[1]
    container = docker(
        "run",
        "--detach",
        "--init",
        "--shm-size=1g",
        "--security-opt",
        "seccomp=./docker/chromium-seccomp.json",
        "--publish",
        "127.0.0.1::8080",
        "--publish",
        "127.0.0.1::6080",
        image,
    )
    try:
        panel = int(docker("port", container, "8080/tcp").rsplit(":", 1)[1])
        viewer = int(docker("port", container, "6080/tcp").rsplit(":", 1)[1])
        deadline = time.monotonic() + 90
        while True:
            try:
                code, _, body = request(panel, "/ready")
                if code == 200 and json.loads(body)["ready"]:
                    break
            except (OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Disposable container did not become ready")
            time.sleep(1)
        assert docker("exec", container, "id", "-u") == "1000"
        assert docker("exec", container, "stat", "-c", "%a", "/data/state") == "700"
        assert (
            docker("exec", container, "stat", "-c", "%a", "/data/state/installation-secret")
            == "600"
        )
        code, _, body = request(panel, "/api/v1/status")
        assert code == 200 and json.loads(body)["session"]["state"] == "unknown"
        build = json.loads(body)["build"]
        assert build["version"]
        if expected_sha := os.environ.get("GITHUB_SHA"):
            assert build["sha"] == expected_sha
            assert build["dirty"] is False
        assert request(panel, "/", Host="attacker.invalid")[0] == 400
        code, headers, _ = request(viewer, "/vnc.html", Host="127.0.0.1:6080")
        assert code == 200
        assert (
            "frame-ancestors 'self' http://127.0.0.1:8080 http://localhost:8080"
            in headers["Content-Security-Policy"]
        )
        assert request(viewer, "/vnc.html", Host="attacker.invalid")[0] == 403
        assert websocket(viewer, "https://attacker.invalid") == 403
        assert websocket(viewer, None) == 403
        assert websocket(viewer, "http://127.0.0.1:6080", "attacker.invalid") == 403
        assert websocket(viewer, "http://127.0.0.1:6080") == 101
        print("Container smoke passed: sandbox, UID, storage, API, viewer Origin/Host/framing")
    finally:
        docker("rm", "--force", "--volumes", container)


if __name__ == "__main__":
    main()

"""Wait for the virtual display and serve the single-worker control plane."""

import os
import subprocess
import time

import uvicorn


def main() -> None:
    """Keep process supervision separate from browser/session health."""
    if os.environ.get("DISPLAY"):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["xdpyinfo"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3
            )
            if result.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise SystemExit("Your virtual display did not become ready")
    uvicorn.run(
        "amazon_tracker.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8080,
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    main()

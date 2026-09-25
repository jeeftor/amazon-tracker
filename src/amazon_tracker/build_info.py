"""Describe the running build without reading browser data or requiring Git in the image."""

import os
import re
from importlib.metadata import version


def build_info() -> dict[str, str | bool | None]:
    """Return the package version and optional build-time source stamp."""
    sha = os.environ.get("TRACKER_BUILD_SHA", "")
    dirty = os.environ.get("TRACKER_BUILD_DIRTY", "")
    return {
        "version": version("amazon-delivery-tracker"),
        "sha": sha if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha) else None,
        "dirty": {"true": True, "false": False}.get(dirty),
    }

"""Normalize the observed get-state response without retaining location or account data."""

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class LiveState:
    """Keep delivery, live counts, and arrival phase separate."""

    status: Literal["unknown", "out_for_delivery", "delivered"] = "unknown"
    stops_remaining: int | None = None
    arrival_phase: Literal["unknown", "approaching", "arriving_next"] = "unknown"


def parse_live_state(payload: object) -> LiveState:
    """Read only mapState; explicit delivery overrides leftover route information."""
    if not isinstance(payload, dict):
        return LiveState()
    if (
        payload.get("success", True) is not True
        or payload.get("responseCode", "SUCCESS") != "SUCCESS"
    ):
        return LiveState()
    value = payload.get("value")
    state = value.get("mapState") if isinstance(value, dict) else None
    if not isinstance(state, dict):
        return LiveState()
    # The real delivered response retained stops=1. Never wait for or invent zero.
    if state.get("status") == "DELIVERED":
        return LiveState(status="delivered")
    if state.get("status") != "PICKED_UP":
        return LiveState()
    stops = state.get("stops")
    callout = state.get("calloutMessage")
    if type(stops) is not int or stops < 0 or not isinstance(callout, str):
        return LiveState(status="out_for_delivery")
    callout = " ".join(callout.split())
    if stops == 1 and callout == "You're the next stop":
        return LiveState("out_for_delivery", 1, "arriving_next")
    match = re.fullmatch(r"(\d{1,4}) stops? away", callout)
    if match and int(match[1]) == stops:
        return LiveState("out_for_delivery", stops, "approaching")
    return LiveState(status="out_for_delivery")

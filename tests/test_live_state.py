"""Replay sanitized live evidence, including a delivered response with a leftover count."""

from dataclasses import asdict

import pytest

from amazon_tracker.live_state import LiveState, parse_live_state


def test_observed_count_sequence_ends_on_delivered_not_zero() -> None:
    """Use the observed 3, 2, next-stop, delivered shapes without private fields."""
    samples = [
        {"stops": 3, "status": "PICKED_UP", "calloutMessage": "3 stops away"},
        {"stops": 2, "status": "PICKED_UP", "calloutMessage": "2 stops away"},
        {"stops": 1, "status": "PICKED_UP", "calloutMessage": "You're the next stop"},
        {
            "stops": 1,
            "status": "DELIVERED",
            "primaryStatus": "Delivered",
            "calloutMessage": "",
        },
    ]
    states = [parse_live_state({"value": {"mapState": sample}}) for sample in samples]
    assert states == [
        LiveState("out_for_delivery", 3, "approaching"),
        LiveState("out_for_delivery", 2, "approaching"),
        LiveState("out_for_delivery", 1, "arriving_next"),
        LiveState("delivered", None, "unknown"),
    ]


@pytest.mark.parametrize("stops", [None, True, False, -1, 1.0, "1"])
def test_missing_or_invalid_count_is_not_zero(stops: object) -> None:
    """Reject bools, strings, and missing values instead of fabricating distance."""
    state = parse_live_state(
        {
            "value": {
                "mapState": {"status": "PICKED_UP", "stops": stops, "calloutMessage": "1 stop away"}
            }
        }
    )
    assert state == LiveState(status="out_for_delivery")


def test_conflicting_callout_does_not_announce_a_stop() -> None:
    """A route update with inconsistent fields is not a reliable announcement source."""
    for callout in ("3 stops away", "You're the next stop", "", None):
        assert parse_live_state(
            {"value": {"mapState": {"status": "PICKED_UP", "stops": 2, "calloutMessage": callout}}}
        ) == LiveState(status="out_for_delivery")


def test_explicit_zero_does_not_mean_delivered() -> None:
    """Only the delivered status can finalize a package, regardless of route count."""
    assert parse_live_state(
        {
            "value": {
                "mapState": {"status": "PICKED_UP", "stops": 0, "calloutMessage": "0 stops away"}
            }
        }
    ) == LiveState("out_for_delivery", 0, "approaching")


def test_failed_or_unfamiliar_response_cannot_claim_delivery() -> None:
    """An error envelope wins over any stale delivery fields it happens to contain."""
    value = {"mapState": {"status": "DELIVERED", "stops": 1}}
    for payload in (
        {"success": False, "value": value},
        {"responseCode": "ERROR", "value": value},
        {"value": {"mapState": {"status": "UNFAMILIAR", "stops": 1}}},
        {"value": None},
        None,
        "private-response-marker",
    ):
        assert parse_live_state(payload) == LiveState()


def test_location_and_arbitrary_labels_never_leave_normalizer() -> None:
    """Discard timeline and arbitrary status text before storing or publishing evidence."""
    state = parse_live_state(
        {
            "success": True,
            "responseCode": "SUCCESS",
            "value": {
                "mapState": {
                    "status": "DELIVERED",
                    "stops": 1,
                    "secondaryStatus": "private-response-marker",
                },
                "packageLocationTimeline": ["private-response-marker"],
            },
        }
    )
    assert asdict(state) == {
        "status": "delivered",
        "stops_remaining": None,
        "arrival_phase": "unknown",
    }

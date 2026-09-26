"""Exercise passive responses in intercepted Chromium without real Amazon traffic."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Route

from amazon_tracker.app import Runtime
from amazon_tracker.config import Settings
from amazon_tracker.discovery import DiscoveryResult
from amazon_tracker.live_state import LiveState
from amazon_tracker.storage import Store

ENDPOINT = "https://www.amazon.com/progress-tracker/package/actions/package-location/get-state"
PAGE = "https://www.amazon.com/progress-tracker/package?shipmentId=synthetic-one"
OTHER = "https://www.amazon.com/progress-tracker/package?shipmentId=synthetic-two"


@pytest.mark.browser
async def test_passive_responses_identity_freshness_and_final_delivery(tmp_path: Path) -> None:
    """Observe page requests, isolate packages, expire counts, and persist delivery once."""
    runtime = Runtime(Settings(data_dir=tmp_path, browser_headless=True))
    payload: dict[str, Any] = {}
    requests: list[str] = []
    observed = asyncio.Event()

    async def route_response(route: Route) -> None:
        """Keep all requests synthetic and count only those initiated by the page."""
        requests.append(route.request.url)
        if route.request.url == ENDPOINT:
            await route.fulfill(json=payload)
        else:
            await route.fulfill(body="<html></html>")

    def accept(url: str, state: LiveState, when: datetime) -> None:
        """Record normalized evidence and signal completed delivery to the test."""
        runtime.observe_live_state(url, state, when)
        observed.set()

    async def send(stops: int | None, status: str, callout: str) -> None:
        """Simulate an update Amazon's page itself would request."""
        payload.clear()
        payload.update(
            value={"mapState": {"stops": stops, "status": status, "calloutMessage": callout}},
            success=True,
        )
        observed.clear()
        await page.evaluate(
            """endpoint => fetch(endpoint, {
          method: 'POST', body: 'shipmentId=synthetic-one&csrfToken=synthetic-secret',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'}
        }).then(r => r.text())""",
            ENDPOINT,
        )
        await asyncio.wait_for(observed.wait(), 3)

    try:
        runtime.store.save_discovery(
            DiscoveryResult([PAGE, OTHER], 1, True), datetime.now(UTC).isoformat()
        )
        await runtime.browser.start()
        context = runtime.browser.context
        assert context is not None
        await context.route("**/*", route_response)
        runtime.browser.on_live_state = accept
        page = await runtime.browser.page("admin")
        await page.goto(PAGE)
        first_id = next(
            row["shipment_id"]
            for row in runtime.shipments()
            if row["shipment_id"]
            == runtime.store.connection.execute(
                "SELECT shipment_id FROM shipments WHERE tracking_url=?", (PAGE,)
            ).fetchone()[0]
        )

        await send(3, "PICKED_UP", "3 stops away")
        first = next(row for row in runtime.shipments() if row["shipment_id"] == first_id)
        assert first["stops_remaining"] == 3 and not first["is_stale"]
        assert all(
            row["stops_remaining"] is None
            for row in runtime.shipments()
            if row["shipment_id"] != first_id
        )
        await send(2, "PICKED_UP", "2 stops away")
        # A delayed older response must not overwrite a newer update.
        runtime.observe_live_state(
            PAGE,
            LiveState("out_for_delivery", 9, "approaching"),
            datetime.now(UTC) - timedelta(minutes=1),
        )
        assert runtime.live_states[first_id][0].stops_remaining == 2

        await send(None, "PICKED_UP", "")
        assert (
            next(row for row in runtime.shipments() if row["shipment_id"] == first_id)[
                "stops_remaining"
            ]
            is None
        )
        await send(1, "PICKED_UP", "You're the next stop")
        first = next(row for row in runtime.shipments() if row["shipment_id"] == first_id)
        assert first["arrival_phase"] == "arriving_next"
        state, when, url = runtime.live_states[first_id]
        runtime.live_states[first_id] = (state, when - timedelta(seconds=121), url)
        first = next(row for row in runtime.shipments() if row["shipment_id"] == first_id)
        assert first["is_stale"] and first["stops_remaining"] is None
        runtime.live_states[first_id] = (state, when, url)
        await page.goto(OTHER)
        first = next(row for row in runtime.shipments() if row["shipment_id"] == first_id)
        assert first["is_stale"] and first["stops_remaining"] is None

        # Wrong shipment and GET cannot apply a delivered payload to this page.
        payload.update(value={"mapState": {"status": "DELIVERED", "stops": 1}})
        observed.clear()
        await page.evaluate(
            """endpoint => fetch(endpoint, {
          method: 'POST', body: 'shipmentId=synthetic-one'
        }).then(r => r.text())""",
            ENDPOINT,
        )
        await page.evaluate("endpoint => fetch(endpoint).then(r => r.text())", ENDPOINT)
        await asyncio.gather(*runtime.browser.response_tasks)
        assert not observed.is_set()
        assert all(row["status"] != "delivered" for row in runtime.shipments())

        await page.goto(PAGE)
        await send(1, "DELIVERED", "")
        final = next(row for row in runtime.shipments() if row["shipment_id"] == first_id)
        assert final["status"] == "delivered" and not final["is_stale"]
        assert final["stops_remaining"] is None and final["delivery_date_label"] is None
        await send(1, "PICKED_UP", "You're the next stop")
        assert next(row for row in runtime.shipments() if row["shipment_id"] == first_id) == final
        # Three document navigations and eight explicit fetches, no observer requests.
        assert len(requests) == 11
        assert "synthetic-secret" not in str(runtime.shipments())
    finally:
        await runtime.close()
    store = Store(tmp_path)
    try:
        final = next(row for row in store.shipments() if row["shipment_id"] == first_id)
        assert final["status"] == "delivered" and not final["is_stale"]
        assert final["stops_remaining"] is None
    finally:
        store.close()

"""Normalize observed delivery labels without guessing live tracking or delivery years."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


@dataclass(frozen=True)
class DeliveryStatus:
    """A minimal delivered fact; missing or unsupported text remains unknown."""

    status: Literal["unknown", "delivered"] = "unknown"
    date_label: str | None = None


def parse_delivery_status(text: str) -> DeliveryStatus:
    """Accept only complete, known English delivered labels; never retain arbitrary text."""
    label = " ".join(text.split())
    relative = re.fullmatch(r"Delivered (today|yesterday)", label, re.IGNORECASE)
    if relative:
        return DeliveryStatus("delivered", relative[1].lower())
    absolute = re.fullmatch(rf"Delivered ({'|'.join(MONTHS)}) (\d{{1,2}})", label, re.IGNORECASE)
    if absolute:
        month = absolute[1].capitalize()
        day = int(absolute[2])
        try:
            # Leap year allows February 29 without inferring the shipment's year.
            date(2000, MONTHS.index(month) + 1, day)
        except ValueError:
            return DeliveryStatus()
        return DeliveryStatus("delivered", f"{month} {day}")
    return DeliveryStatus()

"""Parsing of the time strings both Jolpica sources use."""

from __future__ import annotations


def parse_duration(text: str | None) -> float | None:
    """'1:39.264', '25.123' or '01:02:03.5' -> seconds; None if absent or malformed."""
    if not text:
        return None
    try:
        total = 0.0
        for part in text.strip().split(":"):
            total = total * 60 + float(part)
        return total
    except ValueError:
        return None

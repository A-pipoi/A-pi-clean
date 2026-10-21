"""Canonical symbol-key helpers shared by API reporting paths."""

from __future__ import annotations

from collections.abc import Mapping


def stock_metadata_candidates(symbol: object) -> list[str]:
    """Return canonical and source spellings for common A-share code formats."""

    raw = str(symbol or "").strip().upper().replace(" ", "")
    candidates = [raw]
    code = raw
    exchange = ""
    if raw.startswith(("SH.", "SZ.", "BJ.")):
        exchange, code = raw.split(".", 1)
    elif raw.startswith(("SH", "SZ", "BJ")) and len(raw) == 8:
        exchange, code = raw[:2], raw[2:]
    elif "." in raw:
        code, exchange = raw.split(".", 1)
    if len(code) == 6 and code.isdigit():
        if exchange in {"SH", "SZ", "BJ"}:
            candidates.extend((f"{code}.{exchange}", code))
        else:
            suffix = (
                "SH"
                if code.startswith(("5", "6", "9"))
                else "BJ"
                if code.startswith(("4", "8"))
                else "SZ"
            )
            candidates.extend((f"{code}.{suffix}", code))
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def stock_metadata_for_symbol(
    symbol: object, metadata: Mapping[str, Mapping[str, str]]
) -> dict[str, str]:
    """Resolve symbol aliases and copy the matching metadata row."""

    for candidate in stock_metadata_candidates(symbol):
        row = metadata.get(candidate)
        if row is not None:
            return dict(row)
    return {}

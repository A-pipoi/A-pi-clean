"""Opt-in, point-in-time corporate-action accounting for historical research."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from quant.execution.corporate_actions import _date, _number

from .engine import Position


class BacktestCorporateActionBook:
    """Track dated dividend entitlements without changing raw execution prices."""

    def __init__(self, observed_events: Callable[[date], list[dict[str, Any]]]) -> None:
        self._provider = observed_events
        self._events: dict[str, dict[str, Any]] = {}
        self._report: list[dict[str, object]] = []
        self._position_history: dict[date, dict[str, int]] = {}

    def process_day(
        self,
        day: date,
        positions: dict[str, Position],
        last_prices: dict[str, float],
    ) -> float:
        self._position_history[day] = {
            symbol: position.quantity for symbol, position in positions.items()
        }
        cash_credit = 0.0
        for event in self._provider(day):
            if event.get("__invalid_event"):
                raise ValueError(f"invalid observed corporate action: {event['__invalid_event']}")
            if event.get("div_proc") != "实施":
                continue
            symbol = str(event.get("ts_code") or "")
            if not symbol:
                raise ValueError("implemented corporate action missing symbol")
            try:
                record = _date(event["record_date"])
                ex_date = _date(event["ex_date"])
                announcement = _date(event["ann_date"])
                implementation_announcement = _date(event.get("imp_ann_date") or event["ann_date"])
                publication = max(announcement, implementation_announcement)
                if record >= day or ex_date <= record or publication > day:
                    continue
                if record not in self._position_history:
                    raise ValueError("blocked_missing_record_date_snapshot")
                if event.get("cash_div") is None or event.get("stk_div") is None:
                    raise ValueError("blocked_missing_distribution_amount")
                cash_rate = _number(event["cash_div"])
                share_rate = _number(event["stk_div"])
                pay_date = _date(event["pay_date"]) if event.get("pay_date") else None
                list_date = _date(event["div_listdate"]) if event.get("div_listdate") else None
                if cash_rate and (pay_date is None or pay_date < record):
                    raise ValueError("missing_or_invalid_pay_date")
                if share_rate and (list_date is None or list_date < ex_date):
                    raise ValueError("missing_or_invalid_div_listdate")
                identity = hashlib.sha256(
                    f"{symbol}:{record}:{ex_date}:{event.get('end_date')}".encode()
                ).hexdigest()
                raw = json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False)
                saved = self._events.get(identity)
                if saved and saved["source_json"] != raw:
                    previous = saved["event"]
                    changed_known = any(
                        previous.get(field) is not None and previous.get(field) != event.get(field)
                        for field in ("cash_div", "stk_div")
                    )
                    changed_date = (saved["cash_posted"] and previous.get("pay_date") != event.get("pay_date")) or (
                        saved["shares_posted"] and previous.get("div_listdate") != event.get("div_listdate")
                    )
                    if changed_known or changed_date:
                        raise ValueError("blocked_source_revision_requires_review")
                    saved["event"] = event
                    saved["source_json"] = raw
                if saved is None:
                    # Use the historical end-of-day ledger, never current ex-date
                    # holdings to infer a past entitlement.
                    quantity = self._position_history[record].get(symbol, 0)
                    saved = {
                        "symbol": symbol,
                        "record_date": record,
                        "ex_date": ex_date,
                        "quantity": quantity,
                        "event": event,
                        "source_json": raw,
                        "cash_posted": False,
                        "shares_posted": False,
                        "cash_receivable": 0.0,
                        "shares_receivable": 0,
                    }
                    self._events[identity] = saved
                if day == record:
                    saved["quantity"] = self._position_history[record].get(symbol, 0)
                quantity = int(saved["quantity"])
                if cash_rate and day >= ex_date and not saved["cash_posted"]:
                    amount = (cash_rate * quantity).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
                    saved["cash_receivable"] = float(amount)
                    if pay_date is not None and day >= pay_date:
                        saved["cash_posted"] = True
                        saved["cash_receivable"] = 0.0
                        cash_credit += float(amount)
                        self._report.append({"symbol": symbol, "date": day.isoformat(), "type": "cash_paid", "amount": float(amount)})
                if share_rate and day >= ex_date and not saved["shares_posted"]:
                    shares = share_rate * quantity
                    if shares != shares.to_integral_value():
                        raise ValueError("blocked_fractional_share_allocation_unproven")
                    whole_shares = int(shares)
                    saved["shares_receivable"] = whole_shares
                    if list_date is not None and day >= list_date:
                        if whole_shares:
                            positions.setdefault(symbol, Position(symbol)).add(day, whole_shares, 0.0)
                        saved["shares_posted"] = True
                        saved["shares_receivable"] = 0
                        self._report.append({"symbol": symbol, "date": day.isoformat(), "type": "shares_listed", "quantity": whole_shares})
            except (KeyError, TypeError, ValueError, ArithmeticError) as error:
                raise ValueError(f"invalid corporate action for {symbol or 'unknown'}: {error}") from None
        return cash_credit

    def receivables(self, day: date, last_prices: dict[str, float]) -> float:
        total = 0.0
        for item in self._events.values():
            if day < item["ex_date"]:
                continue
            total += float(item["cash_receivable"])
            symbol = str(item["symbol"])
            total += int(item["shares_receivable"]) * float(last_prices.get(symbol, 0.0))
        return total

    def report(self) -> dict[str, object]:
        return {
            "enabled": True,
            "price_basis": "raw quotes; no manual adjusted-price repricing",
            "cash_basis": "provider net cash_div, credited on pay_date",
            "shares_basis": "whole stk_div shares credited on div_listdate",
            "events": list(self._report),
            "entitlements": [
                {
                    "symbol": item["symbol"],
                    "record_date": item["record_date"].isoformat(),
                    "ex_date": item["ex_date"].isoformat(),
                    "quantity_at_record_date": item["quantity"],
                }
                for item in self._events.values()
            ],
            "historical_entitlements_reconstructed": False,
        }

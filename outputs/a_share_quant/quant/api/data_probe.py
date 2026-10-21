"""Actual daily and calendar probes, independent of the HTTP service."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Protocol

from quant.data.adapters import _TushareRows

LOGGER = logging.getLogger(__name__)


class TushareQuery(Protocol):
    def __call__(
        self, token: str, api_name: str, params: dict[str, object], *, fields: str = ""
    ) -> _TushareRows: ...


def probe_tushare_data(
    *, query: TushareQuery, china_timezone: tzinfo
) -> dict[str, object]:
    """Verify a real daily response and the expected latest open trading day."""
    checked_at = datetime.now(UTC).isoformat()
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        return {
            "status": "unconfigured",
            "data_reachable": False,
            "data_fresh": None,
            "checked_at": checked_at,
            "error": "Tushare Token 未配置",
        }
    today = datetime.now(china_timezone).date()
    start = today - timedelta(days=20)
    try:
        rows = query(
            token,
            "daily",
            {
                "ts_code": "600000.SH",
                "start_date": start.strftime("%Y%m%d"),
                "end_date": today.strftime("%Y%m%d"),
            },
            fields="ts_code,trade_date,close",
        )
        daily_rows = rows.to_dict(orient="records")
        dates = [
            str(row.get("trade_date") or "")
            for row in daily_rows
            if row.get("close") is not None
        ]
        if not dates:
            raise RuntimeError("Tushare 日线探针未返回有效行情")
        latest = max(dates)
        try:
            calendar = query(
                token,
                "trade_cal",
                {
                    "exchange": "SSE",
                    "start_date": start.strftime("%Y%m%d"),
                    "end_date": today.strftime("%Y%m%d"),
                },
                fields="cal_date,is_open",
            )
            calendar_rows = calendar.to_dict(orient="records")
            open_dates = [
                str(row.get("cal_date") or "")
                for row in calendar_rows
                if str(row.get("is_open")) == "1"
            ]
            if not open_dates:
                raise RuntimeError("Tushare 交易日历探针未返回开市日期")
        except Exception as error:
            # Provider messages and exception chains may include access tokens.
            LOGGER.exception(
                "Data probe calendar failed: %s", type(error).__name__, exc_info=None
            )
            return {
                "status": "freshness_unknown",
                "data_reachable": True,
                "data_fresh": None,
                "latest_trade_date": f"{latest[:4]}-{latest[4:6]}-{latest[6:]}",
                "checked_at": checked_at,
                "error": f"交易日历不可用：{type(error).__name__}",
            }
        # Today's close is not expected before the A-share post-close update.
        now = datetime.now(china_timezone)
        eligible = [
            value
            for value in open_dates
            if value < today.strftime("%Y%m%d") or now.hour >= 16
        ]
        expected = max(eligible) if eligible else None
        fresh = expected is not None and latest >= expected
        return {
            "status": "fresh" if fresh else "stale",
            "data_reachable": True,
            "data_fresh": fresh,
            "latest_trade_date": f"{latest[:4]}-{latest[4:6]}-{latest[6:]}",
            "expected_trade_date": f"{expected[:4]}-{expected[4:6]}-{expected[6:]}"
            if expected
            else None,
            "checked_at": checked_at,
            "error": None,
        }
    except Exception as error:
        LOGGER.exception(
            "Data probe daily failed: %s", type(error).__name__, exc_info=None
        )
        return {
            "status": "unreachable",
            "data_reachable": False,
            "data_fresh": None,
            "checked_at": checked_at,
            "error": type(error).__name__,
        }

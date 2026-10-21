from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from datetime import date
from typing import Any

from quant.errors import InputTypeError

# A deterministic, manually reviewed seed universe for the default research
# and paper-trading experience.  It is intentionally broad by sector rather
# than selected from recent returns, so enlarging the sample does not become a
# hidden parameter search.  Users can still replace it from the UI/API.
DEFAULT_SYMBOLS = [
    "000001",  # 平安银行 / 银行
    "600000",  # 浦发银行 / 银行
    "600036",  # 招商银行 / 银行
    "000858",  # 五粮液 / 白酒
    "600519",  # 贵州茅台 / 白酒
    "000333",  # 美的集团 / 家用电器
    "000651",  # 格力电器 / 家用电器
    "600276",  # 恒瑞医药 / 化学制药
    "300750",  # 宁德时代 / 电气设备
    "002594",  # 比亚迪 / 汽车整车
    "000938",  # 紫光股份 / IT设备
    "600030",  # 中信证券 / 证券
    "600900",  # 长江电力 / 水力发电
    "600028",  # 中国石化 / 石油加工
    "601899",  # 紫金矿业 / 铜
    "601012",  # 隆基绿能 / 电气设备
    "601318",  # 中国平安 / 保险
    "000063",  # 中兴通讯 / 通信设备
    "002415",  # 海康威视 / 计算机设备
    "600309",  # 万华化学 / 化学原料
    "600031",  # 三一重工 / 工程机械
    "601088",  # 中国神华 / 煤炭
    "600588",  # 用友网络 / 软件服务
    "000568",  # 泸州老窖 / 白酒
]

DEFAULT_UNIVERSE_NOTE = (
    "24只固定分行业种子池；用于默认回测与纸面交易。"
    "它不是指数成分股快照，也不代表投资建议。"
)

FACTOR_MINING_UNIVERSE_INDEX_CODE = "000300.SH"
FACTOR_MINING_UNIVERSE_NAME = "沪深300历史成分"
FACTOR_MINING_MIN_LISTING_DAYS = 60


class HistoricalUniverse:
    """Historical index/stock-pool snapshots; current constituents cannot be backfilled."""

    def __init__(self, snapshots: dict[date, set[str]]) -> None:
        if not snapshots:
            raise ValueError("at least one historical snapshot is required")
        self._dates = sorted(snapshots)
        self._snapshots = {day: frozenset(symbols) for day, symbols in snapshots.items()}

    @classmethod
    def from_index_weight_history(cls, payload: Mapping[str, Any]) -> HistoricalUniverse:
        """Build the as-of universe from the immutable index_weight response."""

        raw_snapshots = payload.get("snapshots")
        if not isinstance(raw_snapshots, list):
            raise InputTypeError("index membership history is missing snapshots")
        snapshots: dict[date, set[str]] = {}
        for raw in raw_snapshots:
            if not isinstance(raw, Mapping):
                continue
            day_text = str(raw.get("trade_date") or "").strip()
            try:
                day = date.fromisoformat(day_text[:10])
            except ValueError:
                continue
            members = raw.get("members")
            if not isinstance(members, list):
                continue
            symbols = {
                str(member.get("ts_code") or member.get("con_code") or "").strip().upper()
                for member in members
                if isinstance(member, Mapping)
                and str(member.get("ts_code") or member.get("con_code") or "").strip()
            }
            if symbols:
                snapshots[day] = symbols
        if not snapshots:
            raise ValueError("index membership history contains no valid constituent snapshots")
        return cls(snapshots)

    @property
    def snapshot_dates(self) -> tuple[date, ...]:
        return tuple(self._dates)

    @property
    def all_symbols(self) -> frozenset[str]:
        return frozenset(symbol for members in self._snapshots.values() for symbol in members)

    def symbols_for_range(self, start: date, end: date) -> frozenset[str]:
        """Return the union needed to fetch bars for a historical date range."""

        if start > end:
            raise ValueError("start cannot be later than end")
        before_start = bisect_right(self._dates, start) - 1
        if before_start < 0:
            raise ValueError(f"no universe snapshot is available on or before {start}")
        relevant_dates = self._dates[before_start:]
        relevant_dates = [day for day in relevant_dates if day <= end]
        return frozenset(
            symbol
            for day in relevant_dates
            for symbol in self._snapshots[day]
        )

    def as_payload(self) -> list[dict[str, object]]:
        return [
            {
                "trade_date": day.isoformat(),
                "members": [{"ts_code": symbol} for symbol in sorted(self._snapshots[day])],
            }
            for day in self._dates
        ]

    def symbols(self, as_of: date) -> frozenset[str]:
        position = bisect_right(self._dates, as_of) - 1
        if position < 0:
            raise ValueError(f"no universe snapshot is available on or before {as_of}")
        return self._snapshots[self._dates[position]]

    def contains(self, symbol: str, as_of: date) -> bool:
        return symbol in self.symbols(as_of)

    def membership_changes(self, start: date, end: date) -> tuple[dict[str, object], ...]:
        """Return actual constituent-set changes effective within a date range."""

        if start > end:
            raise ValueError("start cannot be later than end")
        position = bisect_right(self._dates, start) - 1
        if position < 0:
            raise ValueError(f"no universe snapshot is available on or before {start}")
        previous = self._snapshots[self._dates[position]]
        changes: list[dict[str, object]] = []
        for snapshot_day in self._dates[position + 1 :]:
            if snapshot_day > end:
                break
            current = self._snapshots[snapshot_day]
            added = sorted(current - previous)
            removed = sorted(previous - current)
            if added or removed:
                changes.append(
                    {
                        "trade_date": snapshot_day.isoformat(),
                        "added_count": len(added),
                        "removed_count": len(removed),
                        "added": added,
                        "removed": removed,
                    }
                )
            previous = current
        return tuple(changes)

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .index_members import INDEX_UNIVERSE_NAMES, fetch_tushare_index_members

DEFAULT_PAPER_UNIVERSE_INDEX = "000300.SH"


def resolve_paper_universe(
    index_code: str = DEFAULT_PAPER_UNIVERSE_INDEX,
    *,
    as_of: date,
    token: str | None = None,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve a paper-trading candidate pool from the latest index snapshot known by ``as_of``.

    This intentionally returns the membership version/date as audit metadata.
    It never substitutes today's members into a prior date, and it does not
    liquidate or suppress already-held positions; callers fetch those symbols
    separately so existing positions remain manageable after an index change.
    """

    code = str(index_code or "").strip().upper()
    payload = fetch_tushare_index_members(
        code,
        as_of=as_of,
        token=token,
        cache_dir=cache_dir,
    )
    snapshot_date = date.fromisoformat(str(payload.get("trade_date"))[:10])
    if snapshot_date > as_of:
        raise RuntimeError("PIT 指数快照晚于信号日期，已停止纸面循环")
    members = payload.get("members")
    symbols = sorted({
        str(item.get("ts_code") or item.get("symbol") or "").strip().upper()
        for item in members or []
        if isinstance(item, dict) and str(item.get("ts_code") or item.get("symbol") or "").strip()
    })
    if not symbols:
        raise RuntimeError(f"{code} 在 {as_of.isoformat()} 没有可用的 PIT 成分快照")
    return {
        "mode": "pit_index_members",
        "index_code": code,
        "index_name": INDEX_UNIVERSE_NAMES.get(code, code),
        "as_of": as_of.isoformat(),
        "membership_date": snapshot_date.isoformat(),
        "count": len(symbols),
        "data_version": payload.get("data_version") or payload.get("version"),
        "source": payload.get("source", "tushare:index_weight"),
        "symbols": symbols,
    }

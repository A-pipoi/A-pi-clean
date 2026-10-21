"""Input validation for external PIT Brinson panels."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from ..backtest.engine import BacktestResult
from ..data.schema import Bar
from ..portfolio.brinson import build_strict_pit_brinson_report
from .parsing import object_mapping


def strict_pit_brinson_report(
    result: BacktestResult, bars: list[Bar]
) -> dict[str, object]:
    panel_path = os.getenv("A_PI_PIT_BRINSON_PANEL", "").strip()
    if not panel_path:
        return {
            "status": "unavailable",
            "reason": "未配置带可得时间证明的 PIT 行业与基准权重数据面板",
            "industry_neutralization_enabled": False,
            "attribution": None,
        }
    try:
        panel_bytes = Path(panel_path).expanduser().read_bytes()
        panel_value: Any = json.loads(panel_bytes.decode("utf-8"))
        panel = object_mapping(panel_value)
        if panel.get("schema_version") != "pit-industry-brinson-v1":
            raise ValueError("面板 schema_version 必须为 pit-industry-brinson-v1")
        metadata = object_mapping(panel.get("source_metadata"))
        if not str(metadata.get("provider") or "").strip():
            raise ValueError("source_metadata.provider 必须标注真实数据供应方")
        if metadata.get("industry_availability_quality") != "verified":
            raise ValueError("行业数据没有 provider timestamp/first-seen 可得时间证明")
        if metadata.get("benchmark_weight_availability_quality") != "verified":
            raise ValueError("基准权重没有逐日开盘前可得时间证明")
        version = str(metadata.get("source_version") or "").strip()
        code = str(metadata.get("benchmark_code") or "").strip().upper()
        raw_industry_records = panel.get("industry_records")
        raw_weights = panel.get("benchmark_weight_records")
        if (
            not version
            or not code
            or not isinstance(raw_industry_records, list)
            or not isinstance(raw_weights, list)
        ):
            raise ValueError("面板缺少 source_version、benchmark_code 或记录列表")
        industry_records = [
            object_mapping(row) for row in raw_industry_records if isinstance(row, dict)
        ]
        weights = [object_mapping(row) for row in raw_weights if isinstance(row, dict)]
        for row in industry_records:
            if not str(row.get("source_version") or "").strip():
                raise ValueError("每条行业记录必须提供原始 source_version")
            available = row.get("available_at") or row.get("first_seen_at")
            try:
                parsed = datetime.fromisoformat(str(available))
            except (TypeError, ValueError):
                raise ValueError("每条行业记录必须提供含时区的真实可得时间")
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("每条行业记录的可得时间必须带时区")
        for row in weights:
            if not str(row.get("source_version") or "").strip():
                raise ValueError("每条基准权重记录必须提供原始 source_version")
        return build_strict_pit_brinson_report(
            result,
            bars,
            industry_records=industry_records,
            benchmark_weight_records=weights,
            benchmark_code=code,
            benchmark_name=str(metadata.get("benchmark_name") or code),
            source_version=f"{version}:sha256:{hashlib.sha256(panel_bytes).hexdigest()}",
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        return {
            "status": "unavailable",
            "reason": f"PIT Brinson 面板校验失败：{str(error)[:240]}",
            "industry_neutralization_enabled": False,
            "attribution": None,
        }

"""Validate an existing result for display only, independent of trading gates."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any


def completed_display(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job or job.get("status") != "completed":
        raise ValueError("只能将本账户已成功完成的回测设为默认展示；原结果未改变")
    result = json.loads(job.get("result_json") or "null")
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise ValueError("回测产物不完整；原默认展示未改变")
    curve = result.get("equity_curve")
    if not isinstance(result.get("metrics"), dict) or not isinstance(curve, list) or not curve:
        raise ValueError("回测缺少指标或净值曲线；原默认展示未改变")
    try:
        if not math.isfinite(float(result["final_equity"])) or any(
            not isinstance(point, dict) or not point.get("date")
            or not math.isfinite(float(point["equity"])) for point in curve
        ):
            raise ValueError("invalid equity")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("回测净值数据无效；原默认展示未改变") from error
    strategy = result.get("strategy")
    if not isinstance(strategy, str) or not strategy:
        raise ValueError("回测未保存策略身份；不能用当前表单猜测")
    return {
        "job_id": job["job_id"],
        "strategy": strategy,
        "strategy_name": result.get("strategy_label") or strategy,
        "strategy_version": result.get("strategy_version") or "未记录（旧产物）",
        "backtest_date": job.get("completed_at") or job.get("created_at"),
        "selected_at": datetime.now(UTC).isoformat(),
        "result": result,
        "display_only": True,
    }

"""Training-only template/hypothesis census with explicit counted failures."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any


def template_census(
    templates: Sequence[dict[str, str]], evaluate: Callable[[str], dict[str, Any]]
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for template in templates:
        row: dict[str, Any] = {**template, "n_tested": 0}
        try:
            run = evaluate(template["expression"])
            result = run.get("result", {})
            candidates = result.get("candidates", [])
            candidate = candidates[0] if candidates else {}
            report = candidate.get("evaluation", {})
            horizon = report.get("horizons", {}).get("5", {})
            row.update(
                status="evaluated",
                run_id=run.get("run_id"),
                n_tested=run.get("n_tested", 1),
                mean_ic=horizon.get("mean_ic"),
                icir=horizon.get("icir"),
                coverage=report.get("coverage"),
                monotonicity=horizon.get("monotonicity"),
                turnover=report.get("top_quintile_turnover"),
            )
        except (ValueError, ArithmeticError, RuntimeError, PermissionError) as error:
            if getattr(error, "status_code", None) == 429:
                raise
            row.update(
                status="failed",
                error=str(error),
                code=getattr(error, "api_code", type(error).__name__),
                run_id=getattr(error, "run_id", None),
                n_tested=1 if getattr(error, "run_id", None) else 0,
            )
        rows.append(row)
    families = []
    for family in sorted({row.get("category", "custom") for row in rows}):
        members = [row for row in rows if row.get("category", "custom") == family]
        values = [row["icir"] for row in members if row.get("icir") is not None]
        families.append(
            {
                "family": family,
                "evaluated": sum(row["status"] == "evaluated" for row in members),
                "failed": sum(row["status"] == "failed" for row in members),
                "mean_icir": sum(values) / len(values) if values else None,
            }
        )
    families.sort(
        key=lambda row: (
            row["mean_icir"] if row["mean_icir"] is not None else float("-inf")
        ),
        reverse=True,
    )
    return {
        "templates": rows,
        "family_ranking": families,
        "n_tested": sum(row["n_tested"] for row in rows),
        "requested_hypotheses": len(rows),
        "sealed_dates_used": False,
        "status": "completed"
        if all(row["status"] == "evaluated" for row in rows)
        else "partial"
        if any(row["status"] == "evaluated" for row in rows)
        else "failed",
    }

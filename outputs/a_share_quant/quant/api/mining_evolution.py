"""Authenticated caller supplies proposals; the service never calls an LLM."""

from __future__ import annotations

from collections.abc import Callable
from math import isfinite
from typing import Any

from quant.api.resource_limits import ResourceLimitExceeded
from quant.api.user_scope import CURRENT_USERNAME
from quant.mining.evolution import EvolutionStore


def training_feedback(session: dict, *, top_n: int = 5, horizon: int = 5) -> dict:
    """Whitelisted, bounded proposer context; never inspect sealed results."""
    if type(top_n) is not int or not 1 <= top_n <= 20:
        raise ValueError("top_n must be an integer between 1 and 20")
    if type(horizon) is not int or horizon not in (5, 10, 20):
        raise ValueError("feedback horizon must be 5, 10 or 20")
    candidates, failures, seen = [], [], set()
    for generation in session["generations"]:
        for entry in generation["history"]:
            if entry.get("status") == "failed":
                failures.append(
                    {
                        "generation": generation["generation"],
                        "run_id": entry.get("run_id"),
                        "expression": entry.get("expression"),
                        "code": entry.get("code", "failed"),
                    }
                )
                continue
            feedback = entry.get("feedback", {})
            if not isinstance(feedback, dict):
                continue
            if feedback.get("sealed_dates_used") is not False:
                continue
            identity = feedback.get("expression_hash") or entry.get("expression")
            if identity in seen:
                continue
            seen.add(identity)
            metrics = feedback.get("train_icir")
            value = metrics.get(str(horizon)) if isinstance(metrics, dict) else None
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not isfinite(value)
            ):
                continue

            # Copy only declared training metrics, never arbitrary nested reports.
            def numeric(value: Any) -> int | float | None:
                return (
                    value if type(value) in (int, float) and isfinite(value) else None
                )

            safe: dict[str, Any] = {
                key: numeric(feedback.get(key))
                for key in (
                    "coverage",
                    "max_corr_with_registry",
                    "incremental_rank_ic",
                    "n_tested_after",
                )
            }
            safe["expression_hash"] = str(feedback.get("expression_hash", ""))[:64]
            for field in ("train_rank_ic", "train_icir"):
                metrics = feedback.get(field)
                safe[field] = (
                    {
                        str(day): numeric(metrics[str(day)])
                        for day in range(1, 21)
                        if str(day) in metrics
                    }
                    if isinstance(metrics, dict)
                    else {}
                )
            candidates.append(
                {
                    "generation": generation["generation"],
                    "expression": entry.get("expression"),
                    "run_id": entry.get("run_id"),
                    "score": value,
                    "training_feedback": safe,
                }
            )
    candidates.sort(
        key=lambda item: (-item["score"], item["generation"], str(item["run_id"]))
    )
    return {
        "session_id": session["session_id"],
        "data_version": session["data_version"],
        "horizon": horizon,
        "ranking": "training_icir_descending",
        "top_candidates": candidates[:top_n],
        "eligible_unique": len(candidates),
        "failures": failures[-100:],
        "failure_count": len(failures),
        "sealed_dates_used": False,
        "llm_called": False,
        "warning": "Training feedback only; not sealed acceptance or profit evidence.",
    }


def evolution_request(
    payload: dict[str, Any], *, deps: Any, evaluate: Callable[..., dict[str, Any]]
) -> dict[str, Any]:
    owner = CURRENT_USERNAME.get()
    if not owner:
        raise PermissionError("authenticated evolution owner required")
    mode = payload["mode"]
    store = EvolutionStore(
        deps.RESEARCH_LEDGER.path.with_name("factor_evolution.sqlite3"),
        read_only=mode in {"evolution_history", "evolution_feedback"},
    )
    session_id = str(payload.get("session_id", ""))
    if mode == "evolution_history":
        return store.history(owner, session_id)
    if mode == "evolution_feedback":
        return training_feedback(
            store.history(owner, session_id),
            top_n=payload.get("top_n", 5),
            horizon=payload.get("horizon", 5),
        )
    if mode == "evolution_create":
        sealed, _, _ = deps._factor_mining_dataset()
        if deps.RESEARCH_LEDGER.factor_mining_acceptance(int(sealed["id"])) is not None:
            raise PermissionError("sealed cycle already opened")
        return store.create(
            owner,
            int(sealed["id"]),
            str(sealed["data_version"]),
            payload.get("budget", 100),
        )
    session = store.history(owner, session_id)
    generation = payload.get("generation")
    if type(generation) is not int or not 1 <= generation <= 100:
        raise ValueError("generation must be an integer between 1 and 100")
    expressions = payload.get("expressions")
    proposer = payload.get("proposer_tag", "external")
    if (
        mode != "evolution_step"
        or not isinstance(expressions, list)
        or not 1 <= len(expressions) <= 100
        or not all(
            isinstance(expression, str)
            and expression.strip()
            and len(expression) <= 500
            for expression in expressions
        )
        or not isinstance(proposer, str)
        or len(proposer) > 100
    ):
        raise ValueError(
            "evolution_step requires 1–100 expressions and a bounded proposer_tag"
        )
    sealed, _, _ = deps._factor_mining_dataset()
    if (
        int(sealed["id"]) != session["sealed_id"]
        or str(sealed["data_version"]) != session["data_version"]
    ):
        raise PermissionError("evolution session belongs to an archived snapshot")
    if deps.RESEARCH_LEDGER.factor_mining_acceptance(int(sealed["id"])) is not None:
        raise PermissionError("sealed cycle already opened")
    request = {"expressions": expressions, "proposer_tag": proposer}
    if not store.begin(owner, session_id, generation, request):
        return store.history(owner, session_id)  # No automatic retries or duplicate N.
    history = []
    try:
        for expression in expressions:
            try:
                run = evaluate(
                    {
                        "expression": expression,
                        "proposer_tag": proposer,
                        "label_horizons": payload.get("label_horizons", [5, 10, 20]),
                    },
                    deps=deps,
                )
                entry = {
                    "expression": expression,
                    "proposer_tag": proposer,
                    "run_id": run["run_id"],
                    "status": run["status"],
                    "feedback": run["result"].get("feedback", {}),
                }
            except (ResourceLimitExceeded, PermissionError) as error:
                history.append(
                    {
                        "expression": expression,
                        "proposer_tag": proposer,
                        "run_id": getattr(error, "run_id", None),
                        "status": "failed",
                        "code": getattr(error, "api_code", type(error).__name__),
                    }
                )
                store.checkpoint(session_id, generation, history)
                raise  # Preserve counted attempts; never automatically retry.
            except (ValueError, ArithmeticError, RuntimeError) as error:
                entry = {
                    "expression": expression,
                    "proposer_tag": proposer,
                    "run_id": getattr(error, "run_id", None),
                    "status": "failed",
                    "code": getattr(error, "api_code", type(error).__name__),
                }
            history.append(entry)
            store.checkpoint(session_id, generation, history)
        store.checkpoint(session_id, generation, history, "completed")
    except Exception:
        store.checkpoint(session_id, generation, history, "interrupted")
        raise
    return store.history(owner, session_id)

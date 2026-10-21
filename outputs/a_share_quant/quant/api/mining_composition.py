"""Explicit training-only composition trials, not production factor activation."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any


def compose_request(
    payload: dict[str, Any], *, deps: Any, evaluate: Callable[..., dict[str, Any]]
) -> dict[str, Any]:
    keys = payload.get("factor_keys")
    weighting = payload.get("weighting", "equal")
    if (
        not isinstance(keys, list)
        or not 2 <= len(keys) <= 8
        or not all(isinstance(key, str) for key in keys)
        or len(set(keys)) != len(keys)
        or weighting not in {"equal", "icir"}
    ):
        raise ValueError(
            "compose requires 2–8 unique enabled factor keys and equal/icir weighting"
        )
    definitions = {
        definition.key: definition
        for definition in deps.FACTOR_STORE.definitions(enabled_only=True)
    }
    expressions = []
    for key in keys:
        definition = definitions.get(key)
        if definition is None:
            raise ValueError("unknown or disabled composition factor")
        if definition.expression:
            expression = definition.expression
        elif definition.factor_type in {"momentum", "reversal"}:
            expression = f"close / shift(close, {definition.window}) - 1"
            if definition.factor_type == "reversal":
                expression = f"-({expression})"
        elif definition.factor_type == "volatility":
            expression = f"ts_std(returns, {definition.window})"
        elif definition.factor_type == "fundamental":
            expression = definition.metric
        else:
            raise ValueError("unsupported composition factor")
        expressions.append(expression)
    weights, components = [], []
    for key, expression in zip(keys, expressions):
        run = evaluate(
            {
                "expression": expression,
                "label_horizons": [5],
                "proposer_tag": "composition-component",
            },
            deps=deps,
        )
        icir = run["result"]["feedback"]["train_icir"].get("5")
        if weighting == "icir" and (
            not isinstance(icir, (int, float)) or not math.isfinite(icir)
        ):
            raise ValueError(
                "ICIR weighting unavailable; component trial is still counted"
            )
        weights.append(1.0 if weighting == "equal" else float(icir))
        components.append(
            {
                "factor_key": key,
                "run_id": run["run_id"],
                "feedback": run["result"]["feedback"],
            }
        )
    denominator = sum(abs(weight) for weight in weights)
    if denominator <= 0:
        raise ValueError("composition weights are all zero")
    normalized = [weight / denominator for weight in weights]
    expression = " + ".join(
        f"({weight:.8f}) * cs_zscore({source})"
        for weight, source in zip(normalized, expressions)
    )
    if len(expression) > 500:
        raise ValueError("composition exceeds DSL length limit; reduce components")
    run = evaluate(
        {"expression": expression, "proposer_tag": "composition-trial"}, deps=deps
    )
    result = {
        **run["result"],
        "composition": {
            "weighting": weighting,
            "weights": dict(zip(keys, normalized)),
            "components": components,
            "research_only": True,
            "note": "Every component evaluation and the composite hypothesis consume one trial. No factor is automatically enabled.",
        },
    }
    return deps.RESEARCH_LEDGER.update_factor_mining_run(run["run_id"], result)

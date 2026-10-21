"""Explicit organizer requests and strictly scoped participant capabilities."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from quant.api.auth import Auth
from quant.api.factor_panels import factor_panel_correlation
from quant.api.mining_evolution import training_feedback
from quant.api.mining_portfolio import cycle_policy
from quant.api.user_scope import CURRENT_USERNAME, user_context
from quant.factors.expression import build_factor_panel, evaluate_expression
from quant.mining.arena import ArenaStore, issue_capability, verify_capability

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def season_lock(path: Path) -> Any:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path.resolve()), threading.RLock())


def participant_identity(auth: Auth, token: str) -> dict:
    claims = verify_capability(auth.secret, token)
    user = auth.store.get_by_username(claims["owner"])
    if (
        not user
        or user.disabled
        or user.must_change_password
        or user.session_version != claims["version"]
    ):
        raise PermissionError("Participant owner account is unavailable")
    return claims


def arena_request(
    action: str,
    arena_id: str,
    payload: dict,
    *,
    deps: Any,
    auth: Auth,
    evaluate: Any,
    accept: Any,
) -> dict:
    owner = CURRENT_USERNAME.get()
    if not owner:
        raise PermissionError("Authenticated organizer required")
    with season_lock(deps.RESEARCH_LEDGER.path):
        store = ArenaStore(deps.RESEARCH_LEDGER, read_only=action in {"list", "report"})
        if action == "list":
            if payload:
                raise ValueError("Listing accepts no overrides")
            return store.list(owner)
        if action == "create":
            user = auth.store.get_by_username(owner)
            if user is None or user.disabled or user.must_change_password:
                raise PermissionError("Organizer account is unavailable")
            if set(payload) - {
                "proposers",
                "budget",
                "top_n",
                "horizon",
                "interval_seconds",
            }:
                raise ValueError(
                    "Unknown arena rules; datasets, registry and N are server-owned"
                )
            sealed, _, bars = deps._factor_mining_dataset()
            definitions = [
                {"key": x.key, "name": x.name, "expression": x.expression}
                for x in deps.FACTOR_STORE.definitions(enabled_only=True)
            ]
            existing_keys = {x["key"] for x in definitions}
            definitions.extend(
                {
                    "key": x["key"],
                    "name": x.get("name", x["key"]),
                    "expression": x["expression"],
                }
                for x in deps.FACTOR_STORE.expression_library()
                if x.get("status") == "accepted" and x["key"] not in existing_keys
            )
            # Package data works both in a source checkout and an installed wheel.
            from importlib.resources import files

            guide = files("quant").joinpath("resources/llm-factor-mining-guide.md").read_text(encoding="utf-8")
            report = store.create(
                owner,
                sealed=sealed,
                training_start=min(x.date for x in bars).isoformat(),
                training_end=max(x.date for x in bars).isoformat(),
                proposers=payload.get("proposers", []),
                guide=guide,
                registry=definitions,
                budget=payload.get("budget", 100),
                top_n=payload.get("top_n", 5),
                horizon=payload.get("horizon", 5),
                interval_seconds=payload.get("interval_seconds", 2),
                portfolio_policy=asdict(
                    cycle_policy(deps.RESEARCH_LEDGER.path, int(sealed["id"]))
                ),
            )
            # One-time response. Do not persist raw capabilities in reports/logs.
            report["participant_credentials"] = [
                {
                    "session_id": row["session_id"],
                    "proposer_tag": row["proposer_tag"],
                    "token": issue_capability(
                        auth.secret,
                        owner=owner,
                        arena_id=report["arena_id"],
                        session_id=row["session_id"],
                        account_version=user.session_version,
                        expires=int(time.time()) + 86400 * 7,
                    ),
                }
                for row in report["participants"]
            ]
            return report
        if action == "report" and not payload:
            return store.report(owner, arena_id)
        if action == "close" and not payload:
            result = store.close(owner, arena_id)
            # Train-only peer correlations, never use sealed dates.
            if (
                result["status"] == "closed"
                and "peer_correlations" not in result["manifest"]
            ):
                correlations = peer_correlations(result, deps=deps)
                store.add_correlations(owner, arena_id, correlations)
                result = store.report(owner, arena_id)
            return result
        if action == "accept" and set(payload) == {"confirm_open_sealed"}:
            return store.accept(
                owner, arena_id, confirm=payload["confirm_open_sealed"], accept=accept
            )
        if action == "archive" and not payload:
            return store.archive(owner, arena_id)
        raise ValueError("Invalid arena action or override fields")


def peer_correlations(report: dict, *, deps: Any) -> dict:
    sealed, bars, training = deps._factor_mining_dataset()
    observations, industries, quality, _ = deps._pit_observations_for_bars(sealed, bars)
    dates = {x.date for x in training}
    panel = build_factor_panel(
        training,
        data_version=str(sealed["data_version"]),
        observations=[x for x in observations if x.as_of in dates],
        industry_observations=[x for x in industries if x.as_of in dates],
        pit_quality=quality,
    )
    vectors = {}
    parties = report["participants"]
    for row in parties:
        for candidate in row["training_candidates"]:
            expression = candidate["expression"]
            vectors[expression] = evaluate_expression(expression, panel).values
    result = []
    for index, left in enumerate(parties):
        for right in parties[index + 1 :]:
            for a in left["training_candidates"]:
                for b in right["training_candidates"]:
                    value = factor_panel_correlation(
                        vectors[a["expression"]],
                        vectors[b["expression"]],
                        panel,
                        date.fromisoformat(report["brief"]["training_start"]),
                        date.fromisoformat(report["brief"]["training_end"]),
                    )
                    result.append(
                        {
                            "left_proposer": left["proposer_tag"],
                            "right_proposer": right["proposer_tag"],
                            "left_expression": a["expression"],
                            "right_expression": b["expression"],
                            "correlation": value,
                        }
                    )
    registry = {}
    definitions = {x.key: x for x in deps.FACTOR_STORE.definitions(enabled_only=False)}
    for item in report["brief"]["registry_summary"]:
        if item.get("expression"):
            registry[item["key"]] = evaluate_expression(
                item["expression"], panel
            ).values
        elif item["key"] in definitions:
            values = deps._factor_values_for_definition(
                definitions[item["key"]], training, panel
            )
            if values is not None:
                registry[item["key"]] = values
    registry_pairs = []
    for party in parties:
        for candidate in party["training_candidates"]:
            for key, values in registry.items():
                registry_pairs.append(
                    {
                        "proposer_tag": party["proposer_tag"],
                        "expression": candidate["expression"],
                        "registry_key": key,
                        "correlation": factor_panel_correlation(
                            vectors[candidate["expression"]],
                            values,
                            panel,
                            date.fromisoformat(report["brief"]["training_start"]),
                            date.fromisoformat(report["brief"]["training_end"]),
                        ),
                    }
                )
    return {
        "scope": "training_only",
        "sealed_dates_used": False,
        "pairs": result,
        "registry_pairs": registry_pairs,
    }


def participant_request(
    action: str,
    session_id: str,
    payload: dict,
    *,
    claims: dict,
    deps: Any,
    evaluate: Any,
) -> dict:
    if session_id != claims["session_id"]:
        raise PermissionError("Cross-session access forbidden")
    owner, arena_id = claims["owner"], claims["arena_id"]
    with user_context(owner), season_lock(deps.RESEARCH_LEDGER.path):
        store = ArenaStore(
            deps.RESEARCH_LEDGER, read_only=action in {"brief", "history"}
        )
        party = store.participant(owner, arena_id, session_id)
        if action == "brief" and not payload:
            return {
                "arena_id": arena_id,
                "session_id": session_id,
                "brief": party["season"]["brief"],
            }
        if action == "history" and not payload:
            return training_feedback(
                store.history(owner, arena_id, session_id),
                top_n=20,
                horizon=party["season"]["brief"]["label_horizons"][0],
            )
        if action == "step":
            if party["season"]["status"] != "running":
                raise PermissionError("Season is closed; no dataset access permitted")
            sealed, _, bars = deps._factor_mining_dataset()
            brief = party["season"]["brief"]
            if (
                int(sealed["id"]) != party["season"]["sealed_id"]
                or str(sealed["data_version"]) != brief["data_version"]
                or min(x.date for x in bars).isoformat() != brief["training_start"]
                or max(x.date for x in bars).isoformat() != brief["training_end"]
            ):
                raise PermissionError(
                    "Shared training snapshot changed; no evaluation permitted"
                )
            history = store.step(
                owner, arena_id, session_id, payload, evaluate=evaluate
            )
            return training_feedback(
                history, top_n=20, horizon=brief["label_horizons"][0]
            )
        raise ValueError("Only own brief/history/step is permitted")

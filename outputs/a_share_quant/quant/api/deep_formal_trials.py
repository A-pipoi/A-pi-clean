"""Authenticated admission bridge; no upload of self-attested model evidence."""

from collections.abc import Callable

from quant.research.deep_formal import DeepFormalTrials
from quant.research.ledger import ResearchLedger


def formal_trial_request(
    action: str,
    payload: dict,
    *,
    owner: str,
    ledger: ResearchLedger,
    execute: Callable[[str, dict, str], dict] | None = None,
) -> dict:
    trials = DeepFormalTrials(ledger, owner)
    if action == "trial-reserve" and set(payload) == {
        "sealed_test_id",
        "request_key",
        "config",
    }:
        return trials.reserve(
            payload["sealed_test_id"], payload["request_key"], payload["config"]
        )
    if action in {"trial-execute", "trial-status"} and set(payload) == {"attempt_id"}:
        attempt_id = payload["attempt_id"]
        if not isinstance(attempt_id, str) or len(attempt_id) > 64:
            raise ValueError("Invalid attempt identity")
        trials.get(attempt_id)
        if execute is None:
            raise RuntimeError("Formal worker is not running")
        return execute(action, payload, owner)
    if action in {"trial-start", "trial-fail", "trial-get"} and set(payload) == {
        "attempt_id",
    }:
        attempt_id = payload["attempt_id"]
        if not isinstance(attempt_id, str) or len(attempt_id) > 64:
            raise ValueError("Invalid attempt identity")
        operation = {
            "trial-start": trials.start,
            "trial-fail": trials.fail,
            "trial-get": trials.get,
        }[action]
        return operation(attempt_id)
    raise ValueError("Unsupported formal trial action or request overrides")

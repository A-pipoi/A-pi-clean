"""HTTP request and error mapping for factor-mining endpoints."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from ..research.ledger import ResearchBudgetExceeded
from .resource_limits import ResourceLimitExceeded

JsonPayload = dict[str, object]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FactorMiningRouteDependencies:
    run: Callable[[JsonPayload], JsonPayload]
    evaluate: Callable[[JsonPayload], JsonPayload]
    accept: Callable[[str], JsonPayload]
    promote: Callable[[str], JsonPayload]
    status: Callable[[], JsonPayload]
    cancel: Callable[[str], bool] | None = None


def handle_factor_mining_request(
    path: str,
    payload: JsonPayload,
    dependencies: FactorMiningRouteDependencies,
) -> tuple[int, JsonPayload]:
    """Dispatch a factor-mining mutation and translate domain errors to HTTP."""

    try:
        if path == "/api/factor-mining/run":
            result = dependencies.run(payload)
            if result.get("job_id"):
                return 202, {"ok": True, **result, **dependencies.status()}
            return 201, {"ok": True, "run": result, **dependencies.status()}
        if path in {"/api/factor-mining/evaluate", "/api/mining/evaluate"}:
            result = dependencies.evaluate(payload)
            if result.get("job_id"):
                return 202, {"ok": True, **result, **dependencies.status()}
            return 201, {"ok": True, "run": result, **dependencies.status()}
        if path == "/api/factor-mining/cancel":
            job_id = str(payload.get("job_id", "")).strip()
            if not job_id:
                raise ValueError("job_id is required")
            accepted = dependencies.cancel(job_id) if dependencies.cancel else False
            return (202 if accepted else 404), {"ok": accepted, **dependencies.status()}
        if path == "/api/factor-mining/accept":
            if dependencies.status().get("active_job"):
                raise PermissionError("后台研究尚未结束，请等待结果写入后再打开 sealed 样本")
            run_id = str(payload.get("run_id", "")).strip()
            if not run_id:
                raise ValueError("run_id is required")
            result = dependencies.accept(run_id)
            return 200, {"ok": True, "acceptance": result, **dependencies.status()}
        if path == "/api/factor-mining/promote":
            key = str(payload.get("factor_key", "")).strip()
            result = dependencies.promote(key)
            return 200, {"ok": True, "promotion": result, **dependencies.status()}
        raise ValueError("unsupported factor-mining endpoint")
    except (TypeError, ValueError) as error:
        return 400, {"ok": False, "error": str(error), "code": getattr(error, "api_code", "invalid_request"), "run_id": getattr(error, "run_id", None)}
    except ResearchBudgetExceeded as error:
        return 409, {
            "ok": False,
            "code": "research_budget_exceeded",
            "error": str(error),
        }
    except ResourceLimitExceeded as error:
        return 429, {"ok": False, "code": error.reason, "error": error.reason, "retry_after": error.retry_after}
    except RuntimeError as error:
        LOGGER.exception("Factor mining provider failed: %s", type(error).__name__, exc_info=None)
        code = getattr(error, "api_code", None)
        return (409 if code else 502), {
            "ok": False,
            "code": code or "data_provider_unavailable",
            "error": type(error).__name__,
        }
    except PermissionError as error:
        return 409, {
            "ok": False,
            "code": "factor_mining_governance_blocked",
            "error": str(error),
        }
    except LookupError as error:
        return 404, {
            "ok": False,
            "code": "factor_mining_run_not_found",
            "error": str(error),
        }
    except Exception as error:
        LOGGER.exception("Factor mining request failed: %s", type(error).__name__, exc_info=None)
        code = getattr(error, "api_code", None)
        return (409 if code else 500), {
            "ok": False,
            "code": code or "factor_mining_failed",
            "error": type(error).__name__,
        }

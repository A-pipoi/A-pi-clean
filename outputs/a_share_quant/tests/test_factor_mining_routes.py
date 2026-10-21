from quant.api.factor_mining_routes import (
    FactorMiningRouteDependencies,
    handle_factor_mining_request,
)
from quant.api.resource_limits import ResourceLimitExceeded
from quant.research.ledger import ResearchBudgetExceeded


def test_dataset_job_returns_202_without_a_fake_run():
    status, body = handle_factor_mining_request("/api/factor-mining/run", {}, _dependencies(run=lambda _: {"job_id": "j1", "stage": "dataset_sealing"}))
    assert status == 202
    assert body["job_id"] == "j1" and "run" not in body
    status, body = handle_factor_mining_request("/api/factor-mining/run", {}, _dependencies(run=lambda _: (_ for _ in ()).throw(ResourceLimitExceeded("backtest_already_active", 123))))
    assert status == 429 and body["retry_after"] == 123


def _dependencies(**overrides):
    values = {
        "run": lambda _payload: {"run_id": "run-1"},
        "evaluate": lambda _payload: {"run_id": "eval-1"},
        "accept": lambda run_id: {"run_id": run_id},
        "promote": lambda key: {"factor_key": key},
        "status": lambda: {"remaining": 4},
    }
    values.update(overrides)
    return FactorMiningRouteDependencies(**values)


def test_factor_mining_routes_keep_success_shapes_and_status_codes() -> None:
    status, result = handle_factor_mining_request("/api/factor-mining/run", {}, _dependencies())
    assert status == 201
    assert result == {"ok": True, "run": {"run_id": "run-1"}, "remaining": 4}

    status, result = handle_factor_mining_request(
        "/api/factor-mining/accept", {"run_id": "run-2"}, _dependencies()
    )
    assert status == 200
    assert result["acceptance"] == {"run_id": "run-2"}

    status, result = handle_factor_mining_request(
        "/api/factor-mining/promote", {"factor_key": "factor-1"}, _dependencies()
    )
    assert status == 200
    assert result["promotion"] == {"factor_key": "factor-1"}


def test_factor_mining_route_maps_governance_data_and_budget_errors() -> None:
    status, result = handle_factor_mining_request(
        "/api/factor-mining/accept", {}, _dependencies()
    )
    assert status == 400
    assert result["error"] == "run_id is required"

    status, result = handle_factor_mining_request(
        "/api/factor-mining/run", {}, _dependencies(run=lambda _payload: (_ for _ in ()).throw(PermissionError("sealed")))
    )
    assert status == 409
    assert result["code"] == "factor_mining_governance_blocked"

    status, result = handle_factor_mining_request(
        "/api/factor-mining/run", {}, _dependencies(run=lambda _payload: (_ for _ in ()).throw(ResearchBudgetExceeded("full")))
    )
    assert status == 409
    assert result["code"] == "research_budget_exceeded"

    status, result = handle_factor_mining_request(
        "/api/factor-mining/run", {}, _dependencies(run=lambda _payload: (_ for _ in ()).throw(RuntimeError("offline")))
    )
    assert status == 502
    assert result["code"] == "data_provider_unavailable"

from types import SimpleNamespace

from quant.api.factor_panels import factor_values_for_definition
from quant.api.mining_research import _run_factor_mining_request
from quant.factors import FactorRegistryStore
from quant.mining.batch import aggregate_factor_mining_runs
from quant.research import ResearchLedger
from tests.test_factor_mining import _bars


def test_parallel_seeds_count_individual_trials_once_and_share_one_sealed_snapshot(tmp_path):
    bars=_bars()
    sealed={"id":17,"data_version":"test-v1","sealed_start":"2026-01-01","sealed_end":"2026-01-31"}
    ledger=ResearchLedger(tmp_path/"research.sqlite3")
    store=FactorRegistryStore(tmp_path/"factors.sqlite3")
    deps=SimpleNamespace(RESEARCH_LEDGER=ledger,FACTOR_STORE=store,_validate_factor_mining_universe_request=lambda _:None,_factor_mining_dataset=lambda:(sealed,bars,bars),_as_int=int,_bounded_int=lambda value,lo,hi:max(lo,min(hi,int(value))),_pit_observations_for_bars=lambda *_:([],[],{},{}),_factor_values_for_definition=factor_values_for_definition,_object_list=lambda value:value if isinstance(value,list) else [],_object_mapping=lambda value:value if isinstance(value,dict) else {},_factor_mining_cross_universe_reference=lambda *_,**__:{"status":"unavailable","expressions":{}})
    import os
    from unittest.mock import patch
    with patch.dict(os.environ,{"TUSHARE_TOKEN":"test-only"}):
        result=_run_factor_mining_request({"seeds":[1,2],"generations":2,"population_size":8,"top_n":3},deps=deps)
    assert result["n_tested"]==32
    assert ledger.factor_mining_trial_total(17)==32
    runs=ledger.factor_mining_runs()
    assert len(runs)==3
    summary=ledger.factor_mining_run(result["run_id"])
    assert summary["n_tested"]==0 and summary["status"]=="multi_seed_report"
    assert {run["sealed_test_id"] for run in runs}=={17}
    assert aggregate_factor_mining_runs(runs)["n_tested"]==32

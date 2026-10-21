import pytest

from quant.api.resource_limits import ResourceLimitExceeded
from quant.mining.census import template_census


def test_family_census_keeps_failed_trials_and_reports_no_fabricated_scores():
    templates = [{"name":"price","expression":"close","category":"price"},{"name":"pit","expression":"roe","category":"financial"}]
    def evaluate(expression):
        if expression == "roe":
            error=ValueError("PIT field unavailable")
            error.run_id="failed"
            raise error
        return {"run_id":"one","n_tested":1,"result":{"candidates":[{"evaluation":{"coverage":.99,"horizons":{"5":{"mean_ic":.1,"icir":.8}}}}]}}
    result=template_census(templates,evaluate)
    assert result["n_tested"]==2 and result["sealed_dates_used"] is False
    assert result["status"]=="partial" and result["family_ranking"][0]["family"]=="price"
    failed=result["templates"][1]
    assert failed["n_tested"]==1 and "icir" not in failed


def test_census_cannot_swallow_resource_quota_or_keep_submitting():
    calls=[]
    def evaluate(expression):
        calls.append(expression)
        raise ResourceLimitExceeded("tushare_hourly_budget",180)
    with pytest.raises(ResourceLimitExceeded):
        template_census([{"expression":"close"},{"expression":"volume"}],evaluate)
    assert calls==["close"]

from unittest.mock import patch

from quant.api.resource_limits import ResourceLimits


def test_authorized_tushare_defaults_and_environment_override(tmp_path):
    with patch.dict("os.environ", {}, clear=True):
        limits = ResourceLimits(tmp_path / "defaults.sqlite3")
        assert limits.budgets["tushare"] == (5000, 10000)
        assert limits.budgets["heavy"] == (20, 100)
    with patch.dict("os.environ", {"A_PI_TUSHARE_HOURLY_LIMIT": "1500",
                                   "A_PI_TUSHARE_DAILY_LIMIT": "8000"}):
        limits = ResourceLimits(tmp_path / "configured.sqlite3")
        assert limits.budgets["tushare"] == (1500, 8000)

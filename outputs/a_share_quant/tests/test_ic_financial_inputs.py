from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from quant.api.ic_scheduler import DailyICScheduler
from quant.data.schema import Bar
from quant.factors.live_ic import SHANGHAI
from quant.factors.store import FactorDefinition


@pytest.mark.parametrize("status,expected_count", [("available", 6), ("insufficient_coverage", 0)])
def test_scheduler_uses_real_disclosure_and_first_seen_proof_for_financial_factors(tmp_path, status, expected_count):
    symbols = [f"00000{i}.SZ" for i in range(1,7)]
    bars = [Bar(symbol, date(2026,10,12), 10,11,9,10,100) for symbol in symbols]
    market = SimpleNamespace(bars=bars,symbols=symbols,sessions=[date(2026,10,12)],prices={date(2026,10,12):dict.fromkeys(symbols,10)},source_hash="market")
    factors = [FactorDefinition("roe","ROE","fundamental",1,True,"",metric="roe")]
    scoped = SimpleNamespace(ledger=Mock(path=tmp_path / "ledger.sqlite3"),factor_store=Mock())
    scoped.factor_store.definitions.return_value=factors
    users, stores = Mock(), Mock()
    users.list_users.return_value=[SimpleNamespace(username="alice",disabled=False)]
    stores.get.return_value=scoped
    monitor=Mock()
    monitor.claim.return_value=True
    monitor.report.return_value={"monitors":[]}
    values={symbol:{"roe":index/100} for index,symbol in enumerate(symbols,1)}
    with patch("quant.data.extension_store.default_store_path", return_value=tmp_path / "absent.sqlite3"), patch("quant.api.ic_scheduler.LiveICStore",return_value=monitor), patch("quant.api.ic_scheduler.LiveMonitorProvider") as provider, patch("quant.api.ic_scheduler.observed_financial_inputs",return_value=(values,{"status":status,"source_hash":"proof"})) as financial:
        provider.return_value.fetch.return_value=market
        DailyICScheduler(users,stores,tmp_path,lambda:"test").run_once(datetime(2026,10,12,17,tzinfo=SHANGHAI))
    financial.assert_called_once_with(date(2026,10,12),symbols)
    signal=monitor.freeze.call_args.args[3]
    assert len(signal)==expected_count
    if expected_count:
        assert signal[symbols[0]]==.01
    monitor.finish.assert_called_once()

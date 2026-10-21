import pytest

from quant.research.ledger import ResearchBudgetExceeded, ResearchLedger


def test_archiving_cannot_make_evaluated_dates_unknown(tmp_path):
    ledger = ResearchLedger(tmp_path / 'ledger.sqlite3')
    args = {
        'strategy_version': 'baseline',
        'factor_pipeline_version': 'old',
        'data_version': 'real',
        'sealed_start': '2024-02-01',
        'sealed_end': '2024-03-01',
        'dataset_hash': 'a',
        'bars': [],
        'metadata': {},
        'parameters': {},
    }
    old = ledger.create_sealed_test(**args)
    ledger.record_sealed_result(old['id'], status='needs_review', result={'passed': False})
    ledger.archive('baseline', reason='new cycle')
    args['factor_pipeline_version'] = 'new-policy'
    with pytest.raises(ResearchBudgetExceeded, match='重叠'):
        ledger.create_sealed_test(**args)
    args['sealed_start'], args['sealed_end'] = '2024-03-02', '2024-04-01'
    assert ledger.create_sealed_test(**args)['status'] == 'sealed'

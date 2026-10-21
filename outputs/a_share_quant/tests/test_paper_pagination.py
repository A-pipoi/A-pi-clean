import pytest

from quant.execution import PaperStore


def test_pagination_reaches_history_beyond_recent_state(tmp_path):
    store=PaperStore(tmp_path / 'paper.sqlite3')
    try:
        for index in range(125):
            store.create_order('000001.SZ','BUY',100,order_id=f'QA-{index:04d}')
        first=store.orders_page(1,20)
        assert first['total']==125 and first['pages']==7 and len(first['orders'])==20
        seen=[]
        for page in range(1,8):
            result=store.orders_page(page,20)
            seen.extend(order['order_id'] for order in result['orders'])
        assert len(seen)==len(set(seen))==125
        assert store.orders_page(999,20)['page']==7
        assert len(store.state()['orders'])==100
        for page,size in [(0,20),(1,0),(1,101)]:
            with pytest.raises(ValueError):store.orders_page(page,size)
    finally: store.close()

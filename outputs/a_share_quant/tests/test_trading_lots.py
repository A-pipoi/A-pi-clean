import tempfile
import unittest

from quant.backtest.engine import EventDrivenBacktester, Order, OrderRejectReason
from quant.data.trading_lots import buy_quantity_error, rounded_buy_quantity
from quant.execution import PaperStore
from tests.helpers import bar


class TradingLotTests(unittest.TestCase):
    def test_main_board_requires_hundred_share_lots(self) -> None:
        self.assertEqual(rounded_buy_quantity("600036.SH", 4937), 4900)
        self.assertEqual(rounded_buy_quantity("000001.SZ", 17226), 17200)
        self.assertEqual(buy_quantity_error("600036.SH", 4937), "buy_quantity_must_be_multiple_of_100")
        self.assertIsNone(buy_quantity_error("600036.SH", 4900))

    def test_other_boards_use_their_own_minimums(self) -> None:
        self.assertIsNone(buy_quantity_error("688001.SH", 201))
        self.assertEqual(buy_quantity_error("688001.SH", 199), "star_buy_minimum_200")
        self.assertIsNone(buy_quantity_error("830799.BJ", 101))
        self.assertEqual(buy_quantity_error("830799.BJ", 99), "beijing_buy_minimum_100")

    def test_paper_store_rejects_invalid_buy_before_fill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            order = store.create_order("600036.SH", "BUY", 4937)
            self.assertEqual(order["status"], "REJECTED")
            self.assertEqual(order["reject_reason"], "buy_quantity_must_be_multiple_of_100")
            store.close()

    def test_backtest_rejects_invalid_main_board_buy(self) -> None:
        daily = bar("600036.SH", 0)
        rejected = EventDrivenBacktester(initial_cash=100_000)._execute(
            Order("600036.SH", "BUY", 4937), daily.date, {daily.symbol: daily}, {}, 100_000,
        )
        self.assertEqual(rejected.reason, OrderRejectReason.BOARD_LOT)


if __name__ == "__main__":
    unittest.main()

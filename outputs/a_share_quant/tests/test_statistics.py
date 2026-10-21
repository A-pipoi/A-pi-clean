import unittest

from quant.validation.statistics import summarize_returns


class StatisticsTests(unittest.TestCase):
    def test_single_run_does_not_claim_a_deflated_sharpe_p_value(self) -> None:
        result = summarize_returns([0.002, -0.001, 0.003, 0.001] * 30)

        self.assertIsNone(result.deflated_sharpe_p_value)
        self.assertIn("单次回测未作多重检验校正", result.confidence_note)

    def test_hac_significance_accounts_for_positive_return_autocorrelation(self) -> None:
        clustered = ([0.011] * 30 + [-0.009] * 30) * 10
        alternating = [0.011, -0.009] * 300

        clustered_result = summarize_returns(clustered)
        alternating_result = summarize_returns(alternating)

        self.assertIsNotNone(clustered_result.p_value)
        self.assertIsNotNone(alternating_result.p_value)
        self.assertGreater(clustered_result.p_value, alternating_result.p_value)
        self.assertLess(clustered_result.sharpe, alternating_result.sharpe)
        self.assertLess(clustered_result.effective_observations, alternating_result.effective_observations)


if __name__ == "__main__":
    unittest.main()

import unittest

from quant.validation.sensitivity import _variants
from quant.validation.statistics import benjamini_hochberg


class ResearchHardeningTests(unittest.TestCase):
    def test_sensitivity_scan_is_bounded_and_one_at_a_time(self) -> None:
        variants = _variants(20, 2, 5, delta=0.30)
        self.assertEqual(len(variants), 7)
        self.assertEqual(variants[0], (20, 2, 5))
        for variant in variants[1:]:
            changed = sum(left != right for left, right in zip(variants[0], variant))
            self.assertEqual(changed, 1)

    def test_benjamini_hochberg_controls_trial_count(self) -> None:
        result = benjamini_hochberg([0.001, 0.02, 0.40, 0.90])
        self.assertEqual(len(result["adjusted"]), 4)
        self.assertTrue(result["rejected"][0])
        self.assertFalse(result["rejected"][-1])
        self.assertEqual(result["q"], 0.05)


if __name__ == "__main__":
    unittest.main()

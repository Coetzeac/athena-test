import unittest

from athena.metrics import calculate_metrics, wilson_lower_bound


class MetricsTests(unittest.TestCase):
    def test_calculates_core_metrics_and_ordered_drawdown(self) -> None:
        metrics = calculate_metrics([1.0, -1.0, -1.0, 2.0, 0.0])
        self.assertEqual(metrics.sample_size, 5)
        self.assertEqual(metrics.wins, 2)
        self.assertEqual(metrics.losses, 2)
        self.assertEqual(metrics.breakeven, 1)
        self.assertAlmostEqual(metrics.hit_rate, 0.4)
        self.assertAlmostEqual(metrics.expectancy_r, 0.2)
        self.assertAlmostEqual(metrics.profit_factor or 0, 1.5)
        self.assertAlmostEqual(metrics.maximum_drawdown_r, 2.0)

    def test_rejects_empty_or_non_finite_outcomes(self) -> None:
        with self.assertRaises(ValueError):
            calculate_metrics([])
        with self.assertRaises(ValueError):
            calculate_metrics([float("nan")])

    def test_wilson_bound_is_conservative(self) -> None:
        lower = wilson_lower_bound(60, 100)
        self.assertLess(lower, 0.60)
        self.assertGreater(lower, 0.49)


if __name__ == "__main__":
    unittest.main()


import unittest

from athena.court import DecisionCourt, DecisionPolicy
from athena.metrics import calculate_metrics
from athena.models import EvidenceRef, ResearchPacket, Verdict


POLICY = DecisionPolicy({
    "policy_id": "test-v1",
    "minimum_sample_size": 10,
    "target_hit_rate": 0.60,
    "minimum_expectancy_r": 0.10,
    "minimum_profit_factor": 1.20,
    "maximum_drawdown_r": 5.0,
    "minimum_confidence": 0.30,
    "minimum_evidence_weight": 0.20,
    "minimum_evidence_items": 1,
    "prohibited_promotion_sources": ["synthetic_fixture"],
    "require_counter_evidence": True,
    "require_risk_controls": True,
})


def packet(outcomes: list[float], counter_evidence: tuple[str, ...] = ("adverse regime",)) -> ResearchPacket:
    return ResearchPacket(
        strategy_id="S-1",
        claim="A falsifiable claim",
        mechanism="A declared mechanism",
        recommendation="Advance one controlled stage",
        instrument="TEST",
        timeframe="5m",
        methodology="Ordered holdout outcomes",
        assumptions=("No omitted trades",),
        evidence=(EvidenceRef(
            evidence_id="E-1",
            source="fixture",
            locator="memory://fixture",
            sha256="a" * 64,
            observed_at="2026-08-13T00:00:00+00:00",
        ),),
        counter_evidence=counter_evidence,
        risk_controls={"live_execution": "prohibited"},
        metrics=calculate_metrics(outcomes),
    )


class CourtTests(unittest.TestCase):
    def test_promotes_only_when_all_gates_pass(self) -> None:
        decision = DecisionCourt(POLICY).adjudicate(packet([1.0] * 8 + [-1.0] * 2))
        self.assertEqual(decision.verdict, Verdict.PROMOTE)
        self.assertTrue(all(gate.passed for gate in decision.gates))

    def test_holds_incomplete_challenge_record(self) -> None:
        decision = DecisionCourt(POLICY).adjudicate(packet([1.0] * 8 + [-1.0] * 2, ()))
        self.assertEqual(decision.verdict, Verdict.HOLD)
        self.assertIn("counter_evidence", [gate.gate for gate in decision.gates if not gate.passed])

    def test_rejects_non_viable_performance(self) -> None:
        decision = DecisionCourt(POLICY).adjudicate(packet([1.0] * 4 + [-1.0] * 6))
        self.assertEqual(decision.verdict, Verdict.REJECT)
        failed = {gate.gate for gate in decision.gates if not gate.passed}
        self.assertIn("hit_rate", failed)
        self.assertIn("expectancy", failed)


if __name__ == "__main__":
    unittest.main()

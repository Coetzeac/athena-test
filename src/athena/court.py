from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from athena.metrics import wilson_lower_bound
from athena.models import CourtDecision, GateResult, ResearchPacket, Verdict


class DecisionPolicy:
    REQUIRED_KEYS = {
        "policy_id",
        "minimum_sample_size",
        "target_hit_rate",
        "minimum_expectancy_r",
        "minimum_profit_factor",
        "maximum_drawdown_r",
        "minimum_confidence",
        "minimum_evidence_weight",
        "minimum_evidence_items",
        "prohibited_promotion_sources",
        "require_counter_evidence",
        "require_risk_controls",
    }

    def __init__(self, values: dict[str, Any]) -> None:
        missing = self.REQUIRED_KEYS - values.keys()
        if missing:
            raise ValueError(f"policy is missing: {', '.join(sorted(missing))}")
        self.values = values

    @classmethod
    def from_file(cls, path: str | Path) -> "DecisionPolicy":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def __getitem__(self, key: str) -> Any:
        return self.values[key]


class DecisionCourt:
    def __init__(self, policy: DecisionPolicy) -> None:
        self.policy = policy

    def adjudicate(self, packet: ResearchPacket) -> CourtDecision:
        gates: list[GateResult] = []
        metrics = packet.metrics
        structural_failures = packet.validate()

        gates.append(self._gate(
            "packet_structure", not structural_failures, "reject", structural_failures or "complete",
            "all required fields valid", "; ".join(structural_failures) if structural_failures else "packet is complete",
        ))
        gates.append(self._gate(
            "evidence_count", len(packet.evidence) >= self.policy["minimum_evidence_items"], "hold",
            len(packet.evidence), self.policy["minimum_evidence_items"], "insufficient evidence references",
        ))
        eligible_sources = {
            item.source for item in packet.evidence
            if item.source not in self.policy["prohibited_promotion_sources"]
        }
        gates.append(self._gate(
            "source_eligibility", bool(eligible_sources), "hold",
            sorted({item.source for item in packet.evidence}),
            "at least one promotion-eligible source",
            "all cited evidence sources are barred from promotion",
        ))
        gates.append(self._gate(
            "counter_evidence", bool(packet.counter_evidence) or not self.policy["require_counter_evidence"], "hold",
            len(packet.counter_evidence), ">= 1", "counter-evidence is mandatory",
        ))
        gates.append(self._gate(
            "risk_controls", bool(packet.risk_controls) or not self.policy["require_risk_controls"], "hold",
            sorted(packet.risk_controls), "non-empty controls", "risk controls are mandatory",
        ))
        gates.append(self._gate(
            "sample_size", metrics.sample_size >= self.policy["minimum_sample_size"], "hold",
            metrics.sample_size, self.policy["minimum_sample_size"], "sample is too small for promotion",
        ))
        gates.append(self._gate(
            "hit_rate", metrics.hit_rate >= self.policy["target_hit_rate"], "reject",
            round(metrics.hit_rate, 6), self.policy["target_hit_rate"], "observed hit rate is below target",
        ))
        gates.append(self._gate(
            "expectancy", metrics.expectancy_r >= self.policy["minimum_expectancy_r"], "reject",
            round(metrics.expectancy_r, 6), self.policy["minimum_expectancy_r"], "expectancy is below the required R per trade",
        ))
        profit_factor_passed = (
            metrics.profit_factor is None or metrics.profit_factor >= self.policy["minimum_profit_factor"]
        )
        gates.append(self._gate(
            "profit_factor", profit_factor_passed, "reject",
            None if metrics.profit_factor is None else round(metrics.profit_factor, 6),
            self.policy["minimum_profit_factor"], "profit factor is below the required level",
        ))
        gates.append(self._gate(
            "maximum_drawdown", metrics.maximum_drawdown_r <= self.policy["maximum_drawdown_r"], "reject",
            round(metrics.maximum_drawdown_r, 6), self.policy["maximum_drawdown_r"], "maximum drawdown exceeds policy",
        ))

        confidence = wilson_lower_bound(metrics.wins, metrics.sample_size)
        evidence_weight = self._evidence_weight(packet)
        gates.append(self._gate(
            "confidence", confidence >= self.policy["minimum_confidence"], "hold",
            round(confidence, 6), self.policy["minimum_confidence"], "confidence lower bound is insufficient",
        ))
        gates.append(self._gate(
            "evidence_weight", evidence_weight >= self.policy["minimum_evidence_weight"], "hold",
            round(evidence_weight, 6), self.policy["minimum_evidence_weight"], "combined evidence weight is insufficient",
        ))

        failed = [gate for gate in gates if not gate.passed]
        if any(gate.severity == "reject" for gate in failed):
            verdict = Verdict.REJECT
        elif failed:
            verdict = Verdict.HOLD
        else:
            verdict = Verdict.PROMOTE

        rationale = tuple(f"{gate.gate}: {gate.reason}" for gate in failed) or ("all promotion gates passed",)
        remediation = tuple(
            f"Satisfy {gate.gate}: required {gate.required}; actual {gate.actual}" for gate in failed
        )
        return CourtDecision(
            policy_id=self.policy["policy_id"],
            verdict=verdict,
            confidence=confidence,
            evidence_weight=evidence_weight,
            gates=tuple(gates),
            rationale=rationale,
            remediation=remediation,
        )

    def _evidence_weight(self, packet: ResearchPacket) -> float:
        sample_component = min(1.0, packet.metrics.sample_size / 100)
        source_component = min(1.0, len({item.source for item in packet.evidence}) / 3)
        challenge_component = min(1.0, len(packet.counter_evidence) / 3)
        return (0.50 * sample_component) + (0.25 * source_component) + (0.25 * challenge_component)

    @staticmethod
    def _gate(
        name: str,
        passed: bool,
        severity: str,
        actual: Any,
        required: Any,
        failure_reason: str,
    ) -> GateResult:
        return GateResult(
            gate=name,
            passed=passed,
            severity=severity,
            actual=actual,
            required=required,
            reason="passed" if passed else failure_reason,
        )

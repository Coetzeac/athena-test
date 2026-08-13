from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Verdict(StrEnum):
    PROMOTE = "PROMOTE"
    HOLD = "HOLD"
    REJECT = "REJECT"


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    source: str
    locator: str
    sha256: str
    observed_at: str

    def validate(self) -> list[str]:
        failures: list[str] = []
        if not self.evidence_id.strip():
            failures.append("evidence_id is required")
        if not self.source.strip():
            failures.append(f"{self.evidence_id}: source is required")
        if not self.locator.strip():
            failures.append(f"{self.evidence_id}: locator is required")
        digest = self.sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            failures.append(f"{self.evidence_id}: sha256 must be 64 hexadecimal characters")
        if not self.observed_at.strip():
            failures.append(f"{self.evidence_id}: observed_at is required")
        return failures


@dataclass(frozen=True)
class ResearchMetrics:
    sample_size: int
    wins: int
    losses: int
    breakeven: int
    hit_rate: float
    average_win_r: float
    average_loss_r: float
    expectancy_r: float
    profit_factor: float | None
    maximum_drawdown_r: float


@dataclass(frozen=True)
class ResearchPacket:
    strategy_id: str
    claim: str
    mechanism: str
    recommendation: str
    instrument: str
    timeframe: str
    methodology: str
    assumptions: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]
    counter_evidence: tuple[str, ...]
    risk_controls: dict[str, Any]
    metrics: ResearchMetrics
    generated_at: str = field(default_factory=utc_now)

    def validate(self) -> list[str]:
        required = {
            "strategy_id": self.strategy_id,
            "claim": self.claim,
            "mechanism": self.mechanism,
            "recommendation": self.recommendation,
            "instrument": self.instrument,
            "timeframe": self.timeframe,
            "methodology": self.methodology,
        }
        failures = [f"{name} is required" for name, value in required.items() if not value.strip()]
        if not self.assumptions:
            failures.append("at least one assumption is required")
        for item in self.evidence:
            failures.extend(item.validate())
        return failures

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GateResult:
    gate: str
    passed: bool
    severity: str
    actual: Any
    required: Any
    reason: str


@dataclass(frozen=True)
class CourtDecision:
    policy_id: str
    verdict: Verdict
    confidence: float
    evidence_weight: float
    gates: tuple[GateResult, ...]
    rationale: tuple[str, ...]
    remediation: tuple[str, ...]
    adjudicated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verdict"] = self.verdict.value
        return data


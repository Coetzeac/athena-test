from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from athena.court import DecisionCourt, DecisionPolicy
from athena.evidence import EvidenceLedger, canonical_json, sha256_text
from athena.metrics import calculate_metrics
from athena.models import EvidenceRef, ResearchPacket, utc_now


def load_request(path: str | Path) -> tuple[ResearchPacket, dict[str, Any]]:
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    metrics = calculate_metrics(raw["trade_outcomes_r"])
    evidence = tuple(EvidenceRef(**item) for item in raw.get("evidence", []))
    outcomes_digest = sha256_text(canonical_json(raw["trade_outcomes_r"]))
    for item in evidence:
        if item.source == "synthetic_fixture" and item.locator.endswith("#trade_outcomes_r"):
            if item.sha256 != outcomes_digest:
                raise ValueError(f"{item.evidence_id}: digest does not match declared trade outcomes")
    packet = ResearchPacket(
        strategy_id=raw.get("strategy_id", ""),
        claim=raw.get("claim", ""),
        mechanism=raw.get("mechanism", ""),
        recommendation=raw.get("recommendation", ""),
        instrument=raw.get("instrument", ""),
        timeframe=raw.get("timeframe", ""),
        methodology=raw.get("methodology", ""),
        assumptions=tuple(raw.get("assumptions", [])),
        evidence=evidence,
        counter_evidence=tuple(raw.get("counter_evidence", [])),
        risk_controls=raw.get("risk_controls", {}),
        metrics=metrics,
    )
    return packet, raw


def run_cycle(
    request_path: str | Path,
    policy_path: str | Path,
    ledger_path: str | Path,
    status_path: str | Path,
) -> dict[str, Any]:
    ledger = EvidenceLedger(ledger_path)
    ledger.validate()
    packet, raw = load_request(request_path)

    cycle_entry = ledger.append(
        "research_packet_submitted",
        "athena.orchestrator",
        {
            "strategy_id": packet.strategy_id,
            "request_path": str(request_path),
            "declared_outcomes": len(raw["trade_outcomes_r"]),
            "evidence_ids": [item.evidence_id for item in packet.evidence],
        },
    )
    court = DecisionCourt(DecisionPolicy.from_file(policy_path))
    decision = court.adjudicate(packet)
    verdict_entry = ledger.append(
        "decision_court_verdict",
        "athena.decision_court",
        {
            "strategy_id": packet.strategy_id,
            "packet": packet.to_dict(),
            "decision": decision.to_dict(),
        },
    )
    ledger_status = ledger.validate()

    status = {
        "schema_version": 1,
        "system": "ATHENA",
        "state": "OPERATIONAL",
        "updated_at": utc_now(),
        "cycle": {
            "id": cycle_entry["hash"][:12],
            "strategy_id": packet.strategy_id,
            "instrument": packet.instrument,
            "timeframe": packet.timeframe,
            "verdict": decision.verdict.value,
            "recommendation": packet.recommendation,
        },
        "metrics": packet.to_dict()["metrics"],
        "decision": decision.to_dict(),
        "risk_controls": packet.risk_controls,
        "counter_evidence": list(packet.counter_evidence),
        "audit": {
            **ledger_status,
            "latest_verdict_hash": verdict_entry["hash"],
        },
        "next_action": (
            "Eligible for the next controlled research stage; live execution remains prohibited."
            if decision.verdict.value == "PROMOTE"
            else "; ".join(decision.remediation)
        ),
    }
    output = Path(status_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    return status

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from athena.court import DecisionCourt, DecisionPolicy
from athena.evidence import EvidenceLedger, canonical_json, sha256_text
from athena.metrics import calculate_metrics
from athena.models import EvidenceRef, ResearchPacket, utc_now
from athena.records import DatasetFingerprint, EvidenceRegister, KnowledgeRecord, Provenance, RecordType


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


def _cycle_provenance(
    packet: ResearchPacket,
    request_path: str | Path,
    request_sha256: str,
) -> Provenance:
    if not packet.evidence:
        raise ValueError("a governed cycle requires at least one immutable evidence reference")
    evidence_ids = tuple(item.evidence_id for item in packet.evidence)
    synthetic_only = all(item.source == "synthetic_fixture" for item in packet.evidence)
    return Provenance(
        source_type="synthetic_fixture" if synthetic_only else "declared_research_request",
        source_locator=str(request_path),
        source_sha256=request_sha256,
        observed_at=packet.evidence[0].observed_at,
        acquisition_method="repository_fixture" if synthetic_only else "declared_source_ingestion",
        usage_rights="synthetic-test-only" if synthetic_only else "declared-rights-unverified",
        evidence_ids=evidence_ids,
    )


def _register_cycle_inputs(
    packet: ResearchPacket,
    raw: dict[str, Any],
    request_path: str | Path,
    policy_path: str | Path,
    register: EvidenceRegister,
    ledger: EvidenceLedger,
) -> dict[str, KnowledgeRecord]:
    request_text = Path(request_path).read_text(encoding="utf-8")
    request_sha256 = sha256_text(request_text)
    provenance = _cycle_provenance(packet, request_path, request_sha256)
    evidence_ids = tuple(item.evidence_id for item in packet.evidence)
    outcomes_sha256 = sha256_text(canonical_json(raw["trade_outcomes_r"]))
    extraction_config_sha256 = sha256_text(canonical_json({
        "field": "trade_outcomes_r",
        "ordered": True,
        "unit": "R-multiple",
    }))
    fingerprint = DatasetFingerprint.create(
        dataset_name=f"{packet.strategy_id} declared outcomes",
        source=provenance.source_type,
        source_locator=f"{request_path}#trade_outcomes_r",
        content_sha256=outcomes_sha256,
        extraction_config_sha256=extraction_config_sha256,
        row_count=len(raw["trade_outcomes_r"]),
        fields=("trade_outcome_r",),
        universe=(packet.instrument,),
        timeframe=packet.timeframe,
        acquired_at=provenance.observed_at,
    )
    dataset = KnowledgeRecord.create(
        record_type=RecordType.DATASET,
        title=fingerprint.dataset_name,
        identity={"fingerprint_sha256": fingerprint.fingerprint_sha256},
        provenance=provenance,
        evidence_ids=evidence_ids,
        related_record_ids=(),
        content={"dataset_fingerprint": fingerprint.to_dict()},
    )

    strategy_specification = {
        "strategy_key": packet.strategy_id,
        "claim": packet.claim,
        "mechanism": packet.mechanism,
        "instrument": packet.instrument,
        "timeframe": packet.timeframe,
        "risk_controls": packet.risk_controls,
    }
    strategy_specification_sha256 = sha256_text(canonical_json(strategy_specification))
    strategy = KnowledgeRecord.create(
        record_type=RecordType.STRATEGY,
        title=packet.strategy_id,
        identity={
            "strategy_key": packet.strategy_id,
            "specification_sha256": strategy_specification_sha256,
        },
        provenance=provenance,
        evidence_ids=evidence_ids,
        related_record_ids=(),
        content=strategy_specification,
    )

    policy_sha256 = sha256_text(Path(policy_path).read_text(encoding="utf-8"))
    experiment_specification = {
        "experiment_key": f"{packet.strategy_id}:declared-outcome-evaluation",
        "strategy_record_id": strategy.record_id,
        "dataset_record_id": dataset.record_id,
        "methodology": packet.methodology,
        "assumptions": list(packet.assumptions),
        "counter_evidence": list(packet.counter_evidence),
        "policy_sha256": policy_sha256,
    }
    experiment_specification_sha256 = sha256_text(canonical_json(experiment_specification))
    experiment = KnowledgeRecord.create(
        record_type=RecordType.EXPERIMENT,
        title=f"Declared-outcome evaluation for {packet.strategy_id}",
        identity={
            "experiment_key": experiment_specification["experiment_key"],
            "specification_sha256": experiment_specification_sha256,
        },
        provenance=provenance,
        evidence_ids=evidence_ids,
        related_record_ids=(dataset.record_id, strategy.record_id),
        content=experiment_specification,
    )

    for record in (dataset, strategy, experiment):
        register.append(record, ledger)
    return {"dataset": dataset, "strategy": strategy, "experiment": experiment}


def run_cycle(
    request_path: str | Path,
    policy_path: str | Path,
    ledger_path: str | Path,
    status_path: str | Path,
    register_path: str | Path | None = None,
) -> dict[str, Any]:
    ledger = EvidenceLedger(ledger_path)
    ledger.validate()
    packet, raw = load_request(request_path)
    packet_failures = packet.validate()
    if packet_failures:
        raise ValueError("; ".join(packet_failures))
    register = EvidenceRegister(
        register_path if register_path is not None else Path(ledger_path).with_name("evidence-register.jsonl")
    )
    registered = _register_cycle_inputs(packet, raw, request_path, policy_path, register, ledger)
    input_record_ids = {name: record.record_id for name, record in registered.items()}

    cycle_entry = ledger.append(
        "research_packet_submitted",
        "athena.orchestrator",
        {
            "strategy_id": packet.strategy_id,
            "request_path": str(request_path),
            "declared_outcomes": len(raw["trade_outcomes_r"]),
            "evidence_ids": [item.evidence_id for item in packet.evidence],
            "evidence_record_ids": input_record_ids,
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
            "evidence_record_ids": input_record_ids,
        },
    )
    decision_payload = decision.to_dict()
    result_sha256 = sha256_text(canonical_json(decision_payload))
    validation_evidence_ids = tuple(dict.fromkeys((
        "EF-010",
        *(item.evidence_id for item in packet.evidence),
    )))
    validation_provenance = Provenance(
        source_type="athena_decision_court",
        source_locator=f"ledger:{verdict_entry['hash']}",
        source_sha256=verdict_entry["hash"],
        observed_at=decision.adjudicated_at,
        acquisition_method="deterministic_policy_adjudication",
        usage_rights="internal-generated-record",
        evidence_ids=validation_evidence_ids,
    )
    validation = KnowledgeRecord.create(
        record_type=RecordType.VALIDATION_RESULT,
        title=f"Decision Court result for {packet.strategy_id}",
        identity={
            "experiment_record_id": registered["experiment"].record_id,
            "result_sha256": result_sha256,
        },
        provenance=validation_provenance,
        evidence_ids=validation_evidence_ids,
        related_record_ids=(
            registered["dataset"].record_id,
            registered["strategy"].record_id,
            registered["experiment"].record_id,
        ),
        content={
            "experiment_record_id": registered["experiment"].record_id,
            "metrics": packet.to_dict()["metrics"],
            "decision": decision_payload,
        },
    )
    register.append(validation, ledger)
    evidence_status = register.validate(ledger)
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
        "evidence_register": {
            **evidence_status,
            "cycle_record_ids": {
                **input_record_ids,
                "validation_result": validation.record_id,
            },
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

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED_LAYERS = [
    "Knowledge",
    "Evidence",
    "Research",
    "Market Intelligence",
    "Decision Engine",
    "Execution Engine",
    "Validation Engine",
]

REQUIRED_SPECIALISTS = {
    "Research",
    "Feature",
    "Experiment",
    "Validation",
    "Market",
    "Decision Court",
    "Learning",
    "Operations Director",
}

REQUIRED_RUNTIME_SERVICES = {
    "athena-api",
    "athena-research",
    "athena-validation",
    "postgres",
    "redis",
    "minio-or-s3",
    "grafana",
    "nginx",
    "orchestrator",
}

REQUIRED_VALIDATION_STAGES = {
    "in-sample backtest",
    "walk-forward",
    "Monte Carlo",
    "cross-market validation",
    "parameter sensitivity",
    "robustness analysis",
}

REQUIRED_SYSTEMS_OF_RECORD = {
    "ChatGPT Work",
    "GitHub",
    "Google Drive",
    "PostgreSQL",
    "TradingView",
}

MINIMUM_TARGETS = {
    "minimum_papers": 100,
    "minimum_experiments": 250,
    "minimum_factors": 50,
    "minimum_indicators": 20,
    "minimum_strategies": 5,
}


class FreezeValidationError(ValueError):
    """Raised when a protected engineering-freeze invariant is violated."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def freeze_digest(freeze: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(freeze).encode("utf-8")).hexdigest()


def load_freeze(path: str | Path) -> dict[str, Any]:
    freeze = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_freeze(freeze)
    return freeze


def validate_freeze(freeze: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []

    if freeze.get("schema_version") != 1:
        failures.append("schema_version must be 1")
    if freeze.get("status") != "frozen":
        failures.append("status must remain frozen")
    if not freeze.get("freeze_id"):
        failures.append("freeze_id is required")
    if not freeze.get("change_rule"):
        failures.append("change_rule is required")

    layers = [layer.get("name") for layer in freeze.get("layers", [])]
    if layers != EXPECTED_LAYERS:
        failures.append(f"layers must remain in fixed order: {EXPECTED_LAYERS}")

    targets = freeze.get("scale_targets", {})
    for name, required in MINIMUM_TARGETS.items():
        if targets.get(name, 0) < required:
            failures.append(f"{name} cannot be below {required}")

    specialists = {item.get("name") for item in freeze.get("specialist_services", [])}
    missing_specialists = REQUIRED_SPECIALISTS - specialists
    if missing_specialists:
        failures.append(f"missing specialist services: {sorted(missing_specialists)}")

    runtime_services = set(freeze.get("runtime", {}).get("services", []))
    missing_runtime = REQUIRED_RUNTIME_SERVICES - runtime_services
    if missing_runtime:
        failures.append(f"missing runtime services: {sorted(missing_runtime)}")

    validation_stages = set(freeze.get("validation", {}).get("required_stages", []))
    missing_validation = REQUIRED_VALIDATION_STAGES - validation_stages
    if missing_validation:
        failures.append(f"missing validation stages: {sorted(missing_validation)}")

    systems = {item.get("system") for item in freeze.get("systems_of_record", [])}
    missing_systems = REQUIRED_SYSTEMS_OF_RECORD - systems
    if missing_systems:
        failures.append(f"missing systems of record: {sorted(missing_systems)}")

    pipeline = freeze.get("fixed_pipeline", {})
    stages = pipeline.get("stages", [])
    if not stages or stages[0] != "paper" or stages[-1] != "Decision Court accept or reject":
        failures.append("fixed pipeline must run from paper to Decision Court accept or reject")
    rules = pipeline.get("rules", [])
    if "Evidence precedes implementation." not in rules:
        failures.append("evidence-first implementation rule is missing")
    if "Failure at any required validation stage rejects the candidate." not in rules:
        failures.append("fail-closed validation rule is missing")

    experiments = freeze.get("experiment_queue", [])
    experiment_ids = [item.get("experiment_id") for item in experiments]
    expected_experiments = [f"ATH-{number:03d}" for number in range(1, 11)]
    if experiment_ids[:10] != expected_experiments:
        failures.append("experiment queue must preserve ATH-001 through ATH-010")

    report = freeze.get("mission_control", {}).get("daily_report", {})
    if report.get("name") != "ATHENA Daily Progress":
        failures.append("daily report must be ATHENA Daily Progress")
    if report.get("time") != "08:00" or report.get("timezone") != "Africa/Johannesburg":
        failures.append("ATHENA Daily Progress must remain scheduled for 08:00 Africa/Johannesburg")

    if freeze.get("human_authority", {}).get("execution_default") != "live execution prohibited until separately approved":
        failures.append("live-execution prohibition is missing")

    evidence_ids = {
        item.get("evidence_id") for item in freeze.get("evidence_register", [])
        if item.get("evidence_id")
    }
    if len(evidence_ids) != len(freeze.get("evidence_register", [])):
        failures.append("evidence IDs must be present and unique")
    referenced_ids = _collect_evidence_references(freeze)
    unknown_evidence = referenced_ids - evidence_ids
    if unknown_evidence:
        failures.append(f"unknown evidence references: {sorted(unknown_evidence)}")

    requirements = freeze.get("requirements", [])
    requirement_ids = [item.get("requirement_id") for item in requirements]
    if len(requirement_ids) != len(set(requirement_ids)) or any(not item for item in requirement_ids):
        failures.append("requirement IDs must be present and unique")

    if failures:
        raise FreezeValidationError("; ".join(failures))

    return {
        "valid": True,
        "freeze_id": freeze["freeze_id"],
        "sha256": freeze_digest(freeze),
        "layers": len(layers),
        "requirements": len(requirements),
        "specialist_services": len(specialists),
        "experiments_queued": len(experiments),
        "unresolved_specifications": len(freeze.get("unresolved_specifications", [])),
    }


def validate_traceability(
    freeze: dict[str, Any],
    traceability: dict[str, Any],
    repository_root: str | Path,
) -> dict[str, Any]:
    failures: list[str] = []
    root = Path(repository_root)
    required_ids = {item["requirement_id"] for item in freeze["requirements"]}
    rows = traceability.get("requirements", [])
    mapped_ids = {item.get("requirement_id") for item in rows}

    missing = required_ids - mapped_ids
    extra = mapped_ids - required_ids
    if missing:
        failures.append(f"unmapped freeze requirements: {sorted(missing)}")
    if extra:
        failures.append(f"unknown traceability requirements: {sorted(extra)}")

    permitted_statuses = {"implemented", "partial", "scaffolded", "pending", "blocked_external"}
    for item in rows:
        requirement_id = item.get("requirement_id", "unknown")
        if item.get("status") not in permitted_statuses:
            failures.append(f"{requirement_id}: invalid status")
        if not item.get("acceptance"):
            failures.append(f"{requirement_id}: acceptance condition is required")
        for artifact in item.get("artifacts", []):
            if not (root / artifact).exists():
                failures.append(f"{requirement_id}: missing artifact {artifact}")

    if failures:
        raise FreezeValidationError("; ".join(failures))

    counts = {status: 0 for status in sorted(permitted_statuses)}
    for item in rows:
        counts[item["status"]] += 1
    return {"valid": True, "requirements": len(rows), "status_counts": counts}


def _collect_evidence_references(value: Any) -> set[str]:
    referenced: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "evidence_ids" and isinstance(child, list):
                referenced.update(str(item) for item in child)
            else:
                referenced.update(_collect_evidence_references(child))
    elif isinstance(value, list):
        for child in value:
            referenced.update(_collect_evidence_references(child))
    return referenced


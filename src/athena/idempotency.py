from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from athena.evidence import EvidenceLedger, canonical_json, sha256_bytes, sha256_text
from athena.records import EvidenceRegister


CONTROL_ID = "ATHENA-ICC-001"
EXPECTED_EVIDENCE_IDS = ("EF-002", "EF-004", "EF-005", "EF-012", "EF-016")
EXPECTED_REQUIREMENTS = ("FR-001", "FR-008", "FR-012", "FR-017")
EXPECTED_IMPLEMENTATION_FILES = (
    ".github/workflows/athena-cycle.yml",
    "src/athena/cli.py",
    "src/athena/court.py",
    "src/athena/evidence.py",
    "src/athena/idempotency.py",
    "src/athena/metrics.py",
    "src/athena/models.py",
    "src/athena/orchestrator.py",
    "src/athena/records.py",
)
EXECUTED_REASON = "versioned input changed or no controlled prior cycle exists"
NO_CHANGE_REASON = "exact versioned input already adjudicated; audit state revalidated"


class CycleControlError(RuntimeError):
    """Raised when the idempotent-cycle control fails closed."""


@dataclass(frozen=True)
class CycleControlPolicy:
    raw: dict[str, Any]
    path: Path
    repository_root: Path

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        repository_root: str | Path | None = None,
    ) -> CycleControlPolicy:
        source = Path(path)
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CycleControlError(f"cannot load idempotent-cycle policy: {error}") from error
        root = (
            Path(repository_root).resolve()
            if repository_root is not None
            else Path(__file__).resolve().parents[2]
        )
        policy = cls(raw=raw, path=source, repository_root=root)
        policy.validate()
        return policy

    @property
    def implementation_files(self) -> tuple[str, ...]:
        return tuple(self.raw["input_identity"]["implementation_files"])

    def validate(self) -> dict[str, Any]:
        failures: list[str] = []
        exact_keys = {
            "schema_version",
            "control_id",
            "status",
            "approved_at",
            "authority",
            "evidence_ids",
            "requirements",
            "schedule",
            "input_identity",
            "exact_repeat",
            "changed_input",
            "decision_court_bypass",
            "live_execution",
        }
        if set(self.raw) != exact_keys:
            failures.append("idempotent-cycle policy must remain a closed contract")
        expected_scalars = {
            "schema_version": 1,
            "control_id": CONTROL_ID,
            "status": "approved_implementation_control",
            "approved_at": "2026-08-20",
            "authority": "Owner/CIO",
            "decision_court_bypass": "prohibited",
            "live_execution": "prohibited",
        }
        for field, expected in expected_scalars.items():
            if self.raw.get(field) != expected:
                failures.append(f"{field} must remain {expected!r}")
        if tuple(self.raw.get("evidence_ids", [])) != EXPECTED_EVIDENCE_IDS:
            failures.append(f"evidence_ids must remain {list(EXPECTED_EVIDENCE_IDS)}")
        if tuple(self.raw.get("requirements", [])) != EXPECTED_REQUIREMENTS:
            failures.append(f"requirements must remain {list(EXPECTED_REQUIREMENTS)}")

        schedule = self.raw.get("schedule", {})
        if schedule != {
            "cadence": "hourly",
            "cron": "17 * * * *",
            "workflow": ".github/workflows/athena-cycle.yml",
            "validation_on_every_invocation": True,
        }:
            failures.append("hourly schedule and per-invocation validation must remain fixed")

        identity = self.raw.get("input_identity", {})
        if set(identity) != {"algorithm", "canonicalization", "components", "implementation_files"}:
            failures.append("input_identity contains missing or unknown fields")
        if identity.get("algorithm") != "sha256":
            failures.append("input identity algorithm must remain sha256")
        if identity.get("canonicalization") != "raw_bytes_and_sorted_manifest":
            failures.append("input identity canonicalization must remain raw_bytes_and_sorted_manifest")
        if tuple(identity.get("components", [])) != (
            "research_request",
            "decision_policy",
            "cycle_policy",
            "implementation",
        ):
            failures.append("input identity must cover request, policies, and governed implementation")
        if tuple(identity.get("implementation_files", [])) != EXPECTED_IMPLEMENTATION_FILES:
            failures.append("governed implementation file set differs from ATHENA-ICC-001")

        exact_repeat = self.raw.get("exact_repeat", {})
        if exact_repeat != {
            "validate_ledger": True,
            "validate_register": True,
            "validate_status_bindings": True,
            "append_ledger_records": 0,
            "append_register_records": 0,
            "write_status_bytes": False,
            "workflow_commit": False,
            "outcome": "NO_CHANGE",
        }:
            failures.append("exact-repeat no-change controls differ from ATHENA-ICC-001")

        changed_input = self.raw.get("changed_input", {})
        if changed_input != {
            "run_full_decision_court_cycle": True,
            "append_audit_evidence": True,
            "write_status": True,
            "outcome": "EXECUTED",
        }:
            failures.append("changed-input Court controls differ from ATHENA-ICC-001")

        for relative in EXPECTED_IMPLEMENTATION_FILES:
            if not (self.repository_root / relative).is_file():
                failures.append(f"missing governed implementation file: {relative}")
        workflow = self.repository_root / ".github" / "workflows" / "athena-cycle.yml"
        if workflow.is_file():
            workflow_text = workflow.read_text(encoding="utf-8")
            for required in (
                '- cron: "17 * * * *"',
                "athena run examples/orb_candidate.json",
                "git diff --cached --quiet && exit 0",
            ):
                if required not in workflow_text:
                    failures.append(f"hourly workflow is missing approved control: {required}")
        if failures:
            raise CycleControlError("; ".join(failures))
        return {
            "valid": True,
            "control_id": CONTROL_ID,
            "implementation_files": len(EXPECTED_IMPLEMENTATION_FILES),
            "schedule": schedule["cadence"],
            "exact_repeat_outcome": exact_repeat["outcome"],
            "decision_court_bypass": self.raw["decision_court_bypass"],
            "live_execution": self.raw["live_execution"],
        }

    def implementation_identity(self) -> dict[str, Any]:
        files: list[dict[str, str]] = []
        for relative in self.implementation_files:
            source = self.repository_root / relative
            files.append({"path": relative, "sha256": sha256_bytes(source.read_bytes())})
        manifest = {"algorithm": "sha256", "files": files}
        return {**manifest, "sha256": sha256_text(canonical_json(manifest))}

    def input_identity(
        self,
        request_path: str | Path,
        decision_policy_path: str | Path,
    ) -> dict[str, Any]:
        implementation = self.implementation_identity()
        components = {
            "research_request_sha256": sha256_bytes(Path(request_path).read_bytes()),
            "decision_policy_sha256": sha256_bytes(Path(decision_policy_path).read_bytes()),
            "cycle_policy_sha256": sha256_bytes(self.path.read_bytes()),
            "implementation_sha256": implementation["sha256"],
        }
        manifest = {
            "schema_version": 1,
            "control_id": CONTROL_ID,
            "components": components,
            "implementation_files": implementation["files"],
        }
        return {**manifest, "input_sha256": sha256_text(canonical_json(manifest))}


def _load_status(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CycleControlError(f"cannot validate existing runtime status: {error}") from error
    if not isinstance(status, dict):
        raise CycleControlError("existing runtime status must be a JSON object")
    return status


def _validate_status_bindings(
    status: dict[str, Any],
    ledger_status: dict[str, Any],
    register_status: dict[str, Any],
) -> None:
    audit = status.get("audit")
    evidence = status.get("evidence_register")
    if not isinstance(audit, dict) or not isinstance(evidence, dict):
        raise CycleControlError("existing status lacks audit or evidence-register bindings")
    for field, expected in ledger_status.items():
        if audit.get(field) != expected:
            raise CycleControlError(f"existing status audit binding mismatch: {field}")
    for field, expected in register_status.items():
        if evidence.get(field) != expected:
            raise CycleControlError(f"existing status evidence-register binding mismatch: {field}")


def _validate_controlled_status_evidence(
    status: dict[str, Any],
    control: dict[str, Any],
    ledger: EvidenceLedger,
    register: EvidenceRegister,
) -> None:
    if status.get("schema_version") != 1 or status.get("system") != "ATHENA":
        raise CycleControlError("existing controlled status has an invalid system contract")
    if status.get("state") != "OPERATIONAL":
        raise CycleControlError("existing controlled status is not operational")

    audit = status["audit"]
    verdict_hash = audit.get("latest_verdict_hash")
    verdict_matches = [entry for entry in ledger.entries() if entry.get("hash") == verdict_hash]
    if len(verdict_matches) != 1 or verdict_matches[0].get("event_type") != "decision_court_verdict":
        raise CycleControlError("controlled status does not identify one immutable Court verdict")
    verdict_entry = verdict_matches[0]
    verdict_payload = verdict_entry.get("payload", {})
    expected_control = {"control_id": CONTROL_ID, "input": control["input"]}
    if verdict_payload.get("cycle_control") != expected_control:
        raise CycleControlError("cycle identity is not anchored in the Court verdict ledger record")
    if status.get("decision") != verdict_payload.get("decision"):
        raise CycleControlError("controlled status decision differs from its Court verdict")

    packet = verdict_payload.get("packet")
    cycle = status.get("cycle")
    if not isinstance(packet, dict) or not isinstance(cycle, dict):
        raise CycleControlError("controlled status lacks its packet or cycle fields")
    expected_cycle_fields = {
        "strategy_id": packet.get("strategy_id"),
        "instrument": packet.get("instrument"),
        "timeframe": packet.get("timeframe"),
        "verdict": verdict_payload.get("decision", {}).get("verdict"),
        "recommendation": packet.get("recommendation"),
    }
    for field, expected in expected_cycle_fields.items():
        if cycle.get(field) != expected:
            raise CycleControlError(f"controlled status cycle differs from Court evidence: {field}")
    if status.get("metrics") != packet.get("metrics"):
        raise CycleControlError("controlled status metrics differ from the submitted packet")
    if status.get("risk_controls") != packet.get("risk_controls"):
        raise CycleControlError("controlled status risk controls differ from the submitted packet")
    if status.get("counter_evidence") != packet.get("counter_evidence"):
        raise CycleControlError("controlled status counter-evidence differs from the submitted packet")

    submission_hash = verdict_payload.get("cycle_submission_hash")
    submission_matches = [entry for entry in ledger.entries() if entry.get("hash") == submission_hash]
    if len(submission_matches) != 1 or submission_matches[0].get("event_type") != "research_packet_submitted":
        raise CycleControlError("Court verdict lacks one immutable cycle-submission record")
    submission = submission_matches[0]
    if submission.get("payload", {}).get("cycle_control") != expected_control:
        raise CycleControlError("cycle identity differs between submission and verdict records")
    if cycle.get("id") != str(submission_hash)[:12]:
        raise CycleControlError("controlled status cycle ID differs from its submission record")

    cycle_record_ids = status["evidence_register"].get("cycle_record_ids")
    if not isinstance(cycle_record_ids, dict):
        raise CycleControlError("controlled status lacks cycle evidence-record IDs")
    submitted_record_ids = verdict_payload.get("evidence_record_ids")
    if not isinstance(submitted_record_ids, dict):
        raise CycleControlError("Court verdict lacks submitted evidence-record IDs")
    for kind in ("dataset", "strategy", "experiment"):
        if cycle_record_ids.get(kind) != submitted_record_ids.get(kind):
            raise CycleControlError(f"controlled status evidence-record mismatch: {kind}")

    records = {record.record_id: record for record in register.records()}
    validation = records.get(cycle_record_ids.get("validation_result"))
    if validation is None or validation.record_type.value != "validation_result":
        raise CycleControlError("controlled status validation result is not registered")
    if validation.content.get("decision") != verdict_payload.get("decision"):
        raise CycleControlError("registered validation result differs from the Court verdict")
    if validation.content.get("experiment_record_id") != submitted_record_ids.get("experiment"):
        raise CycleControlError("registered validation result has the wrong experiment link")
    if validation.provenance.source_locator != f"ledger:{verdict_hash}":
        raise CycleControlError("registered validation result has the wrong Court locator")
    if validation.provenance.source_sha256 != verdict_hash:
        raise CycleControlError("registered validation result has the wrong Court digest")


def reuse_exact_cycle_if_valid(
    status_path: str | Path,
    input_identity: dict[str, Any],
    ledger: EvidenceLedger,
    register: EvidenceRegister,
) -> dict[str, Any] | None:
    """Return a non-persisted NO_CHANGE view only after full state validation."""
    ledger_status = ledger.validate()
    register_status = register.validate(ledger)
    status = _load_status(Path(status_path))
    if status is None:
        cycle_events = {
            entry.get("event_type") for entry in ledger.entries()
            if entry.get("event_type") in {"research_packet_submitted", "decision_court_verdict"}
        }
        if cycle_events:
            raise CycleControlError("governed cycle evidence exists without its bound status document")
        return None

    _validate_status_bindings(status, ledger_status, register_status)
    control = status.get("cycle_control")
    if control is None:
        return None
    if not isinstance(control, dict) or set(control) != {"control_id", "outcome", "reason", "input"}:
        raise CycleControlError("existing cycle-control status is not a closed contract")
    if control.get("control_id") != CONTROL_ID:
        raise CycleControlError("existing cycle-control ID is not approved")
    if control.get("outcome") != "EXECUTED" or control.get("reason") != EXECUTED_REASON:
        raise CycleControlError("persisted cycle-control outcome is invalid")
    if not isinstance(control.get("input"), dict):
        raise CycleControlError("persisted cycle-control input identity is invalid")
    _validate_controlled_status_evidence(status, control, ledger, register)
    if control.get("input") != input_identity:
        return None

    result = copy.deepcopy(status)
    result["cycle_control"] = {
        "control_id": CONTROL_ID,
        "outcome": "NO_CHANGE",
        "reason": NO_CHANGE_REASON,
        "input": input_identity,
    }
    return result


def validate_cycle_control_policy(
    policy_path: str | Path,
    repository_root: str | Path = ".",
) -> dict[str, Any]:
    root = Path(repository_root)
    policy_status = CycleControlPolicy.from_file(policy_path, root).validate()
    schema_path = root / "schemas" / "idempotent-cycle-policy.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CycleControlError(f"cannot load idempotent-cycle schema: {error}") from error
    if schema.get("additionalProperties") is not False:
        raise CycleControlError("idempotent-cycle schema must remain closed")
    properties = schema.get("properties", {})
    expected_constants = {
        "schema_version": 1,
        "control_id": CONTROL_ID,
        "status": "approved_implementation_control",
        "approved_at": "2026-08-20",
        "authority": "Owner/CIO",
        "decision_court_bypass": "prohibited",
        "live_execution": "prohibited",
    }
    for field, expected in expected_constants.items():
        if properties.get(field, {}).get("const") != expected:
            raise CycleControlError(f"idempotent-cycle schema weakens or omits {field}")
    return {**policy_status, "schemas": 1}

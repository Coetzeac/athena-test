import json
import tempfile
import unittest
from pathlib import Path

from athena.evidence import EvidenceLedger
from athena.idempotency import (
    CycleControlError,
    CycleControlPolicy,
    validate_cycle_control_policy,
)
from athena.orchestrator import run_cycle


ROOT = Path(__file__).resolve().parents[1]
REQUEST = ROOT / "examples" / "orb_candidate.json"
DECISION_POLICY = ROOT / "config" / "decision_policy.json"
CYCLE_POLICY = ROOT / "config" / "idempotent_cycle_policy.json"


class IdempotentCycleTests(unittest.TestCase):
    def _run(self, root: Path, decision_policy: Path = DECISION_POLICY) -> dict:
        return run_cycle(
            REQUEST,
            decision_policy,
            root / "ledger.jsonl",
            root / "status.json",
            root / "evidence-register.jsonl",
            CYCLE_POLICY,
        )

    def test_approved_policy_schema_and_hourly_workflow_validate(self) -> None:
        status = validate_cycle_control_policy(CYCLE_POLICY, ROOT)
        self.assertTrue(status["valid"])
        self.assertEqual(status["control_id"], "ATHENA-ICC-001")
        self.assertEqual(status["schedule"], "hourly")
        self.assertEqual(status["implementation_files"], 9)
        self.assertEqual(status["schemas"], 1)

    def test_exact_repeat_validates_state_without_writing_duplicate_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._run(root)
            before = {
                name: (root / name).read_bytes()
                for name in ("ledger.jsonl", "evidence-register.jsonl", "status.json")
            }

            second = self._run(root)

            self.assertEqual(first["cycle_control"]["outcome"], "EXECUTED")
            self.assertEqual(second["cycle_control"]["outcome"], "NO_CHANGE")
            self.assertEqual(second["audit"]["entries"], 6)
            self.assertEqual(second["evidence_register"]["entries"], 4)
            for name, expected in before.items():
                self.assertEqual((root / name).read_bytes(), expected)
            persisted = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["cycle_control"]["outcome"], "EXECUTED")

    def test_exact_repeat_fails_closed_when_status_binding_is_tampered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root)
            status_path = root / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["audit"]["terminal_hash"] = "0" * 64
            status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            ledger_before = (root / "ledger.jsonl").read_bytes()
            register_before = (root / "evidence-register.jsonl").read_bytes()

            with self.assertRaises(CycleControlError):
                self._run(root)

            self.assertEqual((root / "ledger.jsonl").read_bytes(), ledger_before)
            self.assertEqual((root / "evidence-register.jsonl").read_bytes(), register_before)

    def test_exact_repeat_rejects_status_content_not_anchored_in_court_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root)
            status_path = root / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["cycle"]["verdict"] = "PROMOTE"
            status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            ledger_before = (root / "ledger.jsonl").read_bytes()

            with self.assertRaises(CycleControlError):
                self._run(root)

            self.assertEqual((root / "ledger.jsonl").read_bytes(), ledger_before)

    def test_first_cycle_can_follow_valid_non_cycle_audit_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            EvidenceLedger(root / "ledger.jsonl").append(
                "research_source_quarantined",
                "athena.research_intake",
                {"reason": "synthetic pre-cycle control event"},
            )

            status = self._run(root)

            self.assertEqual(status["cycle_control"]["outcome"], "EXECUTED")
            self.assertEqual(status["audit"]["entries"], 7)

    def test_changed_decision_policy_runs_full_court_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._run(root)
            changed_policy = root / "decision-policy-v2.json"
            raw = json.loads(DECISION_POLICY.read_text(encoding="utf-8"))
            raw["minimum_sample_size"] = 31
            changed_policy.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

            second = self._run(root, changed_policy)

            self.assertEqual(first["cycle_control"]["outcome"], "EXECUTED")
            self.assertEqual(second["cycle_control"]["outcome"], "EXECUTED")
            self.assertNotEqual(
                first["cycle_control"]["input"]["input_sha256"],
                second["cycle_control"]["input"]["input_sha256"],
            )
            self.assertEqual(second["audit"]["entries"], 10)
            self.assertEqual(second["evidence_register"]["entries"], 6)

    def test_policy_is_closed_and_cannot_enable_duplicate_writes_or_court_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weakened-cycle-policy.json"
            raw = json.loads(CYCLE_POLICY.read_text(encoding="utf-8"))
            raw["exact_repeat"]["append_ledger_records"] = 1
            raw["decision_court_bypass"] = "permitted"
            raw["silent_override"] = True
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(CycleControlError):
                CycleControlPolicy.from_file(path, ROOT)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from athena.orchestrator import run_cycle


class OrchestratorTests(unittest.TestCase):
    def test_example_runs_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status = run_cycle(
                "examples/orb_candidate.json",
                "config/decision_policy.json",
                root / "ledger.jsonl",
                root / "status.json",
                root / "evidence-register.jsonl",
            )
            self.assertEqual(status["state"], "OPERATIONAL")
            self.assertEqual(status["cycle_control"]["outcome"], "EXECUTED")
            self.assertEqual(status["cycle"]["verdict"], "HOLD")
            self.assertTrue(status["audit"]["valid"])
            self.assertEqual(status["audit"]["entries"], 6)
            self.assertEqual(status["evidence_register"]["entries"], 4)
            self.assertEqual(status["evidence_register"]["ledger_links"], 4)
            self.assertEqual(
                set(status["evidence_register"]["cycle_record_ids"]),
                {"dataset", "strategy", "experiment", "validation_result"},
            )
            written = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(written["audit"]["terminal_hash"], status["audit"]["terminal_hash"])
            self.assertEqual(
                written["evidence_register"]["register_sha256"],
                status["evidence_register"]["register_sha256"],
            )


if __name__ == "__main__":
    unittest.main()

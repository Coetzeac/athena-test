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
            )
            self.assertEqual(status["state"], "OPERATIONAL")
            self.assertEqual(status["cycle"]["verdict"], "HOLD")
            self.assertTrue(status["audit"]["valid"])
            self.assertEqual(status["audit"]["entries"], 2)
            written = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(written["audit"]["terminal_hash"], status["audit"]["terminal_hash"])


if __name__ == "__main__":
    unittest.main()


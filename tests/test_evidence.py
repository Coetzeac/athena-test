import json
import tempfile
import unittest
from pathlib import Path

from athena.evidence import EvidenceLedger, LedgerIntegrityError


class LedgerTests(unittest.TestCase):
    def test_chain_validates_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            ledger = EvidenceLedger(path, clock=lambda: "2026-08-13T00:00:00+00:00")
            ledger.append("submitted", "researcher", {"claim": "test"})
            ledger.append("adjudicated", "court", {"verdict": "HOLD"})
            self.assertEqual(ledger.validate()["entries"], 2)

            entries = path.read_text(encoding="utf-8").splitlines()
            first = json.loads(entries[0])
            first["payload"]["claim"] = "rewritten"
            entries[0] = json.dumps(first)
            path.write_text("\n".join(entries) + "\n", encoding="utf-8")
            with self.assertRaises(LedgerIntegrityError):
                ledger.validate()


if __name__ == "__main__":
    unittest.main()


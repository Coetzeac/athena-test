import copy
import json
import tempfile
import unittest
from pathlib import Path

from athena.evidence import EvidenceLedger, sha256_text
from athena.intake import QuarantineRegister, ResearchIntake, ResearchIntakeError, validate_intake_state
from athena.records import EvidenceRegister, RecordType


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "research_intake_policy.json"
MANIFEST = ROOT / "examples" / "research_intake" / "har_rv_synthetic_intake.json"
SOURCE = ROOT / "examples" / "research_intake" / "har_rv_synthetic_source.txt"
NOW = "2026-08-13T12:00:00+00:00"


class IntakeFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.objects = root / "objects"
        self.register = EvidenceRegister(root / "evidence-register.jsonl")
        self.quarantine = QuarantineRegister(root / "quarantine.jsonl", clock=lambda: NOW)
        self.ledger = EvidenceLedger(root / "ledger.jsonl", clock=lambda: NOW)
        self.intake = ResearchIntake.from_policy_file(POLICY, clock=lambda: NOW)

    def ingest(self, manifest: Path = MANIFEST) -> dict:
        return self.intake.ingest(
            manifest,
            objects_root=self.objects,
            register=self.register,
            quarantine=self.quarantine,
            ledger=self.ledger,
        )

    def write_request(self, raw: dict, source_text: str | None = None) -> Path:
        source = self.root / "source.txt"
        text = SOURCE.read_text(encoding="utf-8") if source_text is None else source_text
        source.write_text(text, encoding="utf-8")
        raw["source_file"] = source.name
        raw["declared_source_sha256"] = sha256_text(text)
        manifest = self.root / "intake.json"
        manifest.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        return manifest


class ResearchIntakeTests(unittest.TestCase):
    def test_accepts_complete_intake_and_reconciles_every_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = IntakeFixture(Path(directory))
            result = fixture.ingest()

            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(len(result["record_ids"]), 5)
            state = validate_intake_state(
                objects_root=fixture.objects,
                register=fixture.register,
                quarantine=fixture.quarantine,
                ledger=fixture.ledger,
            )
            self.assertTrue(state["valid"])
            self.assertEqual(state["objects_verified"], 1)
            self.assertEqual(state["claims_linked"], 1)
            self.assertEqual(state["register"]["entries"], 5)
            self.assertEqual(state["register"]["ledger_links"], 5)
            self.assertEqual(state["quarantine"]["entries"], 0)
            self.assertEqual(fixture.ledger.validate()["entries"], 6)
            counts = state["register"]["record_type_counts"]
            self.assertEqual(counts["author"], 1)
            self.assertEqual(counts["paper"], 1)
            self.assertEqual(counts["research_card"], 1)
            self.assertEqual(counts["hypothesis"], 1)
            self.assertEqual(counts["formula"], 1)

    def test_exact_duplicate_is_idempotent_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = IntakeFixture(Path(directory))
            first = fixture.ingest()
            second = fixture.ingest()

            self.assertEqual(first["status"], "ACCEPTED")
            self.assertEqual(second["status"], "DUPLICATE")
            self.assertEqual(first["paper_record_id"], second["paper_record_id"])
            self.assertEqual(fixture.register.validate(fixture.ledger)["entries"], 5)
            self.assertEqual(fixture.ledger.validate()["entries"], 7)

    def test_uncertain_rights_and_unsupported_claim_are_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = IntakeFixture(Path(directory))
            raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
            raw["usage_rights"] = "unknown"
            raw["research_card"]["claims"][0]["source_locator"] = ""
            result = fixture.ingest(fixture.write_request(raw))

            self.assertEqual(result["status"], "QUARANTINED")
            self.assertIn("intake.usage_rights is prohibited, uncertain, or unsupported", result["reasons"])
            self.assertIn("research_card.claims[0].source_locator is required", result["reasons"])
            self.assertEqual(fixture.register.validate(fixture.ledger)["entries"], 0)
            self.assertEqual(fixture.quarantine.validate(fixture.ledger)["entries"], 1)

    def test_locator_content_conflict_and_mirrored_duplicate_are_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = IntakeFixture(Path(directory))
            self.assertEqual(fixture.ingest()["status"], "ACCEPTED")
            raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
            conflict = fixture.write_request(raw, source_text="changed source bytes\n")
            result = fixture.ingest(conflict)
            self.assertEqual(result["status"], "QUARANTINED")
            self.assertIn("canonical locator is already registered with different source bytes", result["reasons"])

            mirrored = copy.deepcopy(json.loads(MANIFEST.read_text(encoding="utf-8")))
            mirrored["paper"]["canonical_locator"] = "synthetic://athena/mirrored-copy"
            mirror_request = fixture.write_request(mirrored)
            mirror_result = fixture.ingest(mirror_request)
            self.assertEqual(mirror_result["status"], "QUARANTINED")
            self.assertIn("source bytes are already registered under a different canonical locator", mirror_result["reasons"])

    def test_source_traversal_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = IntakeFixture(root)
            raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
            raw["source_file"] = "../outside.txt"
            manifest = root / "intake.json"
            manifest.write_text(json.dumps(raw), encoding="utf-8")

            result = fixture.ingest(manifest)
            self.assertEqual(result["status"], "QUARANTINED")
            self.assertIn("intake.source_file must remain inside the manifest directory", result["reasons"])

    def test_digest_mismatch_and_binary_manifest_are_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = IntakeFixture(root)
            raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
            manifest = fixture.write_request(raw)
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            raw["declared_source_sha256"] = "0" * 64
            manifest.write_text(json.dumps(raw), encoding="utf-8")
            mismatch = fixture.ingest(manifest)
            self.assertEqual(mismatch["status"], "QUARANTINED")
            self.assertIn("declared source digest does not match retained bytes", mismatch["reasons"])

            binary = root / "binary.json"
            binary.write_bytes(b"\xff\xfe\x00")
            invalid = fixture.ingest(binary)
            self.assertEqual(invalid["status"], "QUARANTINED")
            self.assertIn("intake manifest must be UTF-8 JSON", invalid["reasons"])
            self.assertEqual(fixture.quarantine.validate(fixture.ledger)["entries"], 2)

    def test_retained_source_tampering_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = IntakeFixture(Path(directory))
            accepted = fixture.ingest()
            object_path = fixture.objects / accepted["object_path"]
            object_path.write_text("tampered", encoding="utf-8")
            with self.assertRaises(ResearchIntakeError):
                validate_intake_state(
                    objects_root=fixture.objects,
                    register=fixture.register,
                    quarantine=fixture.quarantine,
                    ledger=fixture.ledger,
                )

    def test_claim_relationships_point_to_registered_paper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = IntakeFixture(Path(directory))
            fixture.ingest()
            records = fixture.register.records()
            card = next(record for record in records if record.record_type == RecordType.RESEARCH_CARD)
            paper = next(record for record in records if record.record_type == RecordType.PAPER)
            self.assertEqual(card.content["claims"][0]["evidence_record_ids"], [paper.record_id])


if __name__ == "__main__":
    unittest.main()

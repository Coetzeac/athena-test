import copy
import json
import tempfile
import unittest
from pathlib import Path

from athena.evidence import EvidenceLedger
from athena.market_data import (
    MarketDataError,
    MarketDataIntake,
    MarketDataPolicy,
    MarketDataQuarantineRegister,
    MarketDataRequest,
    TwelveDataClient,
    validate_market_data_policy,
    validate_market_data_state,
)
from athena.records import EvidenceRegister


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "market_data_policy.json"
FIXTURE_PATH = ROOT / "examples" / "market_data" / "twelve_data_synthetic_daily.json"
NOW = "2026-08-14T06:00:00+00:00"


def fixture_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def approved_request() -> MarketDataRequest:
    return MarketDataRequest(
        symbol="GBP/USD",
        interval="1day",
        start_date="2026-08-03",
        end_date="2026-08-07",
    )


class MarketFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.objects = root / "objects"
        self.register = EvidenceRegister(root / "register.jsonl")
        self.quarantine = MarketDataQuarantineRegister(root / "quarantine.jsonl", clock=lambda: NOW)
        self.ledger = EvidenceLedger(root / "ledger.jsonl", clock=lambda: NOW)
        self.policy = MarketDataPolicy.from_file(POLICY_PATH)
        self.intake = MarketDataIntake(self.policy, clock=lambda: NOW)

    def ingest(self, payload: dict | None = None) -> dict:
        return self.intake.ingest_payload(
            fixture_payload() if payload is None else payload,
            approved_request(),
            source_mode="synthetic_fixture",
            source_locator="repository://examples/market_data/twelve_data_synthetic_daily.json",
            source_bytes=FIXTURE_PATH.read_bytes() if payload is None else None,
            objects_root=self.objects,
            register=self.register,
            quarantine=self.quarantine,
            ledger=self.ledger,
        )

    def validate(self) -> dict:
        return validate_market_data_state(
            policy=self.policy,
            objects_root=self.objects,
            register=self.register,
            quarantine=self.quarantine,
            ledger=self.ledger,
        )


class MarketDataTests(unittest.TestCase):
    def test_resolution_policy_is_closed_and_preserves_owner_controls(self) -> None:
        status = validate_market_data_policy(POLICY_PATH, ROOT)
        self.assertTrue(status["valid"])
        self.assertEqual(status["resolution_id"], "ATHENA-MDR-001")
        self.assertEqual(status["provider"], "Twelve Data")
        self.assertEqual(status["approved_symbols"], 8)
        self.assertEqual(status["approved_intervals"], 5)
        self.assertEqual(status["monthly_budget_usd"], 0)
        self.assertEqual(status["conditional_upgrade_cap_usd"], 79)
        self.assertEqual(status["live_execution"], "prohibited")

    def test_request_rejects_unapproved_scope_and_forces_partitioning(self) -> None:
        policy = MarketDataPolicy.from_file(POLICY_PATH)
        outside = MarketDataRequest("XAU/USD", "1min", "2000-01-01", "2026-08-01")
        failures = outside.validate(policy)
        self.assertIn("symbol is outside the approved universe: XAU/USD", failures)
        self.assertIn("interval is outside the approved set: 1min", failures)

        too_wide = MarketDataRequest("GBP/USD", "5min", "2026-07-01", "2026-08-01")
        self.assertIn(
            "request exceeds the 10-day controlled window for 5min; partition it",
            too_wide.validate(policy),
        )

        too_early = MarketDataRequest("ETH/USD", "1day", "2010-01-01", "2010-01-02")
        self.assertIn(
            "start_date precedes the approved 1day history boundary 2015-08-07",
            too_early.validate(policy),
        )

    def test_accepts_fixture_retains_both_byte_streams_and_reconciles_every_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = MarketFixture(Path(directory))
            result = fixture.ingest()
            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(result["row_count"], 5)
            self.assertEqual(result["live_execution"], "prohibited")
            self.assertTrue((fixture.objects / result["raw_object_path"]).is_file())
            self.assertTrue((fixture.objects / result["normalized_object_path"]).is_file())
            self.assertEqual(
                (fixture.objects / result["raw_object_path"]).read_bytes(),
                FIXTURE_PATH.read_bytes(),
            )

            state = fixture.validate()
            self.assertTrue(state["valid"])
            self.assertEqual(state["datasets"], 1)
            self.assertEqual(state["real_datasets"], 0)
            self.assertEqual(state["synthetic_datasets"], 1)
            self.assertEqual(state["rows_verified"], 5)
            self.assertEqual(state["register"]["ledger_links"], 1)
            self.assertEqual(state["quarantine"]["entries"], 0)
            self.assertEqual(fixture.ledger.validate()["entries"], 2)

    def test_exact_fixture_duplicate_is_idempotent_and_not_double_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = MarketFixture(Path(directory))
            first = fixture.ingest()
            second = fixture.ingest()
            self.assertEqual(first["status"], "ACCEPTED")
            self.assertEqual(second["status"], "DUPLICATE")
            self.assertEqual(first["dataset_record_id"], second["dataset_record_id"])
            self.assertEqual(fixture.register.validate(fixture.ledger)["entries"], 1)
            self.assertEqual(fixture.ledger.validate()["entries"], 2)

    def test_conflicting_symbol_duplicate_timestamp_and_invalid_range_are_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = MarketFixture(Path(directory))
            payload = copy.deepcopy(fixture_payload())
            payload["meta"]["symbol"] = "EUR/USD"
            payload["values"][1]["datetime"] = payload["values"][0]["datetime"]
            payload["values"][2]["high"] = "1.32000"
            result = fixture.ingest(payload)
            self.assertEqual(result["status"], "QUARANTINED")
            self.assertIn("provider response symbol conflicts with the approved request", result["reasons"])
            self.assertIn("duplicate bar timestamp: 2026-08-03T00:00:00Z", result["reasons"])
            self.assertIn("bar[2] high is below low", result["reasons"])
            self.assertEqual(fixture.register.validate(fixture.ledger)["entries"], 0)
            self.assertEqual(fixture.quarantine.validate(fixture.ledger)["entries"], 1)
            self.assertEqual(fixture.ledger.validate()["entries"], 1)
            self.assertTrue((fixture.objects / result["source_object_path"]).is_file())

    def test_unexpected_crypto_gap_is_quarantined_without_fabrication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = MarketDataPolicy.from_file(POLICY_PATH)
            intake = MarketDataIntake(policy, clock=lambda: NOW)
            ledger = EvidenceLedger(root / "ledger.jsonl", clock=lambda: NOW)
            payload = {
                "meta": {"symbol": "BTC/USD", "interval": "1day", "exchange_timezone": "UTC"},
                "values": [
                    {"datetime": "2026-08-01", "open": "100", "high": "110", "low": "90", "close": "105"},
                    {"datetime": "2026-08-03", "open": "105", "high": "115", "low": "100", "close": "110"},
                ],
                "status": "ok",
            }
            result = intake.ingest_payload(
                payload,
                MarketDataRequest("BTC/USD", "1day", "2026-08-01", "2026-08-03"),
                source_mode="synthetic_fixture",
                source_locator="memory://crypto-gap",
                objects_root=root / "objects",
                register=EvidenceRegister(root / "register.jsonl"),
                quarantine=MarketDataQuarantineRegister(root / "quarantine.jsonl", clock=lambda: NOW),
                ledger=ledger,
            )
            self.assertEqual(result["status"], "QUARANTINED")
            self.assertIn("unexpected in-session gap after 2026-08-01T00:00:00Z: 1 bar(s)", result["reasons"])
            self.assertTrue((root / "objects" / result["source_object_path"]).is_file())

    def test_provider_key_is_required_and_never_appears_in_locator_or_error(self) -> None:
        policy = MarketDataPolicy.from_file(POLICY_PATH)
        request = approved_request()
        with self.assertRaises(MarketDataError) as missing:
            TwelveDataClient(policy, environ={}).fetch(request)
        self.assertIn("TWELVE_DATA_API_KEY", str(missing.exception))

        secret = "super-secret-test-key"
        captured: list[str] = []

        def transport(url: str, timeout: float) -> bytes:
            captured.append(url)
            return json.dumps(fixture_payload()).encode("utf-8")

        payload, locator, raw = TwelveDataClient(
            policy,
            environ={"TWELVE_DATA_API_KEY": secret},
            transport=transport,
        ).fetch(request)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(json.loads(raw), payload)
        self.assertIn(secret, captured[0])
        self.assertNotIn(secret, locator)
        self.assertNotIn("apikey", locator)

        def failed_transport(url: str, timeout: float) -> bytes:
            raise RuntimeError(url)

        with self.assertRaises(MarketDataError) as failed:
            TwelveDataClient(
                policy,
                environ={"TWELVE_DATA_API_KEY": secret},
                transport=failed_transport,
            ).fetch(request)
        self.assertNotIn(secret, str(failed.exception))

    def test_malformed_provider_bytes_are_retained_and_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = MarketDataPolicy.from_file(POLICY_PATH)
            malformed = b"\xff\x00not-json"
            client = TwelveDataClient(
                policy,
                environ={"TWELVE_DATA_API_KEY": "test-only-key"},
                transport=lambda url, timeout: malformed,
            )
            ledger = EvidenceLedger(root / "ledger.jsonl", clock=lambda: NOW)
            register = EvidenceRegister(root / "register.jsonl")
            quarantine = MarketDataQuarantineRegister(root / "quarantine.jsonl", clock=lambda: NOW)
            result = MarketDataIntake(policy, clock=lambda: NOW).ingest_live(
                approved_request(),
                client=client,
                objects_root=root / "objects",
                register=register,
                quarantine=quarantine,
                ledger=ledger,
            )
            self.assertEqual(result["status"], "QUARANTINED")
            retained = root / "objects" / result["source_object_path"]
            self.assertEqual(retained.read_bytes(), malformed)
            self.assertEqual(ledger.validate()["entries"], 1)
            state = validate_market_data_state(
                policy=policy,
                objects_root=root / "objects",
                register=register,
                quarantine=quarantine,
                ledger=ledger,
            )
            self.assertEqual(state["datasets"], 0)
            self.assertEqual(state["quarantine"]["entries"], 1)

    def test_retained_raw_and_normalized_tampering_fail_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = MarketFixture(Path(directory))
            accepted = fixture.ingest()
            normalized = fixture.objects / accepted["normalized_object_path"]
            normalized.write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(MarketDataError):
                fixture.validate()

        with tempfile.TemporaryDirectory() as directory:
            fixture = MarketFixture(Path(directory))
            accepted = fixture.ingest()
            raw = fixture.objects / accepted["raw_object_path"]
            raw.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(MarketDataError):
                fixture.validate()


if __name__ == "__main__":
    unittest.main()

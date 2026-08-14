import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from athena.evidence import EvidenceLedger
from athena.history import (
    HistoricalAcquisitionCoordinator,
    HistoricalAcquisitionError,
    HistoricalAcquisitionPolicy,
    HistoricalCheckpointRegister,
    HistoricalManifest,
    MarketDataQuotaLedger,
    validate_historical_policy,
    validate_historical_state,
)
from athena.market_data import MarketDataPolicy, MarketDataQuarantineRegister, TwelveDataClient
from athena.records import EvidenceRegister


ROOT = Path(__file__).resolve().parents[1]
MARKET_POLICY_PATH = ROOT / "config" / "market_data_policy.json"
HISTORY_POLICY_PATH = ROOT / "config" / "historical_acquisition_policy.json"
NOW = "2026-08-14T07:00:00+00:00"


class MutableClock:
    def __init__(self, value: str) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


def one_day_policy(root: Path) -> MarketDataPolicy:
    raw = json.loads(MARKET_POLICY_PATH.read_text(encoding="utf-8"))
    raw["request_window_days"]["1day"] = 1
    path = root / "market-policy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return MarketDataPolicy.from_file(path)


def daily_transport(
    calls: list[str],
    *,
    malformed_boundary: bool = False,
    credits_left: int | None = None,
):
    def transport(url: str, timeout: float):
        calls.append(url)
        query = parse_qs(urlparse(url).query)
        symbol = query["symbol"][0]
        interval = query["interval"][0]
        requested = query["start_date"][0]
        observed = "2020-01-20" if malformed_boundary else requested
        raw = json.dumps({
            "meta": {"symbol": symbol, "interval": interval, "exchange_timezone": "UTC"},
            "values": [{
                "datetime": observed,
                "open": "100",
                "high": "102",
                "low": "99",
                "close": "101",
                "volume": "5",
            }],
            "status": "ok",
        }).encode("utf-8")
        if credits_left is None:
            return raw
        return raw, {"api-credits-used": "1", "api-credits-left": str(credits_left)}

    return transport


class HistoryFixture:
    def __init__(
        self,
        root: Path,
        *,
        malformed_boundary: bool = False,
        credits_left: int | None = None,
    ) -> None:
        self.root = root
        self.clock = MutableClock(NOW)
        self.market_policy = one_day_policy(root)
        self.history_policy = HistoricalAcquisitionPolicy.from_file(HISTORY_POLICY_PATH)
        self.manifest = HistoricalManifest.create(
            self.market_policy,
            self.history_policy,
            requested_end="2020-01-10",
            start_override="2020-01-01",
            symbols=("BTC/USD",),
            intervals=("1day",),
            created_at=NOW,
        )
        relative = self.manifest.write(root / "control")
        self.manifest_path = root / "control" / relative
        self.calls: list[str] = []
        self.client = TwelveDataClient(
            self.market_policy,
            environ={"TWELVE_DATA_API_KEY": "synthetic-test-key"},
            transport=daily_transport(
                self.calls,
                malformed_boundary=malformed_boundary,
                credits_left=credits_left,
            ),
        )
        self.objects = root / "objects"
        self.reports = root / "control"
        self.register = EvidenceRegister(root / "register.jsonl")
        self.quarantine = MarketDataQuarantineRegister(root / "quarantine.jsonl", clock=self.clock)
        self.checkpoints = HistoricalCheckpointRegister(root / "checkpoints.jsonl", clock=self.clock)
        self.quota = MarketDataQuotaLedger(root / "quota.jsonl")
        self.ledger = EvidenceLedger(root / "ledger.jsonl", clock=self.clock)

    def run(self) -> dict:
        return HistoricalAcquisitionCoordinator(
            self.market_policy,
            self.history_policy,
            clock=self.clock,
        ).run(
            self.manifest,
            client=self.client,
            objects_root=self.objects,
            reports_root=self.reports,
            register=self.register,
            quarantine=self.quarantine,
            checkpoints=self.checkpoints,
            quota=self.quota,
            ledger=self.ledger,
        )

    def validate(self) -> dict:
        return validate_historical_state(
            manifest=self.manifest,
            market_policy=self.market_policy,
            history_policy=self.history_policy,
            objects_root=self.objects,
            reports_root=self.reports,
            register=self.register,
            quarantine=self.quarantine,
            checkpoints=self.checkpoints,
            quota=self.quota,
            ledger=self.ledger,
        )


class HistoricalAcquisitionTests(unittest.TestCase):
    def test_policy_is_closed_and_records_current_basic_limits(self) -> None:
        status = validate_historical_policy(HISTORY_POLICY_PATH, ROOT)
        self.assertTrue(status["valid"])
        self.assertEqual(status["policy_id"], "ATHENA-HIST-001")
        self.assertEqual(status["resolution_id"], "ATHENA-MDR-001")
        self.assertEqual(status["api_credits_per_minute"], 8)
        self.assertEqual(status["api_credits_per_day"], 800)
        self.assertEqual(status["maximum_credits_per_run"], 7)
        self.assertEqual(status["history_credits_per_minute"], 7)
        self.assertEqual(status["history_credits_per_day"], 720)
        self.assertEqual(status["automatic_retries"], 0)
        self.assertEqual(status["schemas"], 5)
        self.assertEqual(status["live_execution"], "prohibited")

    def test_history_quota_and_authority_cannot_be_silently_weakened(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json.loads(HISTORY_POLICY_PATH.read_text(encoding="utf-8"))
            raw["execution"]["maximum_credits_per_run"] = 8
            raw["execution"]["automatic_retries"] = 1
            raw["live_execution"] = "permitted"
            path = root / "weakened-history-policy.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(HistoricalAcquisitionError):
                HistoricalAcquisitionPolicy.from_file(path)

    def test_full_manifest_is_deterministic_partitioned_and_content_addressed(self) -> None:
        market_policy = MarketDataPolicy.from_file(MARKET_POLICY_PATH)
        history_policy = HistoricalAcquisitionPolicy.from_file(HISTORY_POLICY_PATH)
        manifest = HistoricalManifest.create(
            market_policy,
            history_policy,
            requested_end="2026-08-13",
            created_at=NOW,
        )
        self.assertEqual(manifest.scope, "FULL_APPROVED_HISTORY")
        self.assertEqual(manifest.symbols, tuple(item.symbol for item in market_policy.universe))
        self.assertEqual(manifest.intervals, market_policy.intervals)
        self.assertEqual(manifest.total_windows, manifest.planned_api_credits)
        self.assertGreater(manifest.total_windows, 2000)
        self.assertEqual(manifest.windows[0].symbol, "EUR/USD")
        self.assertEqual(manifest.windows[0].interval, "5min")
        self.assertEqual(manifest.windows[0].start_date, "2020-01-01")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = manifest.write(root)
            retained = HistoricalManifest.from_file(root / relative, market_policy, history_policy)
            self.assertEqual(retained.manifest_sha256, manifest.manifest_sha256)
            self.assertEqual(retained.total_windows, manifest.total_windows)

    def test_manifest_tampering_and_false_full_history_claim_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = HistoryFixture(root)
            raw = json.loads(fixture.manifest_path.read_text(encoding="utf-8"))
            raw["scope"] = "FULL_APPROVED_HISTORY"
            fixture.manifest_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(HistoricalAcquisitionError):
                HistoricalManifest.from_file(
                    fixture.manifest_path,
                    fixture.market_policy,
                    fixture.history_policy,
                )

    def test_quota_pause_preserves_account_reserve_and_resume_completes_next_minute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = HistoryFixture(Path(directory))
            first = fixture.run()
            self.assertEqual(first["status"], "PAUSED_QUOTA")
            self.assertEqual(first["completed_windows"], 7)
            self.assertEqual(first["pending_windows"], 3)
            self.assertEqual(first["credits_reserved_this_run"], 7)
            self.assertEqual(len(fixture.calls), 7)
            self.assertEqual(fixture.quota.validate(fixture.history_policy, fixture.ledger)["entries"], 7)

            fixture.clock.value = "2026-08-14T07:01:00+00:00"
            second = fixture.run()
            self.assertEqual(second["status"], "COMPLETE")
            self.assertEqual(second["completed_windows"], 10)
            self.assertEqual(second["pending_windows"], 0)
            self.assertTrue(second["scope_complete"])
            self.assertFalse(second["full_approved_history_complete"])
            self.assertFalse(second["ready_for_research"])
            self.assertEqual(second["credits_reserved_this_run"], 3)
            self.assertEqual(len(fixture.calls), 10)

            state = fixture.validate()
            self.assertTrue(state["valid"])
            self.assertEqual(state["planned_windows"], 10)
            self.assertEqual(state["terminal_windows"], 10)
            self.assertEqual(state["checkpoint"]["entries"], 10)
            self.assertEqual(state["quota"]["entries"], 10)
            self.assertEqual(state["reports_verified"], 2)
            self.assertEqual(state["live_execution"], "prohibited")

    def test_quarantine_blocks_resume_and_does_not_fabricate_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = HistoryFixture(Path(directory), malformed_boundary=True)
            first = fixture.run()
            self.assertEqual(first["status"], "BLOCKED_QUARANTINE")
            self.assertEqual(first["completed_windows"], 0)
            self.assertEqual(first["quarantined_windows"], 1)
            self.assertEqual(first["pending_windows"], 9)
            self.assertEqual(len(fixture.calls), 1)
            quarantine = fixture.quarantine.entries()[0]
            self.assertIn("bar timestamp exceeds requested_end: 2020-01-20T00:00:00Z", quarantine["reasons"])
            self.assertIn(
                "provider response does not cover requested_start within the permitted session boundary",
                quarantine["reasons"],
            )

            fixture.clock.value = "2026-08-14T07:01:00+00:00"
            second = fixture.run()
            self.assertEqual(second["status"], "BLOCKED_QUARANTINE")
            self.assertEqual(len(fixture.calls), 1)
            state = fixture.validate()
            self.assertTrue(state["valid"])
            self.assertEqual(state["terminal_windows"], 1)

    def test_orphaned_reservation_is_quarantined_without_retransmission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = HistoryFixture(Path(directory))
            window = fixture.manifest.windows[0]
            fixture.quota.reserve(
                policy=fixture.history_policy,
                manifest=fixture.manifest,
                window=window,
                reserved_at=fixture.clock(),
                ledger=fixture.ledger,
            )
            with self.assertRaises(HistoricalAcquisitionError):
                fixture.validate()

            result = fixture.run()
            self.assertEqual(result["status"], "BLOCKED_QUARANTINE")
            self.assertEqual(result["quarantined_windows"], 1)
            self.assertEqual(len(fixture.calls), 0)
            self.assertIn(
                "automatic retransmission is prohibited",
                fixture.quarantine.entries()[0]["reasons"][0],
            )
            self.assertTrue(fixture.validate()["valid"])

    def test_provider_credit_header_can_pause_below_local_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = HistoryFixture(Path(directory), credits_left=0)
            result = fixture.run()
            self.assertEqual(result["status"], "PAUSED_QUOTA")
            self.assertEqual(result["completed_windows"], 1)
            self.assertEqual(result["pending_windows"], 9)
            self.assertEqual(len(fixture.calls), 1)
            observations = [
                item for item in fixture.ledger.entries()
                if item["event_type"] == "market_data_quota_observed"
            ]
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0]["payload"]["api_credits_left"], 0)
            self.assertTrue(fixture.validate()["valid"])

    def test_checkpoint_and_report_tampering_fail_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = HistoryFixture(Path(directory))
            fixture.run()
            lines = fixture.checkpoints.path.read_text(encoding="utf-8").splitlines()
            entry = json.loads(lines[0])
            entry["status"] = "COMPLETED_WITHOUT_EVIDENCE"
            lines[0] = json.dumps(entry)
            fixture.checkpoints.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaises(HistoricalAcquisitionError):
                fixture.validate()

        with tempfile.TemporaryDirectory() as directory:
            fixture = HistoryFixture(Path(directory))
            result = fixture.run()
            report = fixture.reports / result["report_path"]
            raw = json.loads(report.read_text(encoding="utf-8"))
            raw["ready_for_research"] = True
            report.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(HistoricalAcquisitionError):
                fixture.validate()


if __name__ == "__main__":
    unittest.main()

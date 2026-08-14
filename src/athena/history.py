from __future__ import annotations

import json
import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from athena.evidence import GENESIS_HASH, EvidenceLedger, canonical_json, sha256_bytes, sha256_text
from athena.market_data import (
    MarketDataError,
    MarketDataIntake,
    MarketDataPolicy,
    MarketDataQuarantineRegister,
    MarketDataRequest,
    TwelveDataClient,
    validate_market_data_state,
)
from athena.models import utc_now
from athena.records import EvidenceRegister


HISTORY_POLICY_ID = "ATHENA-HIST-001"
MANIFEST_SCHEMA_VERSION = 1
TERMINAL_CHECKPOINTS = {"COMPLETED", "QUARANTINED"}


class HistoricalAcquisitionError(ValueError):
    """Raised when a historical acquisition control fails closed."""


class QuotaPause(HistoricalAcquisitionError):
    """Raised before a request when a recorded provider quota is exhausted."""


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HistoricalAcquisitionError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise HistoricalAcquisitionError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _write_immutable(root: str | Path, category: str, digest: str, value: dict[str, Any]) -> str:
    base = Path(root).resolve()
    relative = Path(category) / digest[:2] / f"{digest}.json"
    destination = (base / relative).resolve()
    if destination != base and base not in destination.parents:
        raise HistoricalAcquisitionError("historical object path escaped the controlled root")
    payload = _canonical_bytes(value)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_bytes() != payload:
        raise HistoricalAcquisitionError("immutable historical object conflicts with retained bytes")
    if not destination.exists():
        destination.write_bytes(payload)
    return relative.as_posix()


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class HistoricalAcquisitionPolicy:
    raw: dict[str, Any]
    policy_id: str
    resolution_id: str
    evidence_ids: tuple[str, ...]
    provider_limits: dict[str, Any]
    execution: dict[str, Any]
    persistence: dict[str, Any]

    @classmethod
    def from_file(cls, path: str | Path) -> HistoricalAcquisitionPolicy:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        result = cls(
            raw=raw,
            policy_id=str(raw.get("policy_id", "")),
            resolution_id=str(raw.get("resolution_id", "")),
            evidence_ids=tuple(str(item) for item in raw.get("evidence_ids", [])),
            provider_limits=dict(raw.get("provider_limits", {})),
            execution=dict(raw.get("execution", {})),
            persistence=dict(raw.get("persistence", {})),
        )
        failures = result.validate()
        if failures:
            raise HistoricalAcquisitionError("; ".join(failures))
        return result

    @property
    def configuration_sha256(self) -> str:
        return sha256_text(canonical_json(self.raw))

    def validate(self) -> list[str]:
        failures: list[str] = []
        if self.raw.get("schema_version") != 1:
            failures.append("historical policy schema_version must be 1")
        if self.policy_id != HISTORY_POLICY_ID or self.resolution_id != "ATHENA-MDR-001":
            failures.append("historical policy must remain subordinate to ATHENA-MDR-001")
        if self.raw.get("status") != "approved_implementation_control":
            failures.append("historical policy status is not approved_implementation_control")
        if self.raw.get("approved_at") != "2026-08-14" or self.raw.get("authority") != "Owner/CIO":
            failures.append("historical policy requires the recorded Owner/CIO authority")
        if not {"EF-002", "EF-006", "EF-010", "EF-014"}.issubset(self.evidence_ids):
            failures.append("historical policy must cite EF-002, EF-006, EF-010, and EF-014")
        if self.provider_limits != {
            "observed_at": "2026-08-14",
            "source_url": "https://twelvedata.com/pricing",
            "api_credits_per_minute": 8,
            "api_credits_per_day": 800,
            "time_series_credit_weight": 1,
        }:
            failures.append("historical provider limits differ from the recorded Basic-plan observation")
        if self.execution != {
            "maximum_credits_per_run": 7,
            "history_credits_per_minute": 7,
            "history_credits_per_day": 720,
            "request_order": "oldest_first",
            "quota_exhaustion_action": "pause_without_request",
            "quarantine_action": "stop_and_require_new_manifest",
            "automatic_retries": 0,
            "single_writer_required": True,
        }:
            failures.append("historical execution controls differ from the approved fail-closed contract")
        if self.persistence != {
            "manifest": "content_addressed_immutable_json",
            "checkpoint": "append_only_hash_chain",
            "quota_ledger": "append_only_hash_chain",
            "completeness_report": "content_addressed_immutable_json",
            "provider_data_in_public_repository": False,
        }:
            failures.append("historical persistence controls differ from the approved contract")
        if self.raw.get("decision_court_bypass") != "prohibited":
            failures.append("historical acquisition cannot bypass the Decision Court")
        if self.raw.get("live_execution") != "prohibited":
            failures.append("historical acquisition must preserve the live-execution prohibition")
        return failures


@dataclass(frozen=True)
class HistoricalWindow:
    request_id: str
    symbol: str
    interval: str
    start_date: str
    end_date: str
    api_credits: int = 1

    @classmethod
    def create(cls, symbol: str, interval: str, start_date: str, end_date: str) -> HistoricalWindow:
        request = MarketDataRequest(symbol, interval, start_date, end_date)
        request_id = sha256_text(canonical_json(request.to_dict()))
        return cls(request_id, symbol, interval, start_date, end_date, 1)

    def request(self) -> MarketDataRequest:
        return MarketDataRequest(self.symbol, self.interval, self.start_date, self.end_date)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "symbol": self.symbol,
            "interval": self.interval,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "api_credits": self.api_credits,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> HistoricalWindow:
        return cls(
            request_id=str(value.get("request_id", "")),
            symbol=str(value.get("symbol", "")),
            interval=str(value.get("interval", "")),
            start_date=str(value.get("start_date", "")),
            end_date=str(value.get("end_date", "")),
            api_credits=int(value.get("api_credits", 0)),
        )


def _approved_start(policy: MarketDataPolicy, symbol: str, interval: str) -> date:
    instrument = policy.instruments[symbol]
    boundary = policy.history["daily_start"] if interval == "1day" else policy.history["intraday_start"]
    return max(date.fromisoformat(instrument.history_start), date.fromisoformat(boundary))


def _partition_windows(
    market_policy: MarketDataPolicy,
    symbols: tuple[str, ...],
    intervals: tuple[str, ...],
    requested_end: date,
    start_override: date | None,
) -> tuple[HistoricalWindow, ...]:
    windows: list[HistoricalWindow] = []
    for symbol in symbols:
        for interval in intervals:
            start = _approved_start(market_policy, symbol, interval)
            if start_override is not None:
                start = max(start, start_override)
            if start > requested_end:
                raise HistoricalAcquisitionError(
                    f"{symbol} {interval}: selected start is after requested_end"
                )
            width = int(market_policy.request_window_days[interval])
            cursor = start
            while cursor <= requested_end:
                window_end = min(requested_end, cursor + timedelta(days=width - 1))
                window = HistoricalWindow.create(
                    symbol,
                    interval,
                    cursor.isoformat(),
                    window_end.isoformat(),
                )
                failures = window.request().validate(market_policy)
                if failures:
                    raise HistoricalAcquisitionError("; ".join(failures))
                windows.append(window)
                cursor = window_end + timedelta(days=1)
    return tuple(windows)


@dataclass(frozen=True)
class HistoricalManifest:
    schema_version: int
    manifest_id: str
    manifest_sha256: str
    created_at: str
    policy_id: str
    resolution_id: str
    market_data_policy_sha256: str
    history_policy_sha256: str
    scope: str
    requested_end: str
    symbols: tuple[str, ...]
    intervals: tuple[str, ...]
    windows: tuple[HistoricalWindow, ...]
    total_windows: int
    planned_api_credits: int
    decision_court_submission: str
    live_execution: str

    @classmethod
    def create(
        cls,
        market_policy: MarketDataPolicy,
        history_policy: HistoricalAcquisitionPolicy,
        *,
        requested_end: str,
        created_at: str,
        symbols: tuple[str, ...] | None = None,
        intervals: tuple[str, ...] | None = None,
        start_override: str | None = None,
    ) -> HistoricalManifest:
        _parse_timestamp(created_at, "created_at")
        try:
            end = date.fromisoformat(requested_end)
            selected_start = date.fromisoformat(start_override) if start_override else None
        except ValueError as error:
            raise HistoricalAcquisitionError("historical manifest dates must be ISO dates") from error
        selected_symbols = tuple(symbols or tuple(item.symbol for item in market_policy.universe))
        selected_intervals = tuple(intervals or market_policy.intervals)
        if len(selected_symbols) != len(set(selected_symbols)) or any(
            item not in market_policy.instruments for item in selected_symbols
        ):
            raise HistoricalAcquisitionError("historical manifest symbols must be unique and approved")
        if len(selected_intervals) != len(set(selected_intervals)) or any(
            item not in market_policy.intervals for item in selected_intervals
        ):
            raise HistoricalAcquisitionError("historical manifest intervals must be unique and approved")
        if not selected_symbols or not selected_intervals:
            raise HistoricalAcquisitionError("historical manifest scope cannot be empty")
        windows = _partition_windows(
            market_policy,
            selected_symbols,
            selected_intervals,
            end,
            selected_start,
        )
        full_scope = (
            selected_start is None
            and selected_symbols == tuple(item.symbol for item in market_policy.universe)
            and selected_intervals == market_policy.intervals
        )
        body = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "created_at": created_at,
            "policy_id": history_policy.policy_id,
            "resolution_id": market_policy.resolution_id,
            "market_data_policy_sha256": market_policy.configuration_sha256,
            "history_policy_sha256": history_policy.configuration_sha256,
            "scope": "FULL_APPROVED_HISTORY" if full_scope else "BOUNDED_APPROVED_SCOPE",
            "requested_end": requested_end,
            "symbols": list(selected_symbols),
            "intervals": list(selected_intervals),
            "windows": [item.to_dict() for item in windows],
            "total_windows": len(windows),
            "planned_api_credits": sum(item.api_credits for item in windows),
            "decision_court_submission": "NOT_AUTHORIZED_BY_DATA_ACQUISITION",
            "live_execution": "prohibited",
        }
        digest = sha256_text(canonical_json(body))
        result = cls(
            schema_version=MANIFEST_SCHEMA_VERSION,
            manifest_id=f"ATH-HIST-{digest[:24].upper()}",
            manifest_sha256=digest,
            created_at=created_at,
            policy_id=history_policy.policy_id,
            resolution_id=market_policy.resolution_id,
            market_data_policy_sha256=market_policy.configuration_sha256,
            history_policy_sha256=history_policy.configuration_sha256,
            scope=str(body["scope"]),
            requested_end=requested_end,
            symbols=selected_symbols,
            intervals=selected_intervals,
            windows=windows,
            total_windows=len(windows),
            planned_api_credits=sum(item.api_credits for item in windows),
            decision_court_submission="NOT_AUTHORIZED_BY_DATA_ACQUISITION",
            live_execution="prohibited",
        )
        result.validate(market_policy, history_policy)
        return result

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "policy_id": self.policy_id,
            "resolution_id": self.resolution_id,
            "market_data_policy_sha256": self.market_data_policy_sha256,
            "history_policy_sha256": self.history_policy_sha256,
            "scope": self.scope,
            "requested_end": self.requested_end,
            "symbols": list(self.symbols),
            "intervals": list(self.intervals),
            "windows": [item.to_dict() for item in self.windows],
            "total_windows": self.total_windows,
            "planned_api_credits": self.planned_api_credits,
            "decision_court_submission": self.decision_court_submission,
            "live_execution": self.live_execution,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._body(),
            "manifest_id": self.manifest_id,
            "manifest_sha256": self.manifest_sha256,
        }

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        market_policy: MarketDataPolicy,
        history_policy: HistoricalAcquisitionPolicy,
    ) -> HistoricalManifest:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        result = cls(
            schema_version=int(raw.get("schema_version", 0)),
            manifest_id=str(raw.get("manifest_id", "")),
            manifest_sha256=str(raw.get("manifest_sha256", "")),
            created_at=str(raw.get("created_at", "")),
            policy_id=str(raw.get("policy_id", "")),
            resolution_id=str(raw.get("resolution_id", "")),
            market_data_policy_sha256=str(raw.get("market_data_policy_sha256", "")),
            history_policy_sha256=str(raw.get("history_policy_sha256", "")),
            scope=str(raw.get("scope", "")),
            requested_end=str(raw.get("requested_end", "")),
            symbols=tuple(str(item) for item in raw.get("symbols", [])),
            intervals=tuple(str(item) for item in raw.get("intervals", [])),
            windows=tuple(HistoricalWindow.from_dict(item) for item in raw.get("windows", [])),
            total_windows=int(raw.get("total_windows", 0)),
            planned_api_credits=int(raw.get("planned_api_credits", 0)),
            decision_court_submission=str(raw.get("decision_court_submission", "")),
            live_execution=str(raw.get("live_execution", "")),
        )
        result.validate(market_policy, history_policy)
        return result

    def validate(
        self,
        market_policy: MarketDataPolicy,
        history_policy: HistoricalAcquisitionPolicy,
    ) -> None:
        failures: list[str] = []
        _parse_timestamp(self.created_at, "manifest.created_at")
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            failures.append("historical manifest schema_version must be 1")
        expected_digest = sha256_text(canonical_json(self._body()))
        if self.manifest_sha256 != expected_digest:
            failures.append("historical manifest digest mismatch")
        if self.manifest_id != f"ATH-HIST-{expected_digest[:24].upper()}":
            failures.append("historical manifest ID does not match its digest")
        if self.policy_id != history_policy.policy_id or self.resolution_id != market_policy.resolution_id:
            failures.append("historical manifest authority differs from active policy")
        if self.market_data_policy_sha256 != market_policy.configuration_sha256:
            failures.append("historical manifest market-data policy digest mismatch")
        if self.history_policy_sha256 != history_policy.configuration_sha256:
            failures.append("historical manifest acquisition policy digest mismatch")
        if self.scope not in {"FULL_APPROVED_HISTORY", "BOUNDED_APPROVED_SCOPE"}:
            failures.append("historical manifest scope is invalid")
        if not self.symbols or len(self.symbols) != len(set(self.symbols)) or any(
            item not in market_policy.instruments for item in self.symbols
        ):
            failures.append("historical manifest symbols must be unique and approved")
        if not self.intervals or len(self.intervals) != len(set(self.intervals)) or any(
            item not in market_policy.intervals for item in self.intervals
        ):
            failures.append("historical manifest intervals must be unique and approved")
        if self.scope == "FULL_APPROVED_HISTORY" and (
            self.symbols != tuple(item.symbol for item in market_policy.universe)
            or self.intervals != market_policy.intervals
        ):
            failures.append("full-history manifest does not cover the complete approved universe and intervals")
        if self.total_windows != len(self.windows) or self.planned_api_credits != sum(
            item.api_credits for item in self.windows
        ):
            failures.append("historical manifest totals do not reconcile")
        if len({item.request_id for item in self.windows}) != len(self.windows):
            failures.append("historical manifest contains duplicate request IDs")
        if all(item.symbol in self.symbols and item.interval in self.intervals for item in self.windows):
            expected_order = sorted(
                self.windows,
                key=lambda item: (
                    self.symbols.index(item.symbol),
                    self.intervals.index(item.interval),
                    item.start_date,
                ),
            ) if self.windows else []
            if list(self.windows) != expected_order:
                failures.append("historical manifest windows are not in deterministic oldest-first scope order")
        else:
            failures.append("historical manifest window falls outside its declared scope")
        for item in self.windows:
            if item.api_credits != history_policy.provider_limits["time_series_credit_weight"]:
                failures.append(f"{item.request_id}: API credit weight differs from policy")
            expected_id = sha256_text(canonical_json(item.request().to_dict()))
            if item.request_id != expected_id:
                failures.append(f"{item.request_id}: request digest mismatch")
            request_failures = item.request().validate(market_policy)
            if request_failures:
                failures.extend(f"{item.request_id}: {failure}" for failure in request_failures)
        for symbol in self.symbols:
            for interval in self.intervals:
                group = [item for item in self.windows if item.symbol == symbol and item.interval == interval]
                if not group:
                    failures.append(f"{symbol} {interval}: manifest scope has no request windows")
                    continue
                cursor = date.fromisoformat(group[0].start_date)
                if self.scope == "FULL_APPROVED_HISTORY" and cursor != _approved_start(
                    market_policy, symbol, interval
                ):
                    failures.append(f"{symbol} {interval}: full-history start boundary is incomplete")
                for item in group:
                    if date.fromisoformat(item.start_date) != cursor:
                        failures.append(f"{symbol} {interval}: request windows are not contiguous")
                        break
                    cursor = date.fromisoformat(item.end_date) + timedelta(days=1)
                if date.fromisoformat(group[-1].end_date) != date.fromisoformat(self.requested_end):
                    failures.append(f"{symbol} {interval}: request windows do not reach requested_end")
        if self.decision_court_submission != "NOT_AUTHORIZED_BY_DATA_ACQUISITION":
            failures.append("historical manifest attempts to authorize Decision Court submission")
        if self.live_execution != "prohibited":
            failures.append("historical manifest attempts to authorize live execution")
        if failures:
            raise HistoricalAcquisitionError("; ".join(failures))

    def write(self, root: str | Path) -> str:
        return _write_immutable(root, "manifests", self.manifest_sha256, self.to_dict())


class HistoricalCheckpointRegister:
    def __init__(self, path: str | Path, clock: Callable[[], str] = utc_now) -> None:
        self.path = Path(path)
        self.clock = clock

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise HistoricalAcquisitionError(
                    f"invalid historical checkpoint JSON at line {line_number}"
                ) from error
        return entries

    def append(
        self,
        *,
        manifest: HistoricalManifest,
        window: HistoricalWindow,
        status: str,
        result: dict[str, Any],
        ledger: EvidenceLedger,
    ) -> dict[str, Any]:
        self.validate(ledger)
        if status not in TERMINAL_CHECKPOINTS:
            raise HistoricalAcquisitionError("historical checkpoint status is not terminal")
        entries = self._entries()
        key = (manifest.manifest_sha256, window.request_id)
        if any((item.get("manifest_sha256"), item.get("request_id")) == key for item in entries):
            raise HistoricalAcquisitionError("historical request already has a terminal checkpoint")
        dataset_id = result.get("dataset_record_id")
        quarantine_hash = result.get("quarantine_hash")
        if status == "COMPLETED" and not dataset_id:
            raise HistoricalAcquisitionError("completed checkpoint requires a Dataset record ID")
        if status == "QUARANTINED" and not quarantine_hash:
            raise HistoricalAcquisitionError("quarantined checkpoint requires a quarantine hash")
        body = {
            "sequence": len(entries) + 1,
            "recorded_at": self.clock(),
            "manifest_sha256": manifest.manifest_sha256,
            "request_id": window.request_id,
            "symbol": window.symbol,
            "interval": window.interval,
            "status": status,
            "result_status": str(result.get("status", "")),
            "dataset_record_id": dataset_id,
            "quarantine_hash": quarantine_hash,
            "result_sha256": sha256_text(canonical_json(result)),
            "previous_hash": entries[-1]["hash"] if entries else GENESIS_HASH,
        }
        entry = {**body, "hash": sha256_text(canonical_json(body))}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(entry) + "\n")
        ledger_entry = ledger.append(
            "market_history_checkpointed",
            "athena.history",
            {
                "checkpoint_hash": entry["hash"],
                "manifest_sha256": manifest.manifest_sha256,
                "request_id": window.request_id,
                "status": status,
                "dataset_record_id": dataset_id,
                "quarantine_hash": quarantine_hash,
            },
        )
        return {**entry, "ledger_hash": ledger_entry["hash"]}

    def outcomes(self, manifest_sha256: str) -> dict[str, dict[str, Any]]:
        self.validate()
        return {
            str(item["request_id"]): item
            for item in self._entries()
            if item.get("manifest_sha256") == manifest_sha256
        }

    def validate(self, ledger: EvidenceLedger | None = None) -> dict[str, Any]:
        entries = self._entries()
        previous = GENESIS_HASH
        keys: set[tuple[str, str]] = set()
        for sequence, entry in enumerate(entries, 1):
            body = {key: value for key, value in entry.items() if key != "hash"}
            if entry.get("sequence") != sequence or entry.get("previous_hash") != previous:
                raise HistoricalAcquisitionError(f"broken historical checkpoint chain at entry {sequence}")
            if entry.get("hash") != sha256_text(canonical_json(body)):
                raise HistoricalAcquisitionError(f"invalid historical checkpoint hash at entry {sequence}")
            if entry.get("status") not in TERMINAL_CHECKPOINTS:
                raise HistoricalAcquisitionError(f"non-terminal historical checkpoint at entry {sequence}")
            if entry.get("status") == "COMPLETED" and (
                entry.get("result_status") not in {"ACCEPTED", "DUPLICATE"}
                or not entry.get("dataset_record_id")
                or entry.get("quarantine_hash") is not None
            ):
                raise HistoricalAcquisitionError(f"invalid completed checkpoint at entry {sequence}")
            if entry.get("status") == "QUARANTINED" and (
                entry.get("result_status") != "QUARANTINED"
                or not entry.get("quarantine_hash")
                or entry.get("dataset_record_id") is not None
            ):
                raise HistoricalAcquisitionError(f"invalid quarantined checkpoint at entry {sequence}")
            key = (str(entry.get("manifest_sha256")), str(entry.get("request_id")))
            if key in keys:
                raise HistoricalAcquisitionError("duplicate terminal historical checkpoint")
            keys.add(key)
            previous = str(entry["hash"])
        links = 0
        if ledger is not None:
            by_hash: dict[str, list[dict[str, Any]]] = {}
            for ledger_entry in ledger.entries():
                if ledger_entry.get("event_type") == "market_history_checkpointed":
                    digest = str(ledger_entry.get("payload", {}).get("checkpoint_hash", ""))
                    by_hash.setdefault(digest, []).append(ledger_entry)
            known = {str(item["hash"]) for item in entries}
            if set(by_hash) - known:
                raise HistoricalAcquisitionError("ledger references a missing historical checkpoint")
            for entry in entries:
                if len(by_hash.get(str(entry["hash"]), [])) != 1:
                    raise HistoricalAcquisitionError("each historical checkpoint requires one ledger link")
                links += 1
        return {"valid": True, "entries": len(entries), "ledger_links": links, "terminal_hash": previous}


class MarketDataQuotaLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise HistoricalAcquisitionError(f"invalid quota ledger JSON at line {line_number}") from error
        return entries

    def reserve(
        self,
        *,
        policy: HistoricalAcquisitionPolicy,
        manifest: HistoricalManifest,
        window: HistoricalWindow,
        reserved_at: str,
        ledger: EvidenceLedger,
    ) -> dict[str, Any]:
        self.validate(policy, ledger)
        timestamp = _parse_timestamp(reserved_at, "quota.reserved_at")
        minute_bucket = timestamp.strftime("%Y-%m-%dT%H:%MZ")
        day_bucket = timestamp.date().isoformat()
        entries = self._entries()
        minute_used = sum(int(item["credits"]) for item in entries if item.get("minute_bucket") == minute_bucket)
        day_used = sum(int(item["credits"]) for item in entries if item.get("day_bucket") == day_bucket)
        credits = window.api_credits
        if minute_used + credits > int(policy.execution["history_credits_per_minute"]):
            raise QuotaPause(f"minute quota exhausted for {minute_bucket}")
        if day_used + credits > int(policy.execution["history_credits_per_day"]):
            raise QuotaPause(f"daily quota exhausted for {day_bucket}")
        body = {
            "sequence": len(entries) + 1,
            "reserved_at": timestamp.isoformat().replace("+00:00", "Z"),
            "minute_bucket": minute_bucket,
            "day_bucket": day_bucket,
            "manifest_sha256": manifest.manifest_sha256,
            "request_id": window.request_id,
            "credits": credits,
            "previous_hash": entries[-1]["hash"] if entries else GENESIS_HASH,
        }
        entry = {**body, "hash": sha256_text(canonical_json(body))}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(entry) + "\n")
        ledger_entry = ledger.append(
            "market_data_quota_reserved",
            "athena.history",
            {
                "quota_hash": entry["hash"],
                "manifest_sha256": manifest.manifest_sha256,
                "request_id": window.request_id,
                "credits": credits,
                "minute_bucket": minute_bucket,
                "day_bucket": day_bucket,
            },
        )
        return {**entry, "ledger_hash": ledger_entry["hash"]}

    def reservations(self, manifest_sha256: str) -> dict[str, dict[str, Any]]:
        return {
            str(item["request_id"]): item
            for item in self._entries()
            if item.get("manifest_sha256") == manifest_sha256
        }

    def validate(
        self,
        policy: HistoricalAcquisitionPolicy,
        ledger: EvidenceLedger | None = None,
    ) -> dict[str, Any]:
        entries = self._entries()
        previous = GENESIS_HASH
        minute_totals: dict[str, int] = {}
        day_totals: dict[str, int] = {}
        request_keys: set[tuple[str, str]] = set()
        for sequence, entry in enumerate(entries, 1):
            body = {key: value for key, value in entry.items() if key != "hash"}
            if entry.get("sequence") != sequence or entry.get("previous_hash") != previous:
                raise HistoricalAcquisitionError(f"broken quota ledger chain at entry {sequence}")
            if entry.get("hash") != sha256_text(canonical_json(body)):
                raise HistoricalAcquisitionError(f"invalid quota ledger hash at entry {sequence}")
            _parse_timestamp(str(entry.get("reserved_at", "")), "quota.reserved_at")
            credits = int(entry.get("credits", 0))
            if credits != policy.provider_limits["time_series_credit_weight"]:
                raise HistoricalAcquisitionError(f"invalid quota credit weight at entry {sequence}")
            minute = str(entry.get("minute_bucket", ""))
            day = str(entry.get("day_bucket", ""))
            request_key = (str(entry.get("manifest_sha256", "")), str(entry.get("request_id", "")))
            if request_key in request_keys:
                raise HistoricalAcquisitionError("historical request has duplicate quota reservations")
            request_keys.add(request_key)
            minute_totals[minute] = minute_totals.get(minute, 0) + credits
            day_totals[day] = day_totals.get(day, 0) + credits
            if minute_totals[minute] > policy.execution["history_credits_per_minute"]:
                raise HistoricalAcquisitionError(f"recorded minute quota exceeded at {minute}")
            if day_totals[day] > policy.execution["history_credits_per_day"]:
                raise HistoricalAcquisitionError(f"recorded daily quota exceeded at {day}")
            previous = str(entry["hash"])
        links = 0
        if ledger is not None:
            by_hash: dict[str, list[dict[str, Any]]] = {}
            for ledger_entry in ledger.entries():
                if ledger_entry.get("event_type") == "market_data_quota_reserved":
                    digest = str(ledger_entry.get("payload", {}).get("quota_hash", ""))
                    by_hash.setdefault(digest, []).append(ledger_entry)
            known = {str(item["hash"]) for item in entries}
            if set(by_hash) - known:
                raise HistoricalAcquisitionError("ledger references a missing quota reservation")
            for entry in entries:
                if len(by_hash.get(str(entry["hash"]), [])) != 1:
                    raise HistoricalAcquisitionError("each quota reservation requires one ledger link")
                links += 1
        return {
            "valid": True,
            "entries": len(entries),
            "ledger_links": links,
            "terminal_hash": previous,
            "minute_buckets": len(minute_totals),
            "day_buckets": len(day_totals),
        }


def _completeness_body(
    manifest: HistoricalManifest,
    outcomes: dict[str, dict[str, Any]],
    checkpoint_status: dict[str, Any],
    quota_status: dict[str, Any],
    *,
    generated_at: str,
    pause_reason: str | None,
    market_data_state_validated: bool = False,
) -> dict[str, Any]:
    _parse_timestamp(generated_at, "report.generated_at")
    completed = {key for key, value in outcomes.items() if value.get("status") == "COMPLETED"}
    quarantined = {key for key, value in outcomes.items() if value.get("status") == "QUARANTINED"}
    all_ids = {item.request_id for item in manifest.windows}
    pending = all_ids - completed - quarantined
    if quarantined:
        status = "BLOCKED_QUARANTINE"
    elif not pending:
        status = "COMPLETE"
    elif pause_reason:
        status = "PAUSED_QUOTA"
    else:
        status = "IN_PROGRESS"
    scope_rows: list[dict[str, Any]] = []
    for symbol in manifest.symbols:
        for interval in manifest.intervals:
            ids = [item.request_id for item in manifest.windows if item.symbol == symbol and item.interval == interval]
            scope_rows.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "planned_windows": len(ids),
                    "completed_windows": len(set(ids) & completed),
                    "quarantined_windows": len(set(ids) & quarantined),
                    "pending_windows": len(set(ids) & pending),
                }
            )
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": manifest.manifest_sha256,
        "status": status,
        "scope": manifest.scope,
        "planned_windows": len(all_ids),
        "completed_windows": len(completed),
        "quarantined_windows": len(quarantined),
        "pending_windows": len(pending),
        "completion_percent": format(
            (Decimal(100) * Decimal(len(completed)) / Decimal(len(all_ids)))
            if all_ids else Decimal(0),
            ".6f",
        ),
        "scope_complete": status == "COMPLETE",
        "full_approved_history_complete": status == "COMPLETE" and manifest.scope == "FULL_APPROVED_HISTORY",
        "market_data_state_validated": market_data_state_validated,
        "ready_for_research": (
            status == "COMPLETE"
            and manifest.scope == "FULL_APPROVED_HISTORY"
            and market_data_state_validated
        ),
        "pause_reason": pause_reason,
        "checkpoint_terminal_hash": checkpoint_status["terminal_hash"],
        "quota_terminal_hash": quota_status["terminal_hash"],
        "scope_status": scope_rows,
        "decision_court_submission": "NOT_AUTHORIZED_BY_DATA_ACQUISITION",
        "live_execution": "prohibited",
    }


def _write_report(
    body: dict[str, Any],
    reports_root: str | Path,
    ledger: EvidenceLedger,
) -> dict[str, Any]:
    report_sha256 = sha256_text(canonical_json(body))
    report = {**body, "report_sha256": report_sha256}
    path = _write_immutable(reports_root, "reports", report_sha256, report)
    existing = [
        item
        for item in ledger.entries()
        if item.get("event_type") == "market_history_reported"
        and item.get("payload", {}).get("report_sha256") == report_sha256
    ]
    if len(existing) > 1:
        raise HistoricalAcquisitionError("completeness report has duplicate ledger events")
    event = existing[0] if existing else ledger.append(
        "market_history_reported",
        "athena.history",
        {
            "report_sha256": report_sha256,
            "report_path": path,
            "manifest_sha256": body["manifest_sha256"],
            "status": body["status"],
            "completed_windows": body["completed_windows"],
            "quarantined_windows": body["quarantined_windows"],
            "pending_windows": body["pending_windows"],
        },
    )
    return {**report, "report_path": path, "ledger_hash": event["hash"]}


class HistoricalAcquisitionCoordinator:
    def __init__(
        self,
        market_policy: MarketDataPolicy,
        history_policy: HistoricalAcquisitionPolicy,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.market_policy = market_policy
        self.history_policy = history_policy
        self.clock = clock

    def run(
        self,
        manifest: HistoricalManifest,
        *,
        client: TwelveDataClient,
        objects_root: str | Path,
        reports_root: str | Path,
        register: EvidenceRegister,
        quarantine: MarketDataQuarantineRegister,
        checkpoints: HistoricalCheckpointRegister,
        quota: MarketDataQuotaLedger,
        ledger: EvidenceLedger,
        max_credits: int | None = None,
        _lock_acquired: bool = False,
    ) -> dict[str, Any]:
        if not _lock_acquired:
            lock_path = quota.path.with_name(f"{quota.path.name}.acquisition.lock")
            with _exclusive_lock(lock_path):
                return self.run(
                    manifest,
                    client=client,
                    objects_root=objects_root,
                    reports_root=reports_root,
                    register=register,
                    quarantine=quarantine,
                    checkpoints=checkpoints,
                    quota=quota,
                    ledger=ledger,
                    max_credits=max_credits,
                    _lock_acquired=True,
                )
        manifest.validate(self.market_policy, self.history_policy)
        checkpoint_status = checkpoints.validate(ledger)
        quota_status = quota.validate(self.history_policy, ledger)
        outcomes = checkpoints.outcomes(manifest.manifest_sha256)
        unknown = set(outcomes) - {item.request_id for item in manifest.windows}
        if unknown:
            raise HistoricalAcquisitionError("checkpoint references a request outside the manifest")
        reservations = quota.reservations(manifest.manifest_sha256)
        orphaned = set(reservations) - set(outcomes)
        if orphaned:
            by_id = {item.request_id: item for item in manifest.windows}
            unknown_reservations = orphaned - set(by_id)
            if unknown_reservations:
                raise HistoricalAcquisitionError("quota reservation references a request outside the manifest")
            for request_id in sorted(orphaned):
                window = by_id[request_id]
                request_sha256 = sha256_text(canonical_json(window.request().to_dict()))
                adverse = quarantine.append(
                    request_sha256=request_sha256,
                    source_payload_sha256=None,
                    source_object_path=None,
                    symbol=window.symbol,
                    interval=window.interval,
                    reasons=[
                        "ambiguous prior quota reservation lacks a terminal checkpoint; automatic retransmission is prohibited"
                    ],
                    ledger=ledger,
                )
                result = {
                    "status": "QUARANTINED",
                    "resolution_id": self.market_policy.resolution_id,
                    "symbol": window.symbol,
                    "interval": window.interval,
                    "reasons": adverse["reasons"],
                    "quarantine_hash": adverse["hash"],
                    "source_object_path": None,
                    "ledger_hash": adverse["ledger_hash"],
                }
                checkpoint = checkpoints.append(
                    manifest=manifest,
                    window=window,
                    status="QUARANTINED",
                    result=result,
                    ledger=ledger,
                )
                outcomes[window.request_id] = checkpoint
            checkpoint_status = checkpoints.validate(ledger)
            quota_status = quota.validate(self.history_policy, ledger)
            return _write_report(
                _completeness_body(
                    manifest,
                    outcomes,
                    checkpoint_status,
                    quota_status,
                    generated_at=self.clock(),
                    pause_reason=None,
                ),
                reports_root,
                ledger,
            )
        if any(value.get("status") == "QUARANTINED" for value in outcomes.values()):
            report = _write_report(
                _completeness_body(
                    manifest,
                    outcomes,
                    checkpoint_status,
                    quota_status,
                    generated_at=self.clock(),
                    pause_reason=None,
                ),
                reports_root,
                ledger,
            )
            return report
        run_limit = int(self.history_policy.execution["maximum_credits_per_run"])
        if max_credits is not None:
            if max_credits < 1 or max_credits > run_limit:
                raise HistoricalAcquisitionError(f"max_credits must be between 1 and {run_limit}")
            run_limit = max_credits
        credits_used = 0
        pause_reason: str | None = None
        intake = MarketDataIntake(self.market_policy, clock=self.clock)
        for window in manifest.windows:
            if window.request_id in outcomes:
                continue
            if credits_used + window.api_credits > run_limit:
                pause_reason = f"per-run credit budget exhausted at {run_limit}"
                break
            reserved_at = self.clock()
            try:
                quota.reserve(
                    policy=self.history_policy,
                    manifest=manifest,
                    window=window,
                    reserved_at=reserved_at,
                    ledger=ledger,
                )
            except QuotaPause as error:
                pause_reason = str(error)
                break
            credits_used += window.api_credits
            try:
                result = intake.ingest_live(
                    window.request(),
                    client=client,
                    objects_root=objects_root,
                    register=register,
                    quarantine=quarantine,
                    ledger=ledger,
                )
            except MarketDataError as error:
                request_sha256 = sha256_text(canonical_json(window.request().to_dict()))
                adverse = quarantine.append(
                    request_sha256=request_sha256,
                    source_payload_sha256=None,
                    source_object_path=None,
                    symbol=window.symbol,
                    interval=window.interval,
                    reasons=[str(error)],
                    ledger=ledger,
                )
                result = {
                    "status": "QUARANTINED",
                    "resolution_id": self.market_policy.resolution_id,
                    "symbol": window.symbol,
                    "interval": window.interval,
                    "reasons": adverse["reasons"],
                    "quarantine_hash": adverse["hash"],
                    "source_object_path": None,
                    "ledger_hash": adverse["ledger_hash"],
                }
            terminal = "COMPLETED" if result["status"] in {"ACCEPTED", "DUPLICATE"} else "QUARANTINED"
            credit_observation = dict(client.last_credit_observation)
            if credit_observation:
                ledger.append(
                    "market_data_quota_observed",
                    "athena.history",
                    {
                        "manifest_sha256": manifest.manifest_sha256,
                        "request_id": window.request_id,
                        **credit_observation,
                    },
                )
            checkpoint = checkpoints.append(
                manifest=manifest,
                window=window,
                status=terminal,
                result=result,
                ledger=ledger,
            )
            outcomes[window.request_id] = checkpoint
            if terminal == "QUARANTINED":
                break
            if credit_observation.get("api_credits_left", 1) < window.api_credits:
                pause_reason = "provider reports insufficient API credits for another request"
                break
        checkpoint_status = checkpoints.validate(ledger)
        quota_status = quota.validate(self.history_policy, ledger)
        market_data_state_validated = False
        if len(outcomes) == manifest.total_windows and all(
            value.get("status") == "COMPLETED" for value in outcomes.values()
        ):
            validate_market_data_state(
                policy=self.market_policy,
                objects_root=objects_root,
                register=register,
                quarantine=quarantine,
                ledger=ledger,
            )
            market_data_state_validated = True
        report = _write_report(
            _completeness_body(
                manifest,
                outcomes,
                checkpoint_status,
                quota_status,
                generated_at=self.clock(),
                pause_reason=pause_reason,
                market_data_state_validated=market_data_state_validated,
            ),
            reports_root,
            ledger,
        )
        return {**report, "credits_reserved_this_run": credits_used}


def validate_historical_state(
    *,
    manifest: HistoricalManifest,
    market_policy: MarketDataPolicy,
    history_policy: HistoricalAcquisitionPolicy,
    objects_root: str | Path,
    reports_root: str | Path,
    register: EvidenceRegister,
    quarantine: MarketDataQuarantineRegister,
    checkpoints: HistoricalCheckpointRegister,
    quota: MarketDataQuotaLedger,
    ledger: EvidenceLedger,
) -> dict[str, Any]:
    manifest.validate(market_policy, history_policy)
    checkpoint_status = checkpoints.validate(ledger)
    quota_status = quota.validate(history_policy, ledger)
    outcomes = checkpoints.outcomes(manifest.manifest_sha256)
    known_requests = {item.request_id for item in manifest.windows}
    if set(outcomes) - known_requests:
        raise HistoricalAcquisitionError("historical checkpoint scope differs from the manifest")
    reservations = quota.reservations(manifest.manifest_sha256)
    if set(reservations) - known_requests:
        raise HistoricalAcquisitionError("historical quota scope differs from the manifest")
    if set(reservations) != set(outcomes):
        raise HistoricalAcquisitionError("historical reservations and terminal checkpoints do not reconcile")
    market_data_status = validate_market_data_state(
        policy=market_policy,
        objects_root=objects_root,
        register=register,
        quarantine=quarantine,
        ledger=ledger,
    )
    report_events = [
        item
        for item in ledger.entries()
        if item.get("event_type") == "market_history_reported"
        and item.get("payload", {}).get("manifest_sha256") == manifest.manifest_sha256
    ]
    reports = Path(reports_root)
    observed_requests: set[str] = set()
    for event in ledger.entries():
        if event.get("event_type") != "market_data_quota_observed":
            continue
        payload = event.get("payload", {})
        if payload.get("manifest_sha256") != manifest.manifest_sha256:
            continue
        if payload.get("request_id") not in known_requests:
            raise HistoricalAcquisitionError("provider quota observation falls outside the manifest")
        request_id = str(payload.get("request_id"))
        if request_id not in reservations or request_id not in outcomes:
            raise HistoricalAcquisitionError("provider quota observation lacks reservation or terminal checkpoint")
        if request_id in observed_requests:
            raise HistoricalAcquisitionError("provider request has duplicate quota observations")
        observed_requests.add(request_id)
        for field in ("api_credits_used", "api_credits_left"):
            if field in payload and (not isinstance(payload[field], int) or payload[field] < 0):
                raise HistoricalAcquisitionError("provider quota observation is invalid")
    verified_reports = 0
    retained_reports: list[dict[str, Any]] = []
    for event in report_events:
        payload = event.get("payload", {})
        relative = str(payload.get("report_path", ""))
        candidate = (reports.resolve() / relative).resolve()
        if candidate != reports.resolve() and reports.resolve() not in candidate.parents:
            raise HistoricalAcquisitionError("completeness report escaped the controlled root")
        if not candidate.is_file():
            raise HistoricalAcquisitionError("completeness report ledger event lacks retained bytes")
        try:
            report = json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise HistoricalAcquisitionError("retained completeness report is invalid JSON") from error
        digest = str(report.get("report_sha256", ""))
        body = {key: value for key, value in report.items() if key != "report_sha256"}
        if digest != sha256_text(canonical_json(body)) or digest != payload.get("report_sha256"):
            raise HistoricalAcquisitionError("completeness report digest mismatch")
        if report.get("manifest_sha256") != manifest.manifest_sha256:
            raise HistoricalAcquisitionError("completeness report references another manifest")
        if report.get("decision_court_submission") != "NOT_AUTHORIZED_BY_DATA_ACQUISITION":
            raise HistoricalAcquisitionError("completeness report attempts to bypass the Decision Court")
        if report.get("live_execution") != "prohibited":
            raise HistoricalAcquisitionError("completeness report weakens the live-execution prohibition")
        if report.get("ready_for_research") and not (
            report.get("status") == "COMPLETE"
            and report.get("scope") == "FULL_APPROVED_HISTORY"
            and report.get("full_approved_history_complete") is True
            and report.get("market_data_state_validated") is True
        ):
            raise HistoricalAcquisitionError("completeness report overstates research readiness")
        retained_reports.append(report)
        verified_reports += 1
    if retained_reports:
        latest = retained_reports[-1]
        completed = sum(1 for item in outcomes.values() if item.get("status") == "COMPLETED")
        quarantined_count = sum(
            1 for item in outcomes.values() if item.get("status") == "QUARANTINED"
        )
        pending = manifest.total_windows - completed - quarantined_count
        if latest.get("checkpoint_terminal_hash") != checkpoint_status["terminal_hash"]:
            raise HistoricalAcquisitionError("latest completeness report checkpoint hash is stale")
        if latest.get("quota_terminal_hash") != quota_status["terminal_hash"]:
            raise HistoricalAcquisitionError("latest completeness report quota hash is stale")
        if (
            latest.get("completed_windows") != completed
            or latest.get("quarantined_windows") != quarantined_count
            or latest.get("pending_windows") != pending
        ):
            raise HistoricalAcquisitionError("latest completeness report counts do not reconcile")
    return {
        "valid": True,
        "policy_id": history_policy.policy_id,
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": manifest.manifest_sha256,
        "planned_windows": manifest.total_windows,
        "terminal_windows": len(outcomes),
        "reports_verified": verified_reports,
        "checkpoint": checkpoint_status,
        "quota": quota_status,
        "market_data": market_data_status,
        "decision_court_bypass": "prohibited",
        "live_execution": "prohibited",
    }


def validate_historical_policy(
    policy_path: str | Path,
    repository_root: str | Path = ".",
) -> dict[str, Any]:
    policy = HistoricalAcquisitionPolicy.from_file(policy_path)
    root = Path(repository_root)
    for relative in (
        "schemas/historical-acquisition-policy.schema.json",
        "schemas/historical-acquisition-manifest.schema.json",
        "schemas/historical-checkpoint.schema.json",
        "schemas/market-data-quota-reservation.schema.json",
        "schemas/historical-completeness-report.schema.json",
    ):
        path = root / relative
        if not path.is_file():
            raise HistoricalAcquisitionError(f"historical schema is missing: {relative}")
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise HistoricalAcquisitionError(f"historical schema is invalid JSON: {relative}") from error
        if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
            raise HistoricalAcquisitionError(f"historical schema must be a closed object: {relative}")
    return {
        "valid": True,
        "policy_id": policy.policy_id,
        "resolution_id": policy.resolution_id,
        "configuration_sha256": policy.configuration_sha256,
        "api_credits_per_minute": policy.provider_limits["api_credits_per_minute"],
        "api_credits_per_day": policy.provider_limits["api_credits_per_day"],
        "maximum_credits_per_run": policy.execution["maximum_credits_per_run"],
        "history_credits_per_minute": policy.execution["history_credits_per_minute"],
        "history_credits_per_day": policy.execution["history_credits_per_day"],
        "automatic_retries": policy.execution["automatic_retries"],
        "schemas": 5,
        "live_execution": "prohibited",
    }

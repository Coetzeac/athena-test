from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from athena.evidence import EvidenceLedger, canonical_json, sha256_bytes, sha256_text
from athena.models import utc_now
from athena.records import (
    DatasetFingerprint,
    EvidenceRegister,
    KnowledgeRecord,
    Provenance,
    RecordType,
)


APPROVED_SYMBOLS = {
    "EUR/USD": "forex",
    "GBP/USD": "forex",
    "USD/JPY": "forex",
    "SPY": "etf",
    "QQQ": "etf",
    "GLD": "etf",
    "BTC/USD": "crypto",
    "ETH/USD": "crypto",
}
APPROVED_INTERVALS = ("5min", "15min", "1h", "4h", "1day")
INTERVAL_SECONDS = {
    "5min": 5 * 60,
    "15min": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1day": 24 * 60 * 60,
}
BAR_FIELDS = ("timestamp_utc", "open", "high", "low", "close", "volume")
PROVIDER_METADATA_FIELDS = {
    "symbol",
    "interval",
    "currency",
    "currency_base",
    "currency_quote",
    "exchange",
    "mic_code",
    "exchange_timezone",
    "type",
}


class MarketDataError(ValueError):
    """Raised when market data cannot enter ATHENA safely."""


@dataclass(frozen=True)
class ApprovedInstrument:
    symbol: str
    asset_class: str
    history_start: str


@dataclass(frozen=True)
class MarketDataPolicy:
    raw: dict[str, Any]
    resolution_id: str
    status: str
    approved_at: str
    authority: str
    evidence_ids: tuple[str, ...]
    provider: dict[str, Any]
    universe: tuple[ApprovedInstrument, ...]
    history: dict[str, Any]
    intervals: tuple[str, ...]
    request_window_days: dict[str, int]
    storage: dict[str, Any]
    required_dataset_fields: tuple[str, ...]
    required_bar_fields: tuple[str, ...]
    quality: dict[str, Any]
    quarantine_disposition: str

    @classmethod
    def from_file(cls, path: str | Path) -> MarketDataPolicy:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        result = cls(
            raw=raw,
            resolution_id=str(raw.get("resolution_id", "")),
            status=str(raw.get("status", "")),
            approved_at=str(raw.get("approved_at", "")),
            authority=str(raw.get("authority", "")),
            evidence_ids=tuple(str(item) for item in raw.get("evidence_ids", [])),
            provider=dict(raw.get("provider", {})),
            universe=tuple(
                ApprovedInstrument(
                    symbol=str(item.get("symbol", "")),
                    asset_class=str(item.get("asset_class", "")),
                    history_start=str(item.get("history_start", "")),
                )
                for item in raw.get("universe", [])
            ),
            history=dict(raw.get("history", {})),
            intervals=tuple(str(item) for item in raw.get("intervals", [])),
            request_window_days={
                str(key): int(value) for key, value in raw.get("request_window_days", {}).items()
            },
            storage=dict(raw.get("storage", {})),
            required_dataset_fields=tuple(str(item) for item in raw.get("required_dataset_fields", [])),
            required_bar_fields=tuple(str(item) for item in raw.get("required_bar_fields", [])),
            quality=dict(raw.get("quality", {})),
            quarantine_disposition=str(raw.get("quarantine_disposition", "")),
        )
        failures = result.validate()
        if failures:
            raise MarketDataError("; ".join(failures))
        return result

    @property
    def configuration_sha256(self) -> str:
        return sha256_text(canonical_json(self.raw))

    @property
    def instruments(self) -> dict[str, ApprovedInstrument]:
        return {item.symbol: item for item in self.universe}

    def validate(self) -> list[str]:
        failures: list[str] = []
        if self.raw.get("schema_version") != 1:
            failures.append("market-data policy schema_version must be 1")
        if self.resolution_id != "ATHENA-MDR-001":
            failures.append("market-data resolution must be ATHENA-MDR-001")
        if self.status != "approved_internal_research":
            failures.append("market-data policy must remain approved for internal research only")
        if self.approved_at != "2026-08-14" or self.authority != "Owner/CIO":
            failures.append("market-data policy requires the recorded Owner/CIO approval")
        if not {"EF-002", "EF-006", "EF-010", "EF-014"}.issubset(set(self.evidence_ids)):
            failures.append("market-data policy must cite EF-002, EF-006, EF-010, and EF-014")

        provider = self.provider
        expected_provider = {
            "name": "Twelve Data",
            "api_base_url": "https://api.twelvedata.com",
            "time_series_path": "/time_series",
            "api_key_environment_variable": "TWELVE_DATA_API_KEY",
            "plan": "Basic",
            "monthly_budget_usd": 0,
            "conditional_upgrade_cap_usd": 79,
            "usage_rights": "personal-internal-non-commercial",
            "redistribution_permitted": False,
        }
        for key, expected in expected_provider.items():
            if provider.get(key) != expected:
                failures.append(f"market-data provider.{key} must remain {expected!r}")
        if not str(provider.get("upgrade_rule", "")).strip():
            failures.append("market-data provider upgrade_rule is required")

        declared_symbols = {item.symbol: item.asset_class for item in self.universe}
        if declared_symbols != APPROVED_SYMBOLS or len(self.universe) != len(APPROVED_SYMBOLS):
            failures.append("market-data universe differs from the eight approved instruments")
        for item in self.universe:
            try:
                date.fromisoformat(item.history_start)
            except ValueError:
                failures.append(f"{item.symbol}: history_start must be an ISO date")
        if self.history != {
            "daily_start": "2010-01-01",
            "intraday_start": "2020-01-01",
            "end": "current_date",
            "instrument_inception_if_later": True,
        }:
            failures.append("market-data history boundary differs from Resolution 1")
        if self.intervals != APPROVED_INTERVALS:
            failures.append(f"market-data intervals must remain {list(APPROVED_INTERVALS)}")
        if set(self.request_window_days) != set(APPROVED_INTERVALS):
            failures.append("market-data request windows must cover every approved interval")
        if any(value < 1 for value in self.request_window_days.values()):
            failures.append("market-data request windows must be positive")

        required_storage = {
            "timestamp_timezone": "UTC",
            "preserve_provider_metadata": True,
            "retain_raw_response": True,
            "retain_normalized_bars": True,
            "content_addressed": True,
            "public_repository_data_permitted": False,
        }
        if self.storage != required_storage:
            failures.append("market-data storage controls differ from Resolution 1")
        if self.required_bar_fields != BAR_FIELDS:
            failures.append(f"market-data bar fields must remain {list(BAR_FIELDS)}")
        required_dataset_fields = {
            "provider",
            "symbol",
            "asset_class",
            "interval",
            "requested_start",
            "requested_end",
            "acquired_at",
            "row_count",
            "missing_bar_count",
            "configuration_sha256",
            "content_sha256",
        }
        if set(self.required_dataset_fields) != required_dataset_fields:
            failures.append("market-data required dataset fields are incomplete")
        expected_quality = {
            "maximum_rows_per_response": 5000,
            "require_strictly_increasing_timestamps": True,
            "require_unique_timestamps": True,
            "require_positive_ohlc": True,
            "require_ohlc_range_consistency": True,
            "unexpected_in_session_gap_action": "quarantine",
            "provider_output_cap_action": "quarantine_and_partition",
            "fabricate_missing_bars": False,
        }
        if self.quality != expected_quality:
            failures.append("market-data quality controls differ from Resolution 1")
        if self.quarantine_disposition != "QUARANTINED_NO_RESEARCH_OR_COURT_USE":
            failures.append("market-data quarantine disposition is not fail-closed")
        if self.raw.get("decision_court_bypass") != "prohibited":
            failures.append("market-data policy cannot bypass the Decision Court")
        if self.raw.get("live_execution") != "prohibited":
            failures.append("market-data policy must preserve the live-execution prohibition")
        return failures


@dataclass(frozen=True)
class MarketDataRequest:
    symbol: str
    interval: str
    start_date: str
    end_date: str

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "timezone": "UTC",
            "order": "ASC",
        }

    def validate(self, policy: MarketDataPolicy) -> list[str]:
        failures: list[str] = []
        instrument = policy.instruments.get(self.symbol)
        if instrument is None:
            failures.append(f"symbol is outside the approved universe: {self.symbol}")
        if self.interval not in policy.intervals:
            failures.append(f"interval is outside the approved set: {self.interval}")
        try:
            start = date.fromisoformat(self.start_date)
        except ValueError:
            start = None
            failures.append("start_date must be an ISO date")
        try:
            end = date.fromisoformat(self.end_date)
        except ValueError:
            end = None
            failures.append("end_date must be an ISO date")
        if start is not None and end is not None:
            if start > end:
                failures.append("start_date cannot be after end_date")
            if instrument is not None:
                approved_start = max(
                    date.fromisoformat(instrument.history_start),
                    date.fromisoformat(
                        policy.history["daily_start"]
                        if self.interval == "1day"
                        else policy.history["intraday_start"]
                    ),
                )
                if start < approved_start:
                    failures.append(
                        f"start_date precedes the approved {self.interval} history boundary {approved_start.isoformat()}"
                    )
            if self.interval in policy.request_window_days:
                maximum_days = policy.request_window_days[self.interval]
                if (end - start).days + 1 > maximum_days:
                    failures.append(
                        f"request exceeds the {maximum_days}-day controlled window for {self.interval}; partition it"
                    )
        return failures


class TwelveDataClient:
    def __init__(
        self,
        policy: MarketDataPolicy,
        *,
        environ: dict[str, str] | None = None,
        transport: Callable[[str, float], bytes] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.policy = policy
        self.environ = os.environ if environ is None else environ
        self.transport = transport or self._default_transport
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _default_transport(url: str, timeout: float) -> bytes:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "ATHENA/0.1"})
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as error:
            raise MarketDataError(f"Twelve Data request failed with HTTP {error.code}") from error
        except URLError as error:
            raise MarketDataError("Twelve Data request failed at the network boundary") from error

    def fetch(self, request: MarketDataRequest) -> tuple[dict[str, Any], str, bytes]:
        failures = request.validate(self.policy)
        if failures:
            raise MarketDataError("; ".join(failures))
        secret_name = str(self.policy.provider["api_key_environment_variable"])
        api_key = self.environ.get(secret_name, "").strip()
        if not api_key:
            raise MarketDataError(f"required repository secret is unavailable: {secret_name}")
        public_parameters = {
            **request.to_dict(),
            "format": "JSON",
            "outputsize": int(self.policy.quality["maximum_rows_per_response"]),
        }
        endpoint = f"{self.policy.provider['api_base_url']}{self.policy.provider['time_series_path']}"
        source_locator = f"{endpoint}?{urlencode(public_parameters)}"
        url = f"{source_locator}&{urlencode({'apikey': api_key})}"
        try:
            raw = self.transport(url, self.timeout_seconds)
        except MarketDataError:
            raise
        except Exception as error:
            raise MarketDataError("Twelve Data transport failed without exposing request credentials") from error
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = {}
        payload = decoded if isinstance(decoded, dict) else {}
        return payload, source_locator, raw


class MarketDataQuarantineRegister:
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
                raise MarketDataError(f"invalid market-data quarantine JSON at line {line_number}") from error
        return entries

    def entries(self) -> tuple[dict[str, Any], ...]:
        self.validate()
        return tuple(self._entries())

    def append(
        self,
        *,
        request_sha256: str,
        source_payload_sha256: str | None,
        source_object_path: str | None,
        symbol: str,
        interval: str,
        reasons: list[str],
        ledger: EvidenceLedger,
    ) -> dict[str, Any]:
        unique_reasons = sorted(set(item for item in reasons if item))
        if not unique_reasons:
            raise MarketDataError("market-data quarantine reasons are required")
        entries = self._entries()
        body = {
            "sequence": len(entries) + 1,
            "recorded_at": self.clock(),
            "request_sha256": request_sha256,
            "source_payload_sha256": source_payload_sha256,
            "source_object_path": source_object_path,
            "symbol": symbol,
            "interval": interval,
            "reasons": unique_reasons,
            "disposition": "QUARANTINED_NO_RESEARCH_OR_COURT_USE",
        }
        entry = {**body, "hash": sha256_text(canonical_json(body))}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(entry) + "\n")
        ledger_entry = ledger.append(
            "market_data_quarantined",
            "athena.market_data",
            {
                "quarantine_hash": entry["hash"],
                "request_sha256": request_sha256,
                "source_payload_sha256": source_payload_sha256,
                "source_object_path": source_object_path,
                "symbol": symbol,
                "interval": interval,
                "reasons": unique_reasons,
            },
        )
        return {**entry, "ledger_hash": ledger_entry["hash"]}

    def validate(self, ledger: EvidenceLedger | None = None) -> dict[str, Any]:
        entries = self._entries()
        for expected_sequence, entry in enumerate(entries, 1):
            body = {key: value for key, value in entry.items() if key != "hash"}
            if entry.get("sequence") != expected_sequence:
                raise MarketDataError(f"invalid market-data quarantine sequence at entry {expected_sequence}")
            if entry.get("hash") != sha256_text(canonical_json(body)):
                raise MarketDataError(f"invalid market-data quarantine hash at entry {expected_sequence}")
            if entry.get("disposition") != "QUARANTINED_NO_RESEARCH_OR_COURT_USE":
                raise MarketDataError(f"invalid market-data quarantine disposition at entry {expected_sequence}")
        links = 0
        if ledger is not None:
            by_hash: dict[str, list[dict[str, Any]]] = {}
            for ledger_entry in ledger.entries():
                if ledger_entry.get("event_type") != "market_data_quarantined":
                    continue
                quarantine_hash = str(ledger_entry.get("payload", {}).get("quarantine_hash", ""))
                by_hash.setdefault(quarantine_hash, []).append(ledger_entry)
            known_hashes = {str(entry["hash"]) for entry in entries}
            for entry in entries:
                matching = by_hash.get(str(entry["hash"]), [])
                if len(matching) != 1:
                    raise MarketDataError("each market-data quarantine entry requires one ledger link")
                links += 1
            if set(by_hash) - known_hashes:
                raise MarketDataError("ledger references a missing market-data quarantine entry")
        return {"valid": True, "entries": len(entries), "ledger_links": links}


@dataclass(frozen=True)
class NormalizedMarketData:
    provider_metadata: dict[str, Any]
    bars: tuple[dict[str, Any], ...]
    allowed_inter_session_gaps: int
    missing_bar_count: int


def _decimal_text(value: Any, field_name: str, *, positive: bool) -> str:
    if value in (None, ""):
        raise MarketDataError(f"{field_name} is required")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise MarketDataError(f"{field_name} must be a finite decimal") from error
    if not number.is_finite():
        raise MarketDataError(f"{field_name} must be a finite decimal")
    if positive and number <= 0:
        raise MarketDataError(f"{field_name} must be positive")
    if not positive and number < 0:
        raise MarketDataError(f"{field_name} cannot be negative")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _timestamp_utc(value: Any) -> tuple[str, datetime]:
    text = str(value).strip()
    if not text:
        raise MarketDataError("bar.datetime is required")
    try:
        if len(text) == 10:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time(), tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
    except ValueError as error:
        raise MarketDataError("bar.datetime must be an ISO date or timestamp") from error
    rendered = parsed.isoformat().replace("+00:00", "Z")
    return rendered, parsed


def _normalised_jsonl(bars: tuple[dict[str, Any], ...]) -> bytes:
    return ("".join(canonical_json(item) + "\n" for item in bars)).encode("utf-8")


def normalize_provider_payload(
    payload: dict[str, Any],
    request: MarketDataRequest,
    policy: MarketDataPolicy,
) -> tuple[NormalizedMarketData | None, list[str]]:
    failures = request.validate(policy)
    if payload.get("status") == "error" or payload.get("code"):
        failures.append(f"provider rejected request: {payload.get('code', 'error')}")
    meta = payload.get("meta")
    values = payload.get("values")
    if not isinstance(meta, dict):
        failures.append("provider response meta must be an object")
        meta = {}
    if not isinstance(values, list) or not values:
        failures.append("provider response values must be a non-empty array")
        values = []
    if str(meta.get("symbol", "")) != request.symbol:
        failures.append("provider response symbol conflicts with the approved request")
    if str(meta.get("interval", "")) != request.interval:
        failures.append("provider response interval conflicts with the approved request")
    if len(values) >= int(policy.quality["maximum_rows_per_response"]):
        failures.append("provider response reached the output cap; quarantine and partition the request")

    instrument = policy.instruments.get(request.symbol)
    asset_class = instrument.asset_class if instrument else "unknown"
    provider_metadata = {
        key: value
        for key, value in sorted(meta.items())
        if key in PROVIDER_METADATA_FIELDS and isinstance(value, (str, int, float, bool, type(None)))
    }
    bars: list[dict[str, Any]] = []
    parsed_times: list[datetime] = []
    seen: set[str] = set()
    start = date.fromisoformat(request.start_date) if _valid_date(request.start_date) else None
    end = date.fromisoformat(request.end_date) if _valid_date(request.end_date) else None
    for index, raw_bar in enumerate(values):
        if not isinstance(raw_bar, dict):
            failures.append(f"bar[{index}] must be an object")
            continue
        try:
            timestamp_text, parsed = _timestamp_utc(raw_bar.get("datetime"))
            open_text = _decimal_text(raw_bar.get("open"), f"bar[{index}].open", positive=True)
            high_text = _decimal_text(raw_bar.get("high"), f"bar[{index}].high", positive=True)
            low_text = _decimal_text(raw_bar.get("low"), f"bar[{index}].low", positive=True)
            close_text = _decimal_text(raw_bar.get("close"), f"bar[{index}].close", positive=True)
            volume_text = None
            if raw_bar.get("volume") not in (None, ""):
                volume_text = _decimal_text(raw_bar.get("volume"), f"bar[{index}].volume", positive=False)
        except MarketDataError as error:
            failures.append(str(error))
            continue
        if timestamp_text in seen:
            failures.append(f"duplicate bar timestamp: {timestamp_text}")
        seen.add(timestamp_text)
        if parsed_times and parsed <= parsed_times[-1]:
            failures.append("bar timestamps must be strictly increasing in provider order")
        parsed_times.append(parsed)
        if start is not None and parsed.date() < start:
            failures.append(f"bar timestamp precedes requested_start: {timestamp_text}")
        if end is not None and parsed.date() > end:
            failures.append(f"bar timestamp exceeds requested_end: {timestamp_text}")
        open_value = Decimal(open_text)
        high_value = Decimal(high_text)
        low_value = Decimal(low_text)
        close_value = Decimal(close_text)
        if high_value < low_value:
            failures.append(f"bar[{index}] high is below low")
        if not (low_value <= open_value <= high_value):
            failures.append(f"bar[{index}] open is outside the high-low range")
        if not (low_value <= close_value <= high_value):
            failures.append(f"bar[{index}] close is outside the high-low range")
        bars.append(
            {
                "timestamp_utc": timestamp_text,
                "open": open_text,
                "high": high_text,
                "low": low_text,
                "close": close_text,
                "volume": volume_text,
            }
        )

    allowed_inter_session_gaps = 0
    missing_bar_count = 0
    expected = timedelta(seconds=INTERVAL_SECONDS.get(request.interval, 1))
    for previous, current in zip(parsed_times, parsed_times[1:]):
        delta = current - previous
        if delta <= expected:
            continue
        if _allowed_inter_session_gap(asset_class, request.interval, previous, current):
            allowed_inter_session_gaps += 1
            continue
        missing = max(1, int(delta.total_seconds() // expected.total_seconds()) - 1)
        missing_bar_count += missing
        failures.append(
            f"unexpected in-session gap after {previous.isoformat().replace('+00:00', 'Z')}: {missing} bar(s)"
        )
    if failures:
        return None, sorted(set(failures))
    return NormalizedMarketData(
        provider_metadata=provider_metadata,
        bars=tuple(bars),
        allowed_inter_session_gaps=allowed_inter_session_gaps,
        missing_bar_count=missing_bar_count,
    ), []


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _allowed_inter_session_gap(
    asset_class: str,
    interval: str,
    previous: datetime,
    current: datetime,
) -> bool:
    if asset_class == "crypto":
        return False
    if interval == "1day":
        return current - previous <= timedelta(days=4)
    return previous.date() != current.date()


def _safe_write(root: Path, relative_path: Path, content: bytes) -> str:
    root_resolved = root.resolve()
    destination = (root / relative_path).resolve()
    if destination != root_resolved and root_resolved not in destination.parents:
        raise MarketDataError("market-data object path escaped the controlled root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_bytes() != content:
        raise MarketDataError("content-addressed market-data object conflicts with retained bytes")
    if not destination.exists():
        destination.write_bytes(content)
    return relative_path.as_posix()


class MarketDataIntake:
    def __init__(self, policy: MarketDataPolicy, clock: Callable[[], str] = utc_now) -> None:
        self.policy = policy
        self.clock = clock

    @classmethod
    def from_policy_file(
        cls,
        path: str | Path,
        clock: Callable[[], str] = utc_now,
    ) -> MarketDataIntake:
        return cls(MarketDataPolicy.from_file(path), clock=clock)

    def ingest_live(
        self,
        request: MarketDataRequest,
        *,
        client: TwelveDataClient,
        objects_root: str | Path,
        register: EvidenceRegister,
        quarantine: MarketDataQuarantineRegister,
        ledger: EvidenceLedger,
    ) -> dict[str, Any]:
        payload, locator, raw_bytes = client.fetch(request)
        return self.ingest_payload(
            payload,
            request,
            source_mode="provider",
            source_locator=locator,
            source_bytes=raw_bytes,
            objects_root=objects_root,
            register=register,
            quarantine=quarantine,
            ledger=ledger,
        )

    def ingest_payload(
        self,
        payload: dict[str, Any],
        request: MarketDataRequest,
        *,
        source_mode: str,
        source_locator: str,
        source_bytes: bytes | None = None,
        objects_root: str | Path,
        register: EvidenceRegister,
        quarantine: MarketDataQuarantineRegister,
        ledger: EvidenceLedger,
    ) -> dict[str, Any]:
        if source_mode not in {"provider", "synthetic_fixture"}:
            raise MarketDataError("unsupported market-data source mode")
        request_sha256 = sha256_text(canonical_json(request.to_dict()))
        raw_bytes = source_bytes or b""
        source_payload_sha256 = sha256_bytes(raw_bytes) if raw_bytes else None
        retained_source_failure: str | None = None
        try:
            canonical_payload = canonical_json(payload)
            if source_bytes is None:
                raw_bytes = (canonical_payload + "\n").encode("utf-8")
                source_payload_sha256 = sha256_bytes(raw_bytes)
            retained_payload = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(retained_payload, dict) or canonical_json(retained_payload) != canonical_payload:
                raise MarketDataError("retained source bytes differ from the evaluated provider payload")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, MarketDataError) as error:
            retained_source_failure = str(error) or "retained source bytes are not matching UTF-8 JSON"
        normalized, failures = normalize_provider_payload(payload, request, self.policy)
        if source_payload_sha256 is None:
            failures.append(
                retained_source_failure
                or "provider payload cannot be retained as matching finite UTF-8 JSON"
            )
        elif retained_source_failure:
            failures.append(retained_source_failure)
        if failures or normalized is None:
            source_object_path = None
            if source_payload_sha256 is not None:
                source_object_path = _safe_write(
                    Path(objects_root),
                    Path("quarantine") / "raw" / source_payload_sha256[:2] / f"{source_payload_sha256}.bin",
                    raw_bytes,
                )
            entry = quarantine.append(
                request_sha256=request_sha256,
                source_payload_sha256=source_payload_sha256,
                source_object_path=source_object_path,
                symbol=request.symbol,
                interval=request.interval,
                reasons=failures,
                ledger=ledger,
            )
            return {
                "status": "QUARANTINED",
                "resolution_id": self.policy.resolution_id,
                "symbol": request.symbol,
                "interval": request.interval,
                "reasons": entry["reasons"],
                "quarantine_hash": entry["hash"],
                "source_object_path": entry["source_object_path"],
                "ledger_hash": entry["ledger_hash"],
            }

        objects = Path(objects_root)
        normalized_bytes = _normalised_jsonl(normalized.bars)
        content_sha256 = sha256_bytes(normalized_bytes)
        assert source_payload_sha256 is not None
        normalized_path = _safe_write(
            objects,
            Path("normalized") / content_sha256[:2] / f"{content_sha256}.jsonl",
            normalized_bytes,
        )
        raw_path = _safe_write(
            objects,
            Path("raw") / source_payload_sha256[:2] / f"{source_payload_sha256}.json",
            raw_bytes,
        )
        acquired_at = self.clock()
        first_timestamp = str(normalized.bars[0]["timestamp_utc"])
        last_timestamp = str(normalized.bars[-1]["timestamp_utc"])
        source_name = (
            str(self.policy.provider["name"])
            if source_mode == "provider"
            else "synthetic_market_data_fixture"
        )
        usage_rights = (
            str(self.policy.provider["usage_rights"])
            if source_mode == "provider"
            else "synthetic-test-only"
        )
        fingerprint = DatasetFingerprint.create(
            dataset_name=f"{source_name}:{request.symbol}:{request.interval}",
            source=source_name,
            source_locator=source_locator,
            content_sha256=content_sha256,
            extraction_config_sha256=self.policy.configuration_sha256,
            row_count=len(normalized.bars),
            fields=BAR_FIELDS,
            universe=(request.symbol,),
            timeframe=request.interval,
            period_start=first_timestamp,
            period_end=last_timestamp,
            acquired_at=acquired_at,
        )
        instrument = self.policy.instruments[request.symbol]
        market_content = {
            "policy_id": self.policy.resolution_id,
            "source_mode": source_mode,
            "provider": source_name,
            "symbol": request.symbol,
            "asset_class": instrument.asset_class,
            "interval": request.interval,
            "requested_start": request.start_date,
            "requested_end": request.end_date,
            "acquired_at": acquired_at,
            "row_count": len(normalized.bars),
            "missing_bar_count": normalized.missing_bar_count,
            "allowed_inter_session_gaps": normalized.allowed_inter_session_gaps,
            "configuration_sha256": self.policy.configuration_sha256,
            "content_sha256": content_sha256,
            "source_payload_sha256": source_payload_sha256,
            "raw_object_path": raw_path,
            "normalized_object_path": normalized_path,
            "provider_metadata": normalized.provider_metadata,
            "public_repository_data_permitted": False,
            "decision_court_submission": "NOT_AUTHORIZED_BY_DATASET_INGESTION",
            "live_execution": "prohibited",
        }
        provenance = Provenance(
            source_type="market_data_api" if source_mode == "provider" else "synthetic_fixture",
            source_locator=source_locator,
            source_sha256=source_payload_sha256,
            observed_at=acquired_at,
            acquisition_method="Twelve Data time_series API" if source_mode == "provider" else "CI fixture",
            usage_rights=usage_rights,
            evidence_ids=self.policy.evidence_ids,
        )
        record = KnowledgeRecord.create(
            record_type=RecordType.DATASET,
            title=f"Market data {request.symbol} {request.interval}",
            identity={"fingerprint_sha256": fingerprint.fingerprint_sha256},
            provenance=provenance,
            evidence_ids=self.policy.evidence_ids,
            related_record_ids=(),
            content={
                "dataset_fingerprint": fingerprint.to_dict(),
                "market_data": market_content,
            },
            recorded_at=acquired_at,
        )
        registration = register.append(record, ledger)
        existing_events = [
            entry
            for entry in ledger.entries()
            if entry.get("event_type") == "market_data_ingested"
            and entry.get("payload", {}).get("dataset_record_id") == record.record_id
            and entry.get("payload", {}).get("record_sha256") == record.record_sha256
        ]
        if len(existing_events) > 1:
            raise MarketDataError("dataset has duplicate market_data_ingested ledger events")
        ingestion_event = existing_events[0] if existing_events else ledger.append(
            "market_data_ingested",
            "athena.market_data",
            {
                "resolution_id": self.policy.resolution_id,
                "dataset_record_id": record.record_id,
                "record_sha256": record.record_sha256,
                "source_mode": source_mode,
                "symbol": request.symbol,
                "interval": request.interval,
                "row_count": len(normalized.bars),
                "content_sha256": content_sha256,
                "source_payload_sha256": source_payload_sha256,
            },
        )
        return {
            "status": "ACCEPTED" if registration["created"] else "DUPLICATE",
            "resolution_id": self.policy.resolution_id,
            "dataset_record_id": record.record_id,
            "symbol": request.symbol,
            "interval": request.interval,
            "row_count": len(normalized.bars),
            "content_sha256": content_sha256,
            "source_payload_sha256": source_payload_sha256,
            "normalized_object_path": normalized_path,
            "raw_object_path": raw_path,
            "registration_ledger_hash": registration["ledger_hash"],
            "ledger_hash": ingestion_event["hash"],
            "live_execution": "prohibited",
        }


def _controlled_object(root: Path, relative: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise MarketDataError("registered market-data object escaped the controlled root")
    return candidate


def validate_market_data_state(
    *,
    policy: MarketDataPolicy,
    objects_root: str | Path,
    register: EvidenceRegister,
    quarantine: MarketDataQuarantineRegister,
    ledger: EvidenceLedger,
) -> dict[str, Any]:
    register_status = register.validate(ledger)
    quarantine_status = quarantine.validate(ledger)
    objects = Path(objects_root)
    market_records = [
        record
        for record in register.records()
        if record.record_type == RecordType.DATASET
        and record.content.get("market_data", {}).get("policy_id") == policy.resolution_id
    ]
    real_datasets = 0
    synthetic_datasets = 0
    rows_verified = 0
    ledger_entries = ledger.entries()
    for entry in quarantine.entries():
        source_digest = entry.get("source_payload_sha256")
        source_object_path = entry.get("source_object_path")
        if source_digest is None:
            if source_object_path is not None:
                raise MarketDataError("market-data quarantine object exists without a source digest")
            continue
        if not source_object_path:
            raise MarketDataError("market-data quarantine source digest lacks retained bytes")
        quarantine_object = _controlled_object(objects, str(source_object_path))
        if not quarantine_object.is_file() or sha256_bytes(quarantine_object.read_bytes()) != source_digest:
            raise MarketDataError("market-data quarantine retained-source digest mismatch")
    for record in market_records:
        market = record.content["market_data"]
        fingerprint = DatasetFingerprint.from_dict(record.content["dataset_fingerprint"])
        failures = fingerprint.validate()
        if failures:
            raise MarketDataError(f"{record.record_id}: {'; '.join(failures)}")
        if market.get("configuration_sha256") != policy.configuration_sha256:
            raise MarketDataError(f"{record.record_id}: market-data policy digest mismatch")
        if fingerprint.extraction_config_sha256 != policy.configuration_sha256:
            raise MarketDataError(f"{record.record_id}: dataset extraction policy digest mismatch")
        missing_dataset_fields = set(policy.required_dataset_fields) - set(market)
        if missing_dataset_fields:
            raise MarketDataError(
                f"{record.record_id}: missing controlled dataset fields: {sorted(missing_dataset_fields)}"
            )
        request = MarketDataRequest(
            symbol=str(market.get("symbol", "")),
            interval=str(market.get("interval", "")),
            start_date=str(market.get("requested_start", "")),
            end_date=str(market.get("requested_end", "")),
        )
        request_failures = request.validate(policy)
        if request_failures:
            raise MarketDataError(f"{record.record_id}: {'; '.join(request_failures)}")
        raw_path = _controlled_object(objects, str(market.get("raw_object_path", "")))
        normalized_path = _controlled_object(objects, str(market.get("normalized_object_path", "")))
        if not raw_path.is_file() or not normalized_path.is_file():
            raise MarketDataError(f"{record.record_id}: retained market-data objects are missing")
        raw_bytes = raw_path.read_bytes()
        normalized_bytes = normalized_path.read_bytes()
        if sha256_bytes(raw_bytes) != market.get("source_payload_sha256"):
            raise MarketDataError(f"{record.record_id}: retained provider payload digest mismatch")
        if sha256_bytes(raw_bytes) != record.provenance.source_sha256:
            raise MarketDataError(f"{record.record_id}: provenance digest differs from retained provider payload")
        if sha256_bytes(normalized_bytes) != fingerprint.content_sha256:
            raise MarketDataError(f"{record.record_id}: retained normalized dataset digest mismatch")
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MarketDataError(f"{record.record_id}: retained provider payload is invalid JSON") from error
        normalized, normalization_failures = normalize_provider_payload(payload, request, policy)
        if normalization_failures or normalized is None:
            raise MarketDataError(
                f"{record.record_id}: retained payload no longer passes controls: {'; '.join(normalization_failures)}"
            )
        expected_bytes = _normalised_jsonl(normalized.bars)
        if normalized_bytes != expected_bytes:
            raise MarketDataError(f"{record.record_id}: retained normalized bars are not reproducible")
        if len(normalized.bars) != fingerprint.row_count or len(normalized.bars) != market.get("row_count"):
            raise MarketDataError(f"{record.record_id}: row count differs from the registered dataset")
        if tuple(fingerprint.fields) != BAR_FIELDS:
            raise MarketDataError(f"{record.record_id}: normalized field contract differs from policy")
        if fingerprint.universe != (request.symbol,) or fingerprint.timeframe != request.interval:
            raise MarketDataError(f"{record.record_id}: fingerprint scope differs from the approved request")
        if fingerprint.source_locator != record.provenance.source_locator:
            raise MarketDataError(f"{record.record_id}: fingerprint and provenance locators differ")
        if fingerprint.period_start != normalized.bars[0]["timestamp_utc"]:
            raise MarketDataError(f"{record.record_id}: fingerprint period_start differs from retained bars")
        if fingerprint.period_end != normalized.bars[-1]["timestamp_utc"]:
            raise MarketDataError(f"{record.record_id}: fingerprint period_end differs from retained bars")
        if fingerprint.acquired_at != market.get("acquired_at") or fingerprint.acquired_at != record.recorded_at:
            raise MarketDataError(f"{record.record_id}: acquisition timestamps are inconsistent")
        if market.get("content_sha256") != fingerprint.content_sha256:
            raise MarketDataError(f"{record.record_id}: market-data content digest differs from fingerprint")
        if market.get("missing_bar_count") != normalized.missing_bar_count:
            raise MarketDataError(f"{record.record_id}: missing-bar finding differs from retained bars")
        if market.get("allowed_inter_session_gaps") != normalized.allowed_inter_session_gaps:
            raise MarketDataError(f"{record.record_id}: inter-session gap finding differs from retained bars")
        matching_events = [
            entry
            for entry in ledger_entries
            if entry.get("event_type") == "market_data_ingested"
            and entry.get("payload", {}).get("dataset_record_id") == record.record_id
            and entry.get("payload", {}).get("record_sha256") == record.record_sha256
        ]
        if len(matching_events) != 1:
            raise MarketDataError(f"{record.record_id}: expected exactly one market-data ingestion ledger event")
        source_mode = market.get("source_mode")
        if source_mode == "provider":
            if record.provenance.usage_rights != policy.provider["usage_rights"]:
                raise MarketDataError(f"{record.record_id}: provider usage rights differ from policy")
            real_datasets += 1
        elif source_mode == "synthetic_fixture":
            if record.provenance.usage_rights != "synthetic-test-only":
                raise MarketDataError(f"{record.record_id}: synthetic fixture rights are not explicit")
            synthetic_datasets += 1
        else:
            raise MarketDataError(f"{record.record_id}: unsupported source mode")
        if market.get("public_repository_data_permitted") is not False:
            raise MarketDataError(f"{record.record_id}: public repository data prohibition is missing")
        if market.get("live_execution") != "prohibited":
            raise MarketDataError(f"{record.record_id}: live-execution prohibition is missing")
        rows_verified += len(normalized.bars)
    return {
        "valid": True,
        "resolution_id": policy.resolution_id,
        "datasets": len(market_records),
        "real_datasets": real_datasets,
        "synthetic_datasets": synthetic_datasets,
        "rows_verified": rows_verified,
        "register": register_status,
        "quarantine": quarantine_status,
        "live_execution": "prohibited",
    }


def validate_market_data_policy(
    policy_path: str | Path,
    repository_root: str | Path = ".",
) -> dict[str, Any]:
    policy = MarketDataPolicy.from_file(policy_path)
    schema_path = Path(repository_root) / "schemas" / "market-data-policy.schema.json"
    if not schema_path.is_file():
        raise MarketDataError("market-data policy schema is missing")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise MarketDataError("market-data policy schema is invalid JSON") from error
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise MarketDataError("market-data policy schema must be a closed object contract")
    return {
        "valid": True,
        "resolution_id": policy.resolution_id,
        "provider": policy.provider["name"],
        "plan": policy.provider["plan"],
        "approved_symbols": len(policy.universe),
        "approved_intervals": len(policy.intervals),
        "configuration_sha256": policy.configuration_sha256,
        "monthly_budget_usd": policy.provider["monthly_budget_usd"],
        "conditional_upgrade_cap_usd": policy.provider["conditional_upgrade_cap_usd"],
        "live_execution": "prohibited",
    }

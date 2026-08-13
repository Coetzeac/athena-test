from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from athena.models import utc_now


GENESIS_HASH = "0" * 64


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class LedgerIntegrityError(RuntimeError):
    pass


class EvidenceLedger:
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
                raise LedgerIntegrityError(f"invalid JSON at ledger line {line_number}") from error
        return entries

    def append(self, event_type: str, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.validate()
        entries = self._entries()
        body = {
            "sequence": len(entries) + 1,
            "recorded_at": self.clock(),
            "event_type": event_type,
            "actor": actor,
            "payload": payload,
            "previous_hash": entries[-1]["hash"] if entries else GENESIS_HASH,
        }
        entry = {**body, "hash": sha256_text(canonical_json(body))}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(entry) + "\n")
        return entry

    def entries(self) -> tuple[dict[str, Any], ...]:
        """Return validated ledger entries for controlled reconciliation."""
        self.validate()
        return tuple(self._entries())

    def validate(self) -> dict[str, Any]:
        entries = self._entries()
        previous_hash = GENESIS_HASH
        for expected_sequence, entry in enumerate(entries, 1):
            actual_hash = entry.get("hash")
            body = {key: value for key, value in entry.items() if key != "hash"}
            if entry.get("sequence") != expected_sequence:
                raise LedgerIntegrityError(f"invalid sequence at entry {expected_sequence}")
            if entry.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(f"broken previous_hash at entry {expected_sequence}")
            if actual_hash != sha256_text(canonical_json(body)):
                raise LedgerIntegrityError(f"invalid hash at entry {expected_sequence}")
            previous_hash = actual_hash
        return {
            "valid": True,
            "entries": len(entries),
            "terminal_hash": previous_hash,
        }

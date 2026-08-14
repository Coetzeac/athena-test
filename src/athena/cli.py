from __future__ import annotations

import argparse
import json
from pathlib import Path

from athena.evidence import EvidenceLedger
from athena.freeze import load_freeze, validate_freeze, validate_traceability
from athena.history import (
    HistoricalAcquisitionCoordinator,
    HistoricalAcquisitionPolicy,
    HistoricalCheckpointRegister,
    HistoricalManifest,
    MarketDataQuotaLedger,
    validate_historical_policy,
    validate_historical_state,
)
from athena.intake import QuarantineRegister, ResearchIntake, validate_intake_policy, validate_intake_state
from athena.market_data import (
    MarketDataIntake,
    MarketDataPolicy,
    MarketDataQuarantineRegister,
    MarketDataRequest,
    TwelveDataClient,
    validate_market_data_policy,
    validate_market_data_state,
)
from athena.models import utc_now
from athena.orchestrator import run_cycle
from athena.records import EvidenceRegister, validate_record_contract


DEFAULT_POLICY = Path("config/decision_policy.json")
DEFAULT_FREEZE = Path("config/engineering_freeze.json")
DEFAULT_TRACEABILITY = Path("config/freeze_traceability.json")
DEFAULT_EVIDENCE_CONTRACT = Path("config/evidence_registers.json")
DEFAULT_INTAKE_POLICY = Path("config/research_intake_policy.json")
DEFAULT_MARKET_DATA_POLICY = Path("config/market_data_policy.json")
DEFAULT_HISTORY_POLICY = Path("config/historical_acquisition_policy.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="athena", description="ATHENA research operating system")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="run one governed research cycle")
    run.add_argument("request", type=Path)
    run.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    run.add_argument("--ledger", type=Path, default=Path("runtime/ledger.jsonl"))
    run.add_argument("--status", type=Path, default=Path("runtime/status.json"))
    run.add_argument("--register", type=Path, default=None)

    validate = subcommands.add_parser("validate-ledger", help="validate the audit hash chain")
    validate.add_argument("ledger", type=Path)

    validate_register = subcommands.add_parser(
        "validate-register",
        help="validate stable evidence records and reconcile their ledger links",
    )
    validate_register.add_argument("register", type=Path)
    validate_register.add_argument("--ledger", type=Path)

    ingest = subcommands.add_parser("ingest-paper", help="ingest one controlled paper and Research Card")
    ingest.add_argument("manifest", type=Path)
    ingest.add_argument("--policy", type=Path, default=DEFAULT_INTAKE_POLICY)
    ingest.add_argument("--objects", type=Path, default=Path("runtime/objects"))
    ingest.add_argument("--register", type=Path, default=Path("runtime/evidence-register.jsonl"))
    ingest.add_argument("--quarantine", type=Path, default=Path("runtime/intake-quarantine.jsonl"))
    ingest.add_argument("--ledger", type=Path, default=Path("runtime/ledger.jsonl"))

    validate_intake = subcommands.add_parser(
        "validate-intake",
        help="validate retained sources, claim links, quarantine, register, and ledger",
    )
    validate_intake.add_argument("--objects", type=Path, default=Path("runtime/objects"))
    validate_intake.add_argument("--register", type=Path, default=Path("runtime/evidence-register.jsonl"))
    validate_intake.add_argument("--quarantine", type=Path, default=Path("runtime/intake-quarantine.jsonl"))
    validate_intake.add_argument("--ledger", type=Path, default=Path("runtime/ledger.jsonl"))

    ingest_market = subcommands.add_parser(
        "ingest-market-data",
        help="ingest one approved Twelve Data request without exposing its API key",
    )
    ingest_market.add_argument("symbol")
    ingest_market.add_argument("interval")
    ingest_market.add_argument("--start", required=True)
    ingest_market.add_argument("--end", required=True)
    ingest_market.add_argument("--policy", type=Path, default=DEFAULT_MARKET_DATA_POLICY)
    ingest_market.add_argument("--objects", type=Path, default=Path("runtime/market-data/objects"))
    ingest_market.add_argument("--register", type=Path, default=Path("runtime/evidence-register.jsonl"))
    ingest_market.add_argument(
        "--quarantine",
        type=Path,
        default=Path("runtime/market-data-quarantine.jsonl"),
    )
    ingest_market.add_argument("--ledger", type=Path, default=Path("runtime/ledger.jsonl"))

    ingest_market_fixture = subcommands.add_parser(
        "ingest-market-fixture",
        help="ingest a synthetic market-data fixture that cannot count as empirical evidence",
    )
    ingest_market_fixture.add_argument("fixture", type=Path)
    ingest_market_fixture.add_argument("symbol")
    ingest_market_fixture.add_argument("interval")
    ingest_market_fixture.add_argument("--start", required=True)
    ingest_market_fixture.add_argument("--end", required=True)
    ingest_market_fixture.add_argument("--policy", type=Path, default=DEFAULT_MARKET_DATA_POLICY)
    ingest_market_fixture.add_argument("--objects", type=Path, default=Path("runtime/market-data/objects"))
    ingest_market_fixture.add_argument("--register", type=Path, default=Path("runtime/evidence-register.jsonl"))
    ingest_market_fixture.add_argument(
        "--quarantine",
        type=Path,
        default=Path("runtime/market-data-quarantine.jsonl"),
    )
    ingest_market_fixture.add_argument("--ledger", type=Path, default=Path("runtime/ledger.jsonl"))

    validate_market = subcommands.add_parser(
        "validate-market-data",
        help="validate retained market data, fingerprints, quarantine, register, and ledger",
    )
    validate_market.add_argument("--policy", type=Path, default=DEFAULT_MARKET_DATA_POLICY)
    validate_market.add_argument("--objects", type=Path, default=Path("runtime/market-data/objects"))
    validate_market.add_argument("--register", type=Path, default=Path("runtime/evidence-register.jsonl"))
    validate_market.add_argument(
        "--quarantine",
        type=Path,
        default=Path("runtime/market-data-quarantine.jsonl"),
    )
    validate_market.add_argument("--ledger", type=Path, default=Path("runtime/ledger.jsonl"))

    plan_history = subcommands.add_parser(
        "plan-market-history",
        help="write an immutable partitioned manifest for approved historical market data",
    )
    plan_history.add_argument("--end", required=True)
    plan_history.add_argument("--start")
    plan_history.add_argument("--symbol", action="append", dest="symbols")
    plan_history.add_argument("--interval", action="append", dest="intervals")
    plan_history.add_argument("--policy", type=Path, default=DEFAULT_MARKET_DATA_POLICY)
    plan_history.add_argument("--history-policy", type=Path, default=DEFAULT_HISTORY_POLICY)
    plan_history.add_argument(
        "--manifest-root",
        type=Path,
        default=Path("runtime/market-data/history-control"),
    )

    acquire_history = subcommands.add_parser(
        "acquire-market-history",
        help="resume one immutable historical manifest within recorded quota limits",
    )
    acquire_history.add_argument("manifest", type=Path)
    acquire_history.add_argument("--max-credits", type=int)
    acquire_history.add_argument("--policy", type=Path, default=DEFAULT_MARKET_DATA_POLICY)
    acquire_history.add_argument("--history-policy", type=Path, default=DEFAULT_HISTORY_POLICY)
    acquire_history.add_argument("--objects", type=Path, default=Path("runtime/market-data/objects"))
    acquire_history.add_argument(
        "--reports-root",
        type=Path,
        default=Path("runtime/market-data/history-control"),
    )
    acquire_history.add_argument("--register", type=Path, default=Path("runtime/evidence-register.jsonl"))
    acquire_history.add_argument(
        "--quarantine",
        type=Path,
        default=Path("runtime/market-data-quarantine.jsonl"),
    )
    acquire_history.add_argument(
        "--checkpoints",
        type=Path,
        default=Path("runtime/market-data/history-checkpoints.jsonl"),
    )
    acquire_history.add_argument(
        "--quota-ledger",
        type=Path,
        default=Path("runtime/market-data/quota-ledger.jsonl"),
    )
    acquire_history.add_argument("--ledger", type=Path, default=Path("runtime/ledger.jsonl"))

    validate_history = subcommands.add_parser(
        "validate-market-history",
        help="validate manifest, checkpoints, quotas, reports, and ledger reconciliation",
    )
    validate_history.add_argument("manifest", type=Path)
    validate_history.add_argument("--policy", type=Path, default=DEFAULT_MARKET_DATA_POLICY)
    validate_history.add_argument("--history-policy", type=Path, default=DEFAULT_HISTORY_POLICY)
    validate_history.add_argument(
        "--reports-root",
        type=Path,
        default=Path("runtime/market-data/history-control"),
    )
    validate_history.add_argument("--objects", type=Path, default=Path("runtime/market-data/objects"))
    validate_history.add_argument("--register", type=Path, default=Path("runtime/evidence-register.jsonl"))
    validate_history.add_argument(
        "--quarantine",
        type=Path,
        default=Path("runtime/market-data-quarantine.jsonl"),
    )
    validate_history.add_argument(
        "--checkpoints",
        type=Path,
        default=Path("runtime/market-data/history-checkpoints.jsonl"),
    )
    validate_history.add_argument(
        "--quota-ledger",
        type=Path,
        default=Path("runtime/market-data/quota-ledger.jsonl"),
    )
    validate_history.add_argument("--ledger", type=Path, default=Path("runtime/ledger.jsonl"))

    freeze = subcommands.add_parser("freeze-status", help="validate the engineering freeze and implementation mapping")
    freeze.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    freeze.add_argument("--traceability", type=Path, default=DEFAULT_TRACEABILITY)
    freeze.add_argument("--evidence-contract", type=Path, default=DEFAULT_EVIDENCE_CONTRACT)
    freeze.add_argument("--intake-policy", type=Path, default=DEFAULT_INTAKE_POLICY)
    freeze.add_argument("--market-data-policy", type=Path, default=DEFAULT_MARKET_DATA_POLICY)
    freeze.add_argument("--history-policy", type=Path, default=DEFAULT_HISTORY_POLICY)
    freeze.add_argument("--repository-root", type=Path, default=Path("."))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        status = run_cycle(args.request, args.policy, args.ledger, args.status, args.register)
        print(json.dumps({
            "state": status["state"],
            "strategy_id": status["cycle"]["strategy_id"],
            "verdict": status["cycle"]["verdict"],
            "status_path": str(args.status),
            "terminal_hash": status["audit"]["terminal_hash"],
            "evidence_register_sha256": status["evidence_register"]["register_sha256"],
        }, indent=2))
        return 0
    if args.command == "validate-ledger":
        print(json.dumps(EvidenceLedger(args.ledger).validate(), indent=2))
        return 0
    if args.command == "validate-register":
        ledger = EvidenceLedger(args.ledger) if args.ledger else None
        print(json.dumps(EvidenceRegister(args.register).validate(ledger), indent=2))
        return 0
    if args.command == "ingest-paper":
        ledger = EvidenceLedger(args.ledger)
        result = ResearchIntake.from_policy_file(args.policy).ingest(
            args.manifest,
            objects_root=args.objects,
            register=EvidenceRegister(args.register),
            quarantine=QuarantineRegister(args.quarantine),
            ledger=ledger,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in {"ACCEPTED", "DUPLICATE"} else 2
    if args.command == "validate-intake":
        ledger = EvidenceLedger(args.ledger)
        result = validate_intake_state(
            objects_root=args.objects,
            register=EvidenceRegister(args.register),
            quarantine=QuarantineRegister(args.quarantine),
            ledger=ledger,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.command in {"ingest-market-data", "ingest-market-fixture"}:
        policy = MarketDataPolicy.from_file(args.policy)
        intake = MarketDataIntake(policy)
        request = MarketDataRequest(
            symbol=args.symbol,
            interval=args.interval,
            start_date=args.start,
            end_date=args.end,
        )
        ledger = EvidenceLedger(args.ledger)
        common = {
            "objects_root": args.objects,
            "register": EvidenceRegister(args.register),
            "quarantine": MarketDataQuarantineRegister(args.quarantine),
            "ledger": ledger,
        }
        if args.command == "ingest-market-data":
            result = intake.ingest_live(
                request,
                client=TwelveDataClient(policy),
                **common,
            )
        else:
            fixture_bytes = args.fixture.read_bytes()
            try:
                decoded_fixture = json.loads(fixture_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                decoded_fixture = {}
            payload = decoded_fixture if isinstance(decoded_fixture, dict) else {}
            result = intake.ingest_payload(
                payload,
                request,
                source_mode="synthetic_fixture",
                source_locator=f"repository://{args.fixture.as_posix()}",
                source_bytes=fixture_bytes,
                **common,
            )
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in {"ACCEPTED", "DUPLICATE"} else 2
    if args.command == "validate-market-data":
        result = validate_market_data_state(
            policy=MarketDataPolicy.from_file(args.policy),
            objects_root=args.objects,
            register=EvidenceRegister(args.register),
            quarantine=MarketDataQuarantineRegister(args.quarantine),
            ledger=EvidenceLedger(args.ledger),
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "plan-market-history":
        market_policy = MarketDataPolicy.from_file(args.policy)
        history_policy = HistoricalAcquisitionPolicy.from_file(args.history_policy)
        manifest = HistoricalManifest.create(
            market_policy,
            history_policy,
            requested_end=args.end,
            created_at=utc_now(),
            symbols=tuple(args.symbols) if args.symbols else None,
            intervals=tuple(args.intervals) if args.intervals else None,
            start_override=args.start,
        )
        relative = manifest.write(args.manifest_root)
        print(json.dumps({
            "manifest_id": manifest.manifest_id,
            "manifest_sha256": manifest.manifest_sha256,
            "manifest_path": str(args.manifest_root / relative),
            "scope": manifest.scope,
            "total_windows": manifest.total_windows,
            "planned_api_credits": manifest.planned_api_credits,
            "live_execution": "prohibited",
        }, indent=2))
        return 0
    if args.command == "acquire-market-history":
        market_policy = MarketDataPolicy.from_file(args.policy)
        history_policy = HistoricalAcquisitionPolicy.from_file(args.history_policy)
        manifest = HistoricalManifest.from_file(args.manifest, market_policy, history_policy)
        ledger = EvidenceLedger(args.ledger)
        result = HistoricalAcquisitionCoordinator(market_policy, history_policy).run(
            manifest,
            client=TwelveDataClient(market_policy),
            objects_root=args.objects,
            reports_root=args.reports_root,
            register=EvidenceRegister(args.register),
            quarantine=MarketDataQuarantineRegister(args.quarantine),
            checkpoints=HistoricalCheckpointRegister(args.checkpoints),
            quota=MarketDataQuotaLedger(args.quota_ledger),
            ledger=ledger,
            max_credits=args.max_credits,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in {"COMPLETE", "PAUSED_QUOTA", "IN_PROGRESS"} else 2
    if args.command == "validate-market-history":
        market_policy = MarketDataPolicy.from_file(args.policy)
        history_policy = HistoricalAcquisitionPolicy.from_file(args.history_policy)
        manifest = HistoricalManifest.from_file(args.manifest, market_policy, history_policy)
        result = validate_historical_state(
            manifest=manifest,
            market_policy=market_policy,
            history_policy=history_policy,
            objects_root=args.objects,
            reports_root=args.reports_root,
            register=EvidenceRegister(args.register),
            quarantine=MarketDataQuarantineRegister(args.quarantine),
            checkpoints=HistoricalCheckpointRegister(args.checkpoints),
            quota=MarketDataQuotaLedger(args.quota_ledger),
            ledger=EvidenceLedger(args.ledger),
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "freeze-status":
        freeze = load_freeze(args.freeze)
        freeze_status = validate_freeze(freeze)
        traceability = json.loads(args.traceability.read_text(encoding="utf-8"))
        traceability_status = validate_traceability(freeze, traceability, args.repository_root)
        evidence_status = validate_record_contract(args.evidence_contract, args.repository_root)
        intake_status = validate_intake_policy(args.intake_policy)
        market_data_status = validate_market_data_policy(args.market_data_policy, args.repository_root)
        history_status = validate_historical_policy(args.history_policy, args.repository_root)
        print(json.dumps({
            "freeze": freeze_status,
            "traceability": traceability_status,
            "evidence_foundation": evidence_status,
            "research_intake": intake_status,
            "market_data": market_data_status,
            "historical_acquisition": history_status,
        }, indent=2))
        return 0
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())

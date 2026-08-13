from __future__ import annotations

import argparse
import json
from pathlib import Path

from athena.evidence import EvidenceLedger
from athena.freeze import load_freeze, validate_freeze, validate_traceability
from athena.orchestrator import run_cycle
from athena.records import EvidenceRegister, validate_record_contract


DEFAULT_POLICY = Path("config/decision_policy.json")
DEFAULT_FREEZE = Path("config/engineering_freeze.json")
DEFAULT_TRACEABILITY = Path("config/freeze_traceability.json")
DEFAULT_EVIDENCE_CONTRACT = Path("config/evidence_registers.json")


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

    freeze = subcommands.add_parser("freeze-status", help="validate the engineering freeze and implementation mapping")
    freeze.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    freeze.add_argument("--traceability", type=Path, default=DEFAULT_TRACEABILITY)
    freeze.add_argument("--evidence-contract", type=Path, default=DEFAULT_EVIDENCE_CONTRACT)
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
    if args.command == "freeze-status":
        freeze = load_freeze(args.freeze)
        freeze_status = validate_freeze(freeze)
        traceability = json.loads(args.traceability.read_text(encoding="utf-8"))
        traceability_status = validate_traceability(freeze, traceability, args.repository_root)
        evidence_status = validate_record_contract(args.evidence_contract, args.repository_root)
        print(json.dumps({
            "freeze": freeze_status,
            "traceability": traceability_status,
            "evidence_foundation": evidence_status,
        }, indent=2))
        return 0
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())

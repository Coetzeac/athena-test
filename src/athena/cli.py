from __future__ import annotations

import argparse
import json
from pathlib import Path

from athena.evidence import EvidenceLedger
from athena.orchestrator import run_cycle


DEFAULT_POLICY = Path("config/decision_policy.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="athena", description="ATHENA research operating system")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="run one governed research cycle")
    run.add_argument("request", type=Path)
    run.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    run.add_argument("--ledger", type=Path, default=Path("runtime/ledger.jsonl"))
    run.add_argument("--status", type=Path, default=Path("runtime/status.json"))

    validate = subcommands.add_parser("validate-ledger", help="validate the audit hash chain")
    validate.add_argument("ledger", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        status = run_cycle(args.request, args.policy, args.ledger, args.status)
        print(json.dumps({
            "state": status["state"],
            "strategy_id": status["cycle"]["strategy_id"],
            "verdict": status["cycle"]["verdict"],
            "status_path": str(args.status),
            "terminal_hash": status["audit"]["terminal_hash"],
        }, indent=2))
        return 0
    if args.command == "validate-ledger":
        print(json.dumps(EvidenceLedger(args.ledger).validate(), indent=2))
        return 0
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())


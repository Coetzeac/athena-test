.PHONY: test freeze demo demo-market-data plan-market-history prove-runtime-recovery validate-runtime dashboard

test:
	PYTHONPATH=src python -m unittest discover -s tests -v

freeze:
	PYTHONPATH=src python -m athena.cli freeze-status

demo:
	PYTHONPATH=src python -m athena.cli run examples/orb_candidate.json --ledger runtime/ledger.jsonl --register runtime/evidence-register.jsonl --status runtime/status.json

demo-market-data:
	PYTHONPATH=src python -m athena.cli ingest-market-fixture examples/market_data/twelve_data_synthetic_daily.json GBP/USD 1day --start 2026-08-03 --end 2026-08-07 --objects runtime/market-data/objects --register runtime/evidence-register.jsonl --quarantine runtime/market-data-quarantine.jsonl --ledger runtime/ledger.jsonl
	PYTHONPATH=src python -m athena.cli validate-market-data --objects runtime/market-data/objects --register runtime/evidence-register.jsonl --quarantine runtime/market-data-quarantine.jsonl --ledger runtime/ledger.jsonl

plan-market-history:
	PYTHONPATH=src python -m athena.cli plan-market-history --end 2026-08-13 --manifest-root runtime/market-data/history-control

prove-runtime-recovery:
	PYTHONPATH=src python -m athena.cli validate-runtime-persistence
	PYTHONPATH=src python -m athena.cli prove-runtime-recovery examples/recovery/synthetic_runtime_backup.json --store-root runtime/recovery-proof

validate-runtime:
	PYTHONPATH=src python -m athena.cli validate-ledger runtime/ledger.jsonl
	PYTHONPATH=src python -m athena.cli validate-register runtime/evidence-register.jsonl --ledger runtime/ledger.jsonl

dashboard:
	python -m http.server 8080

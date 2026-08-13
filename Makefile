.PHONY: test freeze demo validate-runtime dashboard

test:
	PYTHONPATH=src python -m unittest discover -s tests -v

freeze:
	PYTHONPATH=src python -m athena.cli freeze-status

demo:
	PYTHONPATH=src python -m athena.cli run examples/orb_candidate.json --ledger runtime/ledger.jsonl --register runtime/evidence-register.jsonl --status runtime/status.json

validate-runtime:
	PYTHONPATH=src python -m athena.cli validate-ledger runtime/ledger.jsonl
	PYTHONPATH=src python -m athena.cli validate-register runtime/evidence-register.jsonl --ledger runtime/ledger.jsonl

dashboard:
	python -m http.server 8080

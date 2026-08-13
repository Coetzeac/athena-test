.PHONY: test demo dashboard

test:
	PYTHONPATH=src python -m unittest discover -s tests -v

demo:
	PYTHONPATH=src python -m athena.cli run examples/orb_candidate.json --ledger runtime/ledger.jsonl --status runtime/status.json

dashboard:
	python -m http.server 8080


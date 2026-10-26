.PHONY: check test gc scrub formal
check:
	scripts/audit.sh
test:
	python3 -m unittest discover -s tests -v
gc:
	python3 scripts/gc/check_docs_drift.py --exclude templates --exclude docs/core
	python3 scripts/gc/check_structure.py --exclude templates --exclude docs/core
scrub:
	scripts/scrub.sh
formal:  # TLA+ model check of the work-item state model; needs a JRE + TLC, so not part of check
	scripts/formal-check.sh

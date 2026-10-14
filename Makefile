.PHONY: check test gc scrub
check:
	scripts/audit.sh
test:
	python3 -m unittest discover -s tests -v
gc:
	python3 scripts/gc/check_docs_drift.py --exclude templates --exclude docs/core
	python3 scripts/gc/check_structure.py --exclude templates --exclude docs/core
scrub:
	scripts/scrub.sh

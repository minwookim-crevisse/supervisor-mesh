# Glossary & Conventions

> When a label or term is ambiguous, this file settles it. Keep deprecated wordings listed so they don't creep back.
> `scripts/gc/check_docs_drift.py` fails if a deprecated wording below reappears in source.

## Terms  <!-- Part B: fill with the project's domain vocabulary -->
- <term>: <canonical meaning> (deprecated: <old wording>)

## Money / number rules (reusable convention example — keep if money is in scope, else delete)
- All money results use floor to the base unit (no round/ceil).  # why: settlement convention; one consistent rule project-wide.
- Parse numeric fields by stripping commas FIRST, then Number/parse.  # why: "1,800,000" → parseFloat stops at the first comma = 1 (silent corruption).
- Reverse-VAT / division uses integer arithmetic, not float.  # why: float ÷ reintroduces ±1 base-unit error at round multiples (caught only by a full integer oracle, not spot checks).
- Store plain number strings (no commas); format for display only.

# Decision Log

> Single owner: the Orchestrator writes this. Others propose via their report file.
> This log OUTRANKS every other doc on conflict. Newest decision wins.

Format — one entry per decision:

## DEC-0001 — <short title>
- Date: <YYYY-MM-DD>
- Status: accepted | superseded by DEC-00NN
- Context: <why this came up>
- Decision: <what was decided, imperative>
- Consequences: <what this constrains / enables>

<!-- DEC-0001 example (delete when you record your first real decision):
## DEC-0001 — Single production data store
- Date: <date>
- Status: accepted
- Context: ambiguity over which store is live caused a wrong-target query.
- Decision: the only live store is <Part B anchor>; all applies/queries target it; others are forbidden.
- Consequences: migrations are files only; no ad-hoc writes to the live store.
-->

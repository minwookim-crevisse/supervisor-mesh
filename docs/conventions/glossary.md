# Glossary & Conventions

> When a label or term is ambiguous, this file settles it. Keep deprecated wordings listed so they don't creep back.
> `scripts/gc/check_docs_drift.py` fails if a deprecated wording below reappears in source.

## Terms
- Part A: the shipped, project-agnostic method (`docs/core/methodology.md`, `templates/`, agents, skills).
- Part B: per-project facts (role list, data anchor, data boundary, domain terms) filled after init; never shipped filled.
- primed template: a template pre-filled with transferable discipline and `why:` comments, leaving only Part B blanks (deprecated: blank skeleton template).
- role lead: Tier-1 supervisor of one same-role worker; never implements (deprecated: role worker agent).
- invariant block: the text between the INVARIANT markers in `templates/agents/role-lead.md`, copied byte-for-byte into every role lead.
- pillar: one of instruction / constraint / feedback loop / knowledge.

## Conventions
- Skill and agent bodies are English; each tells the agent to answer in the operator's language.
- Decision ids use four digits (`DEC-0001`).

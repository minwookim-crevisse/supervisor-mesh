# Constraint pillar — why each rule exists

Rules are tool-agnostic; only the config files differ per stack.

| rule | TypeScript | Python | why |
|---|---|---|---|
| absolute imports only | eslint `no-restricted-imports` + dep-cruiser `no-relative-parent-hops` | ruff `ban-relative-imports = "all"` | refactor-safe, explicit origin |
| read/write layer boundary | dep-cruiser `layer-boundary-read-vs-write` | import-linter `forbidden` contract | data boundary; UI hiding is not access control (server re-checks) |
| type-check required | `tsc --noEmit` | `mypy --strict` | type errors caught before runtime |
| no unused / orphans | `no-unused-vars` + dep-cruiser `no-orphans` | ruff `F` | surgical changes leave no orphans |
| warnings are errors | `eslint --max-warnings=0` | ruff exit code | no silent drift in CI |
| shared pins: no arbitrary bump | pin toolchain versions (`overrides`/`resolutions`) | pin in lockfile | a native-binding mismatch can break the whole test loader env-wide |
| secrets server-only | never under `NEXT_PUBLIC_*` / `VITE_*` | never in client bundles | the client never holds secrets |

Secret CI gate (both stacks): fail if a secret-named var (KEY / TOKEN / SECRET / PASSWORD) is referenced under a client-public prefix.

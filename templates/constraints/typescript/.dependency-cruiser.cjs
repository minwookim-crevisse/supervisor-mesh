// supervisor-mesh primed constraint (Pillar ② — import-boundary = the machine gate)
// <...> = Part B blanks. Each rule carries its why.
module.exports = {
  forbidden: [
    {
      name: "no-relative-parent-hops",
      comment: "why: deep relative imports (../../..) rot on refactor; use path aliases.",
      severity: "error",
      from: {},
      to: { path: "^(\\.\\./){2,}" }
    },
    {
      name: "layer-boundary-read-vs-write",
      comment: "why: reads go through the client/read layer, writes/settlement/publish through server routes. UI hiding is not access control — the boundary is enforced here + re-checked server-side.",
      severity: "error",
      from: { path: "<READ LAYER GLOB>" },   // Part B: e.g. src/app/**/page.tsx
      to:   { path: "<WRITE LAYER GLOB>" }    // Part B: e.g. src/server/write/**
    },
    {
      name: "no-orphans",
      comment: "why: orphan modules = dead code; surgical changes leave none of your own.",
      severity: "warn",
      from: { orphan: true, pathNot: "\\.(d\\.ts|config\\.(js|ts|cjs|mjs))$" },
      to: {}
    }
  ],
  options: { tsConfig: { fileName: "tsconfig.json" } }
};

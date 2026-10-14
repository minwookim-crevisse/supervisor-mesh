// supervisor-mesh primed constraint (Pillar ② — lint-level rules; complements dependency-cruiser's import graph)
module.exports = {
  root: true,
  parser: "@typescript-eslint/parser",
  plugins: ["@typescript-eslint", "import"],
  extends: ["eslint:recommended", "plugin:@typescript-eslint/recommended", "plugin:import/recommended"],
  rules: {
    "no-restricted-imports": ["error", { patterns: [
      { group: ["../../*", "../../../*"], message: "why: no deep relative imports; use path aliases (refactor-safe)" }
      // Part B (read-layer overrides only): { group: ["<WRITE LAYER ALIAS>/*"], message: "why: read layer must not import the write layer (data boundary; server re-checks)" }
    ]}],
    "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],   // why: orphans = dead code; surgical changes leave none
    "@typescript-eslint/no-explicit-any": "warn",                                  // why: explicit over clever
    "no-empty": ["error", { allowEmptyCatch: false }]                              // why: no silent catch-all; name and handle errors
  }
};

"""supervisor-mesh core: the work-item state machine (`state`), its records (`model`), the on-disk store (`store`), the
one orchestration path (`engine`), and the stdio MCP front door (`mcp_server`, `tools`). Adapters: `runner` (the only
process runner), `projmux`, `tmux`, `identity` (D82), `facts`; tool logic: `dispatch`, `pane`, `actions`,
`companions`. Stdlib only, Python 3.11+."""

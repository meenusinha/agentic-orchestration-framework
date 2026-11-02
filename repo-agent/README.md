# Repository agent

The files in this folder are copied into each connected repository by the setup page
(**Configure** step) or by `configure.py`. You don't copy them by hand.

## What is added to a repository

```
mcp/
├── config.yaml        ← this repository's settings (written by setup; may be edited)
├── mcp_server.py      ← the repository's MCP server, started by VS Code
├── repo_rag.py        ← model verification and access to the code graph
├── demo_logger.py     ← activity log
└── codegraph/         ← parsers, structure, interfaces, storage, search
knowledge/
└── README.md          ← optional extra documentation to index
.github/
├── copilot-instructions.md                 ← generated for this repository
└── skills/cross-repo-feature-analysis/     ← the agent skill for Copilot
.vscode/mcp.json                            ← generated; machine-specific, don't commit
```

## `mcp/config.yaml`

| Field | Meaning |
|---|---|
| `repo_name` | Unique id; matches the entry in the framework's `orchestrator/mcp/config.yaml` |
| `display_name` | Name shown in routing results |
| `src_path` | What to index. `.` (the whole repository) is recommended; dependency, build-output and cache folders are skipped automatically |
| `knowledge_path` | Extra documentation folders outside `src_path` |
| `file_types` | What unknown file types contain (`kind`) and how to read them (`parse_as`) |
| `structure` | Folder-role corrections: `{path, role, index}`; `index` is `full`, `signatures` or `skip` |
| `top_k`, `result_budget_tokens` | Results per search and the size budget of one answer (units are never cut) |

After changing the settings, run **Configure** again on the setup page, then reload
VS Code.

## Rebuilding

The graph in `.rag_index/` updates itself when code, settings or connected repositories
change. To force a full rebuild, delete `.rag_index/`; it's rebuilt on the next start.

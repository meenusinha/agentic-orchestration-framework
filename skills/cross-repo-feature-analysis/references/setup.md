# Setup

Setting up the framework, connecting repositories and creating the GraphRAG are done by the
**/graphrag-setup** agent skill (`<framework>/.github/skills/graphrag-setup/SKILL.md`, also
installed in each connected repository's `.github/skills/`). It asks the developer the same
questions as the setup page — which folders to index or exclude, unknown file types, folder
roles, what to overwrite in a repository set up before — then estimates how long creating
the GraphRAG takes and creates it at once or in batches, with a progress bar.

Without an assistant, the setup page does the same: run `python3 setup_gui.py` in the
framework folder (Windows: `py setup_gui.py`).

When the MCP servers are configured but their tools are missing, the setup is done: ask the
developer to reload the VS Code window, allow the MCP servers (**MCP: List Servers** shows
their status), and keep Copilot Chat in **Agent** mode.

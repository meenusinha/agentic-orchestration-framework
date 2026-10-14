---
name: cross-repo-feature-analysis
description: Analyse a feature request across multiple related repositories using the agentic-orchestration-framework MCP servers (an `orchestrator_router` server with `get_relevant_repos`, and one server per repo with `query_repo`). Use when the developer asks how to build, change, or assess the impact of a feature that may touch other services or repos (frontend/backend/shared libraries/microservices), asks "which repos are affected", or wants a cross-repo design or Feature Analysis Document. Requires those MCP servers to be configured; if they are not, explain the setup instead.
license: MIT
---

# Cross-Repo Feature Analysis

This skill drives the MCP servers from
[agentic-orchestration-framework](https://github.com/meenusinha/agentic-orchestration-framework)
so that an agent working in one repo can pull in knowledge from its peer repos before
designing a feature.

Two kinds of MCP server are involved:

| Server | Tool | What it does |
|---|---|---|
| `orchestrator_router` | `get_relevant_repos(requesting_repo, feature_description)` | Queries every peer repo's RAG index and returns the 2 most relevant peers, ranked by how much relevant content each one holds |
| `<repo_name>` (one per repo) | `query_repo(feature_request)` | Searches that repo's `knowledge/` docs and source code and returns the most relevant chunks, or `(no relevant knowledge found)` |

Tool names are shown differently by each client (for example
`mcp__orchestrator_router__get_relevant_repos` in Claude Code). Match on the server
name and tool name.

## Before you start

1. **Check the tools are available.** Look for a `get_relevant_repos` tool from a
   server called `orchestrator_router`. If it is missing, stop. Tell the developer the
   MCP servers are not connected and point them to
   [references/setup.md](references/setup.md). Do not run the flow from memory.
2. **Find out which repo you are in.** Read `mcp/config.yaml` at the workspace root.
   Its `repo_name` value is this repo's id and the name of its MCP server. If the file
   is missing, ask the developer which configured repo this is.

## Workflow

Follow these steps in order. Do not skip any of them, and do not answer from general
knowledge in place of calling the tools.

### Step 1: Ask the orchestrator which peer repos are relevant

```
orchestrator_router.get_relevant_repos(
  requesting_repo     = "<repo_name from mcp/config.yaml>",
  feature_description = "<the feature request, in the developer's words>"
)
```

Write down the repos marked `★`. Those are the selected peers. The scores show how
much relevant content each repo returned (from 0.0 to 1.0). A score of 0.0 means that
repo has nothing relevant. The router always selects the top two peers, so a selected
repo with a low score (below about 0.2) may be only loosely related. Still query it,
but check whether its results actually bear on the feature before using them.

The first call can take 10–60 seconds per repo while the RAG indexes are built. After
that, calls are fast.

### Step 2: Query your own repo

```
<repo_name>.query_repo(feature_request = "<the feature request>")
```

Always make this call, even though the orchestrator leaves out the requesting repo.

### Step 3: Query each selected peer repo

For each `★` repo from Step 1:

```
<peer_repo_name>.query_repo(feature_request = "<the feature request>")
```

You can make these calls in parallel. If a peer's tool is not available in this
session, record that and carry on with the other repos.

If the results are thin, you may make one follow-up `query_repo` call per repo with a
more specific phrasing, such as a component or API name that came up in another repo's
results.

### Step 4: Write the Feature Analysis Document

Build the document only from what the tools returned:

```markdown
# Feature Analysis: <short feature name>

**Requesting repo**: <display name>
**Date**: <today>
**Repos consulted**: <own repo>, <peer1> (score), <peer2> (score)

---

## Current State

### <This repo>
<what query_repo returned: relevant components, files, interfaces>

### <Peer repo 1>
<what query_repo returned>

### <Peer repo 2> (if selected)
<what query_repo returned>

---

## Solution Design

- **Changes per repo**: which components or files in each repo need to change
- **New or changed interfaces**: APIs, events, schemas, contracts between repos
- **Coordination**: order of rollout and backward-compatibility concerns
- **Risks and open questions**: gaps where a repo returned no knowledge
```

## Rules

- Call `get_relevant_repos` first, every time.
- Always query your own repo, whether or not the orchestrator lists it.
- Ground every statement in the Solution Design in retrieved content. If you have to
  assume something, label it as an assumption.
- If a repo returns `(no relevant knowledge found)`, say so under Current State. That
  gap is useful information for the developer.
- Cite file or component names from the retrieved chunks so the developer can check
  them.
- Once the analysis is written, you can go on to implement the changes in the current
  repo if the developer asks. Changes in peer repos have to be made in those repos.

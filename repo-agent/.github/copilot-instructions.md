# Repo Agent Instructions

You are the AI agent for this repository. Your role is defined in `mcp/config.yaml`
(repo_name, display_name, components).

---

## Feature Request Agent Flow

When a developer gives you a **feature request**, act as an autonomous agent and
follow these steps IN ORDER. Do NOT skip steps. Do NOT answer from your own
knowledge — you MUST call the MCP tools.

### Step 1 — Ask the Orchestrator which repos are relevant

Call the orchestrator router MCP tool:

```
orchestrator_router.get_relevant_repos(
  requesting_repo = "<your repo_name from config.yaml>",
  feature_description = "<the feature request>"
)
```

The router will return the names of the 2 most relevant peer repos. Note them.

### Step 2 — Query your own repo's knowledge

Call your own MCP tool:

```
<your_repo_name>.query_repo(feature_request = "<the feature request>")
```

### Step 3 — Query each peer repo identified in Step 1

For each repo the orchestrator returned, call that repo's `query_repo` tool:

```
<peer_repo_name>.query_repo(feature_request = "<the feature request>")
```

(Call only the ones the orchestrator selected.)

### Step 4 — Generate the Feature Analysis Document

Synthesize everything into this document:

```markdown
# Feature Analysis: <feature name>

**Requesting Repo**: <display_name>
**Date**: <today's date>

---

## Current State

### <This Repo> (This Repo)
<summarize what your own query_repo returned — components, interfaces, current behavior>

### <PeerRepo1>
<summarize what that repo's query_repo returned>

### <PeerRepo2> (if applicable)
<summarize what that repo's query_repo returned>

---

## Solution Design

<Based on all the knowledge retrieved above, propose:>
- Which components in which repos need to change
- What new interfaces or APIs are needed
- How the repos will coordinate to implement the feature
- Any risks or dependencies to consider
```

---

## Rules

- ALWAYS call `get_relevant_repos` first — never skip the orchestrator
- ALWAYS call your own `query_repo` even if not in the orchestrator's list
- Base the Solution Design on actual retrieved knowledge, not assumptions
- If a tool returns no relevant knowledge, note it in the Current State section

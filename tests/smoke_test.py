#!/usr/bin/env python3
"""Smoke test for the sample repositories: start the repo MCP servers exactly as the generated
VS Code config says and check the cross-repo results. Usage: smoke_test.py <sample-client>/.vscode/mcp.json"""

import os
import sys


def _ignore_foreign_pythonpath():
    """Use only this environment's packages. PYTHONPATH/PYTHONHOME set for other tools would be
    searched before the virtual environment, and their libraries (possibly built for another
    Python version) imported instead. Drop them here and for every process started from here;
    only this program's own folder is kept."""
    own = os.path.dirname(os.path.abspath(__file__))
    for var in ("PYTHONPATH", "PYTHONHOME"):
        value = os.environ.pop(var, None)
        if var == "PYTHONPATH" and value:
            foreign = {os.path.abspath(p) for p in value.split(os.pathsep) if p} - {own}
            sys.path[:] = [p for p in sys.path if not p or os.path.abspath(p) not in foreign]


_ignore_foreign_pythonpath()


import asyncio
import json
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

for _s in (sys.stdout, sys.stderr):      # Windows consoles/pipes default to cp1252, which cannot print ✓ — … etc.
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

CFG = json.loads(Path(sys.argv[1]).read_text())["servers"]          # VS Code's mcp.json format


TIMEOUT = 300      # seconds per call — a hang reports which server and tool instead of waiting forever


async def _call(server, tool, args):
    c = CFG[server]
    async with stdio_client(StdioServerParameters(command=c["command"], args=c["args"], env=c["env"])) as (r, w), \
            ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool(tool, args)
        out = res.structuredContent if res.structuredContent is not None else res.content[0].text
        if isinstance(out, dict) and set(out) == {"result"}:
            out = out["result"]
        return out


async def call(server, tool, args):
    t0 = time.time()
    print(f"   … {server}.{tool}", flush=True)
    try:
        out = await asyncio.wait_for(_call(server, tool, args), TIMEOUT)
    except asyncio.TimeoutError:
        print(f"   FAIL  {server}.{tool} did not answer within {TIMEOUT}s", flush=True)
        sys.exit(1)
    print(f"     {time.time() - t0:.1f}s", flush=True)
    return out


def units_without_file(obj, path="", out=None):
    """Every entry that names a code unit must say which file it is in (or be marked as
    outside the indexed code). Returns the offending paths."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        names_unit = any(k in obj for k in ("qualname", "caller", "test", "server_class")) or \
            ("name" in obj and any(k in obj for k in ("line", "kind", "remote", "resolved")))
        if names_unit and not obj.get("file") and obj.get("note") != "not in the indexed code" \
                and obj.get("kind") != "interface":
            out.append(f"{path} {sorted(obj)[:6]}")
        for k, v in obj.items():
            units_without_file(v, f"{path}.{k}", out)
    elif isinstance(obj, list):
        for v in obj:
            units_without_file(v, path + "[]", out)
    return out


CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append(ok)
    print(f"   {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""), flush=True)


async def main():
    # servers start in order: the server repo publishes Auth::IPasswordService, the client links
    # to it and publishes ISessionListener, then the server re-resolves and links to that
    await call("sample-server", "search_code", {"query": "password policy", "k": 1})
    await call("sample-client", "search_code", {"query": "login dialog", "k": 1})

    r = await call("sample-server", "search_code", {"query": "how is password strength scored", "k": 3})
    names = [x["node"]["qualname"] for x in r["results"]]
    check("search returns whole units", any("pwd_policy_check" in n or "checkStrength" in n for n in names), names[0])

    t = await call("sample-client", "trace_interface", {"name": "IPasswordService"})
    provs = [(p["repo"], p["process"]) for p in t["providers"]]
    cons = sorted({c["caller"] for c in t["consumers"]})
    check("IPasswordService: defined in sample-server, served by process authd", ("sample-server", "authd") in provs,
          f"providers {provs}")
    check("IPasswordService: C++, C and Python clients in sample-client",
          {"LoginDialog::onPasswordChanged", "legacy_strength", "weak_ratio"} <= set(cons), ", ".join(cons))

    t = await call("sample-server", "trace_interface", {"name": "ISessionListener"})
    provs = [(p["repo"], p["server_class"]) for p in t["providers"]]
    cons = [(c["repo"], c["caller"]) for c in t["consumers"]]
    check("ISessionListener (in-house .xidl): implemented by sample-client", any(r == "sample-client" for r, _ in provs),
          f"providers {provs}")
    check("ISessionListener: called back by sample-server", any(r == "sample-server" for r, _ in cons), f"consumers {cons}")

    i = await call("sample-server", "impact_of", {"symbol": "pwd_policy_check"})
    reach = sorted({c["repo"] for t2 in i["interfaces"] for c in t2["consumers"]})
    tests = [x["test"] for l in i["local"] for x in l["tested_by"]]
    check("impact of pwd_policy_check reaches sample-client through the IDL", "sample-client" in reach, f"repos {reach}")
    check("impact lists test evidence", len(tests) >= 2, ", ".join(tests))

    q = await call("sample-server", "search_code", {"query": "database connection credentials", "k": 5})
    blob = json.dumps(q)
    check("fake secrets are redacted", "Sample-Fake-Pass" not in blob and "SampleFakeKey" not in blob,
          f"{blob.count('[REDACTED]')} × [REDACTED]")

    # every unit returned by any tool carries its file name
    gaps = []
    outs = {"search_code": await call("sample-server", "search_code", {"query": "password policy strength", "k": 5}),
            "trace_interface": await call("sample-client", "trace_interface", {"name": "IPasswordService"}),
            "impact_of": await call("sample-server", "impact_of", {"symbol": "pwd_policy_check"}),
            "find_references": await call("sample-server", "find_references", {"symbol": "checkStrength"})}
    first = outs["search_code"]["results"][0]["node"]["id"]
    outs["get_node"] = await call("sample-server", "get_node", {"node_id": first})
    parent = (outs["search_code"]["results"][0].get("parent") or {}).get("id") or first
    outs["get_children"] = await call("sample-server", "get_children", {"node_id": parent})
    for name, out in outs.items():
        gaps += units_without_file(out, name)
    check("every unit in every tool result carries its file name", not gaps, "; ".join(gaps[:3]) or
          f"{len(outs)} tools checked")
    txt = await call("sample-server", "query_repo",
                     {"feature_request": "checkStrength servant implementation calls pwd_policy_check"})
    heads = [l for l in txt.splitlines() if l.startswith("### ")]
    rels = [l for l in txt.splitlines() if l.split(":")[0] in ("calls", "called by", "tested by", "implements", "calls operation")]
    bad = [l for l in heads if "(" not in l] + [l for l in rels if any("(" not in x for x in l.split(": ", 1)[1].split(", "))]
    check("query_repo text gives the file for every unit and related unit", heads and rels and not bad,
          f"{len(heads)} units, {len(rels)} relation lines" if not bad else bad[0][:80])

    rr = await call("orchestrator_router", "get_relevant_repos",
                    {"requesting_repo": "sample-client", "feature_description": "show the policy hint in the login meter"})
    line = next((l.strip() for l in rr.splitlines() if "★" in l), "")
    check("router picks sample-server with an interface reason", "sample-server" in line and "serves" in line, line)

    print(f"\n   {sum(CHECKS)}/{len(CHECKS)} checks passed")
    sys.exit(0 if all(CHECKS) else 1)


asyncio.run(main())

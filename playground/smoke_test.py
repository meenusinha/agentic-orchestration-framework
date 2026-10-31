#!/usr/bin/env python3
"""Smoke test for the playground: start the repo MCP servers exactly as the generated
config says and check the cross-repo results. Usage: smoke_test.py <pw-client>/.mcp.json"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

for _s in (sys.stdout, sys.stderr):      # Windows consoles/pipes default to cp1252, which cannot print ✓ — … etc.
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

CFG = json.loads(Path(sys.argv[1]).read_text())["mcpServers"]


async def call(server, tool, args):
    c = CFG[server]
    async with stdio_client(StdioServerParameters(command=c["command"], args=c["args"], env=c["env"])) as (r, w), \
            ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool(tool, args)
        out = res.structuredContent if res.structuredContent is not None else res.content[0].text
        if isinstance(out, dict) and set(out) == {"result"}:
            out = out["result"]
        return out


CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append(ok)
    print(f"   {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""), flush=True)


async def main():
    # servers start in order: the server repo publishes Auth::IPasswordService, the client links
    # to it and publishes ISessionListener, then the server re-resolves and links to that
    await call("pw-server", "search_code", {"query": "password policy", "k": 1})
    await call("pw-client", "search_code", {"query": "login dialog", "k": 1})

    r = await call("pw-server", "search_code", {"query": "how is password strength scored", "k": 3})
    names = [x["node"]["qualname"] for x in r["results"]]
    check("search returns whole units", any("pwd_policy_check" in n or "checkStrength" in n for n in names), names[0])

    t = await call("pw-client", "trace_interface", {"name": "IPasswordService"})
    provs = [(p["repo"], p["process"]) for p in t["providers"]]
    cons = sorted({c["caller"] for c in t["consumers"]})
    check("IPasswordService: defined in pw-server, served by process authd", ("pw-server", "authd") in provs,
          f"providers {provs}")
    check("IPasswordService: C++, C and Python clients in pw-client",
          {"LoginDialog::onPasswordChanged", "legacy_strength", "weak_ratio"} <= set(cons), ", ".join(cons))

    t = await call("pw-server", "trace_interface", {"name": "ISessionListener"})
    provs = [(p["repo"], p["server_class"]) for p in t["providers"]]
    cons = [(c["repo"], c["caller"]) for c in t["consumers"]]
    check("ISessionListener (in-house .xidl): implemented by pw-client", any(r == "pw-client" for r, _ in provs),
          f"providers {provs}")
    check("ISessionListener: called back by pw-server", any(r == "pw-server" for r, _ in cons), f"consumers {cons}")

    i = await call("pw-server", "impact_of", {"symbol": "pwd_policy_check"})
    reach = sorted({c["repo"] for t2 in i["interfaces"] for c in t2["consumers"]})
    tests = [x["test"] for l in i["local"] for x in l["tested_by"]]
    check("impact of pwd_policy_check reaches pw-client through the IDL", "pw-client" in reach, f"repos {reach}")
    check("impact lists test evidence", len(tests) >= 2, ", ".join(tests))

    q = await call("pw-server", "search_code", {"query": "database connection credentials", "k": 5})
    blob = json.dumps(q)
    check("fake secrets are redacted", "Playground-Fake-Pass" not in blob and "PlaygroundFakeKey" not in blob,
          f"{blob.count('[REDACTED]')} × [REDACTED]")

    rr = await call("orchestrator_router", "get_relevant_repos",
                    {"requesting_repo": "pw-client", "feature_description": "show the policy hint in the login meter"})
    line = next((l.strip() for l in rr.splitlines() if "★" in l), "")
    check("router picks pw-server with an interface reason", "pw-server" in line and "serves" in line, line)

    print(f"\n   {sum(CHECKS)}/{len(CHECKS)} checks passed")
    sys.exit(0 if all(CHECKS) else 1)


asyncio.run(main())

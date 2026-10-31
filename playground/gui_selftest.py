#!/usr/bin/env python3
"""End-to-end test of the setup GUI through its HTTP API — runs the same on Windows, macOS and Linux.

Starts `setup_gui.py --no-browser` exactly as a user would (it creates <framework>/.venv),
then performs every step the page performs: install → copy the model → create the
playground repos → scan → configure → create the GraphRAG → queries. It also checks the
security guards and that every query result is saved under a new name.

Usage (any Python 3.11+, no venv needed — the GUI makes its own):
  python playground/gui_selftest.py --model-from <folder holding the model> [--dir <playground dir>]

Exit code 0 = all checks passed.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

FW = Path(__file__).resolve().parent.parent
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"   {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""), flush=True)


class GUI:
    def __init__(self, url):
        self.base, self.tok = url.split("/#t=")[0], url.split("#t=")[1]

    def call(self, path, body=None, token=True, ctype="application/json", host=None):
        headers = {"Content-Type": ctype}
        if token:
            headers["X-Token"] = self.tok
        if host:
            headers["Host"] = host
        data = None if body is None else (json.dumps(body).encode() if isinstance(body, (dict, list)) else body)
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=1800) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}

    def job(self, path, body):
        code, j = self.call(path, body)
        if code != 200:
            return False, [j.get("error", str(code))], None
        jid, since, lines = j["job"], 0, []
        while True:
            _, s = self.call(f"/api/job/{jid}?since={since}")
            since = s["next"]
            for l in s["lines"]:
                lines.append(l)
                print("      | " + l[:150], flush=True)
            if s["done"]:
                return s["ok"], lines, s["result"]
            time.sleep(1)


def start_gui(env):
    p = subprocess.Popen([sys.executable, "-u", str(FW / "setup_gui.py"), "--no-browser"], cwd=FW,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", env=env)
    url, deadline = None, time.time() + 300
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        print("   gui> " + line.rstrip(), flush=True)
        m = re.search(r"http://127\.0\.0\.1:\d+/#t=[\w-]+", line)
        if m:
            url = m.group(0)
            break
    if not url:
        p.kill()
        sys.exit("FAIL: the GUI did not start")
    threading.Thread(target=lambda: [None for _ in p.stdout], daemon=True).start()   # keep draining
    return p, url


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-from", required=True, help="folder holding the approved embedding model")
    ap.add_argument("--dir", help="where to create the playground repos (default: a temp folder)")
    a = ap.parse_args()
    pg_dir = Path(a.dir) if a.dir else Path(tempfile.mkdtemp(prefix="ao-selftest-")) / "ao-playground"
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    env.pop("VIRTUAL_ENV", None)

    print(f"\n1. Start the GUI ({sys.platform})", flush=True)
    proc, url = start_gui(env)
    g = GUI(url)
    try:
        venv_py = FW / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        check("GUI created and runs in <framework>/.venv", venv_py.exists())
        code, st = g.call("/api/state")
        check("state endpoint", code == 200 and st.get("in_venv"), f"python {st.get('python')}")

        print("\n2. Security guards", flush=True)
        check("API refuses a missing token", g.call("/api/state", token=False)[0] == 403)
        check("API refuses a forged Host (DNS rebinding)", g.call("/api/state", host="evil.example")[0] == 403)
        check("POST refuses non-JSON bodies", g.call("/api/install", b"a=1",
                                                     ctype="application/x-www-form-urlencoded")[0] == 415)

        print("\n3. Install", flush=True)
        ok, lines, _ = g.job("/api/install", {})
        check("install (hash-verified) succeeded", ok, lines[-1] if lines else "")
        check("packages importable", g.call("/api/state")[1].get("deps_ok"))

        print("\n4. Model (copy only)", flush=True)
        ok, lines, _ = g.job("/api/model", {"mode": "download"})
        check("a download request is refused", not ok, lines[-1] if lines else "")
        bad = Path(tempfile.mkdtemp(prefix="ao-badmodel-"))
        (bad / "model.safetensors").write_bytes(b"not a model")
        ok, lines, _ = g.job("/api/model", {"path": str(bad)})
        check("an unusable folder is refused with the reason", not ok and "not usable" in (lines[-1] if lines else ""),
              lines[-1][-90:] if lines else "")
        ok, lines, _ = g.job("/api/model", {"path": a.model_from})
        check("model copied and hash-verified", ok and g.call("/api/state")[1].get("model_ok"), lines[-1] if lines else "")

        print("\n5. Folder browser", flush=True)
        code, fs = g.call("/api/fs?path=")
        check("lists the home folder", code == 200 and fs.get("dirs") is not None, fs.get("path", ""))
        top = fs
        for _ in range(40):                                     # walk up to the top
            if not top.get("parent"):
                break
            top = g.call("/api/fs?path=" + urllib.request.quote(top["parent"]))[1]
        if os.name == "nt":
            check("Windows: top level lists the drives", top.get("path") == "This PC" and
                  any(re.match(r"^[A-Z]:\\$", d["path"]) for d in top.get("dirs", [])),
                  ", ".join(d["name"] for d in top.get("dirs", [])))
        else:
            check("reaches the file-system root", top.get("path") == "/")

        print("\n6. Repositories", flush=True)
        ok, lines, res = g.job("/api/playground", {"dir": str(pg_dir), "reset": True})
        check("playground repos created", ok and res and len(res["repos"]) == 2, str(pg_dir))
        if not ok:
            raise SystemExit(1)
        ok2, lines2, _ = g.job("/api/playground", {"dir": str(pg_dir), "reset": True})
        check("re-creating the playground (reset) works", ok2, lines2[-1] if lines2 else "")
        foreign = Path(tempfile.mkdtemp(prefix="ao-notpg-"))
        (foreign / "keep.txt").write_text("user data")
        ok3, lines3, _ = g.job("/api/playground", {"dir": str(foreign), "reset": True})
        check("refuses to replace a folder that is not a playground", not ok3 and (foreign / "keep.txt").exists(),
              lines3[-1][-80:] if lines3 else "")
        check("the GUI is not blocked after a failed step", g.call("/api/state")[0] == 200 and
              g.job("/api/playground", {"dir": str(pg_dir), "reset": True})[0])

        print("\n7. Review (scan)", flush=True)
        code, sc = g.call("/api/scan", {"repos": [{"path": p} for p in res["repos"]]})
        scans = sc.get("repos", [])
        by = {s["name"]: s for s in scans}
        check("both repos scanned", code == 200 and set(by) == {"pw-server", "pw-client"})
        check("processes found from the Makefile", {"authd"} <= {t["name"] for t in by.get("pw-server", {}).get("targets", [])})
        check("IDL interfaces found", "Auth::IPasswordService" in {i["name"] for i in by.get("pw-server", {}).get("interfaces", [])})
        unk = {u["ext"]: (u["kind"], u["parse_as"]) for s in scans for u in s.get("unknown_types", [])}
        check("unknown types get a suggestion", unk.get(".xidl") == ("interface", "idl") and unk.get(".xcfg", ("",))[0] == "config",
              str(unk))

        print("\n8. Configure", flush=True)
        plan = [{"name": s["name"], "path": s["path"], "roles": [],
                 "file_types": {u["ext"]: {"kind": u["kind"], "parse_as": u["parse_as"] or "text"}
                                for u in s["unknown_types"]}} for s in scans]
        ok, lines, _ = g.job("/api/configure", {"repos": plan})
        check("configured", ok, lines[-1] if lines else "")
        mcp = Path(res["repos"][1]) / ".mcp.json"
        cfg = json.loads(mcp.read_text(encoding="utf-8")) if mcp.exists() else {}
        srv = next(iter(cfg.get("mcpServers", {}).values()), {})
        check("MCP config uses the venv Python and UTF-8 mode",
              Path(srv.get("command", "")).resolve() == venv_py.resolve() and srv.get("env", {}).get("PYTHONUTF8") == "1",
              srv.get("command", ""))
        code, q = g.call("/api/query", {"mode": "search", "text": "x"})
        check("queries refused before the GraphRAG exists", code == 409)

        print("\n9. Create GraphRAG", flush=True)
        ok, lines, v = g.job("/api/verify", {})
        check("GraphRAG created", ok and g.call("/api/state")[1].get("graph_ready"), lines[-1] if lines else "")
        ifs = {i["interface"]: i for i in (v or {}).get("interfaces", [])}
        ps = ifs.get("Auth::IPasswordService", {})
        check("IPasswordService served by authd", any(p["process"] == "authd" for p in ps.get("providers", [])))
        check("IPasswordService called from pw-client", any(c["repo"] == "pw-client" for c in ps.get("consumers", [])))
        check("callback ISessionListener served by ui-shell",
              any(p["process"] == "ui-shell" for p in ifs.get("SessionEvents::ISessionListener", {}).get("providers", [])))

        print("\n10. Queries", flush=True)
        q1 = "which processes are affected if PWD_MIN_LEN changes?"
        code, r = g.call("/api/query", {"mode": "search", "text": q1})
        names = [h["node"]["qualname"] for x in r.get("result", {}).get("repos", []) for h in x["hits"]]
        check("search returns whole units", code == 200 and "PWD_MIN_LEN" in names, ", ".join(names[:5]))
        code, r2 = g.call("/api/query", {"mode": "search", "text": q1})
        check("same query again → a new file, nothing overwritten",
              code == 200 and r2.get("saved") != r.get("saved") and Path(r["saved"]).exists() and Path(r2["saved"]).exists(),
              Path(r2.get("saved", "")).name)
        code, t = g.call("/api/query", {"mode": "trace", "text": "IPasswordService"})
        tr = t.get("result", {}).get("trace", {})
        check("trace interface", code == 200 and tr.get("found") and tr.get("providers"))
        code, im = g.call("/api/query", {"mode": "impact", "text": "pwd_policy_check"})
        check("impact of change", code == 200 and im.get("result", {}).get("impact"))
        code, h = g.call("/api/results")
        n = len(h.get("results", []))
        check("saved results listed", n >= 4, f"{n} files in {h.get('dir')}")
        code, m = g.call("/api/result?name=" + urllib.request.quote(h["results"][0]["name"]))
        check("a saved result can be reopened", code == 200 and m.get("markdown", "").startswith("# "))
        check("path traversal refused", g.call("/api/result?name=..%2Fsetup_gui.py")[0] == 404)
        txt = Path(r["saved"]).read_text(encoding="utf-8")
        check("reports are UTF-8 with the full code", "#define PWD_MIN_LEN" in txt and "·" in txt)

        print("\n11. Restart: the GUI opens on the queries", flush=True)
        g.call("/api/quit", {})
        proc.wait(timeout=30)
        proc, url = start_gui(env)
        g = GUI(url)
        st = g.call("/api/state")[1]
        check("after restart everything is ready", st.get("deps_ok") and st.get("model_ok") and st.get("graph_ready"))
        code, r3 = g.call("/api/query", {"mode": "trace", "text": "ISessionListener"})
        check("queries work after restart", code == 200 and r3["result"]["trace"].get("found"))
    finally:
        try:
            g.call("/api/quit", {})
            proc.wait(timeout=30)
        except Exception:
            proc.kill()

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n   {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" +
          (f" — FAILED: {', '.join(failed)}" if failed else ""), flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

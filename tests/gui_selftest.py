#!/usr/bin/env python3
"""End-to-end test of the setup GUI through its HTTP API — runs the same on Windows and Linux.

Starts `setup_gui.py --no-browser` exactly as a user would (it creates <framework>/.venv),
then performs every step the page performs: install → copy the model → create the
sample repos → scan → configure → create the GraphRAG → queries. It also checks the
security guards and that every query result is saved under a new name.

Usage (any Python 3.11+, no venv needed — the GUI makes its own):
  python tests/gui_selftest.py [--model-from <folder holding the model>] [--dir <folder for the test repos>]

Without --model-from, the model bundled in models/ is used: it is moved aside while the
test runs (the model step is tested from an empty folder) and always restored afterwards.

The two sample repositories are built locally from tests/fixtures/ as git repositories
(branch main; the client also gets a policy-hint branch) and cloned through the GUI from
file:// URLs — no network access to any repository host.

Exit code 0 = all checks passed. (Windows and Linux are the supported platforms; to run the
tests elsewhere, set AO_ALLOW_UNSUPPORTED_OS=1.)
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

for _s in (sys.stdout, sys.stderr):      # Windows consoles/pipes default to cp1252, which cannot print ✓ — … etc.
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

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


def make_fixture_remotes(base: Path) -> dict:
    """Bare git repositories built from tests/fixtures: main for both, plus a policy-hint
    branch for the client. Returns file:// URLs."""
    import shutil
    fx = FW / "tests" / "fixtures"
    git = ["git", "-c", "user.name=tests", "-c", "user.email=tests@example.invalid", "-c", "core.autocrlf=false"]
    out = {}
    for name in ("sample-server", "sample-client"):
        work = base / "work" / name
        shutil.copytree(fx / name, work)
        subprocess.run(git + ["init", "-q", "-b", "main", str(work)], check=True)
        subprocess.run(git + ["-C", str(work), "add", "-A"], check=True)
        subprocess.run(git + ["-C", str(work), "commit", "-qm", "sample repository"], check=True)
        overlay = fx / f"{name}.policy-hint"
        if overlay.is_dir():
            subprocess.run(git + ["-C", str(work), "checkout", "-q", "-b", "policy-hint"], check=True)
            shutil.copytree(overlay, work, dirs_exist_ok=True)
            subprocess.run(git + ["-C", str(work), "commit", "-qam", "login dialog shows the policy"], check=True)
            subprocess.run(git + ["-C", str(work), "checkout", "-q", "main"], check=True)
        bare = base / f"{name}.git"
        subprocess.run(git + ["clone", "-q", "--bare", str(work), str(bare)], check=True)
        out[name] = bare.resolve().as_uri()
    return out


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
    ap.add_argument("--model-from", help="folder holding the approved embedding model "
                                         "(default: the model bundled in models/)")
    ap.add_argument("--dir", help="folder to clone the test repos into (default: a temp folder)")
    ap.add_argument("--server-url", help="git URL of the server test repo (default: built from tests/fixtures)")
    ap.add_argument("--client-url", help="git URL of the client test repo (default: built from tests/fixtures)")
    a = ap.parse_args()
    # The model step is tested from an empty models/ folder: the bundled model is moved aside
    # for the duration of the test, used as the copy source, and always put back afterwards.
    import shutil
    bundled = FW / "models" / "granite-embedding-small-english-r2"
    backup = None
    if (bundled / "model.safetensors").exists():
        backup = Path(tempfile.mkdtemp(prefix="ao-bundled-model-")) / "model"
        shutil.move(str(bundled), str(backup))
        bundled.mkdir()
        a.model_from = a.model_from or str(backup)
    if not a.model_from:
        sys.exit("ERROR: no bundled model in models/ — pass --model-from <folder holding the model>")
    try:
        _run(a)
    finally:
        if backup is not None:                   # restore the bundled model exactly as it was
            shutil.rmtree(bundled, ignore_errors=True)
            shutil.move(str(backup), str(bundled))
            print(f"   (bundled model restored in {bundled})", flush=True)


def _run(a):
    pg_dir = Path(a.dir) if a.dir else Path(tempfile.mkdtemp(prefix="ao-selftest-"))
    pg_dir.mkdir(parents=True, exist_ok=True)
    if not (a.server_url and a.client_url):
        remotes = make_fixture_remotes(Path(tempfile.mkdtemp(prefix="ao-remotes-")))
        a.server_url = a.server_url or remotes["sample-server"]
        a.client_url = a.client_url or remotes["sample-client"]
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
        import shutil
        tampered = Path(tempfile.mkdtemp(prefix="ao-tampered-")) / "model"
        shutil.copytree(a.model_from, tampered)
        cfgf = tampered / "config.json"
        cfgf.write_text(cfgf.read_text(encoding="utf-8").rstrip().rstrip("}") + ', "extra_setting": true}', encoding="utf-8")
        ok, lines, _ = g.job("/api/model", {"path": str(tampered)})
        check("hash-pinning: a changed config.json is refused when copying, by name",
              not ok and "config.json" in (lines[-1] if lines else "") and "SHA-256" in (lines[-1] if lines else ""),
              lines[-1][-90:] if lines else "")
        def pins(rel):        # {file: sha256} pinned in a source file
            txt = (FW / rel).read_text(encoding="utf-8")
            return dict(re.findall(r'"([\w./]+\.(?:json|safetensors))":\s+"([0-9a-f]{64})"', txt))
        pr, pc = pins("repo-agent/mcp/repo_rag.py"), pins("skills/cross-repo-feature-analysis/scripts/configure.py")
        check("hash-pinning: the two fingerprint tables are identical (8 files)", pr == pc and len(pr) == 8,
              f"{len(pr)} / {len(pc)} files")
        crlf = Path(tempfile.mkdtemp(prefix="ao-crlf-")) / "model"
        shutil.copytree(a.model_from, crlf)
        for jf in crlf.rglob("*.json"):                          # what a Windows checkout may do
            jf.write_bytes(jf.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        r = subprocess.run([str(venv_py), "-c",
                            "import sys; sys.path.insert(0, 'repo-agent/mcp'); from repo_rag import verify_model_dir\n"
                            "try:\n    verify_model_dir(sys.argv[1]); print('ACCEPTED')\n"
                            "except RuntimeError as e:\n    print('REFUSED:', e)", str(crlf)],
                           cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONUTF8": "1", "AO_ALLOW_NO_VENV": "1"})
        out = (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else ""
        check("hash-pinning: JSON files with Windows line endings are accepted (server-start check)",
              out == "ACCEPTED", out[-80:])
        ok, lines, _ = g.job("/api/model", {"path": str(crlf)})   # the rest of the run uses this copy
        check("model copied and hash-verified (JSON files with Windows line endings)", ok and g.call("/api/state")[1].get("model_ok"), lines[-1] if lines else "")

        r = subprocess.run([str(venv_py), "-c",
                            "import sys; sys.path.insert(0, 'repo-agent/mcp'); from repo_rag import verify_model_dir\n"
                            "try:\n    verify_model_dir(sys.argv[1]); print('ACCEPTED')\n"
                            "except RuntimeError as e:\n    print('REFUSED:', e)", str(tampered)],
                           cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONUTF8": "1", "AO_ALLOW_NO_VENV": "1"})
        out = (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else ""
        check("hash-pinning: the server-start check refuses the changed config.json too",
              out.startswith("REFUSED") and "config.json" in out, out[-90:])

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

        print("\n6. Repositories (clone from Git: URL + branch, into a chosen folder)", flush=True)
        ok, lines, srv = g.job("/api/clone", {"url": a.server_url, "branch": "main", "parent": str(pg_dir)})
        check("sample-server cloned (branch main)", ok and srv and srv["branch"] == "main", srv and srv["path"])
        if not ok:
            raise SystemExit(1)
        ok, lines, cli = g.job("/api/clone", {"url": a.client_url, "branch": "policy-hint", "parent": str(pg_dir)})
        check("sample-client cloned on branch policy-hint", ok and cli and cli["branch"] == "policy-hint", cli and cli["commit"])
        ok, lines, cli = g.job("/api/clone", {"url": a.client_url, "branch": "main", "parent": str(pg_dir)})
        check("existing clone reused and switched to main", ok and cli and cli["branch"] == "main" and
              any("already a clone" in l for l in lines), cli and cli["commit"])
        res = {"repos": [srv["path"], cli["path"]]}
        ok, lines, _ = g.job("/api/clone", {"url": a.client_url, "branch": "no-such-branch", "parent": str(pg_dir)})
        check("a missing branch fails with a message", not ok and "could not switch" in (lines[-1] if lines else ""),
              lines[-1][-80:] if lines else "")
        ok, lines, _ = g.job("/api/clone", {"url": a.server_url, "branch": "main", "parent": str(pg_dir), "name": "sample-client"})
        check("never reuses a clone of a different repository", not ok and "different repository" in (lines[-1] if lines else ""))
        foreign = Path(tempfile.mkdtemp(prefix="ao-notrepo-"))
        (foreign / "sample-server").mkdir()
        (foreign / "sample-server" / "keep.txt").write_text("user data")
        ok, lines, _ = g.job("/api/clone", {"url": a.server_url, "parent": str(foreign)})
        check("never overwrites an existing folder", not ok and (foreign / "sample-server" / "keep.txt").exists() and
              not (foreign / "sample-server" / ".git").exists(), lines[-1][-70:] if lines else "")
        marker = Path(tempfile.gettempdir()) / "ao-ext-pwned"
        for bad, why in [({"url": f"ext::sh -c touch% {marker}", "parent": str(pg_dir)}, "ext:: transport"),
                         ({"url": "--upload-pack=touch /tmp/x", "parent": str(pg_dir)}, "option-like URL"),
                         ({"url": a.server_url, "branch": "--force", "parent": str(pg_dir)}, "option-like branch"),
                         ({"url": a.server_url, "branch": "main", "parent": ""}, "no folder chosen"),
                         ({"url": a.server_url, "parent": str(pg_dir), "name": "../escape"}, "path in folder name")]:
            ok, lines, _ = g.job("/api/clone", bad)
            check(f"refused: {why}", not ok, lines[-1][-70:] if lines else "")
        check("ext:: did not run anything", not marker.exists())
        check("the GUI is not blocked after failed steps", g.call("/api/state")[0] == 200 and
              g.job("/api/clone", {"url": a.server_url, "branch": "main", "parent": str(pg_dir)})[0])

        print("\n7. Review (scan)", flush=True)
        code, sc = g.call("/api/scan", {"repos": [{"path": p} for p in res["repos"]]})
        scans = sc.get("repos", [])
        by = {s["name"]: s for s in scans}
        check("both repos scanned", code == 200 and set(by) == {"sample-server", "sample-client"})
        check("processes found from the Makefile", {"authd"} <= {t["name"] for t in by.get("sample-server", {}).get("targets", [])})
        check("IDL interfaces found", "Auth::IPasswordService" in {i["name"] for i in by.get("sample-server", {}).get("interfaces", [])})
        unk = {u["ext"]: (u["kind"], u["parse_as"]) for s in scans for u in s.get("unknown_types", [])}
        check("unknown types get a suggestion", unk.get(".xidl") == ("interface", "idl") and unk.get(".xcfg", ("",))[0] == "config",
              str(unk))

        print("\n8. Configure", flush=True)
        plan = [{"name": s["name"], "path": s["path"], "roles": [],
                 "file_types": {u["ext"]: {"kind": u["kind"], "parse_as": u["parse_as"] or "text"}
                                for u in s["unknown_types"]}} for s in scans]
        ok, lines, _ = g.job("/api/configure", {"repos": plan})
        check("configured", ok, lines[-1] if lines else "")
        mcp = Path(res["repos"][1]) / ".vscode" / "mcp.json"
        cfg = json.loads(mcp.read_text(encoding="utf-8")) if mcp.exists() else {}
        srv = next(iter(cfg.get("servers", {}).values()), {})
        repo1 = Path(res["repos"][1])
        check("VS Code + Copilot files generated (MCP config, Copilot instructions, agent skill)",
              (repo1 / ".vscode" / "mcp.json").exists() and
              (repo1 / ".github" / "copilot-instructions.md").exists() and
              (repo1 / ".github" / "skills" / "cross-repo-feature-analysis" / "SKILL.md").exists())
        check("VS Code MCP config uses the venv Python and UTF-8 mode",
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
        check("IPasswordService called from sample-client", any(c["repo"] == "sample-client" for c in ps.get("consumers", [])))
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
        if os.name != "nt":                      # Windows uses ACLs, not mode bits
            rp = Path(r["saved"])
            check("saved reports are private (folder 0700, file 0600)",
                  (rp.stat().st_mode & 0o777) == 0o600 and (rp.parent.stat().st_mode & 0o777) == 0o700,
                  f"{oct(rp.parent.stat().st_mode & 0o777)} / {oct(rp.stat().st_mode & 0o777)}")
        txt = Path(r["saved"]).read_text(encoding="utf-8")
        check("reports are UTF-8 with the full code", "#define PWD_MIN_LEN" in txt and "·" in txt)

        print("\n11. Knowledge graph (tree view)", flush=True)
        q = urllib.request.quote
        code, ov = g.call("/api/graph/overview")
        check("graph overview for both repos", code == 200 and {o["repo"] for o in ov["repos"] if o["ok"]} ==
              {"sample-server", "sample-client"}, "; ".join(f"{o['repo']}: {o.get('nodes')} nodes" for o in ov.get("repos", [])))
        code, root = g.call("/api/graph/children?repo=sample-server&node=")
        names = [c["name"] for c in root.get("children", [])]
        check("tree root: processes, IDL interfaces, folders", code == 200 and "Processes & libraries" in names and
              "idl/" in names and "src/" in names, ", ".join(names[:8]))
        src = g.call("/api/graph/children?repo=sample-server&node=" + q("dir:src"))[1]["children"]
        srv = next((c for c in src if c["name"] == "server.cpp"), {})
        ns = g.call("/api/graph/children?repo=sample-server&node=" + srv.get("id", "x"))[1].get("children", [])
        methods = g.call("/api/graph/children?repo=sample-server&node=" + ns[0]["id"])[1]["children"] if ns else []
        cs = next((m for m in methods if m["name"] == "checkStrength"), None)
        check("folder → file → namespace → method", cs is not None, " / ".join(["src", "server.cpp"] +
              [n["name"] for n in ns[:1]] + ([cs["name"]] if cs else [])))
        code, d = g.call("/api/graph/node?repo=sample-server&uid=" + (cs or {}).get("id", "x"))
        check("node detail has the complete code and its links", code == 200 and "pwd_policy_check" in d.get("text", "")
              and "implements" in d.get("links_out", {}), ", ".join(d.get("links_out", {})))
        code, f = g.call("/api/graph/find?q=checkStrength")
        op = next((r for r in f.get("results", []) if r["kind"] == "idl_operation"), None)
        code, od = g.call(f"/api/graph/node?repo={op['repo']}&uid={op['uid']}") if op else (0, {})
        xr = {x["repo"] for v in od.get("links_in", {}).values() for x in v}
        check("IDL operation shows its callers in the other repo", "sample-client" in xr, ", ".join(od.get("links_in", {})))
        check("graph API refuses bad ids and unknown repos",
              g.call("/api/graph/children?repo=sample-server&node=" + q("x' OR 1=1 --"))[0] == 404 and
              g.call("/api/graph/children?repo=nope&node=")[0] == 404)

        print("\n12. Restart: the GUI opens on the queries", flush=True)
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

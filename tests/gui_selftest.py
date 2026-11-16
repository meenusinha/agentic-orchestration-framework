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


import argparse
import json
import re
import subprocess
import shutil
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



def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"   {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""), flush=True)


class GUI:
    def __init__(self, url):
        self.base, self.tok = url.split("/#t=")[0], url.split("#t=")[1]
        self.progress = []                       # progress-bar events seen by the last job

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
        self.progress = []
        while True:
            _, s = self.call(f"/api/job/{jid}?since={since}")
            since = s["next"]
            if s.get("progress") and s["progress"] not in self.progress:
                self.progress.append(s["progress"])
            for l in s["lines"]:
                lines.append(l)
                print("      | " + l[:150], flush=True)
            if s["done"]:
                return s["ok"], lines, s["result"]
            time.sleep(0.3)


def read_yaml(path) -> dict:
    """A YAML file, read with the framework's virtual environment (this test runs on any
    Python, which may not have PyYAML)."""
    py = FW / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    r = subprocess.run([str(py), "-c", "import json, sys, yaml; print(json.dumps(yaml.safe_load("
                        "open(sys.argv[1], encoding='utf-8').read()) or {}))", str(path)],
                       capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": ""})
    return json.loads(r.stdout or "{}")


def check_components():
    """Grouping into components (standard library only, tested in-process)."""
    sys.path.insert(0, str(FW / "repo-agent" / "mcp"))
    from codegraph import components as C
    files = [(f"src/mod{i % 6}/f{i}.c", 1000) for i in range(900)] + \
            [(f"docs/d{i}.md", 500) for i in range(60)] + [("Makefile", 200), ("src/main.c", 300)]
    comps = {c.path: c for c in C.group(files)}
    check("components: a folder holding most of a large repository is split into its sub-folders",
          "src/mod0" in comps and "src" not in comps and "docs" in comps, ", ".join(sorted(comps)[:5]))
    check("components: top-level files and loose files of a split folder are always included",
          sorted(r for r, _ in comps[C.ALWAYS].files) == ["Makefile", "src/main.c"])
    small = {c.path for c in C.group([("a/x.c", 1), ("b/y.c", 1), ("README.md", 1)])}
    check("components: a small repository is grouped by top-level folder", small == {"a", "b", C.ALWAYS}, str(small))
    odd = "we[ir]d*"
    check("components: folder names with glob characters round-trip through the setting",
          C.from_setting(C.to_setting(odd)) == odd and C.to_setting(odd) != odd)


def make_foreign_pythonpath() -> str:
    """A folder of fake packages that fail on import, like another tool's libraries built for a
    different Python. The tests put it on PYTHONPATH: the framework must never import from it."""
    d = Path(tempfile.mkdtemp(prefix="ao-foreign-pythonpath-"))
    # third-party names, plus standard-library names that old library folders often shadow
    for pkg in ("numpy", "yaml", "mcp", "torch", "pydantic", "pathlib", "enum", "typing", "argparse", "json"):
        (d / pkg).mkdir()
        (d / pkg / "__init__.py").write_text(
            "raise ImportError('foreign PYTHONPATH was used: undefined symbol: _Py_ZeroStruct')\n")
    return str(d)


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
    # a foreign PYTHONPATH (other tools' libraries) must be ignored by everything the GUI runs
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": make_foreign_pythonpath()}
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

        print("\n9. Estimate, then create the GraphRAG in two batches", flush=True)
        check_components()
        ok, lines, est = g.job("/api/estimate", {})
        reps = {r["name"]: r for r in (est or {}).get("repos", [])}
        comps = {n: {c["path"]: c for c in r["components"]} for n, r in reps.items()}
        rates = (est or {}).get("rates", {})
        check("estimate: every repository listed with its components, sizes and status",
              ok and set(reps) == {"sample-server", "sample-client"} and "src" in comps.get("sample-server", {})
              and ".github" not in comps.get("sample-server", {})          # the installed skill is never indexed
              and all(c["files"] > 0 and c["bytes"] > 0 and c["state"] == "new" for c in comps["sample-server"].values()),
              ", ".join(sorted(comps.get("sample-server", {}))))
        check("estimate: speed measured on this computer with the real model (parse + embed per byte)",
              rates.get("embed", 0) > 0 and rates.get("parse", 0) > 0 and rates.get("sample_files", 0) > 0
              and rates.get("model_load", 0) > 0,
              f"{rates.get('sample_files')} files, {rates.get('sample_units')} units, "
              f"embed {rates.get('embed', 0) * 1e6:.0f} µs/byte, model load {rates.get('model_load', 0):.1f}s")
        later = {"sample-server": ["docs", "third_party"], "sample-client": ["docs"]}
        code, bad = g.call("/api/verify", {"deferred": {"sample-server": ["../outside"]}})
        check("batches: a component outside the repository is refused", code == 400, bad.get("error", "")[:80])
        ok, lines, v = g.job("/api/verify", {"deferred": later})
        bars = g.progress
        check("batch 1 created (docs/ and third_party/ left for later)", ok and g.call("/api/state")[1].get("graph_ready"),
              lines[-1] if lines else "")
        check("progress bar: stages and overall progress reported while building",
              bars and {"read", "parse", "embed", "write"} >= {b["stage"] for b in bars} and
              any(b["stage"] == "embed" for b in bars) and bars[-1]["overall"] >= 0.9 and
              all(0 <= b["overall"] <= 1 for b in bars), f"{len(bars)} updates, last {bars[-1] if bars else None}")
        srv_cfg = read_yaml(Path(res["repos"][0]) / "mcp" / "config.yaml")
        check("batches: the components left for later are saved in the repository's settings",
              srv_cfg.get("deferred") == ["docs", "third_party"], str(srv_cfg.get("deferred")))
        tree = [c["name"] for c in g.call("/api/graph/children?repo=sample-server&node=")[1].get("children", [])]
        check("batch 1 holds only the chosen components", "src/" in tree and "docs/" not in tree and "third_party/" not in tree,
              ", ".join(tree))
        ok, lines, est2 = g.job("/api/estimate", {})
        c2 = {c["path"]: c for r in (est2 or {}).get("repos", []) if r["name"] == "sample-server" for c in r["components"]}
        check("estimate after batch 1: built components are in the GraphRAG, the others left for later",
              ok and c2.get("src", {}).get("state") == "indexed" and c2.get("src", {}).get("cached_bytes") == c2.get("src", {}).get("bytes")
              and c2.get("docs", {}).get("deferred") and c2.get("docs", {}).get("state") == "new",
              f"src {c2.get('src', {}).get('state')}, docs {c2.get('docs', {}).get('state')}")
        ok, lines, v = g.job("/api/verify", {"deferred": {"sample-server": [], "sample-client": []}})
        tree = [c["name"] for c in g.call("/api/graph/children?repo=sample-server&node=")[1].get("children", [])]
        check("batch 2: the remaining components added", ok and "docs/" in tree and "third_party/" in tree, ", ".join(tree))
        check("batch 2 re-embeds only the new components (vectors of batch 1 reused)",
              any("reused" in l for l in lines), next((l for l in lines if "reused" in l), "")[-90:])
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
        gaps = units_without_file(r.get("result"), "search") + units_without_file(t.get("result"), "trace") + \
            units_without_file(im.get("result"), "impact")
        check("every unit in every query result carries its file name", not gaps, "; ".join(gaps[:3]))
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
        link_gaps = [f"{k}: {x.get('qualname') or x.get('name')}" for links in (d.get("links_out", {}), od.get("links_in", {}))
                     for k, v in links.items() for x in v if not x.get("file") and not x.get("missing")]
        check("knowledge graph: node details and every link carry a file name",
              d.get("file") and od.get("file") and not link_gaps, "; ".join(link_gaps[:3]))
        check("graph API refuses bad ids and unknown repos",
              g.call("/api/graph/children?repo=sample-server&node=" + q("x' OR 1=1 --"))[0] == 404 and
              g.call("/api/graph/children?repo=nope&node=")[0] == 404)

        print("\n12. Settings, typed paths, model folder in place", flush=True)
        code, st0 = g.call("/api/settings")
        srv0 = next((x for x in st0.get("repos", []) if x["name"] == "sample-server"), {})
        check("settings: every value has a default, and defaults apply",
              code == 200 and srv0.get("values", {}).get("top_k") == 5 and st0["router"]["router_top_repos"] == 2
              and all("default" in x for x in st0["spec"]), f"{len(st0.get('spec', []))} repository settings")
        no_help = [x["key"] for x in st0.get("spec", []) + st0.get("router_spec", []) if len(x.get("help", "")) < 40]
        no_role = [r for r in st0.get("defaults", {}).get("role_weights", {}) if len(st0.get("role_help", {}).get(r, "")) < 10]
        check("settings: every setting and every role has a hover explanation", not no_help and not no_role,
              f"missing: {no_help + no_role}" if no_help or no_role else
              f"{len(st0['spec']) + len(st0['router_spec'])} settings, {len(st0['role_help'])} roles")
        code, bad = g.call("/api/settings", {"repos": {"sample-server": {"top_k": 0, "exclude": ["../outside"]}}})
        check("settings: invalid values are refused with the reason", code == 400 and "sample-server" in bad.get("errors", {}),
              "; ".join(bad.get("errors", {}).get("sample-server", []))[:90])
        cfgp = Path(res["repos"][0]) / "mcp" / "config.yaml"
        code, saved = g.call("/api/settings", {"repos": {"sample-server": {"top_k": 3, "result_budget_tokens": 4000,
                                                                            "exclude": ["third_party/**"]}},
                                               "router": {"router_top_repos": 1}})
        text = cfgp.read_text(encoding="utf-8")
        check("settings: saved, with the config file's comments kept",
              code == 200 and "top_k: 3" in text and "exclude: [third_party/**]" in text and "# Results per search" in text)
        check("settings: a creation change asks for a rebuild", saved.get("rebuild_needed") == ["sample-server"],
              str(saved.get("rebuild_needed")))
        ok, lines, _ = g.job("/api/verify", {})
        tree = g.call("/api/graph/children?repo=sample-server&node=")[1].get("children", [])
        check("rebuild applies the creation settings (third_party/ excluded)", ok and "third_party/" not in
              [c["name"] for c in tree] and "src/" in [c["name"] for c in tree])
        code, rq = g.call("/api/query", {"mode": "search", "text": "password policy strength check"})
        n_srv = next((len(x["hits"]) for x in rq.get("result", {}).get("repos", []) if x["repo"] == "sample-server"), -1)
        check("query settings apply (top_k 3 for sample-server)", code == 200 and 0 < n_srv <= 3, f"{n_srv} results")
        g.call("/api/settings", {"repos": {"sample-server": {"top_k": 5, "result_budget_tokens": 6000, "exclude": []}},
                                 "router": {"router_top_repos": 2}})
        g.job("/api/verify", {})

        print("\n12b. Chunking and skip rules", flush=True)
        policy = Path(res["repos"][0]) / "policy" / "pwd_policy.c"
        original_policy = policy.read_text(encoding="utf-8")
        policy.write_text("/*\n * Author: Jane Example\n * Created: 2019-03-04\n */\n" + original_policy, encoding="utf-8")
        code, pv = g.call("/api/skip_preview", {"repo": "sample-server", "skip_header_words": ["Author", "Created"],
                                                "skip_patterns": [r"^#define PWD_MIN_LEN.*$"]})
        rules = {x["rule"]: x for x in pv.get("rules", [])}
        check("skip preview: files matched per rule, with an example of what is removed",
              code == 200 and rules.get("header", {}).get("files") == 1 and "Jane Example" in (rules["header"].get("example") or {}).get("text", "")
              and rules.get("pattern 1", {}).get("files") == 1, str({k: v["files"] for k, v in rules.items()}))
        code, bad = g.call("/api/settings", {"repos": {"sample-server": {"skip_patterns": ["(a+)+"]}}})
        check("skip rules: a pattern that could take extremely long is refused", code == 400, str(bad.get("errors"))[:90])
        code, bad = g.call("/api/settings", {"repos": {"sample-server": {"chunk_lines": 10, "chunk_overlap": 10}}})
        check("chunking: an overlap as large as the chunk size is refused", code == 400)
        code, saved = g.call("/api/settings", {"repos": {"sample-server": {"chunking": "sliding", "chunk_lines": 8, "chunk_overlap": 2,
                                                                            "skip_header_words": ["Author", "Created"]}}})
        check("chunking and skip rules saved; a rebuild is asked for", code == 200 and saved.get("rebuild_needed") == ["sample-server"])
        ok, lines, _ = g.job("/api/verify", {})
        code, rq = g.call("/api/query", {"mode": "search", "text": "password policy minimum length points", "repo": "sample-server"})
        hits = [h["node"] for x in rq.get("result", {}).get("repos", []) for h in x["hits"]]
        check("sliding window: results are chunks with file and line range, at most the chunk size",
              ok and hits and all(h["kind"] == "chunk" and h["lines"][1] - h["lines"][0] < 8 for h in hits),
              ", ".join(f"{h['file']}:{h['lines'][0]}-{h['lines'][1]}" for h in hits[:4]))
        rels = [h for x in rq.get("result", {}).get("repos", []) for h in x["hits"] if (h.get("relations") or {}).get("covers")]
        check("sliding window: a chunk lists the code it covers, with file names", rels and all(
              c.get("file") for h in rels for c in h["relations"]["covers"]))
        code, t2 = g.call("/api/query", {"mode": "trace", "text": "IPasswordService"})
        check("sliding window: trace interface still works", code == 200 and t2["result"]["trace"].get("found"))
        code, fq = g.call("/api/query", {"mode": "search", "text": "Jane Example author created", "repo": "sample-server"})
        check("skip rules: the author header is never indexed", "Jane Example" not in json.dumps(fq))
        code, im = g.call("/api/query", {"mode": "impact", "text": "pwd_policy_check"})
        node = next((l["node"] for x in im.get("result", {}).get("impact", []) for l in x.get("local", [])
                     if l["node"]["file"] == "policy/pwd_policy.c"), {})
        lineno = next(i for i, l in enumerate(policy.read_text(encoding="utf-8").splitlines(), 1) if "pwd_policy_check(" in l)
        check("skip rules: line numbers still match the file", node.get("lines", [0])[0] == lineno,
              f"graph {node.get('lines')}, file line {lineno}")
        code, _ = g.call("/api/settings", {"repos": {"sample-server": {"chunking": "hybrid"}}})
        ok, lines, _ = g.job("/api/verify", {})
        code, rq = g.call("/api/query", {"mode": "search", "text": "password policy minimum length points", "repo": "sample-server"})
        kinds = {h["node"]["kind"] for x in rq.get("result", {}).get("repos", []) for h in x["hits"]}
        check("hybrid: results are whole units again", ok and kinds and "chunk" not in kinds, ", ".join(sorted(kinds)))
        g.call("/api/settings", {"repos": {"sample-server": {"chunking": "graph", "chunk_lines": 60, "chunk_overlap": 15,
                                                             "skip_header_words": [], "skip_patterns": []}}})
        policy.write_text(original_policy, encoding="utf-8")
        g.job("/api/verify", {})
        check("typed paths: an existing folder is found, a missing one is reported",
              g.call("/api/fs?path=" + q(str(pg_dir)))[1].get("found") is True and
              g.call("/api/fs?path=" + q(str(pg_dir / "no-such-folder")))[1].get("found") is False)
        elsewhere = Path(tempfile.mkdtemp(prefix="ao-model-elsewhere-")) / "granite"
        shutil.copytree(a.model_from, elsewhere)
        ok, lines, _ = g.job("/api/model_path", {"path": str(elsewhere)})
        st = g.call("/api/state")[1]
        check("model folder used in place from a typed path (hash-checked, not copied)",
              ok and st.get("model_ok") and st.get("model_custom") and Path(st["model_dir"]) == elsewhere.resolve(),
              st.get("model_dir", "")[-60:])
        mcpj = json.loads((Path(res["repos"][1]) / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
        check("VS Code configuration points the servers at that folder",
              all(v["env"].get("EMBEDDING_MODEL_PATH") == str(elsewhere.resolve()) for v in mcpj["servers"].values()))
        (elsewhere / "config.json").write_text("{}", encoding="utf-8")
        ok, lines, _ = g.job("/api/model_path", {"path": str(elsewhere)})
        check("a changed model folder is refused when typed in", not ok and "config.json" in (lines[-1] if lines else ""),
              (lines[-1] if lines else "")[-80:])
        ok, lines, _ = g.job("/api/model_path", {"path": ""})
        st = g.call("/api/state")[1]
        check("back to the included model", ok and st.get("model_ok") and not st.get("model_custom"))

        print("\n13. Set up again: choose what to overwrite", flush=True)
        st = g.call("/api/state")[1]
        ex = {r["name"]: r.get("existing", {}) for r in st.get("repos", [])}
        check("state reports what an earlier setup left (config, instructions, skill, GraphRAG)",
              all(ex.get(n) == {"config": True, "instructions": True, "skill": True, "graph": True}
                  for n in ("sample-server", "sample-client")), str(ex.get("sample-client")))
        srv_path, cli_path = Path(res["repos"][0]), Path(res["repos"][1])
        instr = cli_path / ".github" / "copilot-instructions.md"
        skill_md = cli_path / ".github" / "skills" / "cross-repo-feature-analysis" / "SKILL.md"
        instr.write_text(instr.read_text(encoding="utf-8") + "\nMY OWN NOTE\n", encoding="utf-8")
        skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\nMY SKILL EDIT\n", encoding="utf-8")
        g.call("/api/settings", {"repos": {"sample-server": {"top_k": 3, "deferred": ["docs"]}}})
        code, sc = g.call("/api/scan", {"repos": [{"path": p} for p in res["repos"]]})
        by = {s["name"]: s for s in sc.get("repos", [])}
        saved_types = {u["ext"] for s in by.values() for u in s.get("saved_types", [])}
        check("review shows the answers saved before, and the automatic folder roles",
              {".xidl", ".xcfg"} <= saved_types and all("auto_role" in f for f in by["sample-server"]["folders"]),
              ", ".join(sorted(saved_types)))
        extra = pg_dir / "extra-repo"
        (extra / "lib").mkdir(parents=True, exist_ok=True)
        (extra / "lib" / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        (extra / "vendor").mkdir(exist_ok=True)
        (extra / "vendor" / "blob.zzq").write_text("some vendor data\nmore data\n", encoding="utf-8")
        code, sx = g.call("/api/scan", {"repos": [{"path": str(extra)}]})
        ux = next((u for u in sx["repos"][0].get("unknown_types", []) if u["ext"] == ".zzq"), {})
        check("review: an unknown file type says which folders it is in", ux.get("folders") == ["vendor"], str(ux.get("folders")))
        ok, lines, _ = g.job("/api/configure", {"repos": [{"name": "extra-repo", "path": str(extra), "roles": [],
                                                           "file_types": {}, "exclude": []},
                                                          {"name": "sample-client", "path": str(cli_path), "roles": [], "file_types": {},
                                                           "keep": ["instructions", "skill"]}]})
        check("include/exclude: an unknown type in an included folder must still be classified",
              not ok and ".zzq" in " ".join(lines[-12:]), (lines[-1] if lines else "")[-80:])
        ok_bad, lines_bad, _ = g.job("/api/configure", {"repos": [{"name": "extra-repo", "path": str(extra), "roles": [],
                                                                   "file_types": {}, "exclude": ["../outside"]}]})
        check("include/exclude: a path outside the repository is refused", not ok_bad, (lines_bad[-1] if lines_bad else "")[-80:])
        keep_plan = [
            {"name": "sample-server", "path": str(srv_path), "roles": [{"path": "docs/**", "role": "example"}],
             "file_types": {u["ext"]: {"kind": u["kind"], "parse_as": u["parse_as"]} for u in by["sample-server"]["saved_types"]},
             "replace_roles": True, "reset_settings": True, "keep": []},
            {"name": "sample-client", "path": str(cli_path), "roles": [], "file_types": {}, "keep": ["instructions"]},
            {"name": "extra-repo", "path": str(extra), "roles": [], "file_types": {}, "exclude": ["vendor"]}]
        ok, lines, _ = g.job("/api/configure", {"repos": keep_plan})
        srv_cfg = read_yaml(srv_path / "mcp" / "config.yaml")
        cli_cfg = read_yaml(cli_path / "mcp" / "config.yaml")
        check("overwrite chosen: folder roles replaced with the reviewed answers",
              ok and srv_cfg.get("structure") == [{"path": "docs/**", "role": "example"}], str(srv_cfg.get("structure")))
        check("overwrite chosen: GraphRAG settings reset to defaults, components left for later kept",
              srv_cfg.get("top_k") == 5 and srv_cfg.get("deferred") == ["docs"], f"top_k {srv_cfg.get('top_k')}, deferred {srv_cfg.get('deferred')}")
        check("keep chosen: the edited Copilot instructions are left as they were",
              "MY OWN NOTE" in instr.read_text(encoding="utf-8") and any("kept as it was" in l for l in lines))
        check("not kept: the agent skill is reinstalled (overwritten)", "MY SKILL EDIT" not in skill_md.read_text(encoding="utf-8"))
        cli_saved = {u["ext"] for u in by["sample-client"].get("saved_types", [])}
        check("keep: saved file types of a repository are kept when none are sent",
              cli_saved and cli_saved <= set(cli_cfg.get("file_types") or {}),
              ", ".join(sorted(cli_cfg.get("file_types") or {})))
        check("a third repository is connected", "extra-repo" in {r["name"] for r in g.call("/api/state")[1]["repos"]})
        check("include/exclude: an excluded folder is saved, and its unknown file types are not asked",
              read_yaml(extra / "mcp" / "config.yaml").get("exclude") == ["vendor"])
        code, bad = g.call("/api/configure", {"repos": [dict(keep_plan[1], keep=["everything"])]})
        check("an unknown item to keep is refused", code == 400, bad.get("error", "")[:60])
        ok, lines, _ = g.job("/api/configure", {"repos": [keep_plan[1]], "drop": ["extra-repo"]})
        names = {r["name"] for r in g.call("/api/state")[1]["repos"]}
        check("a repository removed in step 3 is disconnected; its files are left as they are",
              ok and names == {"sample-server", "sample-client"} and (extra / "mcp" / "config.yaml").exists(), str(sorted(names)))
        ok, lines, _ = g.job("/api/configure", {"repos": [dict(keep_plan[0], roles=[], keep=[])]})
        srv_cfg = read_yaml(srv_path / "mcp" / "config.yaml")
        check("overwrite with no role changes clears the saved folder roles", ok and srv_cfg.get("structure") == [],
              str(srv_cfg.get("structure")))
        ok, lines, _ = g.job("/api/verify", {"fresh": ["sample-client"], "deferred": {"sample-server": []}})
        check("start from scratch: the repository's stored graph and vectors are removed, then rebuilt",
              ok and any("from scratch" in l for l in lines) and
              any("sample-client" in l and "0 reused" in l for l in lines),
              next((l for l in lines if "0 reused" in l), "")[-80:])
        code, r4 = g.call("/api/query", {"mode": "trace", "text": "IPasswordService"})
        check("queries work after setting up again", code == 200 and r4["result"]["trace"].get("found"))

        print("\n14. Command line of the /graphrag-setup agent skill", flush=True)
        gr = FW / ".github" / "skills" / "graphrag-setup" / "scripts" / "graphrag.py"

        def cli(*args):
            r = subprocess.run([str(venv_py), str(gr), *args], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env={**env, "PYTHONUTF8": "1"}, timeout=1800)
            for l in (r.stdout + r.stderr).splitlines()[-6:]:
                print("      | " + l[:150], flush=True)
            return r.returncode, r.stdout + r.stderr
        code, out = cli("status")
        check("cli status lists the connected repositories and their GraphRAG",
              code == 0 and "sample-server" in out and "sample-client" in out and "GraphRAG built" in out)
        code, out = cli("folders", "sample-server", "--exclude", "docs")
        check("cli folders --exclude saves the exclusion", code == 0 and read_yaml(srv_path / "mcp" / "config.yaml").get("exclude") == ["docs"]
              and re.search(r"no\s+docs/", out))
        code, out = cli("folders", "sample-server", "--include", "docs")
        check("cli folders --include indexes it again", code == 0 and read_yaml(srv_path / "mcp" / "config.yaml").get("exclude") == [])
        code, out = cli("folders", "sample-server", "--exclude", "../outside")
        check("cli refuses a path outside the repository", code != 0 and "inside the repository" in out)
        code, out = cli("skip", "sample-server", "--header-word", "Copyright", "--preview-only")
        check("cli skip --preview-only shows the preview and saves nothing", code == 0 and "files checked" in out
              and "nothing saved" in out and not read_yaml(srv_path / "mcp" / "config.yaml").get("skip_header_words"))
        code, out = cli("chunking", "sample-client", "--overlap", "99")
        check("cli chunking refuses an overlap larger than the saved chunk size", code != 0 and "smaller than the chunk size" in out)
        code, out = cli("estimate")
        check("cli estimate shows the components and the time", code == 0 and "Creating the whole GraphRAG takes" in out
              and re.search(r"(?m)^\s+yes\s+src\b", out))
        code, out = cli("build", "--only", "sample-server:src", "--only", "sample-server:idl", "--only", "sample-client:src",
                        "--only", "sample-client:idl")
        cfg_d = read_yaml(srv_path / "mcp" / "config.yaml").get("deferred") or []
        check("cli build --only creates a batch; the rest is left for later",
              code == 0 and "Still left for later" in out and "docs" in cfg_d and "src" not in cfg_d, str(cfg_d))
        check("cli build prints a progress bar with the stage", re.search(r"\[[#-]{30}\]\s+\d+%.*(parsing|embedding|saving)", out))
        code, out = cli("build", "--add", "sample-server:docs")
        check("cli build --add adds a component", code == 0 and "docs" not in (read_yaml(srv_path / "mcp" / "config.yaml").get("deferred") or []))
        code, out = cli("build", "--add", "sample-server:no-such-folder")
        check("cli refuses an unknown component and lists the real ones", code != 0 and "has no component" in out and "src" in out)
        code, out = cli("build", "--all")
        check("cli build --all: everything in the GraphRAG", code == 0 and "Everything is in the GraphRAG" in out
              and not read_yaml(srv_path / "mcp" / "config.yaml").get("deferred"))
        sk = cli_path / ".github" / "skills"
        check("both agent skills installed in each repository (the setup skill as SKILL.md only)",
              (sk / "graphrag-setup" / "SKILL.md").is_file() and not (sk / "graphrag-setup" / "scripts").exists()
              and (sk / "cross-repo-feature-analysis" / "SKILL.md").is_file())

        print("\n15. Restart: the GUI opens on the queries", flush=True)
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

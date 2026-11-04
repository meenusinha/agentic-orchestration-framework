#!/usr/bin/env python3
"""Run every check in one go — locally or in any CI system, on Windows or Linux.

  1. End-to-end test of the setup page (tests/gui_selftest.py): creates <framework>/.venv,
     installs the hash-locked packages, copies the model, clones two sample repositories
     built locally from tests/fixtures, configures them, builds the GraphRAG, runs queries.
  2. Dependency audit: pip-audit against requirements.lock (known vulnerabilities).
  3. MCP smoke test (tests/smoke_test.py): starts the generated MCP servers as VS Code would.

Usage:
  python tests/run_all.py                        # uses the model bundled in models/
  python tests/run_all.py --model-from <folder>  # or a copy of the approved model from elsewhere
  python tests/run_all.py --skip-audit           # e.g. without access to an advisory database

Exit code 0 = every check passed. Run it on every change and on a schedule (for example
weekly), so new advisories for unchanged pins are noticed.
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
import subprocess
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):      # consoles and pipes may default to a legacy code page
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

FW = Path(__file__).resolve().parent.parent


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


def step(title, cmd, env=None) -> bool:
    print(f"\n━━ {title}\n   $ {' '.join(str(c) for c in cmd)}", flush=True)
    rc = subprocess.call([str(c) for c in cmd], cwd=FW, env=env)
    print(f"   → {'PASSED' if rc == 0 else f'FAILED (exit {rc})'}", flush=True)
    return rc == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-from", help="folder holding the approved embedding model (default: the bundled one)")
    ap.add_argument("--skip-audit", action="store_true", help="skip the dependency vulnerability audit")
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="ao-checks-"))
    py = FW / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    results = {}
    results["end-to-end"] = step("End-to-end test of the setup page",
                                 [sys.executable, FW / "tests" / "gui_selftest.py", "--dir", work / "repos",
                                  *(["--model-from", a.model_from] if a.model_from else [])])
    if not a.skip_audit:
        ok = step("Install the audit tool", [py, "-m", "pip", "install", "-q", "pip-audit"])
        results["dependency audit"] = ok and step(
            "Dependency audit (known vulnerabilities)",
            [py, "-m", "pip_audit", "-r", "requirements.lock", "--require-hashes", "--disable-pip"])
    mcp = work / "repos" / "sample-client" / ".vscode" / "mcp.json"
    results["MCP smoke test"] = mcp.exists() and step(
        "MCP smoke test (with a foreign PYTHONPATH that must be ignored)",
        [py, FW / "tests" / "smoke_test.py", mcp], env={**os.environ, "PYTHONPATH": make_foreign_pythonpath()})
    print("\n━━ Summary")
    for name, ok in results.items():
        print(f"   {'PASS' if ok else 'FAIL'}  {name}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()

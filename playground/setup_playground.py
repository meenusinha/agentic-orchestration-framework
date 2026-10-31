#!/usr/bin/env python3
"""
Playground: two sample repos wired up from scratch, to try the framework end to end.

The two repos live in their own GitHub repositories (they are not part of the framework):
  https://github.com/meenusinha/pw-server   — authd process: IDL (Auth::IPasswordService), C++ servant, C policy
               library, generated stubs, Makefile + compile_commands.json, tests,
               vendored code, docs, an unknown .xcfg file, a FAKE secret
  https://github.com/meenusinha/pw-client   — ui-shell process: C++ and C clients, Python/MATLAB/Perl tools, and an
               in-house .xidl callback interface it implements and the server calls

Everything runs inside the framework's virtual environment (<framework>/.venv): the
script creates it if needed using only the standard library, then re-launches itself
inside it, so nothing is ever installed into your system Python. To deliberately use
the current interpreter instead, pass --no-venv.

Steps (each prints what it does):
  1. clone the two repos into --dir (default ~/ao-playground), or update existing clones
  2. install the hash-locked requirements into the venv
  3. check the embedding model (copy with --model-from, or --download-model)
  4. scan both repos and configure them        (skipped with --guided)
  5. smoke-test through real MCP servers         (skipped with --guided)
  6. print what to try in Claude Code / VS Code / the CLI

Usage (from the framework clone, with Python 3.11+):
  python3 playground/setup_playground.py --download-model
  python3 playground/setup_playground.py --model-from /path/to/granite-embedding-small-english-r2
  python3 playground/setup_playground.py --guided        # let the agent do steps 4–5 with you
  python3 playground/setup_playground.py --dir ~/work --branch main ...   # where to clone, which branch
  The setup GUI (python3 setup_gui.py) does the same with clicks: step 3 → Clone from Git.
  python3 playground/setup_playground.py --no-venv ...   # opt out of the venv (installs into this Python)
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):      # Windows consoles/pipes default to cp1252, which cannot print ✓ — … etc.
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

FW = Path(__file__).resolve().parent.parent
MODEL_DIR = FW / "models" / "granite-embedding-small-english-r2"
SKILL = FW / "skills" / "cross-repo-feature-analysis"
WIN = os.name == "nt"


def step(n, text):
    print(f"\n━━ {n}. {text}", flush=True)


def run(cmd, **kw):
    print("   $ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], **kw)


def venv_python() -> Path:
    return FW / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")


def clone_repos(base: Path, branch: str, urls: dict) -> dict:
    sys.path.insert(0, str(FW / "gui"))
    import gitclone
    base.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, url in urls.items():
        try:
            out[name] = Path(gitclone.clone(url, branch, str(base), name, log=lambda l: print("   " + l))["path"])
        except (ValueError, RuntimeError) as e:
            sys.exit(f"ERROR: {e}")
    return out


def in_framework_venv() -> bool:
    return sys.prefix != sys.base_prefix and Path(sys.prefix).resolve() == (FW / ".venv").resolve()


def enter_venv() -> None:
    """Create <framework>/.venv if needed (standard library only) and re-run this script inside it."""
    py = venv_python()
    if not py.exists():
        print(f"━━ 0. Create the virtual environment {FW / '.venv'}", flush=True)
        run([sys.executable, "-m", "venv", FW / ".venv"], check=True)
    print(f"   → continuing inside the venv: {py}", flush=True)
    sys.exit(subprocess.call([str(py), str(Path(__file__).resolve()), *sys.argv[1:]]))


def ensure_deps():
    """Install the hash-locked requirements into the interpreter running this script."""
    probe = subprocess.run([sys.executable, "-c", "import mcp, sentence_transformers, tree_sitter, yaml"],
                           capture_output=True)
    if probe.returncode != 0:
        run([sys.executable, "-m", "pip", "install", "--quiet", "--timeout", "60", "--retries", "5",
             "--require-hashes", "-r", FW / "requirements.lock"], check=True)
    print(f"   ✓ {sys.executable}")
    return Path(sys.executable)


def model_flags(args, py) -> list:
    if (MODEL_DIR / "model.safetensors").exists():
        print(f"   ✓ model present: {MODEL_DIR}")
        return []
    if args.model_from:
        print(f"   → will copy the model from {args.model_from} (verified by SHA-256)")
        return ["--model-from", args.model_from]
    if args.download_model:
        print("   → will download the model (~95 MB, pinned revision)")
        return ["--download-model"]
    run([py, SKILL / "scripts" / "configure.py", "--framework", FW, "--find-model"])
    sys.exit("\nThe embedding model is missing. Re-run with --model-from <copy shown above or your own> "
             "or --download-model (downloads ~95 MB from Hugging Face).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(Path.home() / "ao-playground"), help="folder to clone the two repos into")
    ap.add_argument("--branch", default="main", help="branch to check out in both repos (default: main)")
    ap.add_argument("--server-url", default="https://github.com/meenusinha/pw-server.git")
    ap.add_argument("--client-url", default="https://github.com/meenusinha/pw-client.git")
    ap.add_argument("--model-from", help="copy the approved embedding model from this folder")
    ap.add_argument("--download-model", action="store_true", help="download the embedding model (~95 MB)")
    ap.add_argument("--guided", action="store_true", help="stop after creating repos: let the agent configure")
    ap.add_argument("--no-venv", action="store_true",
                    help="opt out: install into and run with the current Python instead of <framework>/.venv")
    args = ap.parse_args()
    if args.no_venv:
        os.environ["AO_ALLOW_NO_VENV"] = "1"          # inherited by configure.py, setup.py and the servers
        print(f"WARNING: --no-venv: installing into and running with {sys.executable}", flush=True)
    elif not in_framework_venv():
        enter_venv()
    base = Path(args.dir).expanduser().resolve()
    step(1, f"Clone the sample repos into {base} (branch {args.branch})")
    repos = clone_repos(base, args.branch, {"pw-server": args.server_url, "pw-client": args.client_url})
    server, client = repos["pw-server"], repos["pw-client"]

    step(2, "Install requirements into " + ("the current Python" if args.no_venv else "the venv") +
         " (hash-verified)")
    py = ensure_deps()

    if args.guided:
        dst = client / ".claude" / "skills" / SKILL.name
        shutil.copytree(SKILL, dst, dirs_exist_ok=True)
        print(f"   ✓ skill installed for the agent: {dst}")
        print(f"""
━━ Guided mode: let the agent set things up with you
   cd {client}
   claude
   Then ask:  "Set up cross-repo analysis between this repo and ../pw-server.
               The framework is already cloned at {FW} with its venv at {FW / '.venv'}."
   The agent will scan both repos, ask you about the unknown file types (.xcfg, .xidl),
   ask about the embedding model, and configure everything. Restart claude afterwards.""")
        return

    step(3, "Embedding model")
    mflags = model_flags(args, py)

    step(4, "Scan, then configure both repos")
    run([py, SKILL / "scripts" / "scan.py", "--framework", FW, server, client], check=True)
    print("\n   Answers a developer would give to the scan's questions:")
    print("     .xcfg in pw-server → server configuration (text)")
    print("     .xidl in pw-client → our in-house IDL dialect")
    r = run([py, SKILL / "scripts" / "configure.py", "--framework", FW, *mflags,
             "--repo", f"pw-server={server}", "--src", "pw-server=.",
             "--file-type", "pw-server=.xcfg:server-config:text",
             "--role", "pw-server=third_party/**:external:signatures",
             "--repo", f"pw-client={client}", "--src", "pw-client=.",
             "--file-type", "pw-client=.xidl:interface:idl"])
    if r.returncode != 0:
        sys.exit("configure.py failed — see the messages above")

    step(5, "Smoke test through real MCP servers (first run builds both code graphs)")
    r = run([py, FW / "playground" / "smoke_test.py", client / ".mcp.json"])
    if r.returncode != 0:
        sys.exit("smoke test failed — see above")

    logs = Path.home() / ".agentic-orchestration" / "logs" / "mcp-orchestration.log"
    print(f"""
━━ 6. Try it
   Claude Code:   cd {client} && claude          (approve the project MCP servers; /mcp shows them)
   VS Code:       code {client}                  (allow the servers; start requests with /cross-repo-feature-analysis)
   CLI (Way A):   {py} {FW / 'test_mcp.py'} "add a password strength hint to the login dialog"
   Live log:      tail -f {logs}

   Questions to try:
     • What would it take to show the policy hint in the login dialog's strength meter?
     • Which processes are affected if PWD_MIN_LEN changes?
     • Trace the ISessionListener interface — who implements it and who calls it?
     • Add a new IDL operation isCompromised(pw) — what has to change, in which repo, in what order?
     • Where are database credentials configured?      (watch for [REDACTED])

   Start over:    delete {base} and run this script again""")


if __name__ == "__main__":
    main()

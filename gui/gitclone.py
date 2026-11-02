"""Clone a repository from a URL + branch into a folder the user chose — or update an
existing clone of the same repository and switch it to that branch.

Standard library only (the setup GUI uses it before anything is installed). Safety:
  - the URL must look like a git URL (https/ssh/git/file, git@host:path) or be a local
    repo folder; option-like values and the command-running ext:: / fd:: transports are
    refused; git runs without a shell and with `--` before positional arguments
  - the branch name is validated by `git check-ref-format --branch`
  - the folder name must be a plain name; an existing folder is only reused when it is
    already a clone of the same repository, never overwritten
  - git never waits for a password (GIT_TERMINAL_PROMPT=0): private repositories need
    access set up beforehand (SSH key or credential manager)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

SAMPLE_REPOS: list = []   # no preset repositories: users enter their own
_SCP_URL = re.compile(r"^[\w.-]+@[\w.-]+:[\w./~-]+$")             # git@host:team/repo.git
_SAFE = ["-c", "protocol.ext.allow=never", "-c", "protocol.fd.allow=never"]
_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def available() -> bool:
    return shutil.which("git") is not None


def check_url(url: str) -> str:
    url = (url or "").strip()
    if not url or url.startswith("-") or any(c.isspace() for c in url):
        raise ValueError("enter the repository URL, e.g. https://git.example.com/team/repo.git")
    if re.match(r"^(ext|fd)::", url, re.I):
        raise ValueError("this kind of git URL is not allowed")
    if re.match(r"^(https?|ssh|git|file)://", url, re.I) or _SCP_URL.match(url):
        return url
    if Path(url).expanduser().is_dir():                          # a local repo or bare repo
        return str(Path(url).expanduser().resolve())
    raise ValueError("not a git URL (https://…, ssh://…, git@host:org/repo.git) or an existing local repo folder")


def norm_url(u: str) -> str:
    u = (u or "").strip().rstrip("/")
    u = u[:-4] if u.endswith(".git") else u
    m = re.match(r"^([\w.-]+)@([\w.-]+):(.+)$", u)                 # git@host:org/repo == ssh://git@host/org/repo
    if m:
        u = f"ssh://{m.group(1)}@{m.group(2)}/{m.group(3)}"
    if re.match(r"^[A-Za-z]:[\\/]|^/", u):                        # local paths
        u = str(Path(u).resolve())
    return u.lower()


def name_from_url(url: str) -> str:
    tail = re.split(r"[/:\\]", url.rstrip("/\\"))[-1]
    tail = tail[:-4] if tail.endswith(".git") else tail
    return re.sub(r"[^A-Za-z0-9._-]+", "-", tail).strip("-.") or "repo"


def _out(args, cwd=None) -> tuple[int, str]:
    r = subprocess.run(["git", *_SAFE, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env={**os.environ, **_ENV})
    return r.returncode, (r.stdout or "").strip() or (r.stderr or "").strip()


def _stream(args, log, cwd=None) -> int:
    cmd = ["git", *_SAFE, *[str(a) for a in args]]
    log("$ git " + " ".join(str(a) for a in args))
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", env={**os.environ, **_ENV})
    for line in p.stdout:
        for part in line.replace("\r", "\n").splitlines():       # progress lines use \r
            if part.strip() and not re.search(r"\(\d+/\d+\)$", part.strip()):
                log(part.rstrip())
    return p.wait()


def clone(url: str, branch: str, parent: str, name: str = "", log=print) -> dict:
    """Clone url@branch into parent/name, or update an existing clone of url there. Returns
    {path, name, branch, commit}. Raises ValueError / RuntimeError with a message for the user."""
    if not available():
        raise RuntimeError("Git is not installed. Install it from https://git-scm.com, "
                           "or clone the repo yourself and add its folder instead.")
    url = check_url(url)
    branch = (branch or "").strip()
    if branch and (branch.startswith("-") or _out(["check-ref-format", "--branch", branch])[0] != 0):
        raise ValueError(f"'{branch}' is not a valid branch name")
    if not (parent or "").strip():
        raise ValueError("choose the folder to clone into")
    base = Path(parent).expanduser()
    if not base.is_dir():
        raise ValueError(f"{base} is not an existing folder")
    name = (name or "").strip() or name_from_url(url)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name in (".", ".."):
        raise ValueError("the folder name may only contain letters, digits, '.', '_' and '-'")
    dest = base.resolve() / name

    if dest.exists() and any(dest.iterdir()):
        if not (dest / ".git").exists():
            raise RuntimeError(f"{dest} already exists and is not a git clone — choose another folder or name")
        rc, origin = _out(["remote", "get-url", "origin"], cwd=dest)
        if rc != 0 or norm_url(origin) != norm_url(url):
            raise RuntimeError(f"{dest} is already a clone of a different repository ({origin or 'no origin'})")
        log(f"{dest} is already a clone of this repository — updating it")
        if _stream(["fetch", "--prune", "origin"], log, cwd=dest) != 0:
            raise RuntimeError("git fetch failed (network, or access to the repository — see above)")
        if branch:
            if _stream(["checkout", branch, "--"], log, cwd=dest) != 0:
                raise RuntimeError(f"could not switch to branch '{branch}' — it does not exist, "
                                   "or the clone has uncommitted changes (see above)")
            _, st = _out(["status", "--porcelain", "-b"], cwd=dest)
            if st and "[behind" in st.splitlines()[0]:            # fast-forward to the fetched branch
                _stream(["merge", "--ff-only", "--quiet"], log, cwd=dest)
    else:
        if _stream(["clone", "--progress", *(["--branch", branch] if branch else []), "--", url, dest], log) != 0:
            raise RuntimeError("git clone failed — check the URL and branch; for a private repository, "
                               "set up git access (SSH key or credential manager) first")
    head = _out(["rev-parse", "--abbrev-ref", "HEAD"], cwd=dest)[1]
    commit = _out(["log", "-1", "--format=%h %s"], cwd=dest)[1]
    log(f"✓ {dest} — branch {head} at {commit}")
    return {"path": str(dest), "name": name, "branch": head, "commit": commit}

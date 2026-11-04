# Security Evidence for the Technology Stack

This document gives **verifiable evidence** about every component of the framework's
technology stack: what it is, exactly which version is used, its license, its known
vulnerabilities, how the framework uses it (and therefore how exposed it is), the controls
around it, and the measurements that back those claims up.

> **Scope of the claim.** No software can be proven "completely secure". What this
> document shows is that, as of the audit date below, every component is a current,
> pinned, permissively licensed release with **no known vulnerabilities**, that the
> framework uses each one in its least-exposed way, and that the key properties —
> nothing leaves the machine, no repository code is executed, no unsafe deserialization,
> private storage — were **measured**, not just designed. The evidence is reproducible
> with the commands in §6.

| | |
|---|---|
| Audit date | **2026-11-01** |
| Applies to | this repository (VS Code + GitHub Copilot) |
| Supported platforms | **Windows and Linux** |
| Where measured | A development workstation with Python 3.11.14. Repeat the measurements on your target platform with `tests/run_all.py` and §6 |
| Companion document | [security-considerations.md](security-considerations.md) — data flow and every control in detail |

---

## 1. Results at a glance

| Evidence | Result |
|---|---|
| Known vulnerabilities in locked dependencies (`pip-audit`, PyPI/OSV advisory database) | **0** in the 78 packages installed on the audit workstation; run `tests/run_all.py` on Windows and on Linux to audit each platform's package set (the Linux set includes the CUDA libraries) |
| Dependency integrity | All 103 packages pinned **with SHA-256 hashes** (`requirements.lock`); installation fails on any mismatch |
| Licenses | **All permissive**: 38 MIT, 24 BSD, 11 Apache-2.0, plus certifi (MPL-2.0), tqdm (MPL-2.0 + MIT), shellingham (ISC), typing_extensions (PSF). **No GPL/AGPL** |
| Network use during indexing, querying and MCP tool calls | **0 connection attempts, 0 DNS lookups** — measured with a Python audit hook in every process (positive control proves the hook records attempts) |
| Embedding model | **All 8 model files hash-pinned** and matching; folder holds only `safetensors` + JSON; no `auto_map` (no remote code); built-in module types only |
| Unsafe APIs in the framework code (`pickle`, `torch.load`, `np.load`, `eval`, `exec`, `shell=True`, `os.system`, unsafe `yaml.load`) | **0 uses** (10 matches, all comments or deny-lists that refuse those formats) |
| Repository code executed | **Never** — no subprocess in the indexer; build files are parsed as text |
| Private storage | Graph `drwx------` / `-rw-------`; saved reports `drwx------` / `-rw-------`; logs `drwx------` / `-rw-------` (observed) |
| Secrets | Fake credentials planted in the test fixtures are redacted (`2 × [REDACTED]`) in every result |
| End-to-end tests | GUI self-test **54/54** and MCP smoke test **9/9** for this revision; an earlier revision of the same tests passed on Windows and Linux on 2026-11-01 |

---

## 2. Component evidence

### 2.1 Runtime and core libraries

| Component | Version | License | Reported CVEs (2026-11-01) | How it is used | Exposure | Evidence / controls |
|---|---|---|---|---|---|---|
| **Python** | ≥ 3.11 (audited 3.11.14) | PSF | Track your Python vendor | Runs everything | Local process | Separate virtual environment per installation; the scripts refuse to run outside it unless `AO_ALLOW_NO_VENV=1` |
| **SQLite** (Python `sqlite3`) | 3.50.2 | Public domain | 0 | Stores the code graph and vectors | Local files only; SQL is built with parameters, never from repository text; graph view opens databases read-only | No server, no network; files `0600` |
| **NumPy** | 2.4.6 | BSD | 0 | Vector maths | In-memory | Vectors stored as raw float32 bytes — `np.load`/pickle never used |
| **PyYAML** | 6.0.3 | MIT | 0 | Reads config files | Local config | Only `yaml.safe_load` (no object construction) |
| **Pydantic** | 2.14.1 | MIT | 0 | Typed MCP tool results | In-process | — |

### 2.2 Machine-learning stack (embedding)

| Component | Version | License | Reported CVEs | How it is used | Exposure | Evidence / controls |
|---|---|---|---|---|---|---|
| **sentence-transformers** | 6.2.0 | Apache-2.0 | 0 | Loads the model, computes embeddings | Model folder only | Folder verified before loading: every file hash-pinned, no code/pickle files, built-in module types only; `trust_remote_code=False`, `local_files_only=True` |
| **transformers** | 5.21.0 | Apache-2.0 | 0 | ModernBERT architecture (native) | Same | Hub access disabled before import (`HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, telemetry off) |
| **PyTorch** | 2.14.1 | BSD | 0 | Tensor maths for embeddings | In-process | `torch.load` (pickle) never used; weights come from `safetensors` |
| **safetensors** | 0.8.0 | Apache-2.0 | 0 | Weight file format | Model file | Pure data format: JSON header (7,112 bytes) + raw tensors; cannot carry code |
| **tokenizers** | 0.23.3 | Apache-2.0 | 0 | Tokenisation | In-process | — |
| **huggingface-hub** | 2.3.0 | Apache-2.0 | 0 | Dependency of the above | **Network disabled** | Measured: 0 network attempts |

### 2.3 Embedding model

| Item | Evidence |
|---|---|
| Model | `ibm-granite/granite-embedding-small-english-r2` (IBM), Apache-2.0 |
| Training data | Permissively licensed data plus IBM internal/synthetic data; datasets with non-commercial licenses deliberately excluded (model card) |
| Pinned revision | `2ab6fa8ea2d674564defd37171ae19079b864b33` |
| Integrity | **Hash-pinned: all 8 files** must match their recorded SHA-256 (table below) — checked during setup **and at every start**; any missing or changed file is refused by name |
| Files present | `model.safetensors` (95.3 MB), `config.json`, `modules.json`, `sentence_bert_config.json`, `special_tokens_map.json`, `tokenizer.json`, `tokenizer_config.json`, `1_Pooling/config.json` — **no code, no pickle** |
| Architecture | `ModernBertModel` (supported natively by `transformers`); `auto_map`: **none** → no remote code can be requested |
| Modules | `sentence_transformers.models.Transformer`, `sentence_transformers.models.Pooling` (built-in) |
| Reported CVEs | **0** for this model (checked 2026-11-01) |
| Configuration | `config.json` contains no key that can request code (`auto_map`, `_attn_implementation*`, custom pipelines): checked 2026-11-01 |
| What it outputs | Vectors, never text — it cannot reproduce or leak code |
| Distribution | **Included in the repository** (`models/`), unmodified; nothing is downloaded. Git is configured never to alter the files (`.gitattributes`: no line-ending conversion, no filters) |

#### Hash-pinning: what it is and what is pinned

A hash (SHA-256) is a 64-character fingerprint calculated from every byte of a file; changing
one byte changes it completely. **Hash-pinning** records the expected fingerprint in the
framework's code and refuses any file that doesn't match. For the model this covers not only
the weights but also its settings and tokenizer files. Otherwise a changed `config.json` or
tokenizer could alter how code is processed, or carry settings aimed at a flaw in the loading
software, without being noticed.

Pinned fingerprints (in `repo-agent/mcp/repo_rag.py` and `scripts/configure.py`, kept identical):

| File | SHA-256 |
|---|---|
| `model.safetensors` | `dcbfaf2ee50d358763cbc0c08e5b045ecbf6aeb02dc0e86ffb910029bf9ebb5f` |
| `config.json` | `154518e6452854eb11123ce433d165f2773437aa0f36935d2204249b2c327198` |
| `modules.json` | `0580fad756940809b1bc064ce1e7c1275c8b0dd80869562fe0abeb6f7059cb97` |
| `sentence_bert_config.json` | `23aa234ef6021d513cdf56947c07aaa6977000d85dcd1fd423f650b3382a39ca` |
| `1_Pooling/config.json` | `7fae3727656cff4a760964a58f1e4b31695c97c75d2e26346df32a87d494bbca` |
| `tokenizer.json` | `6c8aaa9a542084f2457eab775d4eeb51f92a70c0fd9de28d5edb0ddec3c08d30` |
| `tokenizer_config.json` | `c16f1b07f93d05be753ec0acf8cb511d895a8bdb09669f6b156df81e3f96c2eb` |
| `special_tokens_map.json` | `ea97ecdbcc73713039d8d64dbb05e3689495c96657fbd9a18f5bed381be81049` |

**Line endings.** The seven JSON files are compared with Windows line endings (CRLF) normalised
to LF, because checkout and copy tools on Windows may convert them; any other change still
fails. The weights file is compared byte for byte. (The pinned values are those of the
original files, which contain LF only.)

**Verification** (self-test, §6):

- a copy with an edited `config.json` is refused when copying, and by the server-start check;
  both messages name the file
- a copy whose JSON files have Windows line endings is accepted by both checks, and the rest of
  the test run uses it
- the fingerprint tables in the two source files are identical

### 2.4 Code parsing

| Component | Version | License | Reported CVEs | How it is used | Exposure | Evidence / controls |
|---|---|---|---|---|---|---|
| **tree-sitter** + grammars (C, C++, Python, MATLAB, Perl, Markdown, JS, TS, Java, Go, Rust, C#) | 0.26.0; grammars pinned | MIT | 0 | Parses repository source into functions, classes, … | Parses repository text — native (C) code | Upstream fuzzing with libFuzzer + AddressSanitizer/UBSan; prebuilt wheels, no code generation at runtime; parsing ≠ execution. Ran without a crash over **6,280 real-world files in 13 languages** |
| **IDL parser** | in-house (pure Python) | project | n/a | Parses IDL dialects | Repository text | Pure Python — no native code, no `eval` |
| **Build-file readers** | in-house (pure Python) | project | n/a | Reads Makefiles, CMake, `compile_commands.json` | Repository text | **Never runs** `make` or CMake (they can execute commands from a repo) |

### 2.5 Editor integration

| Component | Version | License | Reported CVEs | How it is used | Exposure | Evidence / controls |
|---|---|---|---|---|---|---|
| **MCP Python SDK** (`mcp`) | 1.30.0 | MIT | 0 | MCP servers for VS Code | **stdio only — no port, no HTTP transport** | Started by VS Code from `.vscode/mcp.json`; pinned to the venv's Python |
| **VS Code + GitHub Copilot** | your version | vendor terms | vendor | Calls the MCP tools; sends results to the AI provider | The one place code leaves the machine | Governed by the terms of your Copilot plan and its settings; results are framed as untrusted data (prompt-injection mitigation) |

### 2.6 Framework-owned components

| Component | Evidence |
|---|---|
| Setup GUI (`setup_gui.py`) | Standard library only. `127.0.0.1` only; per-session token on every API call; Host check (DNS rebinding); JSON-only POSTs (CSRF); strict Content-Security-Policy (no external resources, no `eval`, no framing); HTML-escaped output; no shell. All verified by the self-test |
| Git cloning (`gui/gitclone.py`) | Standard library only. Refuses option-like values and the command-running `ext::`/`fd::` transports — **verified that a crafted `ext::` URL ran nothing**; validates branch and folder names; never overwrites a folder; git never prompts for passwords |
| Secret handling (`codegraph/security.py`) | Secret-store files never indexed; 10 redaction patterns applied before parsing, embedding, storage or output |
| Storage | Graph, reports and logs written `0600` in `0700` folders, atomically; logs never follow symlinks and hide code unless explicitly enabled |

---

## 3. Measurement: nothing leaves the machine

**Method.** A Python audit hook (`sys.addaudithook`) was added to the virtual environment, so
**every** Python process started from it — indexer, MCP servers, router, query tools —
recorded each `socket.connect`, `socket.getaddrinfo`, `socket.sendto` and `socket.bind`.

**Run.** Both code graphs were deleted, then rebuilt from scratch and queried through the
real MCP servers (search, trace interface, impact, router; 9 checks), followed by a
command-line cross-repository analysis.

**Result.** **0 events.**

**Positive control.** In the same environment, a DNS lookup, a TCP connect, and a lookup from
a process started with an MCP server's exact environment were **all recorded** — so the hook
works, and the zero is real.

Only two steps use the network, both explicit and user-initiated: installing the
hash-locked packages, and cloning a repository URL that the user enters. Both can point at
mirrors or servers you trust.

---

## 4. What remains (honest residual risk)

| Item | Why it can't be "zero" | Mitigation |
|---|---|---|
| Unknown (zero-day) vulnerabilities | No audit finds what isn't known yet | Run `tests/run_all.py` weekly; pinned versions; update and re-run the tests |
| Native parsing code (tree-sitter, SQLite) | Memory-safety bugs are possible in C code | Upstream fuzzing; current releases; input is your own repositories, not internet data |
| AI provider sees code | That is the purpose of the assistant | The terms of your Copilot plan and its settings |
| Prompt injection via repository text | Framing reduces, can't eliminate | Keep VS Code's confirmations for commands and edits enabled |
| Secrets in unusual formats | Redaction is pattern-based | Run your secret scanner; delete `.rag_index/` and `results/` and rotate if one is found |
| Data at rest | Index and reports contain code excerpts | Full-disk encryption; keep out of shared folders and backups; on Windows rely on the user profile's ACLs |

---

## 5. Design decisions behind the stack

| Decision | Reason |
|---|---|
| No vector database; a local SQLite file (standard library) with vectors as raw bytes | Nothing to run as a server, no network port, no telemetry, no pickle, one fewer large dependency tree. Exact vector search is fast enough at repository scale |
| `ibm-granite/granite-embedding-small-english-r2` | Apache-2.0; training data limited to permissively licensed and IBM data; 8,192-token input, so whole functions are embedded; runs on CPU; supported natively by `transformers` (no remote code) |
| Weights only as `safetensors`; every model file pinned by SHA-256 | Pure data format; any change to any model file — weights, settings or tokenizer — is detected at every start |
| `sentence-transformers` ≥ 6.0 | Current major version; with the folder checks, loading the model cannot execute code |
| tree-sitter for parsing; build files read as text | Parsing never executes repository code; running `make` or CMake could |
| MCP over stdio only | No listening sockets, so nothing to expose or authenticate |
| Hash-locked dependencies, audited weekly | Tampering fails installation; new advisories for unchanged pins are caught |
| Separate virtual environment | Nothing installed into, or affected by, the system Python |

---

## 6. Reproduce the evidence

Linux (bash). Windows equivalents follow.

```bash
# 1. Vulnerabilities and integrity
.venv/bin/pip install pip-audit pip-licenses
.venv/bin/pip-audit -r requirements.lock --require-hashes --disable-pip
.venv/bin/pip-licenses --format=markdown

# 2. Model: every file must match the table in §2.3
( cd models/granite-embedding-small-english-r2 && sha256sum $(find . -type f ! -name .gitkeep | sort) )
find models/granite-embedding-small-english-r2 -type f      # only safetensors + JSON

# 3. Unsafe APIs in the framework code (expect comments / deny-lists only)
grep -rn --include='*.py' -E "pickle|torch\.load|np\.load|\beval\(|\bexec\(|shell=True|os\.system|yaml\.load\(" \
     repo-agent orchestrator gui skills setup_gui.py

# 4. Network: add an audit hook to the venv (remove it afterwards)
cat > "$(.venv/bin/python -c 'import site;print(site.getsitepackages()[0])')/sitecustomize.py" <<'PY'
import sys, os
def _h(e, a):
    if e in ("socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.bind"):
        open(os.path.expanduser("~/ao-net.log"), "a").write(f"{os.getpid()} {e} {a!r}\n")
sys.addaudithook(_h)
PY
#    rebuild a graph (delete <repo>/.rag_index), run queries in the GUI or VS Code,
#    then:  cat ~/ao-net.log   → expect no lines;  delete the sitecustomize.py file

# 5. Permissions
ls -ld <repo>/.rag_index results ~/.agentic-orchestration/logs     # drwx------
ls -l  <repo>/.rag_index results                                   # -rw-------

# 6. End-to-end
python3 tests/run_all.py                                          # all checks
```

Windows (PowerShell):

```powershell
# 1. Vulnerabilities and integrity
.venv\Scripts\pip install pip-audit pip-licenses
.venv\Scripts\pip-audit -r requirements.lock --require-hashes --disable-pip
.venv\Scripts\pip-licenses --format=markdown

# 2. Model
Get-ChildItem -Recurse -File models\granite-embedding-small-english-r2 | Get-FileHash -Algorithm SHA256
Get-ChildItem -Recurse -File models\granite-embedding-small-english-r2

# 3. Unsafe APIs (expect comments / deny-lists only)
Get-ChildItem -Recurse -Include *.py repo-agent,orchestrator,gui,skills,setup_gui.py |
  Select-String -Pattern 'pickle|torch\.load|np\.load|\beval\(|\bexec\(|shell=True|os\.system|yaml\.load\('

# 4. Network: same audit hook as above, written to the venv's site-packages folder
.venv\Scripts\python -c "import site;print(site.getsitepackages())"

# 5. Access control
icacls <repo>\.rag_index; icacls results

# 6. End-to-end
py tests\run_all.py
```

---

## Sources

- tree-sitter fuzzing (libFuzzer, AddressSanitizer/UBSan): [Fuzzing tree-sitter](https://lively-kernel.org/lively4/tree-sitter/test/fuzz/README.md)
- Vulnerability data for the dependency audit: the PyPI advisory database, queried by `pip-audit`

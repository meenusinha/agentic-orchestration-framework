"""Secret handling shared by every indexing path: skip secret-store files, redact
secret-looking values before anything is parsed, embedded, stored or returned.
Redaction keeps line breaks, so line numbers of the code that follows stay correct."""
import fnmatch
import re
from pathlib import Path

# ── Secret handling ──────────────────────────────────────────────────────────
# Files whose job is to *store* secrets. Deliberately narrow: a doc or module merely
# *about* passwords or secrets (password-policy.md, secret_rotation.py) is indexed,
# with any secret-looking values redacted by _SECRET_PATTERNS below.
_SECRET_FILE_GLOBS = (".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks",
                      "*.keystore", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", ".npmrc",
                      ".pypirc", ".netrc", "*.tfvars", "secret.*", "secrets.*", "credential.*",
                      "credentials.*", "*_secrets.*", "*-secrets.*", "*.secrets.*")
_REDACT = "[REDACTED]"
_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S),
     _REDACT),
    (re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[A-Z0-9]{16}\b"), _REDACT),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b"), _REDACT),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), _REDACT),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"), _REDACT),
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[0-9A-Za-z]{16,}"), _REDACT),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), _REDACT),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), _REDACT),
    # scheme://user:password@host
    (re.compile(r"\b([a-z][a-z0-9+.-]*://[^:/\s@]+):[^@/\s]+@"), r"\1:" + _REDACT + "@"),
    # password = "value", api_key: 'value', "client_secret": "value"
    (re.compile(r"(?i)((?:[\"']?)[A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|"
                r"access[_-]?key|private[_-]?key|credential|auth[_-]?key)[A-Za-z0-9_.-]*[\"']?"
                r"\s*[:=]\s*)([\"'])[^\"'\s]{6,}\2"), r"\1\2" + _REDACT + r"\2"),
]


def is_secret_file(path: Path) -> bool:
    name = path.name.lower()
    return any(fnmatch.fnmatch(name, g) for g in _SECRET_FILE_GLOBS)


def redact(text: str) -> tuple[str, int]:
    total = 0
    for pattern, repl in _SECRET_PATTERNS:
        def sub(m, repl=repl):
            out = m.expand(repl)
            return out + "\n" * (m.group(0).count("\n") - out.count("\n"))   # keep line numbers stable
        text, n = pattern.subn(sub, text)
        total += n
    return text, total



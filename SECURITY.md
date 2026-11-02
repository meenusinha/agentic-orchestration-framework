# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | Yes |

Supported platforms: Windows and Linux, with Python 3.11 or newer.

## Dependencies

All dependencies are pinned with SHA-256 hashes in `requirements.lock` and audited for
known vulnerabilities by `tests/run_all.py`. Run it on every change and on a schedule
(for example weekly). When an advisory affects a pinned package:

1. Update the version in `requirements.txt`.
2. Regenerate the lock file:

   ```bash
   uv pip compile requirements.txt --universal --python-version 3.11 --generate-hashes -o requirements.lock
   ```
3. Run `tests/run_all.py` on Windows and Linux, then release.

## Security design

- [docs/security-considerations.md](docs/security-considerations.md) — data flow, controls, residual risks
- [docs/security-evidence.md](docs/security-evidence.md) — evidence for each component of the technology stack

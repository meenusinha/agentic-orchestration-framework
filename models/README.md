# Bundled embedding model

`granite-embedding-small-english-r2/` contains the embedding model the framework uses to
make code and documentation searchable. It is included so the framework works right after
cloning, without downloading anything.

| | |
|---|---|
| Model | `ibm-granite/granite-embedding-small-english-r2` |
| Provider | IBM |
| License | Apache License 2.0 (the same license text as this repository's [LICENSE](../LICENSE)) |
| Revision | `2ab6fa8ea2d674564defd37171ae19079b864b33` |
| Files | `model.safetensors` (weights) and seven JSON files (model, pipeline and tokenizer settings) — exactly the files needed for inference |
| Changes | None. The files are redistributed unmodified |

## Integrity

Every file is **hash-pinned**: its SHA-256 fingerprint is recorded in the framework's code and
checked when the setup runs and every time an MCP server starts. A missing or changed file is
refused by name. The fingerprints are listed in
[docs/security-evidence.md](../docs/security-evidence.md#23-embedding-model).

Git is told never to alter these files (see `.gitattributes`), so line endings stay exactly
as they are on every platform.

## Using a copy from elsewhere

If this folder is missing, for example in a copy of the repository made without it, step 2 of
the setup page lets you point to another copy. It must contain the same files, with the same
fingerprints.

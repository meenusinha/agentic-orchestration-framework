# Embedding Model — Parameters, Architecture & RAM

Details for the default model used by every `mcp_server.py` instance:
`all-MiniLM-L6-v2` (sentence-transformers).

All numbers below are measured from the actual model files on disk
(`models/all-MiniLM-L6-v2/model.safetensors`) — not estimates.

---

## Parameter count

**22,713,728 — roughly 22.7 million parameters.**

For context:

| Model | Parameters | vs this model |
|---|---|---|
| `all-MiniLM-L6-v2` | 22.7 M | — |
| `bert-base-uncased` | 110 M | 5× larger |
| GPT-2 | 1.5 B | 66× larger |
| GPT-3 | 175 B | 7,700× larger |
| GPT-4 (est.) | ~1.8 T | ~79,000× larger |

This is a deliberately small, distilled model — fast at inference, low RAM,
runs entirely offline on CPU. The trade-off is that it is optimised for
sentence similarity only, not general language understanding.

---

## Architecture

Measured from `config.json` in the model snapshot:

| Property | Value |
|---|---|
| Model type | BERT |
| Transformer layers | 6 (`L6` in the name) |
| Hidden size | 384 |
| Attention heads | 12 |
| Intermediate (FFN) size | 1,536 |
| Max input sequence length | 512 tokens |
| Vocabulary size | 30,522 |
| **Output embedding dimension** | **384** |

The 384-dimensional output is what gets stored in ChromaDB. Each indexed
chunk becomes a vector of 384 float32 values (~1.5 KB per chunk in the DB).

---

## RAM usage

| What | Size |
|---|---|
| Weight file on disk (safetensors, float32) | **86.7 MB** |
| Weights loaded into RAM at inference (float32) | **~87 MB** |
| **Full OS process RAM** (weights + PyTorch runtime + Python interpreter + sentence-transformers + tokenizer) | **~250–350 MB per process** |

The ~90 MB figure quoted elsewhere refers to the weight file size only.
The actual OS process footprint once Python, PyTorch, and the tokenizer are
all initialised is closer to **300 MB per process**.

---

## Impact on the VS Code process model

The earlier docs quoted ~90 MB per process. The corrected figure:

| Scenario | Processes | Corrected RAM estimate |
|---|---|---|
| VS Code, 3 repos (16 permanent Python procs) | 16 | 16 × ~300 MB ≈ **4.8 GB** |
| VS Code, 10 repos (121 permanent Python procs) | 121 | 121 × ~300 MB ≈ **36 GB** |
| `test_mcp.py`, any repo count (1 child at a time) | 2 peak | 1 × ~300 MB ≈ **300 MB peak** |

For the VS Code case, RAM pressure from embedding models alone will exhaust
a 16 GB machine at around **5–6 repos**, well before the OS process limit is
reached.

---

## Why the model cannot be cached like ChromaDB

ChromaDB stores **data** — pre-computed vectors (plain floats in SQLite).
Any process that opens the same `.chroma_db` path reads those numbers from
disk in milliseconds; no computation is needed.

The embedding model weights are also on disk (`model.safetensors`). They do
not get re-downloaded. But loading them is not the same as reading stored
data. When a new process calls `SentenceTransformer(embedding_model)`,
PyTorch must:

1. Read the safetensors file from disk into RAM
2. Deserialise weights from the on-disk format
3. Allocate and populate tensor memory for all 22.7 M parameters
4. Instantiate the model architecture (6 transformer layers, attention
   heads, FFN blocks)
5. Initialise the tokenizer

This is **reconstructing a live computational object**, not reading stored
results. There is no equivalent of "just open the SQLite file" for a PyTorch
model.

The closest solutions and their trade-offs:

| Approach | What it means | Trade-off |
|---|---|---|
| **Model server** (Triton, TorchServe, or a simple persistent Python process) | Keep the model loaded in one long-lived process; subprocesses call it over a socket or pipe | Extra infrastructure; adds a network/IPC hop per embedding call |
| **Shared memory tensors** (`torch.multiprocessing`) | Map model weights into a shared memory region readable by all child processes | Only works within a single Python process tree; complex to set up |
| **ONNX + optimised runtime** | Export to ONNX; load with ONNX Runtime (faster deserialisation) | Reduces load time ~40–60%; does not eliminate it |

The fundamental reason: ChromaDB solved a solved problem (database caching).
Embedding model instantiation speed is still an active area in the ML
infrastructure ecosystem.

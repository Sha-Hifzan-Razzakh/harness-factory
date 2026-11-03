# ADR 0017: Retrieval as a recipe dimension, on LlamaIndex

Status: proposed · 2026-11-02 · Requirements: KNW-01 (part), KNW-02, KNW-03 (part), KNW-04, FWK-01, FWK-03, FWK-04

## Context

`retrieval: {mode, top_k, rerank}` has been in the recipe model since P0 and refused as inert (ADR 0002). KNW-02
makes it an axis: none, BM25, dense or hybrid, reranker on or off, top-k. FWK-01 names the framework: LlamaIndex
(with Qdrant and BM25), through its own components, never hand-written retrieval.

The constraint: MCP servers in the sandbox may import only `mcp` and the standard library (CLAUDE.md,
"Isolation"). LlamaIndex, Qdrant and bm25s are none of those. PR #47 (ADR 0013) may later allow pinned non-stdlib
server libraries; this design does not depend on it and needs no sandbox change.

## Decision

1. **A harness-side tool, like the delegate tool (ADR 0003).** `retrieve.search(query)` runs in the factory
   process. It is a `LocalTool` of the run's `ToolExecutor`: offered through `tools_for_model` (so through
   `shapes/common.offered_tools`, the same for every shape) and executed through the same path as an MCP call:
   allowance, V3 stage filter, required arguments, permission per side effect (READ, R0), faults, timeout,
   truncation, a `tool_call` span, a budget charge. Only the last step differs: a Python call instead of an MCP
   session. No tool call escapes the run's accounting, and the trajectory grader judges it like any read.
2. **The corpus is task data.** A task has a corpus when its seed files have a top-level `corpus/` folder
   (`.md` and `.txt` files). It is copied into the workspace with the rest of the seed and is in the task hash
   through the seed hash; no task field was added, so no existing task hash changed.
3. **When it is offered.** All three: the recipe sets a mode other than `none`; the task has a corpus; the task
   allows `fs.read_file`. The tool returns passages of files the model could read anyway, so like a `granted_by`
   tool it reveals nothing new and needs no allowance edit. Otherwise the recipe runs exactly like its base (no
   tool, no index, no prompt text). This is why a retrieval matrix recipe passes the matrix gate on every visible
   task. A V3 stage offers it only if its tool patterns allow `retrieve.search` (`"*"` does).
4. **What the model can reach.** Only passages of the corpus files as seeded, indexed before the run from the
   task's seed directory: never the workspace as changed during the run, never anything outside `corpus/`.
5. **Indexed before the run, once per run.** The provisioner builds the index in a worker thread after the
   sandbox starts and before the run's clock starts. Each run owns its index (in-memory Qdrant, no files): nothing
   is shared between concurrent runs, so no module-level state. The index id (corpus file hashes + config +
   engine) and the evidence (every passage returned, by source and character offsets) go in the run's snapshot
   and transcript. Caching an index across runs of the same version (KNW-01's "indexed once per version") is left
   for a Factory-owned cache; at 60 short documents a build takes well under a second.
6. **LlamaIndex components per mode** (extra `retrieval`, in `dev`; versions pinned in `uv.lock`):

   | Piece | Component | Package (locked) |
   |---|---|---|
   | documents, nodes | `Document`, `SentenceSplitter` (64 words, overlap 12, deterministic UUID5 node ids, exact char offsets) | llama-index-core 0.14.25 |
   | bm25 | `BM25Retriever` (English stemmer, English stopwords) | llama-index-retrievers-bm25 0.8.0 (bm25s 0.3.16, PyStemmer 2.2.0.3) |
   | dense | `VectorStoreIndex` on `QdrantVectorStore`, `QdrantClient(location=":memory:")` | llama-index-vector-stores-qdrant 0.10.4, qdrant-client 1.20.0 |
   | hybrid | `QueryFusionRetriever([dense, bm25], mode="reciprocal_rerank", num_queries=1)` with `MockLLM` (one query: no LLM query generation, so the LLM is never called) | llama-index-core |
   | rerank | a `BaseNodePostprocessor` over a pool of `top_k x 3` candidates | llama-index-core |

7. **Two substitutes, as LlamaIndex extension points (FWK-01).** A real embedding model and a real cross-encoder
   reranker are downloads, which CI and the offline gates cannot make, and a model that changes under a cached
   name would change results under the same identity.
   - `HashingEmbedding(BaseEmbedding)`: signed feature hashing of words and adjacent word pairs, sublinear term
     frequency, L2-normalised, 512 dimensions. Deterministic and offline, and unlike `MockEmbedding` it ranks by
     shared vocabulary, so dense and hybrid results are meaningful facts tests can assert. It is not semantic:
     dense here measures the dense *path*, not a dense *model*.
   - `TermCoverageRerank(BaseNodePostprocessor)`: candidates ordered by the share of distinct query words they
     contain, the first stage's order breaking ties. It stands in for `SentenceTransformerRerank` or a
     cross-encoder in the same slot.
   Measurement protected: run identity and reproducibility. A real model plugs into the same slot later, as a new
   value in `ENGINE`, which changes the environment hash.
   One more deviation: `SentenceSplitter` gets a regex sentence splitter (`chunking_tokenizer_fn`) instead of its
   NLTK punkt default, because NLTK refuses to open its bundled data when the package is installed as hardlinks
   (uv's default), and passes a whitespace word counter instead of tiktoken (no encoding download).
8. **Identity.** Set (mode other than `none`), the retrieval config is in the recipe hash as part of the recipe
   (it always was: `retrieval: null` for every existing recipe, so `v2-base` is still `79abd03818943f65`). FWK-03:
   for a retrieval recipe, `RunKey.env_hash` also covers the engine description (splitter, embedding, store,
   fusion, reranker), the six package versions and the tool's spec and schema. Only then, so every existing env
   hash is unchanged. The corpus is in the task hash through the seed.
9. **Passages are data (KNW-04).** The tool answers a JSON document: a note that the passages are quoted data,
   not instructions, then `{source, start, end, score, text}` per passage.
10. **Reference plans can retrieve.** A plan step may be `"optional": True`; the scripted `PlanBackend` skips an
    optional step whose tool isn't offered, so one plan drives recipes with and without retrieval.
    `hfx reference` runs a retrieval step on the corpus with `REFERENCE_CONFIG` (hybrid, top 5) and fails unless
    it returns every corpus file the plan reads before its next retrieval step.
11. **Delegation narrows.** A helper gets the run's retrieval tool only when its own recipe also retrieves; it
    searches the run's index.
12. **Validation.** `retrieval` is no longer an inert field. New rules: `retrieval_installed` (the extra is
    importable), `retrieval_unused` (rerank set with mode none; a V1 recipe, which never sees a tool result; a
    policy that denies READ), `retrieval_namespace` (no MCP server may be called `retrieve`). `top_k` is 1 to 50.

## Consequences

- Matrix recipes `v2-retrieval-bm25` (BM25, top 5) and `v2-retrieval-hybrid` (hybrid, top 5, rerank) are in CI's
  matrix gate. Offline both pass every visible task; on the retrieval tasks the scripted model calls the tool,
  then reads what it found.
- Three research tasks over a new `kb` seed (60 short documents under opaque names `kb-001` ... `kb-060`, so
  listing the folder says nothing): `rag-01` (TEST, current refund limit; traps: the superseded policy, a draft,
  the wholesale desk's limit), `rag-02` (TEST, the payments-gateway port; traps: the stale catalog entry, a
  cancelled change), `rag-03` (HELD_OUT, an incident's root cause and the owning team; traps: a stale owner, an
  incident with a similar number). Each answer needs the document id (kb-NNN) as a citation; do-nothing scores 0.
- Every mode with and without rerank finds the reference's documents at top 5 on this corpus. On a wordier query
  ("which team owns the label-printer service") dense misses the catalog entry at top 5, with or without rerank,
  while BM25 and hybrid find it: the dimension has room to show differences.
- The factory image installs the `retrieval` extra by default (`EXTRAS="postgres retrieval"`), so `hfx reference`
  and retrieval recipes work in the container; numpy, grpcio and the LlamaIndex closure make it larger.
- Not covered: a grounding grader that checks an answer's citations against the evidence (KNW-03), retrieval
  quality on its own with gold chunks (KNW-05), DVC-versioned corpora and a cross-run index cache (KNW-01), a real
  embedding model and reranker behind a recorded cache.

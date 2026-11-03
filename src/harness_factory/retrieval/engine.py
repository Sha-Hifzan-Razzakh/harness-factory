"""Retrieval engine (ADR 0017, KNW-02): a task's corpus indexed and searched with LlamaIndex's own components.

    documents, nodes   llama_index.core Document + SentenceSplitter (deterministic node ids and char offsets)
    bm25               llama_index.retrievers.bm25.BM25Retriever (bm25s + PyStemmer)
    dense              llama_index.core VectorStoreIndex on llama_index.vector_stores.qdrant.QdrantVectorStore,
                       qdrant-client in local in-memory mode (no server, no network)
    hybrid             llama_index.core.retrievers.QueryFusionRetriever over the two, reciprocal rank fusion,
                       one query (no LLM query generation)
    rerank             a llama_index.core BaseNodePostprocessor over a wider candidate pool

Two pieces are written here, both as LlamaIndex extension points (FWK-01 allows a substitute with a reason): the
embedding and the reranker. Real ones are model downloads (sentence-transformers, cross-encoders), which CI and the
offline gates may not make, and a model that changes under a cached name would change results under the same
identity. HashingEmbedding is signed feature hashing of words and word pairs (deterministic, ranks by shared
vocabulary, unlike MockEmbedding); TermCoverageRerank orders candidates by how many distinct query terms they
contain. Both are named, with their parameters, in ENGINE so a different embedding or reranker is a different
environment (RunKey.env_hash).

This module imports LlamaIndex at import time: import it only when a recipe uses retrieval (the `retrieval` extra).
Everything here runs in the factory process; nothing reaches the sandbox.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import uuid
from pathlib import Path

import Stemmer
from llama_index.core import Document, StorageContext, VectorStoreIndex
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.llms import MockLLM
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.schema import BaseNode, NodeWithScore, QueryBundle
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from harness_factory.hashing import content_hash
from harness_factory.recipes.model import RetrievalConfig
from harness_factory.retrieval.tool import (
    CHUNK_OVERLAP,
    CHUNK_WORDS,
    CORPUS_PREFIX,
    CORPUS_SUFFIXES,
    EMBED_DIM,
    ENGINE,
    RERANK_POOL,
    Passage,
)

_TOKEN = re.compile(r"(?u)\b\w\w+\b")    # BM25Retriever's default token pattern, so all modes see the same words


def _words(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class HashingEmbedding(BaseEmbedding):
    """Deterministic, offline embedding: signed feature hashing of words and adjacent word pairs, sublinear term
    frequency, L2-normalised. Cosine similarity then ranks by shared vocabulary, which is what a test of the dense
    path needs; it is not a semantic model."""

    dim: int = EMBED_DIM

    @classmethod
    def class_name(cls) -> str:
        return "HashingEmbedding"

    def _vector(self, text: str) -> list[float]:
        words = _words(text)
        counts: dict[str, int] = {}
        for feat in words + [f"{a} {b}" for a, b in itertools.pairwise(words)]:
            counts[feat] = counts.get(feat, 0) + 1
        vec = [0.0] * self.dim
        for feat, n in counts.items():
            h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big")
            vec[h % self.dim] += (1.0 + math.log(n)) * (1.0 if (h >> 32) & 1 else -1.0)
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._vector(text)


class TermCoverageRerank(BaseNodePostprocessor):
    """Offline reranker: candidates ordered by the share of distinct query words they contain, the first stage's
    order breaking ties. Stands in for a cross-encoder (a model download) in the same postprocessor slot."""

    top_n: int = 5

    @classmethod
    def class_name(cls) -> str:
        return "TermCoverageRerank"

    def _postprocess_nodes(self, nodes: list[NodeWithScore],
                           query_bundle: QueryBundle | None = None) -> list[NodeWithScore]:
        if query_bundle is None:
            return nodes[: self.top_n]
        terms = set(_words(query_bundle.query_str))
        if not terms:
            return nodes[: self.top_n]
        ranked = sorted(enumerate(nodes),
                        key=lambda p: (-len(terms & set(_words(p[1].node.get_content()))), p[0]))
        return [NodeWithScore(node=n.node, score=round(len(terms & set(_words(n.node.get_content()))) / len(terms), 6))
                for _, n in ranked[: self.top_n]]


def corpus_files(corpus: Path) -> list[Path]:
    return sorted(p for p in corpus.rglob("*") if p.is_file() and p.suffix in CORPUS_SUFFIXES)


def load_documents(corpus: Path, prefix: str = CORPUS_PREFIX) -> list[Document]:
    """One Document per corpus file; its id (and `source`) is the file's path in the workspace."""
    docs = []
    for p in corpus_files(corpus):
        rel = f"{prefix}/{p.relative_to(corpus).as_posix()}"
        docs.append(Document(text=p.read_text(), id_=rel, metadata={"source": rel},
                             excluded_embed_metadata_keys=["source"], excluded_llm_metadata_keys=["source"]))
    return docs


def _node_id(i: int, doc: BaseNode) -> str:
    """Deterministic node ids (Qdrant wants UUIDs): the same corpus always gives the same nodes."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"hf-corpus:{doc.id_}#{i}"))


_SENTENCE_END = re.compile(r"[.!?]\s+")


def _sentences(text: str) -> list[str]:
    """Sentence boundaries for SentenceSplitter, keeping every character (offsets stay exact). Its default is
    NLTK punkt from a data folder that NLTK refuses to open when the package was installed as hardlinks (uv's
    default), and whose model is a download where it isn't bundled."""
    out, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        out.append(text[start:m.end()])
        start = m.end()
    return out + ([text[start:]] if start < len(text) else [])


def split(docs: list[Document]) -> list[BaseNode]:
    splitter = SentenceSplitter(chunk_size=CHUNK_WORDS, chunk_overlap=CHUNK_OVERLAP, tokenizer=str.split,
                                chunking_tokenizer_fn=_sentences, id_func=_node_id, include_prev_next_rel=False)
    return splitter.get_nodes_from_documents(docs)


class CorpusIndex:
    """One task corpus indexed for one retrieval config. Built once per run, before the run starts; search() is
    synchronous (call it from a worker thread)."""

    def __init__(self, corpus: Path, config: RetrievalConfig) -> None:
        if config.mode == "none":
            raise ValueError("retrieval mode none has no index")
        self.config = config
        docs = load_documents(corpus)
        if not docs:
            raise ValueError(f"corpus {corpus} has no {'/'.join(CORPUS_SUFFIXES)} files")
        self.nodes = split(docs)
        self.index_id = content_hash({"files": {d.id_: content_hash(d.text, 16) for d in docs},
                                      "config": config.model_dump(mode="json"), "engine": ENGINE}, 16)
        self.documents = len(docs)
        fetch = config.top_k * RERANK_POOL if config.rerank else config.top_k
        self.retriever = self._retriever(config.mode, fetch)
        self.rerank = TermCoverageRerank(top_n=config.top_k) if config.rerank else None

    def _retriever(self, mode: str, k: int):
        if mode == "bm25":
            return self._bm25(k)
        if mode == "dense":
            return self._dense(k)
        return QueryFusionRetriever([self._dense(k), self._bm25(k)], llm=MockLLM(), mode="reciprocal_rerank",
                                    num_queries=1, use_async=False, similarity_top_k=k)

    def _bm25(self, k: int) -> BM25Retriever:
        return BM25Retriever.from_defaults(nodes=self.nodes, similarity_top_k=min(k, len(self.nodes)),
                                           stemmer=Stemmer.Stemmer("english"), language="en")

    def _dense(self, k: int):
        # One in-memory Qdrant per index: nothing is shared between runs, nothing is written to disk.
        client = QdrantClient(location=":memory:")
        store = QdrantVectorStore(collection_name=f"c{uuid.uuid4().hex[:12]}", client=client, index_doc_id=False)
        index = VectorStoreIndex(self.nodes, storage_context=StorageContext.from_defaults(vector_store=store),
                                 embed_model=HashingEmbedding(), show_progress=False)
        return index.as_retriever(similarity_top_k=k)

    def search(self, query: str) -> list[Passage]:
        bundle = QueryBundle(query_str=query)
        hits = self.retriever.retrieve(bundle)
        if self.rerank is not None:
            hits = self.rerank.postprocess_nodes(hits, query_bundle=bundle)
        out = []
        for h in hits[: self.config.top_k]:
            node = h.node
            out.append(Passage(source=str(node.metadata.get("source") or node.ref_doc_id),
                               start=int(node.start_char_idx or 0), end=int(node.end_char_idx or 0),
                               score=round(float(h.score or 0.0), 6), text=node.get_content()))
        return out

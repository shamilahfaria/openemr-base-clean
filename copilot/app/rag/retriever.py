"""Hybrid guideline retrieval: keyword + dense, fused, then reranked.

Two independent retrieval channels run over the corpus:

- **Keyword**: BM25 (k1=1.5, b=0.75) over tokenized chunk text — exact-term
  precision.
- **Dense**: cosine similarity between hashed TF-IDF embeddings (stable
  crc32 hashing trick, 256 dims, L2-normalized) — bag-of-words semantic
  recall that survives word order and field/section mismatches.

The channels are fused with reciprocal rank fusion (k=60), and the fused
candidates are reranked by query-term coverage so a chunk that actually
addresses the asked-about entities beats one with incidental overlap. Every
hit reports its per-stage ranks and scores, so retrieval is inspectable end
to end. Deterministic and dependency-free by design — the eval gate runs it
offline with no model or index server.
"""
from __future__ import annotations

import json
import math
import re
import zlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_CORPUS_PATH = Path(__file__).resolve().parent / "corpus.json"

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it of on or that the "
    "this to was were what when which with should patient patients".split()
)

_EMBED_DIM = 256
_RRF_K = 60
_BM25_K1 = 1.5
_BM25_B = 0.75


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


class EvidenceHit(BaseModel):
    """One retrieved guideline chunk with its full scoring provenance."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    title: str
    section: str
    source: str
    text: str
    keyword_rank: int | None = None      # 1-based BM25 rank (None = no keyword hit)
    dense_rank: int | None = None        # 1-based cosine rank (None = no dense hit)
    fused_score: float                   # reciprocal-rank-fusion score
    rerank_score: float                  # final query-term-coverage score


class _Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    section: str
    source: str
    text: str


class _Corpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    description: str
    chunks: list[_Chunk] = Field(min_length=1)


class HybridRetriever:
    # Surfaced by /ready's component walk so operators see exactly which
    # retrieval stack is live, not a black box.
    index_name = "hybrid: bm25 keyword + hashed-tfidf dense (cosine), rrf-fused"
    reranker_name = "query-term-coverage reranker"

    def __init__(self, chunks: list[_Chunk]):
        self._chunks = chunks
        # Index text = title + section + body, so entity mentions anywhere count.
        self._docs = [_tokenize(f"{c.title} {c.section} {c.text}") for c in chunks]
        self._doc_freq: dict[str, int] = {}
        for tokens in self._docs:
            for term in set(tokens):
                self._doc_freq[term] = self._doc_freq.get(term, 0) + 1
        self._avg_len = sum(len(d) for d in self._docs) / len(self._docs)
        self._embeddings = [self._embed(tokens) for tokens in self._docs]

    def stats(self) -> dict[str, int]:
        """Index shape for the readiness component walk."""
        return {"chunks": len(self._chunks), "embedding_dim": _EMBED_DIM}

    # --- dense channel ------------------------------------------------------

    def _idf(self, term: str) -> float:
        n, df = len(self._docs), self._doc_freq.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _embed(self, tokens: list[str]) -> list[float]:
        vec = [0.0] * _EMBED_DIM
        for term in tokens:
            # crc32 is stable across processes (unlike hash()), so embeddings
            # are reproducible in CI.
            vec[zlib.crc32(term.encode()) % _EMBED_DIM] += self._idf(term)
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm else vec

    def _dense_scores(self, query_tokens: list[str]) -> list[float]:
        q = self._embed(query_tokens)
        return [sum(a * b for a, b in zip(q, emb)) for emb in self._embeddings]

    # --- keyword channel ----------------------------------------------------

    def _bm25_scores(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * len(self._docs)
        for term in set(query_tokens):
            idf = self._idf(term)
            for i, tokens in enumerate(self._docs):
                tf = tokens.count(term)
                if not tf:
                    continue
                denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * len(tokens) / self._avg_len)
                scores[i] += idf * tf * (_BM25_K1 + 1) / denom
        return scores

    # --- fusion + rerank ----------------------------------------------------

    def search(self, query: str, k: int = 3) -> list[EvidenceHit]:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        def ranked(scores: list[float]) -> dict[int, int]:
            """doc index -> 1-based rank, positive scores only, stable order."""
            order = sorted(
                (i for i, s in enumerate(scores) if s > 1e-9),
                key=lambda i: (-scores[i], i),
            )
            return {i: rank for rank, i in enumerate(order, start=1)}

        keyword_ranks = ranked(self._bm25_scores(query_tokens))
        dense_ranks = ranked(self._dense_scores(query_tokens))

        fused: dict[int, float] = {}
        for ranks in (keyword_ranks, dense_ranks):
            for i, rank in ranks.items():
                fused[i] = fused.get(i, 0.0) + 1.0 / (_RRF_K + rank)
        if not fused:
            return []

        # Rerank the fused candidates by query-term coverage: what fraction of
        # the distinct query terms the chunk actually contains. This rewards
        # chunks that address the asked-about entities over incidental overlap.
        unique_terms = set(query_tokens)
        hits: list[EvidenceHit] = []
        for i, fused_score in fused.items():
            doc_terms = set(self._docs[i])
            coverage = len(unique_terms & doc_terms) / len(unique_terms)
            if coverage == 0.0:
                continue                 # never cite a chunk sharing no term
            chunk = self._chunks[i]
            hits.append(EvidenceHit(
                chunk_id=chunk.id,
                title=chunk.title,
                section=chunk.section,
                source=chunk.source,
                text=chunk.text,
                keyword_rank=keyword_ranks.get(i),
                dense_rank=dense_ranks.get(i),
                fused_score=round(fused_score, 6),
                rerank_score=round(coverage + fused_score, 6),
            ))
        hits.sort(key=lambda h: (-h.rerank_score, h.chunk_id))
        return hits[:k]


_default: HybridRetriever | None = None


def default_retriever() -> HybridRetriever:
    """The retriever over the bundled guideline corpus (built once per process)."""
    global _default
    if _default is None:
        corpus = _Corpus.model_validate(json.loads(_CORPUS_PATH.read_text()))
        _default = HybridRetriever(corpus.chunks)
    return _default

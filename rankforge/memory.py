"""Long-term memory: LangGraph PostgresStore, namespaced by user_id, with semantic (vector) search.

Namespaces (all start with the user_id, so one user can never search another user's memory):
  ("rankforge", user_id, "profile")   key = niche | tone | audience     -> one value each, overwritten
  ("rankforge", user_id, "facts")     lasting preferences/facts         -> semantic search
  ("rankforge", user_id, "links")     approved affiliate links
  ("rankforge", user_id, "topics")    past article topics (duplicate check, semantic search)

Embeddings: fastembed BAAI/bge-small-en-v1.5 (384 dims, runs locally on CPU, no extra API key; Claude has
no embeddings endpoint). If fastembed can't load, a 384-dim hashing embedder keeps the app working.
"""
import hashlib
import logging
import math
import os
import re
from uuid import uuid4

from langchain_core.embeddings import Embeddings

log = logging.getLogger("rankforge.memory")
EMBED_DIMS = 384
PROFILE_KEYS = ("niche", "tone", "audience")


class HashingEmbeddings(Embeddings):
    """Fallback only: word + bigram hashing. Not as good as a real model, but deterministic."""

    def _vec(self, text: str) -> list[float]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        grams = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
        v = [0.0] * EMBED_DIMS
        for g in grams:
            h = int(hashlib.md5(g.encode()).hexdigest(), 16)
            v[h % EMBED_DIMS] += 1.0 if (h >> 9) % 2 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


class FastEmbedEmbeddings(Embeddings):
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(model_name=model_name)

    def embed_documents(self, texts):
        return [list(map(float, v)) for v in self.model.embed(list(texts))]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


_embedder: Embeddings | None = None
EMBEDDER_NAME = "none"


def embedder() -> Embeddings:
    global _embedder, EMBEDDER_NAME
    if _embedder is None and os.getenv("RANKFORGE_EMBEDDINGS", "fastembed") == "hashing":
        _embedder, EMBEDDER_NAME = HashingEmbeddings(), "hashing-384"
    if _embedder is None:
        try:
            _embedder = FastEmbedEmbeddings()
            EMBEDDER_NAME = "fastembed:BAAI/bge-small-en-v1.5"
        except Exception as exc:  # model download blocked / no onnxruntime
            log.warning("fastembed unavailable (%s); using hashing embeddings", exc)
            _embedder = HashingEmbeddings()
            EMBEDDER_NAME = "hashing-384"
    return _embedder


def index_config() -> dict:
    return {"dims": EMBED_DIMS, "embed": embedder(), "fields": ["text"]}


def ns(user_id: str, kind: str) -> tuple[str, str, str]:
    if not user_id:
        raise PermissionError("user_id is required for memory access")
    return ("rankforge", user_id, kind)


# ── writes ──

def set_profile(store, user_id: str, key: str, value: str) -> None:
    store.put(ns(user_id, "profile"), key, {"text": f"{key}: {value}", "value": value})


def add_fact(store, user_id: str, text: str) -> None:
    # skip exact duplicates
    for item in store.search(ns(user_id, "facts"), limit=200):
        if item.value.get("text", "").strip().lower() == text.strip().lower():
            return
    store.put(ns(user_id, "facts"), uuid4().hex, {"text": text})


def add_link(store, user_id: str, url: str, label: str = "") -> None:
    key = hashlib.md5(url.encode()).hexdigest()[:16]
    store.put(ns(user_id, "links"), key, {"text": f"{label} {url}".strip(), "url": url, "label": label})


def add_topic(store, user_id: str, topic: str, article_id: str, status: str) -> None:
    store.put(ns(user_id, "topics"), article_id, {"text": topic, "article_id": article_id, "status": status})


def save_memory(store, user_id: str, kind: str, value: str) -> str:
    kind = kind.strip().lower()
    value = value.strip()[:300]
    if not value:
        return "Nothing to save."
    if kind in PROFILE_KEYS:
        set_profile(store, user_id, kind, value)
    elif kind in ("affiliate_link", "link"):
        url = re.search(r"https?://\S+", value)
        if not url:
            return "An affiliate link must be a full http(s) URL."
        add_link(store, user_id, url.group(0).rstrip(".,)"), value.replace(url.group(0), "").strip())
    else:
        add_fact(store, user_id, value)
    return f"Saved to long-term memory ({kind}): {value}"


def forget_all(store, user_id: str) -> int:
    n = 0
    for kind in ("profile", "facts", "links", "topics"):
        for item in store.search(ns(user_id, kind), limit=1000):
            store.delete(item.namespace, item.key)
            n += 1
    return n


# ── reads ──

def profile(store, user_id: str) -> dict[str, str]:
    out = {}
    for key in PROFILE_KEYS:
        item = store.get(ns(user_id, "profile"), key)
        if item:
            out[key] = item.value["value"]
    return out


def relevant_facts(store, user_id: str, query: str, k: int = 5) -> list[str]:
    """Semantic search over this user's facts (vector similarity in pgvector)."""
    items = store.search(ns(user_id, "facts"), query=query or None, limit=k)
    return [i.value["text"] for i in items]


def links(store, user_id: str, limit: int = 10) -> list[dict]:
    return [i.value for i in store.search(ns(user_id, "links"), limit=limit)]


def past_topics(store, user_id: str, limit: int = 20) -> list[str]:
    return [i.value["text"] for i in store.search(ns(user_id, "topics"), limit=limit)]


def similar_topic(store, user_id: str, topic: str, threshold: float = 0.93):
    """Semantic duplicate check: returns the stored topic item if a very similar topic exists."""
    items = store.search(ns(user_id, "topics"), query=topic, limit=1)
    if items and items[0].score is not None and items[0].score >= threshold:
        return items[0]
    return None


def export(store, user_id: str) -> dict:
    return {
        "profile": profile(store, user_id),
        "facts": [i.value["text"] for i in store.search(ns(user_id, "facts"), limit=100)],
        "affiliate_links": links(store, user_id, 100),
        "past_topics": past_topics(store, user_id, 100),
    }

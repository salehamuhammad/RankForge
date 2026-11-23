"""Postgres (Supabase / Neon). Every query on business data filters on business_id."""
import json
from typing import Any
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from rankforge.config import DATABASE_URL

_pool: ConnectionPool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS rf_accounts (
    user_id TEXT PRIMARY KEY,
    pw_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rf_articles (
    id TEXT PRIMARY KEY,
    business_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    meta_description TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    keywords JSONB NOT NULL DEFAULT '[]',
    links JSONB NOT NULL DEFAULT '[]',
    claims JSONB NOT NULL DEFAULT '[]',
    fact_check JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',      -- pending | published | rejected | needs_revision
    live_url TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS rf_articles_business ON rf_articles (business_id, status);
CREATE TABLE IF NOT EXISTS rf_events (
    id BIGSERIAL PRIMARY KEY,
    business_id TEXT NOT NULL,
    post_id TEXT NOT NULL,
    kind TEXT NOT NULL,                           -- view | click
    target TEXT NOT NULL DEFAULT '',
    at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rf_events_business ON rf_events (business_id, post_id);
CREATE TABLE IF NOT EXISTS rf_keywords (
    id BIGSERIAL PRIMARY KEY,
    business_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    keyword TEXT NOT NULL,
    volume INT NOT NULL,
    difficulty INT NOT NULL,
    intent TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rf_keywords_business ON rf_keywords (business_id);
CREATE TABLE IF NOT EXISTS rf_usage (
    id BIGSERIAL PRIMARY KEY,
    business_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    tokens_in INT NOT NULL,
    tokens_out INT NOT NULL,
    cost_usd DOUBLE PRECISION NOT NULL,
    latency_ms INT NOT NULL,
    route TEXT NOT NULL DEFAULT '',
    at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rf_usage_business ON rf_usage (business_id);
"""


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set (use your Supabase or Neon Postgres URL).")
        # prepare_threshold=None: works through Supabase/Neon poolers (pgbouncer transaction mode)
        _pool = ConnectionPool(
            DATABASE_URL, min_size=1, max_size=8, open=True, timeout=30,
            kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
            check=ConnectionPool.check_connection,
        )
    return _pool


def init_schema() -> None:
    with pool().connection() as conn:
        conn.execute(SCHEMA)


def _rows(sql: str, params: tuple) -> list[dict[str, Any]]:
    with pool().connection() as conn:
        return list(conn.execute(sql, params).fetchall())


def _one(sql: str, params: tuple) -> dict[str, Any] | None:
    rows = _rows(sql, params)
    return rows[0] if rows else None


def _exec(sql: str, params: tuple) -> None:
    with pool().connection() as conn:
        conn.execute(sql, params)


# ── accounts (website login) ──

def get_account(user_id: str):
    return _one("SELECT user_id, pw_hash FROM rf_accounts WHERE user_id=%s", (user_id,))


def create_account(user_id: str, pw_hash: str) -> None:
    _exec("INSERT INTO rf_accounts (user_id, pw_hash) VALUES (%s,%s) ON CONFLICT DO NOTHING", (user_id, pw_hash))


# ── articles ──

def insert_article(business_id: str, thread_id: str, topic: str, draft: dict, keywords: list,
                   fact_check: dict, status: str) -> str:
    article_id = uuid4().hex[:12]
    _exec(
        "INSERT INTO rf_articles (id,business_id,thread_id,topic,title,meta_description,body,keywords,links,"
        "claims,fact_check,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (article_id, business_id, thread_id, topic, draft.get("title", topic), draft.get("meta_description", ""),
         draft.get("body", ""), json.dumps(keywords), json.dumps(draft.get("links", [])),
         json.dumps(draft.get("claims", [])), json.dumps(fact_check), status),
    )
    return article_id


def get_article(business_id: str, article_id: str):
    return _one("SELECT * FROM rf_articles WHERE id=%s AND business_id=%s", (article_id, business_id))


def list_articles(business_id: str, status: str | None = None, limit: int = 50):
    if status:
        return _rows("SELECT * FROM rf_articles WHERE business_id=%s AND status=%s ORDER BY created_at DESC LIMIT %s",
                     (business_id, status, limit))
    return _rows("SELECT * FROM rf_articles WHERE business_id=%s ORDER BY created_at DESC LIMIT %s",
                 (business_id, limit))


def find_article_by_topic(business_id: str, topic: str):
    return _one("SELECT * FROM rf_articles WHERE business_id=%s AND lower(topic)=lower(%s) "
                "AND status IN ('pending','published') ORDER BY created_at DESC LIMIT 1", (business_id, topic))


def set_article_status(business_id: str, article_id: str, status: str, live_url: str = "") -> None:
    _exec("UPDATE rf_articles SET status=%s, live_url=COALESCE(NULLIF(%s,''), live_url), "
          "published_at=CASE WHEN %s='published' THEN now() ELSE published_at END "
          "WHERE id=%s AND business_id=%s", (status, live_url, status, article_id, business_id))


def public_posts(business_id: str):
    return _rows("SELECT id,title,meta_description,published_at,live_url FROM rf_articles "
                 "WHERE business_id=%s AND status='published' ORDER BY published_at DESC", (business_id,))


def public_post(post_id: str):
    """Published posts are public (it is a live blog). Drafts never are."""
    return _one("SELECT id,business_id,title,meta_description,body,links,published_at FROM rf_articles "
                "WHERE id=%s AND status='published'", (post_id,))


# ── clicks / views ──

def record_event(business_id: str, post_id: str, kind: str, target: str = "") -> None:
    _exec("INSERT INTO rf_events (business_id,post_id,kind,target) VALUES (%s,%s,%s,%s)",
          (business_id, post_id, kind, target))


def click_stats(business_id: str, post_id: str | None = None):
    sql = ("SELECT a.id AS post_id, a.title, a.live_url, "
           "COUNT(e.id) FILTER (WHERE e.kind='view') AS views, "
           "COUNT(e.id) FILTER (WHERE e.kind='click') AS clicks "
           "FROM rf_articles a LEFT JOIN rf_events e ON e.post_id=a.id AND e.business_id=a.business_id "
           "WHERE a.business_id=%s AND a.status='published'")
    params: tuple = (business_id,)
    if post_id:
        sql += " AND a.id=%s"
        params += (post_id,)
    return _rows(sql + " GROUP BY a.id, a.title, a.live_url ORDER BY a.published_at DESC", params)


def clicks_by_day(business_id: str):
    return _rows("SELECT to_char(date_trunc('day', at),'YYYY-MM-DD') AS day, "
                 "COUNT(*) FILTER (WHERE kind='view') AS views, COUNT(*) FILTER (WHERE kind='click') AS clicks "
                 "FROM rf_events WHERE business_id=%s AND at > now() - interval '30 days' "
                 "GROUP BY 1 ORDER BY 1", (business_id,))


# ── keywords ──

def save_keywords(business_id: str, topic: str, keywords: list[dict]) -> None:
    with pool().connection() as conn:
        for k in keywords:
            conn.execute("INSERT INTO rf_keywords (business_id,topic,keyword,volume,difficulty,intent) "
                         "VALUES (%s,%s,%s,%s,%s,%s)",
                         (business_id, topic, k["keyword"], k["volume"], k["difficulty"], k["intent"]))


def list_keywords(business_id: str, limit: int = 100):
    return _rows("SELECT DISTINCT ON (keyword) keyword, volume, difficulty, intent, topic, at FROM rf_keywords "
                 "WHERE business_id=%s ORDER BY keyword, at DESC LIMIT %s", (business_id, limit))


# ── usage and cost ──

def record_usage(business_id: str, thread_id: str, tokens_in: int, tokens_out: int, cost: float,
                 latency_ms: int, route: str) -> None:
    _exec("INSERT INTO rf_usage (business_id,thread_id,tokens_in,tokens_out,cost_usd,latency_ms,route) "
          "VALUES (%s,%s,%s,%s,%s,%s,%s)", (business_id, thread_id, tokens_in, tokens_out, cost, latency_ms, route))


def user_spend(business_id: str) -> float:
    row = _one("SELECT COALESCE(SUM(cost_usd),0) AS s FROM rf_usage WHERE business_id=%s", (business_id,))
    return float(row["s"]) if row else 0.0


def usage_summary(business_id: str):
    return _one("SELECT COUNT(*) AS requests, COALESCE(SUM(tokens_in),0) AS tokens_in, "
                "COALESCE(SUM(tokens_out),0) AS tokens_out, COALESCE(SUM(cost_usd),0) AS cost_usd, "
                "COALESCE(AVG(latency_ms),0)::int AS avg_latency_ms FROM rf_usage WHERE business_id=%s",
                (business_id,))

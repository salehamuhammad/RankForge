"""RankForge Agent API.

Required for grading (open, no login):
  POST /chat    {"user_id","thread_id","message"} -> {"reply","tool_calls","tokens":{"in","out"}}
  GET  /health  -> ok
Website API (dashboard endpoints need the login token; public blog endpoints don't).
"""
import hashlib
import hmac
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from langchain.messages import AIMessage, HumanMessage
from langchain_core.callbacks import UsageMetadataCallbackHandler
from pydantic import BaseModel

from rankforge import db, memory
from rankforge.config import ANTHROPIC_MODEL, AUTH_SECRET, RFContext, cost_usd
from rankforge.graph import build_graph
from rankforge.tools import publish_post_impl

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("rankforge.api")
STATE: dict[str, Any] = {}
_thread_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def build_runtime():
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.store.postgres import PostgresStore
    pool = db.pool()
    db.init_schema()
    checkpointer = PostgresSaver(pool)
    checkpointer.setup()
    store = PostgresStore(pool, index=memory.index_config())
    store.setup()
    STATE.update(store=store, graph=build_graph(checkpointer=checkpointer, store=store))
    log.info("RankForge ready: model=%s embeddings=%s", ANTHROPIC_MODEL, memory.EMBEDDER_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if "graph" not in STATE:
        build_runtime()
    yield


app = FastAPI(title="RankForge Agent API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def empty_response(reply: str) -> dict:
    return {"reply": reply, "tool_calls": [], "tokens": {"in": 0, "out": 0}}


@app.exception_handler(RequestValidationError)
async def bad_request(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content=empty_response(
        'Bad request: send JSON {"user_id": "...", "thread_id": "...", "message": "..."}.'))


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"


@app.get("/")
def root():
    return {"name": "RankForge Agent API", "model": ANTHROPIC_MODEL, "endpoints": ["POST /chat", "GET /health"]}


class ChatIn(BaseModel):
    user_id: str
    thread_id: str
    message: str


def _text(msg) -> str:
    if isinstance(msg.content, str):
        return msg.content
    return "".join(b.get("text", "") for b in msg.content if isinstance(b, dict) and b.get("type") == "text")


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _thread_locks.setdefault(key, threading.Lock())


def run_chat(user_id: str, thread_id: str, message: str) -> dict:
    user_id, thread_id, message = user_id.strip()[:128], thread_id.strip()[:128], message.strip()
    if not user_id or not thread_id or not message:
        return empty_response("Please send a non-empty user_id, thread_id and message.")
    ctx = RFContext(user_id=user_id, business_id=user_id, thread_id=thread_id)
    thread_key = f"{user_id}::{thread_id}"          # thread ids are prefixed with the user id: no cross-user threads
    usage = UsageMetadataCallbackHandler()
    config = {"configurable": {"thread_id": thread_key}, "callbacks": [usage], "recursion_limit": 40}
    started = time.time()
    route = "error"
    with _lock_for(thread_key):
        try:
            result = STATE["graph"].invoke({"messages": [HumanMessage(message)], "turn_tools": None},
                                           config, context=ctx)
            ai = next((m for m in reversed(result["messages"]) if isinstance(m, AIMessage) and _text(m).strip()), None)
            reply = _text(ai) if ai else "Done."
            tool_calls = result.get("turn_tools") or []
            route = result.get("route", "")
        except Exception as exc:
            log.exception("chat failed")
            reply = f"Sorry, something went wrong on my side ({type(exc).__name__}). Please try again."
            tool_calls = []
    t_in = sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values())
    t_out = sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values())
    latency = int((time.time() - started) * 1000)
    try:
        db.record_usage(user_id, thread_id, t_in, t_out, cost_usd(t_in, t_out), latency, route)
    except Exception:
        log.exception("usage not recorded")
    return {"reply": reply, "tool_calls": tool_calls, "tokens": {"in": t_in, "out": t_out}}


@app.post("/chat")
def chat(body: ChatIn):
    return run_chat(body.user_id, body.thread_id, body.message)


# ── website login (simple, per business) ──

def _hash_pw(user_id: str, pw: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), f"rankforge:{user_id}".encode(), 120_000).hex()


def _token(user_id: str) -> str:
    sig = hmac.new(AUTH_SECRET.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{user_id}.{sig}"


def current_user(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    user_id, _, _sig = token.rpartition(".")
    if not user_id or not hmac.compare_digest(_token(user_id), token):
        raise HTTPException(401, "Please log in.")
    return user_id


class LoginIn(BaseModel):
    user_id: str
    password: str


@app.post("/auth/login")
def login(body: LoginIn):
    user_id = body.user_id.strip()[:128]
    if not user_id or len(body.password) < 4:
        raise HTTPException(400, "Business id and a password of at least 4 characters are required.")
    acct = db.get_account(user_id)
    if acct is None:
        db.create_account(user_id, _hash_pw(user_id, body.password))
    elif not hmac.compare_digest(acct["pw_hash"], _hash_pw(user_id, body.password)):
        raise HTTPException(401, "Wrong password for this business.")
    return {"token": _token(user_id), "user_id": user_id, "created": acct is None}


# ── admin dashboard (only the logged-in business's rows) ──

def _article_out(a: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in a.items()}


@app.get("/api/me/articles")
def my_articles(status: str = "", user: str = Depends(current_user)):
    return [_article_out(a) for a in db.list_articles(user, status or None)]


@app.post("/api/me/articles/{article_id}/approve")
def approve(article_id: str, user: str = Depends(current_user)):
    result = publish_post_impl(user, article_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "cannot publish"))
    a = db.get_article(user, article_id)
    store = STATE["store"]
    memory.add_topic(store, user, a["topic"], article_id, "published")
    for l in a.get("links") or []:
        if l.get("type") == "affiliate":
            memory.add_link(store, user, l["url"], l.get("title", ""))
    return result


@app.post("/api/me/articles/{article_id}/reject")
def reject(article_id: str, user: str = Depends(current_user)):
    a = db.get_article(user, article_id)
    if not a:
        raise HTTPException(404, "Not found")
    db.set_article_status(user, article_id, "rejected")
    return {"ok": True}


@app.get("/api/me/keywords")
def my_keywords(user: str = Depends(current_user)):
    return [_article_out(k) for k in db.list_keywords(user)]


@app.get("/api/me/stats")
def my_stats(user: str = Depends(current_user)):
    return {"posts": [{**r, "views": int(r["views"]), "clicks": int(r["clicks"])} for r in db.click_stats(user)],
            "by_day": [{**r, "views": int(r["views"]), "clicks": int(r["clicks"])} for r in db.clicks_by_day(user)]}


@app.get("/api/me/usage")
def my_usage(user: str = Depends(current_user)):
    u = db.usage_summary(user) or {}
    return {k: (float(v) if k == "cost_usd" else int(v)) for k, v in u.items()}


@app.get("/api/me/memory")
def my_memory(user: str = Depends(current_user)):
    return memory.export(STATE["store"], user)


# ── public live blog ──

@app.get("/api/public/blog/{business_id}")
def blog(business_id: str):
    return [_article_out(p) for p in db.public_posts(business_id)]


@app.get("/api/public/posts/{post_id}")
def post(post_id: str):
    p = db.public_post(post_id)
    if not p:
        raise HTTPException(404, "Post not found")
    db.record_event(p["business_id"], post_id, "view")
    return _article_out(p)


@app.get("/r/{post_id}")
def tracked_click(post_id: str, u: str):
    """Affiliate/source link click tracking. Only redirects to links that are in the article (no open redirect)."""
    p = db.public_post(post_id)
    if not p or u not in [l.get("url") for l in p.get("links") or []]:
        raise HTTPException(404, "Link not found")
    db.record_event(p["business_id"], post_id, "click", u)
    return RedirectResponse(u, status_code=302)

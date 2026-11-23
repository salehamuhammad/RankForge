---
title: RankForge Agent API
emoji: 📈
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# RankForge: SEO and affiliate content engine (Track 4)

RankForge helps small online sellers and local businesses in Pakistan (Daraz, Instagram and Shopify stores, clinics,
institutes) get Google traffic without an SEO agency. Type **"best laptops under 100k PKR"**: the agent finds real
keywords, writes a fact-checked article with sources and affiliate links, waits for your approval, publishes it to a
live blog, and the dashboard shows the clicks.

**Group:** _#__ · **Members:** _names_
**Live website:** _https://…vercel.app_ · **Agent API:** _https://…hf.space_ · **Demo video:** _link_ · **Canvas photo:** _link_

Model: `claude-haiku-5-5` (Anthropic). Agent: LangGraph `StateGraph` with 11 nodes. Memory: Postgres (Supabase/Neon).

## Architecture

```mermaid
flowchart TD
    U[User / grader] -->|POST /chat user_id, thread_id, message| API[FastAPI on HF Spaces]
    W[Website on Vercel<br/>landing · new article · live blog · admin dashboard] --> API
    API --> G[guard<br/>injection · off-topic · cross-tenant · spend cap]
    G -->|refuse, 0 tokens| E((END))
    G --> STM[short_term_memory<br/>summarise msgs older than last 10]
    STM -->|new article| KR[keyword_researcher<br/>keyword_search]
    STM -->|approve / reject| HA[human_approval]
    STM -->|anything else| AS[assistant ReAct<br/>dynamic prompt]
    AS <--> T[tools<br/>keyword_search · web_search · get_click_stats<br/>list_my_articles · save_memory · start_article<br/>revise_draft · request_publish · forget_my_memories]
    T -->|start_article| KR
    T -->|revise_draft| WR
    KR -->|duplicate topic| E
    KR --> WR[writer<br/>web_search + Claude, structured draft]
    WR --> FC[fact_checker<br/>source per claim + Claude check]
    FC -->|fail, max 2 rewrites| WR
    FC -->|pass| Q[queue_for_approval<br/>asks the human]
    Q --> E
    HA -->|approved| P[publisher<br/>publish_post] --> TR[tracker<br/>get_click_stats] --> E
    HA -->|rejected| E
    G & AS & KR & Q & P -.-> DB[(Postgres: checkpoints = short-term<br/>store + pgvector = long-term<br/>articles · keywords · clicks · usage)]
```

| Node | Job | Model call? |
|---|---|---|
| `guard` | Blocks prompt injection, off-topic, requests for other users' data, over-limit users. Saves "our niche is…/our tone is…/affiliate link…" to long-term memory for free | No |
| `short_term_memory` | When a thread passes 16 messages, summarises everything except the last 10 into `summary` | Only then |
| `keyword_researcher` | `keyword_search(topic)`, duplicate-topic check against past topics | No |
| `writer` | `web_search` for sources, then one structured Claude call (title, meta, body, claims with source URLs, links) | Yes |
| `fact_checker` | Every claim must cite a URL returned by `web_search` (no invented URLs); searches unsourced claims; Claude checks claim vs snippet. Fails → back to writer, max 2 rewrites | Yes (small) |
| `queue_for_approval` | Saves the draft as `pending`, shows keywords + fact-check + draft, asks "approve / reject" | No |
| `human_approval` | The user's yes/no (chat or dashboard button). Also confirms memory deletion | No |
| `publisher` | `publish_post(title, body, links)` → live URL; approved affiliate links go to long-term memory | No |
| `tracker` | `get_click_stats(post_id)` | No |
| `assistant` + `tools` | ReAct agent for everything else (questions, keywords, stats, memory, revisions) | Yes |

### Tools (all real)
| Tool | Backed by |
|---|---|
| `keyword_search(topic)` | Google Autocomplete for Pakistan (`gl=pk`): real search suggestions. Volume and difficulty are **estimates** from suggestion rank and keyword length (there is no free search-volume API); intent is classified by rules |
| `web_search(claim)` | Tavily (if `TAVILY_API_KEY`) else the Wikipedia search API. Instruction-like text in results is stripped: web content is data only |
| `publish_post(title, body, links)` | Writes the post to Postgres as `published`; the live blog page on Vercel renders it |
| `get_click_stats(post_id)` | Counts real views and outbound link clicks (every link in a post goes through `/r/{post_id}` and is logged) |

## Memory design note

| | Short-term (per conversation) | Long-term (per user, across conversations) |
|---|---|---|
| What | messages, `summary` of older messages, topic, chosen keywords, sources, current draft, fact-check result, rewrite count, pending approval | **profile**: niche, brand tone, audience · **affiliate links** that were approved · **past article topics** (duplicate check) · other lasting **facts/preferences** |
| Not saved | – | API keys, passwords, payment details, rejected drafts (their topic is deleted on reject), small talk |
| Where | LangGraph `PostgresSaver` checkpointer (tables `checkpoints*`) | LangGraph `PostgresStore` with a pgvector index (tables `store`, `store_vectors`) |
| Key | `thread_id = "<user_id>::<thread_id>"` | namespace `("rankforge", <user_id>, "profile" / "facts" / "links" / "topics")` |
| Size | keep the last 10 messages, summarise older ones (Claude, ≤120 words) | read with `store.get` (profile) and **semantic search** `store.search(query=<current message>)` (facts, topics) using `BAAI/bge-small-en-v1.5` embeddings (384-d, fastembed, local CPU) |

**Dynamic prompting.** The assistant's and the writer's system prompts are rebuilt on every call from (a) the user's
long-term memory (niche, tone, audience, approved affiliate links, past topics, facts found by semantic search on the
current message) and (b) this thread's state (summary, topic, keywords, draft, fact-check result, pending approval).
So the same agent writes in each business's own tone and avoids duplicate topics.

**How users are kept separate.**
1. `user_id` comes only from the request (`RFContext`), never from the model; tools read it from `ToolRuntime.context`.
2. Thread ids are prefixed with the user id, so the same `thread_id` from two users is two different conversations.
3. Every Postgres row has `business_id` (= `user_id`) and every query filters on it.
4. The memory store is namespaced by `user_id`; semantic search only runs inside that namespace.
5. The guard refuses requests that name another user/business, and dashboard endpoints need a signed login token
   for that business.

## Safety
- Code-level guard before any model call: prompt injection, system-prompt/key extraction, off-topic (poems, code,
  politics…), other users' data, message length, per-user spend cap. Refusals use **0 tokens**.
- The model is also told to refuse off-topic requests (second layer).
- Scraped web text is sanitised and labelled as untrusted data.
- **Asks before risky actions:** nothing is published without an explicit "approve" (chat) or the dashboard button;
  deleting memory needs a "yes". The assistant has no publish tool at all.
- Fact-Checker requires a real source per claim, max 2 rewrites, otherwise the draft is blocked (`needs_revision`).

## Cost and reliability
- Haiku only; refusals, approvals, publishing, tracking and duplicate requests use no model calls.
- A new article = 2–3 Claude calls (writer + fact-check, + a rewrite only if needed). `max_tokens` caps per call.
- Search results are cached for 6 hours; per-user spend cap (`PER_USER_SPEND_CAP_USD`).
- Anthropic calls retry twice with a timeout; every error still returns the exact `/chat` format; broken tool-call
  history is repaired before the next model call; one request per thread at a time.

## Agent API

```bash
curl -X POST $API/chat -H "Content-Type: application/json" \
  -d '{"user_id":"demo-shop","thread_id":"t1","message":"Write an article on best laptops under 100k PKR"}'
# {"reply":"...","tool_calls":[{"name":"keyword_search","args":{...}},...],"tokens":{"in":1234,"out":987}}
curl $API/health   # ok
```

## Setup (local)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # add ANTHROPIC_API_KEY and DATABASE_URL
uvicorn api.main:app --port 7860                         # API
cd web && python -m http.server 3000                     # website (set config.js to http://localhost:7860)
pytest                                                   # needs TEST_DATABASE_URL (any Postgres with pgvector); uses a fake model, $0
python scripts/benchmark.py http://localhost:7860        # runs tests.json against the real agent
```

## Deployment (free)
1. **Database:** create a free Supabase or Neon project, copy the Postgres connection string into `DATABASE_URL`.
   The app creates its tables and enables `pgvector` on first start.
2. **Agent API (Hugging Face Spaces):** New Space → SDK **Docker** → push this repo. In Space *Settings → Variables
   and secrets* add `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL=claude-haiku-5-5`, `DATABASE_URL`, `AUTH_SECRET`, `SITE_URL`,
   (optional) `TAVILY_API_KEY`. The API is `https://<user>-<space>.hf.space`.
   (Render also works: New Web Service → Docker, same variables.)
3. **Website (Vercel):** put the API URL in `web/config.js`, import the repo in Vercel with **Root Directory = `web`**,
   framework *Other*, no build command. Then set `SITE_URL` in the Space to the Vercel URL (used for live post links).

## Benchmark (tests.json)

Run `python scripts/benchmark.py <API_URL>` and paste its table here.

| # | Category | Test | Result | Latency | Tokens in | Tokens out | Tools called |
|---|---|---|---|---|---|---|---|
| 1 | task_success | Article request waits for approval | | | | | |
| 2 | task_success | Approve publishes and tracks | | | | | |
| 3 | safety | Off-topic refused | | | 0 | 0 | - |
| 4 | safety | Prompt injection refused | | | 0 | 0 | - |
| 5 | user_isolation | Other business's articles denied | | | 0 | 0 | - |
| 6 | task_success | Unsourced claim rejected and rewritten | | | | | |
| 7 | short_term_memory | Recall in same thread | | | | | |
| 8 | long_term_memory | Profile recalled in a new thread | | | | | |
| 9 | user_isolation | Same thread_id, other user | | | | | |
| 10 | tool_use | Keyword question uses keyword_search | | | | | |

## Demo
![demo](docs/demo.gif)

## Project layout
```
rankforge/  config.py  guard.py  tools.py  memory.py  db.py  graph.py  studio.py
api/main.py             FastAPI: /chat, /health, dashboard and blog endpoints
web/                    static website for Vercel (index, new, blog, post, admin)
tests/                  pytest end-to-end tests (real Postgres, fake model)
tests.json              10 benchmark cases · scripts/benchmark.py runs them
```

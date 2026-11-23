"""Tests run against a real Postgres (set TEST_DATABASE_URL) with a scripted fake Claude, so they cost $0."""
import os
import re
import uuid

import pytest
from langchain.messages import AIMessage

os.environ.setdefault("DATABASE_URL", os.getenv("TEST_DATABASE_URL", "postgresql://postgres@localhost:5432/rankforge"))
os.environ["RANKFORGE_EMBEDDINGS"] = "hashing"
os.environ["PER_USER_SPEND_CAP_USD"] = "5"

from rankforge import graph as G  # noqa: E402


class FakeStructured:
    def __init__(self, schema):
        self.schema = schema

    def invoke(self, messages, config=None):
        system = messages[0].content
        human = messages[-1].content
        if self.schema is G.ArticleDraft:
            urls = re.findall(r"\| (https?://\S+)", system)
            claims = [G.Claim(text="Laptops are portable computers [1]", source_url=urls[0])] if urls else []
            if "tax-free" in human and "REWRITE" not in system:      # user-requested unsourced claim
                claims.append(G.Claim(text="All laptops are tax-free in Pakistan", source_url=""))
            links = [G.Link(title="Source", url=u, type="source") for u in urls[:2]]
            return G.ArticleDraft(title="Best Laptops Under 100k PKR", meta_description="Guide",
                                  body="## Intro\nLaptops [1]\n\n## Sources\n" + "\n".join(urls[:2]),
                                  claims=claims, links=links)
        if self.schema is G.FactCheck:
            return G.FactCheck(passed=True)
        raise AssertionError(self.schema)


class FakeLLM:
    def bind_tools(self, tools, tool_choice=None):
        self.no_tools = tool_choice == {"type": "none"}
        return self

    def with_structured_output(self, schema):
        return FakeStructured(schema)

    def invoke(self, messages, config=None):
        system = messages[0].content if messages[0].type == "system" else ""
        last = messages[-1]
        if last.type == "tool":
            return AIMessage(f"Tool said: {last.content[:200]}")
        text = last.content if isinstance(last.content, str) else ""
        if re.search(r"keywords? for", text, re.I) and not getattr(self, "no_tools", False):
            return AIMessage("", tool_calls=[{"name": "keyword_search", "args": {"topic": "phones"}, "id": "c" + uuid.uuid4().hex[:6]}])
        if re.search(r"click stats", text, re.I):
            return AIMessage("", tool_calls=[{"name": "get_click_stats", "args": {}, "id": "c" + uuid.uuid4().hex[:6]}])
        if re.search(r"forget everything", text, re.I):
            return AIMessage("", tool_calls=[{"name": "forget_my_memories", "args": {}, "id": "c" + uuid.uuid4().hex[:6]}])
        if re.search(r"my name is (\w+)", text, re.I):
            return AIMessage("Nice to meet you!")
        if re.search(r"what('?s| is) my name", text, re.I):
            names = [re.search(r"my name is (\w+)", m.content, re.I) for m in messages if m.type == "human"]
            names = [n.group(1) for n in names if n]
            return AIMessage(f"Your name is {names[-1]}." if names else "I don't know your name.")
        if re.search(r"niche|tone", text, re.I):
            niche = re.search(r"- Niche: (.+)", system).group(1)
            tone = re.search(r"- Brand tone: (.+)", system).group(1)
            return AIMessage(f"Your niche is {niche} and your tone is {tone}.")
        return AIMessage("OK")


@pytest.fixture(scope="session")
def client():
    G.llm.cache_clear()
    G.llm = lambda kind="chat": FakeLLM()
    sources = [{"title": "Laptop - Wikipedia", "url": "https://en.wikipedia.org/wiki/Laptop", "snippet": "A laptop is a portable computer."},
               {"title": "Price list", "url": "https://example.pk/laptops", "snippet": "Laptop prices in Pakistan."}]
    G.web_search_impl = lambda q, n=5: [dict(s) for s in sources[:n]]
    G.keyword_search_impl.__globals__["_google_suggest"] = lambda q: [q, q + " 2026", q + " review"]
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def uid():
    return "test-" + uuid.uuid4().hex[:8]

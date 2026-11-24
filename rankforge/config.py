"""All settings come from environment variables (.env locally, Space/Render secrets in production)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
DATABASE_URL = os.getenv("DATABASE_URL", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
SITE_URL = os.getenv("SITE_URL", "http://localhost:3000").rstrip("/")      # the Vercel website
API_PUBLIC_URL = os.getenv("API_PUBLIC_URL", "http://localhost:7860").rstrip("/")
AUTH_SECRET = os.getenv("AUTH_SECRET", "change-me")

# Cost controls. Prices are USD per million tokens; set them to the published Haiku price.
PRICE_IN_PER_MTOK = float(os.getenv("PRICE_IN_PER_MTOK", "1.0"))
PRICE_OUT_PER_MTOK = float(os.getenv("PRICE_OUT_PER_MTOK", "5.0"))
PER_USER_SPEND_CAP_USD = float(os.getenv("PER_USER_SPEND_CAP_USD", "0.30"))
ARTICLE_MAX_TOKENS = int(os.getenv("ARTICLE_MAX_TOKENS", "2500"))
CHAT_MAX_TOKENS = int(os.getenv("CHAT_MAX_TOKENS", "600"))

MAX_REWRITES = 2           # Fact-Checker -> Writer loop limit (canvas: max 2 rewrites)
KEEP_LAST_MESSAGES = 10    # short-term memory trim: keep the last 10, summarise older ones
SUMMARIZE_AFTER = 16       # start summarising once the thread has more than this many messages
MAX_ASSISTANT_STEPS = 4    # model calls per turn on the assistant path


@dataclass(frozen=True)
class RFContext:
    """Set by the API from the request, never by the model.

    business_id == user_id: each user is one business. Every DB row and memory namespace is keyed by it.
    """
    user_id: str
    business_id: str
    thread_id: str


def cost_usd(tokens_in: int, tokens_out: int) -> float:
    return tokens_in / 1e6 * PRICE_IN_PER_MTOK + tokens_out / 1e6 * PRICE_OUT_PER_MTOK

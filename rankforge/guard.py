"""Code-level safety rules. They run before any model call, so a refusal costs 0 tokens."""
import re

INJECTION = [re.compile(p, re.I) for p in (
    r"\b(ignore|disregard|forget|override|bypass|skip)\b.{0,40}\b(instructions?|rules?|prompts?|guardrails?|guidelines?|polic(y|ies)|safety)\b",
    r"\b(reveal|show|print|repeat|output|leak|display|tell me|give me|what('?s| is| are))\b.{0,30}\b(system|hidden|initial|original|developer)\s*(prompt|message|instructions?)",
    r"\bsystem\s*prompt\b",
    r"\b(you are now|from now on you are|act as|pretend (to be|you are)|roleplay as|jailbreak|developer mode|\bDAN\b)",
    r"\b(api[\s_-]?key|anthropic[\s_-]?key|secret key|access token|\.env\b|database[\s_-]?url|env(ironment)? variables?|password)\b",
    r"<\s*/?\s*(system|instructions?)\s*>",
    r"\bnew instructions?\s*:",
)]

OFF_TOPIC = [re.compile(p, re.I) for p in (
    r"\b(poem|poetry|haiku|limerick|sonnet|song|lyrics|rap|joke|riddle|bedtime story|short story|fairy ?tale|love letter)\b",
    r"\b(recipe|cook|bake)\b(?!.{0,40}\b(blog|article|seo|keyword))",
    r"\b(solve|calculate|integral|derivative|equation|homework|assignment)\b(?!.{0,40}\b(seo|keyword|article))",
    r"\bwrite\b.{0,25}\b(python|java(script)?|c\+\+|sql|code|script|program|function)\b",
    r"\b(weather|horoscope|lottery|cricket score|football score|who won)\b",
    r"\b(election|vote for|political party|prime minister|president of)\b",
    r"\b(diagnos|symptom|medicine dose|legal advice|lawsuit)\w*",
    r"\b(girlfriend|boyfriend|date me|flirt)\b",
    r"\btranslate\b(?!.{0,40}\b(article|post|blog))",
)]

CROSS_TENANT = [re.compile(p, re.I) for p in (
    r"\b(another|other|different|someone else'?s?|competitor'?s?)\s+(business|user|company|client|customer|account|store|seller|tenant)('?s)?\b",
    r"\b(all|every|list (all|the)) (users|businesses|accounts|clients|tenants)\b",
    r"\b(all|every)\s+(users?|business(es)?|accounts?)'?\s*(data|articles|posts|links|stats)\b",
)]
# an explicit identifier of some user/business, e.g. "user_b", "business-42", "user id: abc123"
ID_MENTION = re.compile(r"\b(user|business|account|client|tenant)(?:[\s_-]*id)?\s*(?:[:=#]|\bis\b)?\s*[\"']?"
                        r"([A-Za-z0-9][\w.@-]*)", re.I)
ID_JOINED = re.compile(r"\b((?:user|business|account|client|tenant)[_-][\w.@-]+)\b", re.I)
DATA_WORDS = re.compile(r"\b(articles?|posts?|drafts?|links?|keywords?|stats|clicks|data|memor(y|ies)|history|content)\b", re.I)


APPROVE = re.compile(r"^\s*(yes|y|yeah|yep|approve[ds]?|i approve|confirm(ed)?|go ahead|publish( it| now| the article| this)?"
                     r"|do it|ok(ay)?|sure|looks good|lgtm)\b(?!.{0,15}\b(not|don'?t)\b)", re.I)
REJECT = re.compile(r"^\s*(no|nope|nah|reject(ed)?|cancel|don'?t|do not|stop|discard|decline)\b", re.I)

ARTICLE_REQUEST = re.compile(
    r"\b(write|create|draft|generate|make|prepare|produce|need|want)\b.{0,40}\b(article|blog( post)?|post|guide|listicle|review article|seo content)\b"
    r"|^\s*(article|blog post|post)\s+(on|about)\b", re.I)
TOPIC_PREFIX = re.compile(
    r"^.*?\b(article|blog post|blog|post|guide|listicle|content)\b\s*(on|about|for|regarding|titled|called)?\s*[:\-]?\s*", re.I)

INJECTION_REFUSAL = ("I can't share or change my instructions, keys or internal settings. "
                     "I can help you research keywords and write fact-checked SEO articles.")
OFF_TOPIC_REFUSAL = ("Sorry, that's outside what I do. I'm RankForge: I can research keywords, write "
                     "fact-checked SEO/affiliate articles, publish them after your approval, and show click stats.")
CROSS_TENANT_REFUSAL = ("I can't access another business's or user's data. Every account is kept separate, "
                        "so I can only show your own articles, keywords, links and stats.")
TOO_LONG_REFUSAL = "That message is too long. Please keep requests under 4,000 characters."

# Instruction-like text inside scraped web pages is removed before the model sees it.
WEB_INJECTION = re.compile(
    r"(ignore|disregard|forget|override)[^.\n]{0,60}(instructions?|prompts?|rules?)[^.\n]*|"
    r"(you are (now )?an? (ai|assistant|chatbot))[^.\n]*|system prompt[^.\n]*|(assistant|ai)\s*:\s*[^.\n]*", re.I)


def check(text: str, own_user_id: str) -> tuple[str, str] | None:
    """Return (kind, refusal_text) if the message must be refused, else None."""
    if len(text) > 4000:
        return "too_long", TOO_LONG_REFUSAL
    for p in INJECTION:
        if p.search(text):
            return "injection", INJECTION_REFUSAL
    for p in CROSS_TENANT:
        if p.search(text):
            return "cross_tenant", CROSS_TENANT_REFUSAL
    if DATA_WORDS.search(text):
        own = own_user_id.lower()
        ids = [m.group(1) for m in ID_JOINED.finditer(text)]
        ids += [m.group(2) for m in ID_MENTION.finditer(text)
                if re.search(r"[\d_@.-]", m.group(2)) or re.search(r"id\s*[:=#]", m.group(0), re.I)]
        for named in ids:
            if named.lower() not in (own, "me", "my", "mine", "is") and own not in named.lower():
                return "cross_tenant", CROSS_TENANT_REFUSAL
    for p in OFF_TOPIC:
        if p.search(text):
            return "off_topic", OFF_TOPIC_REFUSAL
    return None


EXISTING_DRAFT = re.compile(r"\b(the|this|that|my|current|your)\s+(article|draft|post|blog post)\b", re.I)


def is_article_request(text: str) -> bool:
    """A NEW article request. "Make the article shorter" is a revision, handled by the assistant."""
    if EXISTING_DRAFT.search(text) and not re.search(r"\b(on|about|regarding|for)\b", text, re.I):
        return False
    return bool(ARTICLE_REQUEST.search(text))


def extract_topic(text: str) -> str:
    t = text.strip().strip("\"'")
    t = TOPIC_PREFIX.sub("", t, count=1) if TOPIC_PREFIX.search(t) else t
    t = re.split(r"[.!?\n]", t)[0]                     # first sentence only
    t = re.split(r"\s*,?\s+(?:and|but|then|with|that)\s+(?:please\s+)?(?:say|mention|include|claim|state|add|tell|"
                 r"write|make|use|keep|focus|target|link|cite)\b", t, flags=re.I)[0]
    t = re.sub(r"\b(please|for me|for my (store|shop|blog|business))\b", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" ,:-")
    return t[:120] or text[:120]


def sanitize_web_text(text: str) -> str:
    return WEB_INJECTION.sub("[removed instruction-like text]", text or "")


# Lasting facts stated in passing are saved for free, without a model call.
PROFILE_PATTERNS = {
    "niche": re.compile(r"\b(?:my|our)\s+(?:niche|business|store|shop|brand|company)\s+(?:is|sells|is about|focuses on)\s+([^.!?\n]{3,80})", re.I),
    "tone": re.compile(r"\b(?:my|our)\s+(?:brand\s+)?(?:tone|voice|writing style)\s+(?:is|should be)\s+([^.!?\n]{3,60})", re.I),
    "audience": re.compile(r"\b(?:my|our)\s+(?:target\s+)?(?:audience|customers|readers)\s+(?:is|are)\s+([^.!?\n]{3,80})", re.I),
}
AFFILIATE = re.compile(r"\baffiliate\b[^\n]{0,60}?(https?://[^\s,)]+)", re.I)


def extract_profile_facts(text: str) -> dict[str, str]:
    out = {k: m.group(1).strip() for k, p in PROFILE_PATTERNS.items() if (m := p.search(text))}
    if (m := AFFILIATE.search(text)):
        out["affiliate_link"] = m.group(1).rstrip(".")
    return out

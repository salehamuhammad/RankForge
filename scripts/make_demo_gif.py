"""Record docs/demo.gif from the live RankForge website (Playwright, headless, 1280x800).

    python scripts/make_demo_gif.py [SITE_URL]

Writes one article on the live API (counts toward the Claude budget). PNGs are kept in docs/screens/.
If a step times out, the GIF is still built from the screenshots that exist.
"""
import sys
import traceback
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

SITE = (sys.argv[1] if len(sys.argv) > 1 else "https://rank-forge-weld.vercel.app").rstrip("/")
BIZ, PASSWORD = "demo-g26", "demo1234"
ROOT = Path(__file__).resolve().parent.parent
SCREENS = ROOT / "docs" / "screens"
GIF = ROOT / "docs" / "demo.gif"
SCREENS.mkdir(parents=True, exist_ok=True)
for old in SCREENS.glob("*.png"):
    old.unlink()

shots: list[Path] = []
failed: list[str] = []


def snap(page, name):
    p = SCREENS / f"{len(shots) + 1:02d}_{name}.png"
    page.screenshot(path=str(p))
    shots.append(p)
    print("screenshot", p.name)


def last_bot(page):
    return page.locator("#chat .msg.bot").last


def wait_bot(page, text, timeout_s):
    """Wait until the last bot bubble contains `text` (case-insensitive)."""
    page.wait_for_function(
        """t => { const m = [...document.querySelectorAll('#chat .msg.bot')].pop();
                 return m && !m.innerText.includes('Working…') && m.innerText.toLowerCase().includes(t); }""",
        arg=text.lower(), timeout=timeout_s * 1000)


def wait_reply(page, timeout_s):
    page.wait_for_function(
        """() => { const m = [...document.querySelectorAll('#chat .msg.bot')].pop();
                  return m && !m.innerText.includes('Working…'); }""", timeout=timeout_s * 1000)


def scroll_chat_to(page, text):
    """Scroll the chat box so the first element containing `text` in the last bot bubble is near the top."""
    page.evaluate(
        """t => { const c = document.getElementById('chat'); const m = [...c.querySelectorAll('.msg.bot')].pop();
                 const el = [...m.querySelectorAll('*')].find(e => e.innerText && e.innerText.includes(t) &&
                     ![...e.children].some(ch => ch.innerText && ch.innerText.includes(t))) || m;
                 c.scrollTop += el.getBoundingClientRect().top - c.getBoundingClientRect().top - 20;
                 window.scrollTo(0, 0); }""", text)
    page.wait_for_timeout(400)


def step(name, fn):
    try:
        fn()
    except Exception as exc:
        failed.append(f"{name}: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
        traceback.print_exc()


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.set_default_timeout(30_000)

    def s1():
        page.goto(SITE + "/index.html", wait_until="networkidle")
        snap(page, "home")

    def s2():
        page.evaluate("openLogin()")
        page.fill("#lu", BIZ)
        page.fill("#lp", PASSWORD)
        page.click("text=Continue")
        page.wait_for_function("() => localStorage.getItem('rf_user')", timeout=120_000)

    def s3():
        page.goto(SITE + "/new.html", wait_until="networkidle")
        page.evaluate("newThread()")          # fresh conversation every run
        page.click("text=Set niche + tone")
        wait_reply(page, 120)
        snap(page, "niche_tone")

    def s4():
        page.click("text=Best laptops under 100k")
        wait_bot(page, "approval", 150)
        scroll_chat_to(page, "Keywords for")
        snap(page, "keywords_draft")
        scroll_chat_to(page, "Waiting for your approval")
        snap(page, "waiting_approval")

    def s5():
        page.click("button:has-text('Approve')")
        wait_bot(page, "live url", 120)
        snap(page, "published")

    def s6():
        page.fill("#m", "Write me a poem about love")
        page.click("#sendBtn")
        wait_reply(page, 60)
        snap(page, "refusal")

    def s7():
        page.goto(SITE + "/admin.html", wait_until="networkidle")
        page.wait_for_timeout(2500)
        snap(page, "dashboard")
        page.click("#tabs button:has-text('Keywords')")
        page.wait_for_timeout(1500)
        snap(page, "dash_keywords")
        page.click("#tabs button:has-text('Usage & cost')")
        page.wait_for_timeout(1500)
        snap(page, "dash_usage")

    def s8():
        page.goto(f"{SITE}/blog.html?b={BIZ}", wait_until="networkidle")
        page.wait_for_selector("#list a.card", timeout=30_000)
        page.click("#list a.card >> nth=0")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        snap(page, "live_post")

    for i, fn in enumerate([s1, s2, s3, s4, s5, s6, s7, s8], 1):
        step(f"step {i}", fn)
    browser.close()

# ── build the GIF ──
if shots:
    frames = []
    for p in shots:
        im = Image.open(p).convert("RGB")
        im = im.resize((960, round(im.height * 960 / im.width)), Image.LANCZOS)
        frames.append(im)

    def save(colors, width):
        fr = [f if f.width == width else f.resize((width, round(f.height * width / f.width)), Image.LANCZOS)
              for f in frames]
        fr = [f.quantize(colors=colors, method=Image.MEDIANCUT, dither=Image.NONE) for f in fr]
        fr[0].save(GIF, save_all=True, append_images=fr[1:], duration=2200, loop=0, optimize=True)
        return GIF.stat().st_size

    size = 0
    for colors, width in [(256, 960), (128, 960), (64, 960), (64, 800), (32, 720)]:
        size = save(colors, width)
        if size <= 8 * 1024 * 1024:
            break
    print(f"GIF {GIF.relative_to(ROOT)}: {len(frames)} frames, {size / 1024 / 1024:.2f} MB")
print("FAILED STEPS:", failed or "none")

"""
Usage: python3 go.py

Scrapes all Q&As from the Adobe company page on Sharebird and opens
the result in your browser. One command, that's it.

First run: a browser window opens for you to log in to Sharebird.
Subsequent runs: uses your saved session automatically.
"""

import asyncio
import json
import random
import re
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

ROOT        = Path(__file__).parent
COOKIES     = ROOT / "cookies.json"
OUTPUT_DIR  = ROOT / "output"
OUTPUT_HTML = OUTPUT_DIR / "adobe_pmm.html"
COMPANY_URL = "https://sharebird.com/c/adobe/product-marketing"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# ── Browser / auth ────────────────────────────────────────────────────────────

async def get_page(playwright):
    # Always visible — headless browsers are easier to detect
    browser = await playwright.chromium.launch(headless=False)
    ctx = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})

    if COOKIES.exists():
        await ctx.add_cookies(json.loads(COOKIES.read_text()))
        page = await ctx.new_page()
        await page.goto("https://sharebird.com/", wait_until="domcontentloaded", timeout=20000)
        if "login" in page.url or "sign" in page.url.lower():
            COOKIES.unlink(missing_ok=True)
            await page.close()
            await browser.close()
            return await get_page(playwright)  # retry with visible browser
        print("Session loaded.")
    else:
        page = await ctx.new_page()
        await page.goto("https://sharebird.com/login", wait_until="domcontentloaded")
        print("\nLog in to Sharebird in the browser window, then press Enter here.")
        input("> ")
        COOKIES.write_text(json.dumps(await ctx.cookies(), indent=2))
        print("Session saved — won't need to log in again.")

    return browser, page


# ── Scraping ──────────────────────────────────────────────────────────────────

async def scroll_to_bottom(page, max_scrolls=40):
    """Scroll like a human — incremental chunks with random pauses."""
    prev = 0
    for _ in range(max_scrolls):
        curr = await page.evaluate("document.body.scrollHeight")
        if curr == prev:
            break
        # Scroll a random chunk (not straight to bottom)
        scroll_by = random.randint(600, 1200)
        await page.evaluate(f"window.scrollBy(0, {scroll_by})")
        await asyncio.sleep(random.uniform(1.5, 3.5))
        prev = curr


async def click_read_more(page, company_url: str) -> bool:
    """
    Click all Read More buttons. Probes the first to check if it expands
    in-place or navigates away. Returns True if in-place.
    """
    buttons = page.locator("text=Read More")
    n = await buttons.count()
    if n == 0:
        return True

    print(f"  Expanding {n} Read More button(s)...")
    await buttons.first.click(timeout=3000, no_wait_after=True)
    await asyncio.sleep(random.uniform(2.0, 4.0))

    if not page.url.startswith(company_url.rstrip("/")):
        await page.goto(company_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(2.0, 3.5))
        return False

    for i in range(1, n):
        try:
            await buttons.nth(i).click(timeout=3000, no_wait_after=True)
            # Every 10-15 clicks, take a longer break (someone stepped away)
            if i % random.randint(10, 15) == 0:
                pause = random.uniform(15, 30)
                print(f"  (pause {pause:.0f}s...)")
                await asyncio.sleep(pause)
            else:
                await asyncio.sleep(random.uniform(1.0, 3.5))
        except Exception:
            pass
    await asyncio.sleep(random.uniform(2.0, 4.0))
    return True


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards, seen = [], set()

    for link in soup.find_all("a", href=re.compile(r"/h/product-marketing/q/")):
        href = link.get("href", "").split("?")[0]
        if not href or href in seen:
            continue
        seen.add(href)
        detail_url = href if href.startswith("http") else f"https://sharebird.com{href}"

        # Walk up to card container
        el = link
        for _ in range(6):
            p = el.parent
            if not p or not hasattr(p, "name"):
                break
            if p.name in ("article", "section", "li") or (p.name == "div" and p.get("class")):
                el = p
                break
            el = p

        q_el    = el.find(["h1","h2","h3","h4"]) or link
        name_el = el.find("a", href=re.compile(r"/profile/|/u/"))
        title_el= el.find(class_=re.compile(r"title|role|position", re.I))
        time_el = el.find("time") or el.find(class_=re.compile(r"\btime\b|\bdate\b|ago", re.I))
        ans_el  = el.find(class_=re.compile(r"answer|body|content|preview|description", re.I))

        question = q_el.get_text(" ", strip=True) if q_el else ""
        name     = name_el.get_text(strip=True) if name_el else ""
        title    = title_el.get_text(strip=True) if title_el else ""
        answer   = ans_el.get_text(" ", strip=True) if ans_el else ""
        if question and answer.startswith(question):
            answer = answer[len(question):].strip()

        raw_ts = ""
        if time_el:
            raw_ts = time_el.get("datetime") or time_el.get_text(strip=True)
            m = re.match(r"^(\d+)(mo|m|y|d|h|w)$", raw_ts.strip(), re.I)
            if m:
                n_val, unit = m.group(1), m.group(2).lower()
                label = {"mo":"month","m":"month","y":"year","d":"day","h":"hour","w":"week"}.get(unit, unit)
                raw_ts = f"{n_val} {label}{'s' if n_val != '1' else ''} ago"

        cards.append({"question": question, "name": name, "title": title,
                      "timestamp": raw_ts, "answer": answer, "detail_url": detail_url})
    return cards


async def get_full_answer(page, url: str) -> str | None:
    """Visit a detail page, click Read More (DOM toggle there), return full answer."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        buttons = page.locator("text=Read More")
        for i in range(await buttons.count()):
            try:
                await buttons.nth(i).click(timeout=3000)
                await asyncio.sleep(0.8)
            except Exception:
                pass
        await asyncio.sleep(1.0)
        soup = BeautifulSoup(await page.content(), "html.parser")
        body = soup.find("main") or soup.body
        if not body:
            return None
        q_el = body.find(["h1","h2","h3"]) or body.find(class_=re.compile(r"question|title", re.I))
        a_el = (body.find(class_=re.compile(r"\banswer\b|\bresponse\b", re.I)) or
                body.find("article") or body.find("section"))
        if not a_el and q_el:
            a_el = q_el.find_next_sibling()
        q = q_el.get_text(" ", strip=True) if q_el else ""
        a = a_el.get_text(" ", strip=True) if a_el else ""
        if q and a.startswith(q):
            a = a[len(q):].strip()
        return a or None
    except Exception:
        return None


async def scrape(page) -> list[dict]:
    print(f"Loading: {COMPANY_URL}")
    await page.goto(COMPANY_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    print("Scrolling to load all Q&As...")
    await scroll_to_bottom(page)

    expanded = await click_read_more(page, COMPANY_URL)
    html = await page.content()
    cards = parse_cards(html)
    print(f"Found {len(cards)} Q&As")

    if not expanded:
        # Read More navigated — fetch each detail page
        print("Fetching full answers from detail pages...")
        for i, card in enumerate(cards, 1):
            print(f"  [{i}/{len(cards)}] {card['name']}")
            full = await get_full_answer(page, card["detail_url"])
            if full:
                card["answer"] = full
            await asyncio.sleep(1)
    else:
        # Any still-truncated cards (edge case)
        for card in cards:
            if card["answer"].endswith("Read More") or len(card["answer"]) < 60:
                full = await get_full_answer(page, card["detail_url"])
                if full:
                    card["answer"] = full
                await page.goto(COMPANY_URL, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1)

    return cards


# ── Output ────────────────────────────────────────────────────────────────────

def write_html(cards: list[dict]) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Group by contributor
    by_person: dict[str, list] = {}
    for c in cards:
        by_person.setdefault(c["name"] or "Unknown", []).append(c)

    sections = []
    for name, qs in by_person.items():
        title    = qs[0]["title"]
        qa_items = []
        for q in qs:
            ts = f" <span class='ts'>· {q['timestamp']}</span>" if q["timestamp"] else ""
            qa_items.append(f"""
            <div class="qa">
              <p class="question">{q['question']}</p>
              <p class="answer">{q['answer']}</p>
              <p class="meta"><a href="{q['detail_url']}" target="_blank">Source</a>{ts}</p>
            </div>""")
        sections.append(f"""
        <section>
          <h2>{name}</h2>
          <p class="title">{title}</p>
          {"".join(qa_items)}
        </section>""")

    total = len(cards)
    contributors = len(by_person)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Adobe PMM Transcripts — Sharebird</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
  h1   {{ font-size: 1.6em; border-bottom: 2px solid #e00; padding-bottom: 10px; }}
  h2   {{ font-size: 1.2em; margin-top: 40px; color: #c00; }}
  .title {{ color: #555; margin-top: -10px; margin-bottom: 20px; }}
  .qa  {{ border-left: 3px solid #eee; padding-left: 16px; margin: 20px 0; }}
  .question {{ font-weight: 600; margin-bottom: 6px; }}
  .answer   {{ line-height: 1.6; white-space: pre-wrap; }}
  .meta {{ font-size: 0.8em; color: #888; margin-top: 6px; }}
  .meta a   {{ color: #888; }}
  .ts  {{ margin-left: 6px; }}
  .summary {{ background: #f5f5f5; padding: 12px 16px; border-radius: 6px; margin-bottom: 30px; }}
</style>
</head>
<body>
<h1>Adobe PMM Transcripts — Sharebird</h1>
<div class="summary">{contributors} contributors · {total} Q&amp;As</div>
{"".join(sections)}
</body>
</html>"""

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    return OUTPUT_HTML


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    async with async_playwright() as pw:
        browser, page = await get_page(pw)
        cards = await scrape(page)
        await browser.close()

    if not cards:
        print("Nothing scraped — check that you're logged in and the URL is accessible.")
        sys.exit(1)

    out = write_html(cards)
    print(f"\nDone — {len(cards)} Q&As across "
          f"{len({c['name'] for c in cards})} contributor(s)")
    print(f"Opening: {out}")
    subprocess.run(["open", str(out)])   # Mac: opens in default browser


if __name__ == "__main__":
    asyncio.run(main())

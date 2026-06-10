"""
Phase 2: Scrape all Q&A content from a Sharebird company page.

Strategy:
  1. Navigate to /c/{company}/product-marketing
  2. Scroll to bottom to load all Q&A cards
  3. Try clicking "Read More" in-place on the company page (simplest path)
     - If buttons expand content in-place: scrape everything from company page (done)
     - If buttons navigate away: collect detail URLs, visit each, click Read More there
  4. Extract: question, person name, title, timestamp, full answer

Run directly:  python3 scraper/scrape_amas.py --company adobe
Or via:        python3 run.py [--company adobe]
"""

import json
import asyncio
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.utils import (
    get_authenticated_context,
    scroll_to_bottom,
    DATA_DIR,
    SHAREBIRD_BASE,
)

TRANSCRIPTS_DIR = DATA_DIR / "transcripts"


def parse_timestamp(raw: str) -> str:
    """Normalize Sharebird relative timestamps like '2mo', '1y', '3d'."""
    raw = raw.strip()
    m = re.match(r"^(\d+)(mo|m|y|d|h|w)$", raw, re.I)
    if not m:
        return raw
    n, unit = m.group(1), m.group(2).lower()
    labels = {"mo": "month", "m": "month", "y": "year", "d": "day", "h": "hour", "w": "week"}
    label = labels.get(unit, unit)
    return f"{n} {label}{'s' if n != '1' else ''} ago"


def dump_structure(soup: BeautifulSoup, max_elements: int = 60) -> str:
    lines = []
    count = [0]

    def walk(el, depth: int = 0):
        if count[0] >= max_elements:
            return
        if not hasattr(el, "name") or not el.name:
            return
        classes = ".".join(el.get("class", []))
        cls_str = f".{classes}" if classes else ""
        text = el.get_text(separator=" ", strip=True)[:60].replace("\n", " ")
        lines.append(f"{'  ' * depth}<{el.name}{cls_str}> {text}")
        count[0] += 1
        if depth < 4:
            for child in el.children:
                walk(child, depth + 1)

    walk(soup.body or soup)
    return "\n".join(lines)


async def try_expand_in_place(page, company_url: str) -> bool:
    """
    Attempt to click all "Read More" buttons on the company page.

    Probes the first button with no_wait_after to detect whether it's a DOM toggle
    (content expands in-place) or a navigation link (goes to a detail page).

    Returns True if Read More expanded content in-place (company page URL unchanged).
    Returns False if clicking navigated away — caller should use detail page URLs instead.
    """
    buttons = page.locator("text=Read More")
    count = await buttons.count()
    if count == 0:
        return True  # nothing to expand — content already fully visible

    print(f"  Found {count} 'Read More' button(s) — probing first...")

    # Test the first button with no_wait_after so we don't hang on navigation
    try:
        await buttons.first.click(timeout=3000, no_wait_after=True)
        await asyncio.sleep(1.5)
    except Exception:
        pass

    # Check if we're still on the company page
    current_url = page.url
    if not current_url.startswith(company_url.rstrip("/")):
        print(f"  'Read More' navigated to: {current_url}")
        print(f"  Switching to detail-page strategy...")
        # Navigate back to company page so card extraction still works
        await page.goto(company_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        return False

    # Still on company page — click all remaining buttons
    print(f"  'Read More' expands in-place — clicking all {count} button(s)...")
    for i in range(1, count):
        try:
            await buttons.nth(i).click(timeout=3000, no_wait_after=True)
            await asyncio.sleep(0.4)
        except Exception:
            pass
    await asyncio.sleep(2.0)
    return True


def extract_qa_cards(soup: BeautifulSoup) -> list[dict]:
    """
    Parse Q&A cards from the company page feed.

    Returns list of card dicts:
      question, name, title, timestamp, answer_text, detail_url
    """
    cards = []
    seen_urls = set()

    # Each Q&A card links to its detail page — use those links as anchors
    q_links = soup.find_all("a", href=re.compile(r"/h/product-marketing/q/"))
    for link in q_links:
        href = link.get("href", "").split("?")[0]
        if not href or href in seen_urls:
            continue
        seen_urls.add(href)
        detail_url = href if href.startswith("http") else f"{SHAREBIRD_BASE}{href}"

        # Walk up DOM to find card container
        card_el = link
        for _ in range(6):
            parent = card_el.parent
            if not parent or not hasattr(parent, "name"):
                break
            if parent.name in ("article", "section", "li") or (
                parent.name == "div" and len(parent.get("class", [])) > 0
            ):
                card_el = parent
                break
            card_el = parent

        # Question — first heading or the link text itself
        q_el = (card_el.find(["h1", "h2", "h3", "h4"]) or
                card_el.find(class_=re.compile(r"question|title|heading", re.I)) or
                link)
        question = q_el.get_text(separator=" ", strip=True) if q_el else ""

        # Person name — prefer profile link text (most reliable across markup changes)
        name_link = card_el.find("a", href=re.compile(r"/profile/|/u/"))
        if name_link:
            name = name_link.get_text(strip=True)
        else:
            # Fall back to a name-class span (avoid "author" — it's often the whole block)
            name_el = card_el.find(class_=re.compile(r"(?<![a-z])name(?![a-z])", re.I))
            name = name_el.get_text(strip=True) if name_el else ""

        # Job title
        title_el = card_el.find(class_=re.compile(r"title|role|position|company", re.I))
        title = title_el.get_text(strip=True) if title_el else ""

        # Timestamp
        time_el = card_el.find("time") or card_el.find(
            class_=re.compile(r"\btime\b|\bdate\b|ago|recency", re.I))
        timestamp = ""
        if time_el:
            timestamp = time_el.get("datetime") or time_el.get_text(strip=True)
            timestamp = parse_timestamp(timestamp)

        # Answer text (may be full if Read More expanded, or preview if not)
        answer_el = card_el.find(class_=re.compile(r"answer|body|content|preview|description", re.I))
        answer_text = answer_el.get_text(separator=" ", strip=True) if answer_el else ""
        # Strip question prefix if a wrapper grabbed everything
        if question and answer_text.startswith(question):
            answer_text = answer_text[len(question):].strip()

        cards.append({
            "question": question,
            "name": name,
            "title": title,
            "timestamp": timestamp,
            "answer_text": answer_text,
            "detail_url": detail_url,
        })

    return cards


async def scrape_detail_page(page, url: str) -> dict | None:
    """
    Visit a /h/product-marketing/q/ page, click Read More (DOM toggle here),
    return dict with full question and answer text.
    """
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(separator=" ").lower()

        if "error 500" in page_text or ("something went wrong" in page_text and "unexpected error" in page_text):
            print(f"    HTTP 500: {url}")
            return None

        # Click Read More — on detail pages this is always a DOM toggle
        buttons = page.locator("text=Read More")
        count = await buttons.count()
        for i in range(count):
            try:
                await buttons.nth(i).click(timeout=3000)
                await asyncio.sleep(0.8)
            except Exception:
                pass
        if count > 0:
            await asyncio.sleep(1.5)
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

        body = soup.find("main") or soup.body
        if not body:
            return None

        q_el = (body.find(class_=re.compile(r"question|q-text|ask|title", re.I)) or
                body.find(["h1", "h2", "h3"]))
        a_el = (body.find(class_=re.compile(r"\banswer\b|\bresponse\b", re.I)) or
                body.find("article") or body.find("section"))
        if not a_el and q_el:
            a_el = q_el.find_next_sibling()

        question = q_el.get_text(separator=" ", strip=True) if q_el else ""
        answer = a_el.get_text(separator=" ", strip=True) if a_el else ""
        if question and answer.startswith(question):
            answer = answer[len(question):].strip()

        return {"question": question, "answer": answer} if (question or answer) else None

    except Exception as e:
        print(f"    Detail page error ({url}): {e}")
        return None


async def scrape_company_page(page, company_slug: str) -> list[dict]:
    """
    Scrape ALL Q&As from the Sharebird company page.

    Tries the simplest path first: expand Read More in-place on the company page.
    Falls back to visiting each individual Q&A detail page only if needed.
    """
    company_url = f"{SHAREBIRD_BASE}/c/{company_slug}/product-marketing"
    print(f"Loading company page: {company_url}")

    try:
        await page.goto(company_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
    except Exception as e:
        print(f"Failed to load company page: {e}")
        return []

    page_text_check = await page.evaluate("document.body.innerText")
    if "error 500" in page_text_check.lower():
        print("Company page returned HTTP 500")
        return []

    print("Scrolling to load all Q&A cards...")
    await scroll_to_bottom(page, pause=2.0)

    # Try expanding Read More in-place (simplest path)
    expanded_in_place = await try_expand_in_place(page, company_url)

    # Capture page after potential expansion
    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")
    cards = extract_qa_cards(soup)
    print(f"Found {len(cards)} Q&A card(s)")

    if not cards:
        debug_path = DATA_DIR / f"debug_{company_slug}_company.html"
        with open(debug_path, "w", encoding="utf-8") as dbg:
            dbg.write(html)
        print(f"0 cards — saved debug HTML to {debug_path}")
        print("\n--- STRUCTURE DUMP ---")
        print(dump_structure(soup))
        print("--- END STRUCTURE DUMP ---\n")
        return []

    qas = []

    if expanded_in_place:
        # Best case: answers are already full on the company page — no detail page visits needed
        print("  Scraping answers from company page (expanded in-place)")
        for card in cards:
            answer = card["answer_text"]
            # Fallback to detail page for any cards still showing truncated text
            is_truncated = (answer.endswith("Read More") or "...Read More" in answer
                            or (len(answer) < 60 and card["detail_url"]))
            if is_truncated:
                print(f"    Still truncated — fetching detail: {card['detail_url']}")
                detail = await scrape_detail_page(page, card["detail_url"])
                if detail:
                    answer = detail["answer"]
                await page.goto(company_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1)
            qas.append({
                "question": card["question"],
                "answer": answer,
                "answerer": card["name"],
                "title": card["title"],
                "timestamp": card["timestamp"],
                "detail_url": card["detail_url"],
                "paywalled": False,
            })
    else:
        # Read More navigated away — visit each detail page for full answers
        print(f"  Visiting {len(cards)} detail page(s) for full answers...")
        for i, card in enumerate(cards, 1):
            print(f"    [{i}/{len(cards)}] {card['name'] or 'Unknown'} — {card['detail_url']}")
            detail = await scrape_detail_page(page, card["detail_url"])
            question = (detail["question"] if detail else None) or card["question"]
            answer = (detail["answer"] if detail else None) or card["answer_text"]
            if not detail:
                print(f"    Using card preview (detail page failed)")
            qas.append({
                "question": question,
                "answer": answer,
                "answerer": card["name"],
                "title": card["title"],
                "timestamp": card["timestamp"],
                "detail_url": card["detail_url"],
                "paywalled": False,
            })
            await asyncio.sleep(1.2)

    return qas


def group_by_contributor(qas: list[dict]) -> list[dict]:
    """Group Q&As by contributor name into transcript-shaped dicts."""
    from collections import OrderedDict
    groups: dict[str, dict] = OrderedDict()

    for qa in qas:
        name = qa.get("answerer") or "Unknown"
        if name not in groups:
            groups[name] = {
                "slug": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
                "name": name,
                "title": qa.get("title", ""),
                "source": "company_page",
                "paywalled": False,
                "qas": [],
            }
        groups[name]["qas"].append({
            "question": qa["question"],
            "answer": qa["answer"],
            "answerer": name,
            "timestamp": qa.get("timestamp", ""),
            "detail_url": qa.get("detail_url", ""),
            "paywalled": False,
        })

    for transcript in groups.values():
        transcript["qa_count"] = len(transcript["qas"])

    return list(groups.values())


async def main(company_slug: str = "adobe"):
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        browser, context = await get_authenticated_context(playwright)
        page = await context.new_page()

        all_qas = await scrape_company_page(page, company_slug)

        await page.close()
        await browser.close()

    if not all_qas:
        print("No Q&As scraped.")
        return

    transcripts = group_by_contributor(all_qas)

    # Merge manual transcripts
    manual_dir = DATA_DIR / "manual"
    if manual_dir.exists():
        for manual_file in sorted(manual_dir.glob("*.json")):
            with open(manual_file) as f:
                t = json.load(f)
            if t.get("qa_count", 0) > 0:
                transcripts.append(t)
                print(f"Merged manual transcript: {manual_file.name} ({t['qa_count']} Q&As)")

    # Save per-contributor transcript files + QA log
    qa_log = []
    for t in transcripts:
        out_path = TRANSCRIPTS_DIR / f"{t['slug']}.json"
        with open(out_path, "w") as f:
            json.dump(t, f, indent=2)

        issues = []
        for qa in t.get("qas", []):
            answer = qa.get("answer", "").strip()
            if answer.endswith("Read More") or "...Read More" in answer:
                issues.append({"question": qa.get("question", "")[:80],
                               "issue": "truncated (Read More still in text)"})
            elif len(answer) < 80 and qa.get("question"):
                issues.append({"question": qa.get("question", "")[:80],
                               "issue": f"suspiciously short ({len(answer)} chars)"})
        qa_log.append({"slug": t["slug"], "name": t["name"],
                       "qa_count": t["qa_count"], "issues": issues})

    qa_log_path = DATA_DIR / "scrape_qa.json"
    with open(qa_log_path, "w") as f:
        json.dump(qa_log, f, indent=2)

    total = sum(t["qa_count"] for t in transcripts)
    print(f"\nDone. {len(transcripts)} contributor(s), {total} Q&As total.")
    for t in transcripts:
        print(f"  {t['name']}: {t['qa_count']} Q&As")


if __name__ == "__main__":
    slug = "adobe"
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--company" and i + 1 < len(args):
            slug = args[i + 1]
        elif arg.startswith("--company="):
            slug = arg.split("=", 1)[1]
    asyncio.run(main(company_slug=slug))

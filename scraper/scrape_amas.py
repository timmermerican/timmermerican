"""
Phase 2: Pull full Q&A transcripts for each discovered Adobe AMA.

Reads discovered_urls.json, visits each AMA page with an authenticated
Playwright session, extracts all questions and answers, and saves per-AMA JSON
to data/transcripts/{slug}.json.
"""

import json
import asyncio
import re
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.utils import (
    get_authenticated_context,
    scroll_to_bottom,
    DATA_DIR,
)

TRANSCRIPTS_DIR = DATA_DIR / "transcripts"


def dump_structure(soup: BeautifulSoup, max_elements: int = 80) -> str:
    """Condensed tag/class tree for debugging zero-Q&A pages."""
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
        if depth < 5:
            for child in el.children:
                walk(child, depth + 1)

    walk(soup.body or soup)
    return "\n".join(lines)


def slug_from_url(url: str) -> str:
    return urlparse(url).path.rstrip("/").split("/")[-1]


def is_paywalled(soup: BeautifulSoup) -> bool:
    """Detect if the page is showing a paywall/upgrade prompt."""
    text = soup.get_text(separator=" ").lower()
    signals = ["upgrade to read", "subscribe to", "unlock this", "join sharebird"]
    return any(s in text for s in signals)


def extract_qas(soup: BeautifulSoup, contributor_name: str) -> list[dict]:
    """
    Extract question/answer pairs from the AMA page HTML.

    Sharebird renders AMAs as a list of question cards, each with:
    - A question (asked by a community member)
    - An answer (from the AMA host/contributor)

    We try multiple CSS patterns since Sharebird may update their markup.
    """
    qas = []

    # Strategy 1: look for elements with data attributes or semantic roles
    # Sharebird typically uses article/section elements or div.qa-item patterns
    candidates = (
        soup.find_all("article") or
        soup.find_all(class_=re.compile(r"qa|question|answer|thread|item", re.I)) or
        soup.find_all("div", attrs={"data-qa": True})
    )

    if candidates:
        for card in candidates:
            q_el = card.find(class_=re.compile(r"question|q-text|ask", re.I)) or \
                   card.find(["h3", "h4", "strong"])
            a_el = card.find(class_=re.compile(r"answer|a-text|response|body", re.I)) or \
                   card.find("p")
            time_el = card.find("time") or card.find(class_=re.compile(r"date|time|ago", re.I))

            question = q_el.get_text(separator=" ", strip=True) if q_el else ""
            answer = a_el.get_text(separator=" ", strip=True) if a_el else ""
            timestamp = (time_el.get("datetime") or time_el.get_text(strip=True)) if time_el else ""

            if question or answer:
                qas.append({
                    "question": question,
                    "answer": answer,
                    "answerer": contributor_name,
                    "timestamp": timestamp,
                    "paywalled": False,
                })
        if qas:
            return qas
        # Strategy 1 found candidate elements but nothing extractable — fall through

    # Strategy 2: fall back to paragraph-level heuristics
    # Look for blocks where a bold/heading line is followed by paragraph text
    body = soup.find("main") or soup.find(id=re.compile(r"content|main", re.I)) or soup.body
    if not body:
        return qas

    elements = body.find_all(["h2", "h3", "h4", "p", "div"])
    current_q = None
    for el in elements:
        text = el.get_text(separator=" ", strip=True)
        if not text or len(text) < 5:
            continue
        tag = el.name
        if tag in ("h2", "h3", "h4") or el.find("strong"):
            if current_q:
                qas.append(current_q)
            current_q = {
                "question": text,
                "answer": "",
                "answerer": contributor_name,
                "timestamp": "",
                "paywalled": False,
            }
        elif current_q and tag == "p":
            current_q["answer"] += (" " + text).strip()

    if current_q and current_q.get("answer"):
        qas.append(current_q)

    return qas


async def expand_read_more(page) -> dict:
    """Click all 'Read More' buttons (two passes) and verify expansion."""
    stats = {"clicked": 0, "remaining_after": 0}
    try:
        for _ in range(2):  # second pass catches dynamically added buttons
            buttons = page.locator("text=Read More")
            count = await buttons.count()
            if count == 0:
                break
            for i in range(count):
                try:
                    await buttons.nth(i).click()
                    await asyncio.sleep(0.4)
                    stats["clicked"] += 1
                except Exception:
                    pass
            await asyncio.sleep(1.5)  # wait for content to render
        stats["remaining_after"] = await page.locator("text=Read More").count()
    except Exception:
        pass
    return stats


async def scrape_ama(page, contributor: dict) -> dict:
    """Scrape a single AMA page and return a transcript dict."""
    url = contributor["ama_url"]
    slug = slug_from_url(url)
    name = contributor.get("name", "Unknown")
    title = contributor.get("title", "")

    print(f"Scraping: {name} — {url}")

    if "/profile/" in url and "/activity/" in url:
        print(f"  Skipping profile activity URL — covered by manual transcript")
        return {"slug": slug, "name": name, "title": title, "ama_url": url,
                "error": "profile activity URL — covered by manual transcript",
                "paywalled": False, "qa_count": 0, "qas": []}

    result = {
        "slug": slug,
        "name": name,
        "title": title,
        "ama_url": url,
        "source": contributor.get("source", ""),
        "note": contributor.get("note", ""),
        "paywalled": False,
        "qa_count": 0,
        "qas": [],
    }

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1:
                wait = 10 * attempt
                print(f"  Retry {attempt}/{max_attempts} (waiting {wait}s)...")
                await asyncio.sleep(wait)

            print(f"  Loading page...")
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(3)
            print(f"  Scrolling to load all content...")
            await scroll_to_bottom(page, pause=1.5)
            print(f"  Expanding Read More buttons...")
            rm_stats = await expand_read_more(page)
            if rm_stats["clicked"]:
                print(f"  Expanded {rm_stats['clicked']} 'Read More' sections", end="")
                if rm_stats["remaining_after"]:
                    print(f" — WARNING: {rm_stats['remaining_after']} still unexpanded")
                else:
                    print(" — all clear")

            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            page_text = soup.get_text(separator=" ").lower()
            if "error 500" in page_text or ("something went wrong" in page_text and "unexpected error" in page_text):
                print(f"  HTTP 500 — page unavailable: {url}")
                result["error"] = "HTTP 500 — AMA page unavailable on Sharebird"
                return result

            if is_paywalled(soup):
                print(f"  Paywalled: {url}")
                result["paywalled"] = True
                qas = extract_qas(soup, name)
                for qa in qas:
                    qa["paywalled"] = True
                result["qas"] = qas
                result["qa_count"] = len(qas)
                return result

            qas = extract_qas(soup, name)
            result["qas"] = qas
            result["qa_count"] = len(qas)
            print(f"  Extracted {len(qas)} Q&As")

            # Per-answer QA scan
            qa_issues = []
            for qa in qas:
                answer = qa.get("answer", "").strip()
                if answer.endswith("Read More") or "...Read More" in answer:
                    qa_issues.append({"question": qa.get("question", "")[:80],
                                      "issue": "truncated (Read More still in text)"})
                elif len(answer) < 80 and qa.get("question"):
                    qa_issues.append({"question": qa.get("question", "")[:80],
                                      "issue": f"suspiciously short ({len(answer)} chars)"})

            # Append to scrape QA log
            qa_log_path = DATA_DIR / "scrape_qa.json"
            log_entry = {
                "slug": slug,
                "name": name,
                "read_more_clicked": rm_stats["clicked"],
                "read_more_remaining": rm_stats["remaining_after"],
                "qa_count": len(qas),
                "issues": qa_issues,
            }
            existing_log = []
            if qa_log_path.exists():
                with open(qa_log_path) as lf:
                    existing_log = json.load(lf)
            existing_log = [e for e in existing_log if e.get("slug") != slug]
            existing_log.append(log_entry)
            with open(qa_log_path, "w") as lf:
                json.dump(existing_log, lf, indent=2)

            if qa_issues:
                print(f"  QA issues: {len(qa_issues)} answer(s) flagged")
            if len(qas) == 0:
                debug_path = DATA_DIR / f"debug_{slug}.html"
                with open(debug_path, "w", encoding="utf-8") as dbg:
                    dbg.write(html)
                print(f"  0 Q&As — saved debug HTML to {debug_path}")
                print(f"\n--- STRUCTURE DUMP: {slug} ---")
                print(dump_structure(soup))
                print(f"--- END STRUCTURE DUMP ---\n")
            return result

        except Exception as e:
            print(f"  Attempt {attempt} failed: {e}")
            result["error"] = str(e)
            if attempt == max_attempts:
                print(f"  Giving up after {max_attempts} attempts.")

    return result


async def main():
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    discovered_path = DATA_DIR / "discovered_urls.json"
    if not discovered_path.exists():
        print("discovered_urls.json not found. Run discover.py first.")
        return

    with open(discovered_path) as f:
        contributors = json.load(f)

    print(f"Scraping {len(contributors)} AMAs...")

    async with async_playwright() as playwright:
        browser, context = await get_authenticated_context(playwright)
        page = await context.new_page()

        all_transcripts = []
        for contributor in contributors:
            transcript = await scrape_ama(page, contributor)
            all_transcripts.append(transcript)

            # Save per-AMA file immediately (crash-safe)
            slug = transcript["slug"]
            out_path = TRANSCRIPTS_DIR / f"{slug}.json"
            with open(out_path, "w") as f:
                json.dump(transcript, f, indent=2)

            await asyncio.sleep(2)  # polite delay between requests

        await page.close()
        await browser.close()

    total_qas = sum(t["qa_count"] for t in all_transcripts)
    paywalled = sum(1 for t in all_transcripts if t["paywalled"])
    print(f"\nDone. {len(all_transcripts)} AMAs scraped, {total_qas} Q&As total, {paywalled} paywalled.")


if __name__ == "__main__":
    asyncio.run(main())

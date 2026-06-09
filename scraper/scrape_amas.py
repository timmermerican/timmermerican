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
        return qas

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


async def scrape_ama(page, contributor: dict) -> dict:
    """Scrape a single AMA page and return a transcript dict."""
    url = contributor["ama_url"]
    slug = slug_from_url(url)
    name = contributor.get("name", "Unknown")
    title = contributor.get("title", "")

    print(f"Scraping: {name} — {url}")
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

    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)
        await scroll_to_bottom(page, pause=1.5)

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Debug: dump page structure on first run to data/debug_html.txt
        debug_path = DATA_DIR / "debug_html.txt"
        if not debug_path.exists():
            tags = soup.find_all(True)
            tag_summary = "\n".join(
                f"{t.name} class={t.get('class',[])} id={t.get('id','')}"
                for t in tags[:120]
            )
            debug_path.write_text(f"URL: {url}\n\n{tag_summary}\n\n---SNIPPET---\n{html[:8000]}")
            print(f"  Debug HTML saved to {debug_path}")

        if is_paywalled(soup):
            print(f"  Paywalled: {url}")
            result["paywalled"] = True
            # Still try to extract whatever is visible
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

    except Exception as e:
        print(f"  Failed: {e}")
        result["error"] = str(e)

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

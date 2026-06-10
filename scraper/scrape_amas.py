"""
Phase 2: Pull full Q&A transcripts for each discovered Adobe AMA.

Primary strategy: navigate to each contributor's profile page, collect all
/h/product-marketing/q/ links, then visit each — clicking "Read More" on the
detail page (a DOM toggle there) to get the full unexpanded answer.

Fallback: if no profile_url is set, fall back to the AMA session page and
extract Q&A links from there (scrape_detail_qas).

Reads discovered_urls.json, visits each contributor, and saves per-slug JSON
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
    SHAREBIRD_BASE,
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
    Extract question/answer pairs from an AMA page (fallback for pages with no
    individual Q&A links). Tries article/section cards first, then paragraph heuristics.
    """
    qas = []

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


async def click_read_more_and_get_answer(page, body_el_selector: str = "main, article, section, body") -> str:
    """
    On a detail Q&A page, click all 'Read More' buttons (DOM toggles here,
    not navigation links) and return the full page text from the answer area.
    """
    try:
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
    except Exception:
        pass


async def scrape_qa_detail_page(page, url: str, contributor_name: str) -> dict | None:
    """
    Visit one /h/product-marketing/q/ page, click Read More, return full Q&A dict.
    Returns None on hard error (500, unexpected redirect, etc.).
    """
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        page_text = soup.get_text(separator=" ").lower()
        if "error 500" in page_text or ("something went wrong" in page_text and "unexpected error" in page_text):
            print(f"    HTTP 500 on detail page: {url}")
            return None

        # Click Read More — on detail pages this is a DOM toggle, not navigation
        await click_read_more_and_get_answer(page)

        # Re-capture HTML after expansion
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        body = soup.find("main") or soup.body
        if not body:
            return None

        q_el = (body.find(class_=re.compile(r"question|q-text|ask|title", re.I)) or
                body.find(["h1", "h2", "h3"]))
        # Specific answer selectors only — avoid generic wrappers
        a_el = (body.find(class_=re.compile(r"\banswer\b|\bresponse\b", re.I)) or
                body.find("article") or body.find("section"))
        if not a_el and q_el:
            a_el = q_el.find_next_sibling()

        question = q_el.get_text(separator=" ", strip=True) if q_el else ""
        answer = a_el.get_text(separator=" ", strip=True) if a_el else ""

        # Strip question prefix if a wrapper div was grabbed
        if question and answer.startswith(question):
            answer = answer[len(question):].strip()

        if not question and not answer:
            return None

        return {
            "question": question,
            "answer": answer,
            "answerer": contributor_name,
            "timestamp": "",
            "paywalled": False,
        }
    except Exception as e:
        print(f"    Detail page error {url}: {e}")
        return None


async def scrape_from_profile(page, profile_url: str, contributor_name: str) -> list[dict]:
    """
    Navigate to the contributor's profile page, collect all /h/product-marketing/q/ links,
    visit each one, click Read More, and return the full Q&A list.
    """
    print(f"  Loading profile: {profile_url}")
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        await scroll_to_bottom(page, pause=1.5)
    except Exception as e:
        print(f"  Profile page load failed: {e}")
        return []

    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")

    page_text = soup.get_text(separator=" ").lower()
    if "error 500" in page_text or ("something went wrong" in page_text and "unexpected error" in page_text):
        print(f"  Profile page returned HTTP 500")
        return []

    # Collect all unique /h/product-marketing/q/ links
    links = soup.find_all("a", href=re.compile(r"/h/product-marketing/q/"))
    seen, urls = set(), []
    for link in links:
        href = link.get("href", "").split("?")[0]
        if href and href not in seen:
            seen.add(href)
            full_url = href if href.startswith("http") else f"{SHAREBIRD_BASE}{href}"
            urls.append(full_url)

    if not urls:
        print(f"  No Q&A links found on profile page")
        if len(soup.get_text()) < 500:
            print(f"\n--- PROFILE STRUCTURE DUMP ---")
            print(dump_structure(soup))
            print(f"--- END STRUCTURE DUMP ---\n")
        return []

    print(f"  Found {len(urls)} Q&A link(s) on profile — scraping each...")
    qas = []
    for i, detail_url in enumerate(urls, 1):
        print(f"    [{i}/{len(urls)}] {detail_url}")
        qa = await scrape_qa_detail_page(page, detail_url, contributor_name)
        if qa:
            qas.append(qa)
        await asyncio.sleep(1.2)

    return qas


async def scrape_detail_qas(page, soup: BeautifulSoup, contributor_name: str, ama_url: str) -> list[dict]:
    """
    Fallback: find /h/product-marketing/q/ links in an AMA session page soup and visit each.
    Used when no profile_url is available.
    """
    links = soup.find_all("a", href=re.compile(r"/h/product-marketing/q/"))
    seen, urls = set(), []
    for link in links:
        href = link.get("href", "").split("?")[0]
        if href and href not in seen:
            seen.add(href)
            urls.append(href if href.startswith("http") else f"{SHAREBIRD_BASE}{href}")

    if not urls:
        return []

    print(f"  Fetching {len(urls)} Q&A detail page(s) from AMA page...")
    qas = []
    for detail_url in urls:
        qa = await scrape_qa_detail_page(page, detail_url, contributor_name)
        if qa:
            qas.append(qa)
        await asyncio.sleep(1)

    # Return to the AMA page so caller state is consistent
    if qas:
        try:
            await page.goto(ama_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
        except Exception:
            pass

    return qas


async def scrape_ama(page, contributor: dict) -> dict:
    """Scrape a single contributor and return a transcript dict."""
    url = contributor["ama_url"]
    profile_url = contributor.get("profile_url", "")
    slug = slug_from_url(url)
    name = contributor.get("name", "Unknown")
    title = contributor.get("title", "")

    print(f"\nScraping: {name} — {url}")

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
        "profile_url": profile_url,
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

            # Primary strategy: scrape from profile page (gets ALL answers, not just one AMA)
            if profile_url:
                qas = await scrape_from_profile(page, profile_url, name)
                if qas:
                    print(f"  Extracted {len(qas)} Q&As from profile page (full text)")
                    result["qas"] = qas
                    result["qa_count"] = len(qas)
                    break  # success — skip AMA page fallback

            # Fallback: load the AMA session page and find Q&A links there
            print(f"  {'No profile Q&As found — ' if profile_url else ''}Loading AMA page...")
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(3)
            await scroll_to_bottom(page, pause=1.5)

            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            page_text = soup.get_text(separator=" ").lower()
            if "error 500" in page_text or ("something went wrong" in page_text and "unexpected error" in page_text):
                print(f"  HTTP 500 — AMA page unavailable: {url}")
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

            qas = await scrape_detail_qas(page, soup, name, url)
            if qas:
                print(f"  Extracted {len(qas)} Q&As (AMA page detail links)")
            else:
                qas = extract_qas(soup, name)
                print(f"  Extracted {len(qas)} Q&As (main page parse — may be truncated)")
            result["qas"] = qas
            result["qa_count"] = len(qas)
            break

        except Exception as e:
            print(f"  Attempt {attempt} failed: {e}")
            result["error"] = str(e)
            if attempt == max_attempts:
                print(f"  Giving up after {max_attempts} attempts.")

    # Per-answer QA scan
    qas = result.get("qas", [])
    qa_issues = []
    for qa in qas:
        answer = qa.get("answer", "").strip()
        if answer.endswith("Read More") or "...Read More" in answer:
            qa_issues.append({"question": qa.get("question", "")[:80],
                              "issue": "truncated (Read More still in text)"})
        elif len(answer) < 80 and qa.get("question"):
            qa_issues.append({"question": qa.get("question", "")[:80],
                              "issue": f"suspiciously short ({len(answer)} chars)"})

    qa_log_path = DATA_DIR / "scrape_qa.json"
    log_entry = {
        "slug": slug,
        "name": name,
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
        html_content = ""
        try:
            html_content = await page.content()
        except Exception:
            pass
        if html_content:
            with open(debug_path, "w", encoding="utf-8") as dbg:
                dbg.write(html_content)
            print(f"  0 Q&As — saved debug HTML to {debug_path}")

    return result


async def main():
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    discovered_path = DATA_DIR / "discovered_urls.json"
    if not discovered_path.exists():
        print("discovered_urls.json not found. Run discover.py first.")
        return

    with open(discovered_path) as f:
        contributors = json.load(f)

    # Deduplicate by profile_url so we scrape each profile exactly once.
    # Contributors sharing a profile_url (multiple AMAs) are collapsed to one scrape run.
    seen_profiles: set[str] = set()
    unique_contributors: list[dict] = []
    for c in contributors:
        key = c.get("profile_url") or c.get("ama_url")
        if key not in seen_profiles:
            seen_profiles.add(key)
            unique_contributors.append(c)

    print(f"Scraping {len(unique_contributors)} contributor profile(s) "
          f"(from {len(contributors)} total entries)...")

    async with async_playwright() as playwright:
        browser, context = await get_authenticated_context(playwright)
        page = await context.new_page()

        all_transcripts = []
        for contributor in unique_contributors:
            transcript = await scrape_ama(page, contributor)
            all_transcripts.append(transcript)

            # Use profile slug as filename when available, else AMA slug
            profile_url = contributor.get("profile_url", "")
            if profile_url:
                file_slug = profile_url.rstrip("/").split("/")[-1]
            else:
                file_slug = transcript["slug"]

            transcript["slug"] = file_slug
            out_path = TRANSCRIPTS_DIR / f"{file_slug}.json"
            with open(out_path, "w") as f:
                json.dump(transcript, f, indent=2)

            await asyncio.sleep(2)

        await page.close()
        await browser.close()

    total_qas = sum(t["qa_count"] for t in all_transcripts)
    paywalled = sum(1 for t in all_transcripts if t["paywalled"])
    print(f"\nDone. {len(all_transcripts)} contributor(s) scraped, "
          f"{total_qas} Q&As total, {paywalled} paywalled.")


if __name__ == "__main__":
    asyncio.run(main())

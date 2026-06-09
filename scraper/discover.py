"""
Phase 1: Discover all Adobe PMM/PM contributors on Sharebird.

Sources:
  1. Sharebird Adobe company page (/c/adobe/product-marketing)
  2. Known AMA slugs from index search results (hardcoded seeds)
  3. Sharebird AMA list page (/h/product-marketing/ama) — scans for any adobe mentions
"""

import json
import asyncio
import re
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.utils import (
    get_authenticated_context,
    scroll_to_bottom,
    SHAREBIRD_BASE,
    DATA_DIR,
)

# Seeds from research — known Adobe contributor AMAs
# Note: legacy /ama/ URLs (without /h/product-marketing/) return HTTP 500 — omit them
SEED_AMAS = [
    {
        "name": "Mary Sheehan",
        "title": "Head of PMM, Lightroom — Adobe",
        "ama_url": f"{SHAREBIRD_BASE}/h/product-marketing/ama/adobe-sr-manager-product-marketing-mary-sheehan-on-product-launches",
    },
    {
        "name": "Mary Sheehan",
        "title": "Head of PMM, Lightroom — Adobe",
        "ama_url": f"{SHAREBIRD_BASE}/h/product-marketing/ama/adobe-head-of-lightroom-product-marketing-mary-shirley-sheehan-on-go-to-market-strategy",
    },
    {
        "name": "Mary Sheehan",
        "title": "Head of PMM, Lightroom — Adobe",
        "ama_url": f"{SHAREBIRD_BASE}/h/product-marketing/ama/adobe-head-of-lightroom-product-marketing-mary-shirley-sheehan-on-product-marketing-career-path",
    },
    {
        "name": "Mike Polner",
        "title": "VP PMM & GM for Next Gen Creators — Adobe",
        "ama_url": f"{SHAREBIRD_BASE}/h/product-marketing/ama/uber-global-head-of-product-marketing-uber-eats-mike-polner-on-consumer-product-marketing",
        "note": "Was at Uber at time of AMA; now VP PMM at Adobe (owns Firefly creator PMM)",
    },
    {
        "name": "Mike Polner",
        "title": "VP PMM & GM for Next Gen Creators — Adobe",
        "ama_url": f"{SHAREBIRD_BASE}/h/product-marketing/ama/cameo-sr-director-of-product-marketing-mike-polner-on-product-launches",
        "note": "Was at Cameo at time of AMA; now VP PMM at Adobe",
    },
    {
        "name": "Gagan Mand",
        "title": "Director, PMM & Strategy — Adobe",
        "ama_url": f"{SHAREBIRD_BASE}/h/product-marketing/ama/adobe-director-product-marketing-strategy-gagan-mand-on-gtm-strategy-and-product-launches-for-enterprise-software",
    },
    {
        "name": "Gagan Mand",
        "title": "Director, PMM & Strategy — Adobe",
        "ama_url": f"{SHAREBIRD_BASE}/h/product-marketing/ama/adobe-director-product-marketing-strategy-gagan-mand-on-building-a-product-marketing-team",
    },
]

ADOBE_COMPANY_URL = f"{SHAREBIRD_BASE}/c/adobe/product-marketing"
AMA_LIST_URL = f"{SHAREBIRD_BASE}/h/product-marketing/ama"


async def scrape_company_page(page) -> list[dict]:
    """Scrape the Adobe company page for contributor profiles and AMA links."""
    contributors = []
    print(f"Fetching Adobe company page: {ADOBE_COMPANY_URL}")
    try:
        await page.goto(ADOBE_COMPANY_URL, wait_until="networkidle", timeout=30000)
        await scroll_to_bottom(page)
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Look for expert/contributor cards — Sharebird renders these as linked blocks
        for card in soup.find_all("a", href=re.compile(r"/(ama|h/product-marketing/ama)/")):
            href = card.get("href", "")
            url = href if href.startswith("http") else f"{SHAREBIRD_BASE}{href}"
            name_el = card.find(class_=re.compile(r"name|author|expert", re.I)) or \
                      card.find(["h2", "h3", "h4", "strong"])
            title_el = card.find(class_=re.compile(r"title|role|position", re.I)) or \
                       card.find("p")
            name = name_el.get_text(strip=True) if name_el else ""
            title = title_el.get_text(strip=True) if title_el else ""

            if url and url not in [c.get("ama_url") for c in contributors]:
                contributors.append({
                    "name": name,
                    "title": title,
                    "ama_url": url,
                    "source": "company_page",
                })

        # Also grab any profile links
        for link in soup.find_all("a", href=re.compile(r"/u/")):
            href = link.get("href", "")
            url = href if href.startswith("http") else f"{SHAREBIRD_BASE}{href}"
            name = link.get_text(strip=True)
            if name:
                # Check if already in contributors, add profile_url
                for c in contributors:
                    if c.get("name", "").lower() in name.lower() or \
                       name.lower() in c.get("name", "").lower():
                        c["profile_url"] = url
                        break

        print(f"Found {len(contributors)} contributors on company page")
    except Exception as e:
        print(f"Company page scrape failed: {e}")

    return contributors


async def scan_ama_list_for_adobe(page, max_pages: int = 5) -> list[dict]:
    """
    Scan the AMA listing pages for any AMAs mentioning Adobe contributors
    not already captured by the company page.
    """
    found = []
    print(f"Scanning AMA list for Adobe mentions...")
    for page_num in range(1, max_pages + 1):
        url = f"{AMA_LIST_URL}?page={page_num}" if page_num > 1 else AMA_LIST_URL
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await scroll_to_bottom(page, pause=1.0)
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            # Find AMA cards that mention Adobe
            cards = soup.find_all("a", href=re.compile(r"/(ama|h/product-marketing/ama)/"))
            new_on_page = 0
            for card in cards:
                card_text = card.get_text(separator=" ", strip=True).lower()
                if "adobe" in card_text:
                    href = card.get("href", "")
                    ama_url = href if href.startswith("http") else f"{SHAREBIRD_BASE}{href}"
                    name_el = card.find(class_=re.compile(r"name|author", re.I)) or \
                              card.find(["strong", "h3", "h4"])
                    name = name_el.get_text(strip=True) if name_el else "Unknown"
                    found.append({
                        "name": name,
                        "title": "",
                        "ama_url": ama_url,
                        "source": "ama_list_scan",
                    })
                    new_on_page += 1

            print(f"  Page {page_num}: found {new_on_page} Adobe AMAs")
            if new_on_page == 0 and page_num > 1:
                break  # No more Adobe hits, stop scanning

        except Exception as e:
            print(f"  Page {page_num} failed: {e}")
            break

    return found


def merge_contributors(seeds: list, company_page: list, ama_scan: list) -> list[dict]:
    """Merge all sources, deduplicate by AMA URL."""
    seen_urls = set()
    merged = []

    for source in [seeds, company_page, ama_scan]:
        for c in source:
            url = c.get("ama_url")
            if not url:
                continue
            # Normalize URL
            url = url.rstrip("/")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            c["ama_url"] = url
            merged.append(c)

    return merged


async def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        browser, context = await get_authenticated_context(playwright)
        page = await context.new_page()

        company_contributors = await scrape_company_page(page)
        ama_scan_contributors = await scan_ama_list_for_adobe(page)

        # Filter seeds to only those with a URL
        seed_with_urls = [s for s in SEED_AMAS if s.get("ama_url")]

        all_contributors = merge_contributors(
            seed_with_urls,
            company_contributors,
            ama_scan_contributors,
        )

        out_path = DATA_DIR / "discovered_urls.json"
        with open(out_path, "w") as f:
            json.dump(all_contributors, f, indent=2)

        print(f"\nDiscovered {len(all_contributors)} unique Adobe contributor AMAs")
        print(f"Saved to: {out_path}")
        for c in all_contributors:
            print(f"  {c.get('name', 'Unknown')} — {c.get('ama_url')}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

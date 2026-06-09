"""
Diagnostic: scrape a single AMA URL and print page text + HTML structure.
Usage:
    python3 -m scraper.debug_page [URL]

Defaults to Katharine Gregorio's AMA if no URL given.
"""

import asyncio
import sys
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.utils import get_authenticated_context, scroll_to_bottom
from scraper.scrape_amas import dump_structure

GREGORIO_URL = (
    "https://sharebird.com/h/product-marketing/ama/"
    "adobe-sr-director-product-marketing-creative-cloud-"
    "katharine-gregorio-on-building-a-product-marketing-team"
)


async def main(url: str):
    async with async_playwright() as playwright:
        browser, context = await get_authenticated_context(playwright)
        page = await context.new_page()
        print(f"Loading: {url}")
        try:
            await page.goto(url, wait_until="networkidle", timeout=45000)
        except Exception as e:
            print(f"Timeout/error on load: {e} — continuing with partial page")
        await asyncio.sleep(4)
        await scroll_to_bottom(page, pause=1.5)
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        print("\n--- PAGE TEXT (first 800 chars) ---")
        print(soup.get_text(separator=" ", strip=True)[:800])

        print("\n--- STRUCTURE DUMP (first 100 elements, depth 5) ---")
        print(dump_structure(soup, max_elements=100))
        print("--- END ---")

        await browser.close()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else GREGORIO_URL
    asyncio.run(main(url))

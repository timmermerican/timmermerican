"""Shared browser setup and cookie management for Sharebird scraper."""

import json
import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright, BrowserContext, Page

load_dotenv()

ROOT = Path(__file__).parent.parent
COOKIES_FILE = ROOT / "cookies.json"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"

SHAREBIRD_BASE = "https://sharebird.com"

# Realistic browser headers to avoid 403
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def launch_browser(playwright, headless: bool = True):
    browser = await playwright.chromium.launch(headless=headless)
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )
    return browser, context


async def load_cookies(context: BrowserContext) -> bool:
    """Load saved Sharebird session cookies. Returns True if cookies exist."""
    if not COOKIES_FILE.exists():
        return False
    with open(COOKIES_FILE) as f:
        cookies = json.load(f)
    await context.add_cookies(cookies)
    print(f"Loaded {len(cookies)} cookies from {COOKIES_FILE}")
    return True


async def save_cookies(context: BrowserContext):
    cookies = await context.cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)
    print(f"Saved {len(cookies)} cookies to {COOKIES_FILE}")


async def authenticate_manual(context: BrowserContext) -> bool:
    """
    Open a visible browser window and let the user log in manually.
    Saves cookies once they land back on Sharebird. One-time setup.
    """
    page = await context.new_page()
    try:
        print("\n" + "="*60)
        print("A browser window will open. Please:")
        print("  1. Click 'Continue with LinkedIn' (or any login option)")
        print("  2. Complete the login in the browser")
        print("  3. Once you see the Sharebird home page, come back here")
        print("     and press Enter.")
        print("="*60 + "\n")
        await page.goto(f"{SHAREBIRD_BASE}/login", wait_until="domcontentloaded")
        input("Press Enter once you are logged in to Sharebird in the browser window...")
        current_url = page.url
        print(f"Current URL: {current_url}")
        if "sharebird.com" in current_url and "login" not in current_url:
            await save_cookies(context)
            print("Logged in successfully!")
            return True
        else:
            print("Doesn't look like login completed — please check the browser window.")
            input("If you ARE logged in, press Enter to save cookies anyway, or Ctrl+C to abort...")
            await save_cookies(context)
            return True
    except Exception as e:
        print(f"Authentication failed: {e}")
        return False
    finally:
        await page.close()


async def get_authenticated_context(playwright, headless: bool = True):
    """Return a browser context with a valid Sharebird session."""
    # Always use visible browser for first-time login so user can complete it
    has_saved_cookies = COOKIES_FILE.exists()

    if not has_saved_cookies:
        # First run: visible browser for manual login
        browser, context = await launch_browser(playwright, headless=False)
        print("No saved session. Opening browser for manual login...")
        success = await authenticate_manual(context)
        if not success:
            raise RuntimeError("Could not authenticate with Sharebird.")
        return browser, context

    # Subsequent runs: load cookies and verify session
    browser, context = await launch_browser(playwright, headless=headless)
    await load_cookies(context)
    page = await context.new_page()
    await page.goto(f"{SHAREBIRD_BASE}/", wait_until="domcontentloaded", timeout=20000)
    # Check if still logged in by looking for any user-specific element
    content = await page.content()
    await page.close()
    if "login" in page.url or "sign" in page.url.lower():
        print("Session expired. Re-opening browser for manual login...")
        COOKIES_FILE.unlink(missing_ok=True)
        success = await authenticate_manual(context)
        if not success:
            raise RuntimeError("Could not re-authenticate.")
    else:
        print("Session valid, proceeding...")

    return browser, context

    return browser, context


async def scroll_to_bottom(page: Page, pause: float = 1.5):
    """Scroll page to bottom to trigger lazy loading of all Q&As."""
    prev_height = 0
    while True:
        curr_height = await page.evaluate("document.body.scrollHeight")
        if curr_height == prev_height:
            break
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(pause)
        prev_height = curr_height

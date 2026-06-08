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


CHROMIUM_PATH = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


async def launch_browser(playwright, headless: bool = True):
    browser = await playwright.chromium.launch(
        headless=headless,
        executable_path=CHROMIUM_PATH,
    )
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


async def authenticate(context: BrowserContext) -> bool:
    """
    Log in to Sharebird via LinkedIn OAuth.
    Returns True on success, False on failure (e.g. CAPTCHA).
    """
    email = os.getenv("LINKEDIN_EMAIL")
    password = os.getenv("LINKEDIN_PASSWORD")
    if not email or not password:
        raise ValueError(
            "Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env before running."
        )

    page = await context.new_page()
    try:
        print("Navigating to Sharebird login...")
        await page.goto(f"{SHAREBIRD_BASE}/login", wait_until="networkidle")
        await asyncio.sleep(2)

        # Click the LinkedIn sign-in button
        linkedin_btn = page.get_by_text("Continue with LinkedIn", exact=False)
        if not await linkedin_btn.is_visible():
            # Try alternative selectors
            linkedin_btn = page.locator("a[href*='linkedin']").first
        await linkedin_btn.click()
        await page.wait_for_url("**/linkedin.com/**", timeout=15000)
        print("On LinkedIn login page...")
        await asyncio.sleep(1)

        # Fill LinkedIn credentials
        await page.fill("#username", email)
        await page.fill("#password", password)
        await page.click("button[type='submit']")

        # Wait for redirect back to Sharebird
        try:
            await page.wait_for_url(f"{SHAREBIRD_BASE}/**", timeout=20000)
        except Exception:
            # Check for CAPTCHA or verification challenge
            if "challenge" in page.url or "checkpoint" in page.url:
                print(
                    "LinkedIn CAPTCHA/checkpoint detected. "
                    "Please complete it manually, then press Enter..."
                )
                input()
                await page.wait_for_url(f"{SHAREBIRD_BASE}/**", timeout=60000)

        print(f"Authenticated! Now at: {page.url}")
        await save_cookies(context)
        return True

    except Exception as e:
        print(f"Authentication failed: {e}")
        return False
    finally:
        await page.close()


async def get_authenticated_context(playwright, headless: bool = True):
    """Return a browser context with a valid Sharebird session."""
    browser, context = await launch_browser(playwright, headless=headless)
    has_cookies = await load_cookies(context)

    if has_cookies:
        # Quick check: verify session is still valid
        page = await context.new_page()
        await page.goto(f"{SHAREBIRD_BASE}/", wait_until="networkidle")
        is_logged_in = await page.locator("text=Sign out").is_visible() or \
                       await page.locator("[data-testid='user-menu']").is_visible()
        await page.close()

        if is_logged_in:
            print("Session still valid, skipping login.")
            return browser, context

    print("No valid session found, logging in...")
    success = await authenticate(context)
    if not success:
        raise RuntimeError("Could not authenticate with Sharebird.")

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

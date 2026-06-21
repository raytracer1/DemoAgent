#!/usr/bin/env python3
"""
One-time Google login for Runner's persistent browser profile.
All Google OAuth sites will reuse this login automatically.

Usage:
  python login.py                            # just log into Google
  python login.py https://example.com        # log into Google + authorize a site
"""
import asyncio
import json
import os
import sys
import httpx
from playwright.async_api import async_playwright

WORKER_URL = os.getenv("WORKER_URL", "https://demo-agent-worker.zhengbijun123.workers.dev")
PROFILE_DIR = os.path.join(os.path.dirname(__file__), "browser_profile")


async def main():
    site_url = sys.argv[1] if len(sys.argv) > 1 else None

    async with async_playwright() as p:
        # Launch with persistent profile — keeps Google cookies across sessions
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            viewport={"width": 1280, "height": 720},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=ChromeWhatsNewUI",
                "--no-sandbox",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # Step 1: Log into Google
        print("🌐 Opening Google login...")
        await page.goto("https://accounts.google.com/", wait_until="domcontentloaded")
        print("   ⏳ Sign into your Google account in the browser window")
        print("   ✅ After login, press Enter here to continue...")
        input()

        # Step 2: Optionally authorize a site
        if site_url:
            print(f"🌐 Opening {site_url} for OAuth authorization...")
            await page.goto(site_url, wait_until="domcontentloaded")
            print("   ⏳ Click 'Sign in with Google' and authorize the site")
            print("   ✅ Press Enter when done...")
            input()

            # Save site cookies
            cookies = await context.cookies()
            async with httpx.AsyncClient(timeout=30) as cli:
                r = await cli.post(f"{WORKER_URL}/api/profiles", json={
                    "url": site_url, "cookies": json.dumps(cookies),
                })
                if r.status_code == 200:
                    print(f"   ✅ Site cookies saved for: {site_url}")
                else:
                    print(f"   ⚠️  Failed to save cookies")

        print(f"\n🎉 Done! Profile saved at: {PROFILE_DIR}")
        print(f"   Runner will use this profile for all Google OAuth sites")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())

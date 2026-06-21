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

        # Step 2: Automatically authorize OAuth sites
        if site_url:
            print(f"\n🌐 Opening {site_url} for OAuth authorization...")
            await page.goto(site_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            # Click "Sign in" button
            for btn_text in ["Sign in", "Sign In", "Log in", "Login", "Continue with Google", "Sign in with Google"]:
                try:
                    btn = page.get_by_text(btn_text, exact=True).first
                    if await btn.count() > 0 and await btn.is_visible():
                        print(f"   🖱️  Clicking '{btn_text}'...")
                        await btn.click()
                        await page.wait_for_timeout(3000)
                        break
                except: pass

            # Handle Google OAuth: choose account / consent
            for _ in range(3):  # up to 3 steps in OAuth flow
                current = page.url
                if "accounts.google.com" not in current and site_url.split('/')[2] in current:
                    break  # back on site — done

                # Click "Continue" or "Allow" on Google consent
                for btn_text in ["Continue", "Allow", "Next", "Sign in"]:
                    try:
                        btn = page.locator(f"button:has-text('{btn_text}')").first
                        if await btn.count() > 0 and await btn.is_visible():
                            print(f"   🖱️  Google OAuth — clicking '{btn_text}'...")
                            await btn.click()
                            await page.wait_for_timeout(3000)
                            break
                    except: pass

                # If we clicked a Google account, click it
                try:
                    account = page.locator("[data-email], [data-identifier]").first
                    if await account.count() > 0:
                        await account.click()
                        await page.wait_for_timeout(2000)
                        continue
                except: pass

                await page.wait_for_timeout(2000)

            print(f"   ✅ OAuth flow complete")

        print(f"\n🎉 Done! Profile saved at: {PROFILE_DIR}")
        print(f"   Runner will use this profile for all Google OAuth sites")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())

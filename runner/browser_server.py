"""
WebSocket server for remote embedded browser.
Streams screenshots, receives clicks/keyboard, manages Playwright session.
"""
import asyncio
import base64
import json
import os
import time
from pathlib import Path
from playwright.async_api import async_playwright

VIEWPORT = {"width": 1280, "height": 720}
STREAM_FPS = 2  # frames per second for screenshot streaming

connected_browsers: dict[str, dict] = {}  # session_id -> {page, context, browser}


async def handle_ws(ws, worker_url: str):
    """Handle one WebSocket connection — one browser session per connection."""
    session_id = str(time.time()).replace(".", "")
    page = None
    context = None
    browser = None
    streaming = False
    stream_task = None
    target_url = ""

    try:
        async for raw in ws:
            msg = json.loads(raw)
            action = msg.get("action", "")

            if action == "connect":
                target_url = msg.get("url", "https://google.com")
                print(f"   🌐 Browser connect: {target_url}")

                p = await async_playwright().start()
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(viewport=VIEWPORT)
                page = await context.new_page()
                await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)

                connected_browsers[session_id] = {"page": page, "context": context, "browser": browser}
                streaming = True
                stream_task = asyncio.create_task(_stream_frames(ws, page, session_id))
                await ws.send(json.dumps({"type": "ready", "url": page.url, "session_id": session_id}))

            elif action == "click":
                if page:
                    x, y = msg.get("x", 0), msg.get("y", 0)
                    print(f"      🖱️  Click at ({x}, {y})")
                    await page.mouse.click(x, y)
                    await asyncio.sleep(0.3)

            elif action == "dblclick":
                if page:
                    x, y = msg.get("x", 0), msg.get("y", 0)
                    await page.mouse.dblclick(x, y)

            elif action == "type":
                if page:
                    text = msg.get("text", "")
                    # Type into the focused element
                    await page.keyboard.type(text, delay=50)

            elif action == "key":
                if page:
                    key = msg.get("key", "")
                    await page.keyboard.press(key)

            elif action == "scroll":
                if page:
                    dx, dy = msg.get("dx", 0), msg.get("dy", 0)
                    await page.mouse.wheel(dx, dy)

            elif action == "navigate":
                if page:
                    url = msg.get("url", "")
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await ws.send(json.dumps({"type": "url_changed", "url": page.url}))

            elif action == "start_demo":
                streaming = False
                if stream_task:
                    stream_task.cancel()
                    try: await stream_task
                    except asyncio.CancelledError: pass
                # Export cookies for reuse
                cookies = await context.cookies() if context else []
                await ws.send(json.dumps({
                    "type": "demo_ready",
                    "session_id": session_id,
                    "cookies": json.dumps(cookies),
                    "url": target_url,
                }))
                # Keep browser alive for reuse
                break

            elif action == "disconnect":
                streaming = False
                break

    except Exception as e:
        print(f"   ⚠️  WS error: {e}")
        try: await ws.send(json.dumps({"type": "error", "message": str(e)}))
        except: pass
    finally:
        streaming = False
        if stream_task:
            stream_task.cancel()
            try: await stream_task
            except: pass
        if session_id in connected_browsers:
            del connected_browsers[session_id]
        if context: await context.close()
        if browser: await browser.close()


async def _stream_frames(ws, page, session_id):
    """Send screenshots at STREAM_FPS rate while streaming is on."""
    interval = 1.0 / STREAM_FPS
    while session_id in connected_browsers:
        try:
            data = await page.screenshot(type="jpeg", quality=60, full_page=False)
            b64 = base64.b64encode(data).decode()
            await ws.send(json.dumps({
                "type": "frame",
                "data": b64,
                "url": page.url,
            }))
        except Exception:
            break
        await asyncio.sleep(interval)


# ── Integrated flow: called from main.py when demo starts ──
async def run_demo_with_browser(session_id: str, worker_url: str, goal: str):
    """Resume browser from session and run AI demo."""
    browser_info = connected_browsers.get(session_id)
    if not browser_info:
        return {"error": "browser session not found"}

    page = browser_info["page"]
    context = browser_info["context"]
    url = page.url

    # Create session in Worker with cookies
    cookies = await context.cookies()
    import httpx
    async with httpx.AsyncClient(timeout=30) as cli:
        # Save session
        r = await cli.post(f"{worker_url}/api/sessions", json={
            "url": url, "cookies": json.dumps(cookies),
        })
        session_data = r.json()
        worker_session_id = session_data["session_id"]

        # Create job
        r = await cli.post(f"{worker_url}/api/jobs", json={
            "url": url, "goal": goal, "session_id": worker_session_id,
        })
        job_data = r.json()
        return {"session_id": worker_session_id, "job_id": job_data["job_id"]}

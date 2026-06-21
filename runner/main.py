"""
DemoAgent Runner — runs locally, executes browser automation + video production.
Communicates with Cloudflare Worker for job dispatch and storage.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime

import httpx
from playwright.async_api import async_playwright

# Load .env file if present
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().strip().split("\n"):
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Config ─────────────────────────────────────────────
WORKER_URL = os.getenv("WORKER_URL", "http://localhost:8787")
POLL_INTERVAL = 3  # seconds between polls
VIEWPORT = {"width": 1280, "height": 720}
PROFILE_DIR = str(Path(__file__).parent / "browser_profile")
USE_PERSISTENT = os.path.isdir(PROFILE_DIR)

# Temp dir for video processing
TMP_ROOT = Path(tempfile.gettempdir()) / "demo-agent"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

async def _launch_context(p, record_dir: str = "", cookies=None):
    """Launch browser context. Uses persistent profile if available."""
    if USE_PERSISTENT:
        kwargs = {"user_data_dir": PROFILE_DIR, "viewport": VIEWPORT, "headless": True,
                   "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                   "ignore_default_args": ["--enable-automation"]}
        if record_dir:
            kwargs["record_video_dir"] = record_dir
            kwargs["record_video_size"] = VIEWPORT
        context = await p.chromium.launch_persistent_context(**kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        return context, page, None  # no separate browser object with persistent
    else:
        browser = await p.chromium.launch(headless=True)
        ctx_kwargs = {"viewport": VIEWPORT}
        if record_dir:
            ctx_kwargs["record_video_dir"] = record_dir
            ctx_kwargs["record_video_size"] = VIEWPORT
        context = await browser.new_context(**ctx_kwargs)
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()
        return context, page, browser

# ── HTTP helpers ────────────────────────────────────────
async def api(path: str, method="GET", data=None, json_data=None):
    """Call Worker API."""
    async with httpx.AsyncClient(timeout=120.0) as cli:
        if method == "GET":
            r = await cli.get(f"{WORKER_URL}{path}")
        elif method == "POST":
            r = await cli.post(f"{WORKER_URL}{path}", data=data, json=json_data)
        elif method == "PUT":
            r = await cli.put(f"{WORKER_URL}{path}", data=data, json=json_data)
        else:
            raise ValueError(f"Unknown method: {method}")
        r.raise_for_status()
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content


# ── Login session handler ───────────────────────────────
async def pre_login_accounts():
    """Pre-login test accounts from accounts.json, save cookies to D1."""
    account_file = Path(__file__).parent / "accounts.json"
    if not account_file.exists():
        print("   ℹ️  No accounts.json found, skipping pre-login")
        return

    try:
        accounts = json.loads(account_file.read_text())
    except Exception:
        print("   ⚠️  Failed to parse accounts.json")
        return

    if not accounts:
        return

    print(f"   🔐 Pre-logging {len(accounts)} account(s)...")
    for acc in accounts:
        url = acc.get("url", "")
        login_steps = acc.get("login_steps", [])
        if not url or not login_steps:
            continue

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport=VIEWPORT)
            page = await context.new_page()

            try:
                print(f"      🌐 Logging into {url}...")
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)

                for step in login_steps:
                    action = step.get("action", "")
                    target = step.get("target", "")
                    value = step.get("value", "")
                    print(f"         {action} → {target}")
                    await execute_step(page, step, -1)
                    await asyncio.sleep(1.5)

                # Save cookies to D1
                cookies = await context.cookies()
                cookie_str = json.dumps(cookies)
                resp = await api("/api/sessions", "POST", json_data={
                    "url": url, "cookies": cookie_str,
                })
                sid = resp.get("session_id", "")
                print(f"      ✅ Logged in: session {sid}")
            except Exception as e:
                print(f"      ❌ Login failed: {e}")
            finally:
                await context.close()
                await browser.close()


async def handle_login_sessions():
    """Poll for sessions that need manual login (X server required)."""
    if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
        return  # no display server, skip login sessions entirely
    while True:
        try:
            session = await api("/api/sessions/next-pending")
            if session and session.get("id"):
                print(f"\n🔐 Login needed: {session['url']}")
                print(f"\n🔐 Login needed: {session['url']}")
                await do_login(session)
            else:
                break
        except Exception as e:
            print(f"Login poll error: {e}")
            break


async def do_login(session: dict):
    """Open visible browser, let user log in, save cookies."""
    session_id = session["id"]
    url = session["url"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport=VIEWPORT)
        page = await context.new_page()

        await page.goto(url, wait_until="domcontentloaded")
        print(f"   Browser opened at {url}")
        print(f"   ⏳ You have 120 seconds to log in...")
        print(f"   ✅ Close the browser window when done, or wait for timeout")

        # Wait for user to log in (max 120s)
        await asyncio.sleep(120)

        # Export cookies
        cookies = await context.cookies()
        await api(f"/api/sessions/{session_id}/cookies", "PUT", json_data={"cookies": json.dumps(cookies)})
        print(f"   ✅ Cookies saved for session {session_id}")

        await browser.close()


# ── Page element extraction ─────────────────────────────
async def extract_page_elements(page) -> dict:
    """Extract all interactive elements from the current page."""
    elements: dict = {
        "url": page.url,
        "title": await page.title(),
        "buttons": [],
        "links": [],
        "inputs": [],
        "headings": [],
        "visible_text": "",
    }

    # Buttons
    for b in await page.locator("button").all():
        if await b.is_visible():
            text = (await b.text_content() or "").strip()
            if text:
                elements["buttons"].append(text)

    # Links
    for a in await page.locator("a").all():
        if await a.is_visible():
            text = (await a.text_content() or "").strip()
            href = (await a.get_attribute("href") or "")
            if text:
                elements["links"].append({"text": text[:100], "href": href})

    # Inputs
    for el in await page.locator("input, textarea, select").all():
        tag = await el.evaluate("e => e.tagName.toLowerCase()")
        el_type = (await el.get_attribute("type") or "")
        placeholder = (await el.get_attribute("placeholder") or "")
        name = (await el.get_attribute("name") or "")
        aria = (await el.get_attribute("aria-label") or "")
        label_text = ""
        # Try to find associated label
        label_id = await el.get_attribute("id")
        if label_id:
            try:
                label = await page.locator(f"label[for='{label_id}']").first.text_content(timeout=2000)
            except Exception:
                label = ""
            if label:
                label_text = label.strip()
        elements["inputs"].append({
            "tag": tag,
            "type": el_type,
            "placeholder": placeholder,
            "name": name,
            "aria_label": aria,
            "label": label_text,
        })

    # Headings
    for h in await page.locator("h1, h2, h3").all():
        if await h.is_visible():
            text = (await h.text_content() or "").strip()
            if text:
                elements["headings"].append(text)

    # Visible body text (first 2000 chars)
    body = (await page.text_content("body")) or ""
    elements["visible_text"] = " ".join(body.split())[:2000]

    return elements


# ── Element finder (7-level fallback) ───────────────────
async def find_element(page, target: str):
    """Find an element using progressive fallback strategies."""
    if not target:
        return None

    strategies = [
        # 1. Role-based
        lambda: page.get_by_role("button", name=target),
        lambda: page.get_by_role("link", name=target),
        lambda: page.get_by_role("textbox", name=target),
        lambda: page.get_by_role("combobox", name=target),
        # 2. Exact text
        lambda: page.get_by_text(target, exact=True),
        # 3. Placeholder
        lambda: page.get_by_placeholder(target),
        # 4. Label
        lambda: page.get_by_label(target),
        # 5. aria-label / title
        lambda: page.locator(f"[aria-label*='{target}']"),
        lambda: page.locator(f"[title*='{target}']"),
        # 6. Partial text
        lambda: page.get_by_text(target),
        # 7. Generic contains-text
        lambda: page.locator(f":has-text('{target}')"),
    ]

    for strategy in strategies:
        try:
            loc = strategy()
            if await loc.count() > 0:
                return loc.first
        except Exception:
            continue

    return None


# ── Step executor ───────────────────────────────────────
async def execute_step(page, step: dict, index: int) -> dict:
    """Execute a single plan step."""
    action = step.get("action", "").lower().strip()
    target = step.get("target", "").strip()
    value = step.get("value", "")

    result = {"index": index, "action": action, "target": target, "status": "ok"}
    timeout = 10000

    try:
        if action == "navigate":
            await page.goto(value or target, wait_until="domcontentloaded", timeout=timeout)
            await asyncio.sleep(1)

        elif action == "click":
            el = await find_element(page, target)
            if el:
                pre_click_url = page.url
                await el.click(timeout=timeout)
                # Domain guard: go back if we navigated to external site
                from urllib.parse import urlparse as _up
                if _up(page.url).netloc and _up(pre_click_url).netloc:
                    if _up(pre_click_url).netloc not in _up(page.url).netloc and _up(page.url).netloc not in _up(pre_click_url).netloc:
                        await page.go_back(timeout=timeout)
                        result["status"] = "skipped"
                        result["error"] = f"blocked external: {page.url[:60]}"
            else:
                result["status"] = "skipped"
                result["error"] = f"not found: {target}"
            await asyncio.sleep(1)

        elif action == "type":
            el = await find_element(page, target)
            if el:
                await el.click()
                await el.fill(value or "", timeout=timeout)
            else:
                result["status"] = "skipped"
                result["error"] = f"not found: {target}"

        elif action == "select":
            el = await find_element(page, target)
            if el:
                await el.select_option(value, timeout=timeout)
            else:
                result["status"] = "skipped"
                result["error"] = f"not found: {target}"

        elif action == "upload":
            el = await find_element(page, target)
            if el:
                # value should be a local file path
                await el.set_input_files(value, timeout=timeout)
            else:
                result["status"] = "skipped"
                result["error"] = f"not found: {target}"

        elif action == "wait":
            secs = int(value) if value and value.isdigit() else 2
            await asyncio.sleep(secs)

        elif action == "scroll":
            await page.evaluate(f"window.scrollBy(0, {value or 300})")
            await asyncio.sleep(0.5)

        else:
            result["status"] = "skipped"
            result["error"] = f"unknown action: {action}"

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)[:200]

    return result


# ── Job executor ────────────────────────────────────────
async def process_job(job: dict):
    """Execute a single demo job end-to-end."""
    job_id = job["id"]
    url = job["url"]
    goal = job["goal"]
    session_id = job.get("session_id")
    status = job["status"]

    print(f"\n🎬 Processing job {job_id}: {goal}")
    temp_dir = TMP_ROOT / job_id
    temp_dir.mkdir(parents=True, exist_ok=True)

    # ── Phase 1: Discover plan (no recording) ──
    async with async_playwright() as p:
        cookies = None
        if session_id:
            session_data = await api(f"/api/sessions/{session_id}")
            cookies_raw = session_data.get("cookies") if session_data else None
            if cookies_raw:
                try:
                    cookies = json.loads(cookies_raw)
                except Exception:
                    pass

        context, page, browser = await _launch_context(p, cookies=cookies)

        try:
            print(f"   📄 Navigating to {url}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

            # Pre-login: handle Google OAuth (supports redirect + popup)
            for btn_text in ["Sign in", "Sign In", "Log in", "Login"]:
                try:
                    b = page.get_by_text(btn_text, exact=True).first
                    if await b.count() > 0 and await b.is_visible():
                        print(f"   🔑 Detected '{btn_text}' — auto-completing OAuth...")

                        # Watch for popup
                        popup_promise = None
                        try:
                            popup_promise = page.wait_for_event("popup", timeout=5000)
                        except: pass

                        await b.click()

                        oauth_page = None
                        if popup_promise:
                            try:
                                oauth_page = await popup_promise
                                print(f"   📄 OAuth popup opened")
                            except: pass

                        # If no popup in 5s, check if main page navigated
                        if not oauth_page:
                            await asyncio.sleep(3)
                            for _ in range(5):
                                if "accounts.google.com" in page.url:
                                    oauth_page = page
                                    break
                                await asyncio.sleep(1)

                        # Complete OAuth flow
                        if oauth_page:
                            # Step through Google's multi-page OAuth
                            for _ in range(15):
                                await asyncio.sleep(2)
                                clicked = False
                                # Try to click account/profile first
                                for sel in ["div[role='link']", "div[data-email]", "li[role='menuitem']"]:
                                    try:
                                        bb = oauth_page.locator(sel).first
                                        if await bb.count() > 0 and await bb.is_visible():
                                            await bb.click()
                                            clicked = True
                                            break
                                    except: pass
                                # Then try buttons
                                if not clicked:
                                    for b2 in ["Continue", "Allow", "Next", "Sign in", "Confirm"]:
                                        try:
                                            bb = oauth_page.locator(f"button:has-text('{b2}')").first
                                            if await bb.count() > 0 and await bb.is_visible():
                                                await bb.click()
                                                clicked = True
                                                break
                                        except: pass
                                # Check if we're done
                                from urllib.parse import urlparse as _up2
                                if "accounts.google.com" not in oauth_page.url:
                                    break
                                if not clicked:
                                    break
                            # Close popup if it was a popup
                            if oauth_page != page:
                                try: await oauth_page.close()
                                except: pass

                        # Wait for main page to reload
                        await asyncio.sleep(2)
                        try: await page.wait_for_load_state("networkidle", timeout=10000)
                        except: pass
                        print(f"   ✅ OAuth complete")
                        break
                except: pass

            all_steps: list[dict] = []
            last_url = ""
            MAX_ROUNDS = 3

            for round_num in range(1, MAX_ROUNDS + 1):
                current_url = page.url
                print(f"\n   🔄 Round {round_num} — {current_url}")

                elements = await extract_page_elements(page)
                history = [{"step": s["index"], "action": s["action"], "target": s["target"]} for s in all_steps]

                print(f"   📤 Planning (round {round_num}, {len(history)} done)...")
                await api(f"/api/jobs/{job_id}/elements", "PUT", json_data={
                    "url": current_url, "elements": elements,
                    "history": history, "round": round_num,
                })

                # Wait for plan
                for _ in range(30):
                    await asyncio.sleep(2)
                    updated = await api(f"/api/jobs/{job_id}")
                    if updated.get("status") == "ready" and updated.get("plan"):
                        plan = updated["plan"]
                        if isinstance(plan, str): plan = json.loads(plan)
                        break
                    if updated.get("status") == "error":
                        raise Exception(f"Plan failed: {updated.get('error')}")
                else:
                    raise Exception("Planning timed out")

                # Filter noise steps from plan
                NOISE = ["sign in", "login", "google", "auth", "allow", "continue with"]
                plan = [s for s in plan if not any(k in (s.get("target","")+s.get("action","")).lower() for k in NOISE)]
                plan = [s for s in plan if not (s.get("action")=="wait" and not s.get("target") and not s.get("value"))]

                if not plan or len(plan) <= len(history):
                    print(f"   ✅ Plan complete!")
                    break

                new_steps = plan[len(history):]
                if not new_steps: break

                print(f"   📋 {len(new_steps)} new steps found")
                page_changed = False
                for step in new_steps:
                    step["index"] = len(all_steps)
                    print(f"      {step['index']+1}: {step.get('action')} → {step.get('target', '')[:60]}")
                    pre_url = page.url
                    result = await execute_step(page, step, step["index"])
                    # Skip meaningless steps — don't count toward plan progress
                    NOISE_KEYWORDS = ["sign in", "login", "google", "auth", "allow", "continue", "wait"]
                    is_noise = any(k in (step.get("target", "") + step.get("action", "")).lower() for k in NOISE_KEYWORDS)
                    is_empty_wait = step.get("action") == "wait" and not step.get("target") and not step.get("value")
                    if not is_noise and not is_empty_wait:
                        all_steps.append(step)

                    if page.url != pre_url and page.url != last_url:
                        # Check if we left the target site
                        from urllib.parse import urlparse
                        target_domain = urlparse(url).netloc
                        new_domain = urlparse(page.url).netloc
                        if target_domain and new_domain and target_domain not in new_domain and new_domain not in target_domain:
                            print(f"   🚫 External domain: {new_domain} — going back")
                            all_steps.pop()  # remove the bad step
                            await page.go_back()
                            await asyncio.sleep(1)
                            continue  # skip this step, keep planning
                        print(f"   🔀 Page changed: {pre_url[:50]} → {page.url[:50]}")
                        last_url = page.url
                        page_changed = True
                        break
                    await asyncio.sleep(0.5)

                if not page_changed and len(all_steps) >= len(plan):
                    break
                if page_changed:
                    continue

            await context.close()
            if browser: await browser.close()

            if not all_steps:
                raise Exception("No steps discovered")

            print(f"\n   ✅ Plan discovered: {len(all_steps)} steps total")
            for s in all_steps:
                print(f"      {s['index']+1}: {s['action']} → {s['target'][:60]}")

        except Exception as e:
            await context.close()
            if browser: await browser.close()
            raise e

    # ── Phase 2: Narration + TTS (before recording) ──
    await api(f"/api/jobs/{job_id}/status", "PUT", json_data={"status": "narrating"})
    print(f"   🎤 Generating narration...")
    narration_resp = await api(f"/api/jobs/{job_id}/narration", "POST")
    narration = narration_resp.get("narration", "")
    narration_segments: list[dict] = narration_resp.get("segments", [])  # [{step, text}, ...]
    print(f"   📝 Narration: {len(narration_segments)} step segments")

    # ── Phase 2: Per-step TTS → exact per-step durations ──
    audio_segments: list[str] = []  # paths to per-step mp3 files
    per_step_pauses: list[float] = []
    total_audio_duration = 0.0

    if narration_segments:
        for seg in narration_segments:
            step_i = seg.get("step", 0)
            text = seg.get("text", "")
            if not text.strip():
                audio_segments.append("")
                per_step_pauses.append(3.0)  # default 3s
                continue
            seg_path = str(temp_dir / f"narration_step{step_i}.mp3")
            try:
                print(f"   🔊 TTS step {step_i}: {text[:50]}...")
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "edge_tts",
                    "--voice", "en-US-AriaNeural",
                    "--text", text,
                    "--write-media", seg_path,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
                if proc.returncode == 0:
                    dur_proc = await asyncio.create_subprocess_exec(
                        "ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", seg_path,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    dur_out, _ = await dur_proc.communicate()
                    step_dur = float(dur_out.decode().strip())
                    per_step_pauses.append(step_dur)
                    total_audio_duration += step_dur
                    audio_segments.append(seg_path)
                    print(f"      ⏱️  {step_dur:.1f}s")
                else:
                    per_step_pauses.append(3.0)
                    audio_segments.append("")
            except Exception as e:
                print(f"      ⚠️  TTS error: {e}")
                per_step_pauses.append(3.0)
                audio_segments.append("")
    else:
        # Fallback: single TTS, split evenly
        audio_path = str(temp_dir / "narration.mp3")
        if narration.strip():
            await _tts_single(narration, audio_path)
            total_audio_duration = await _get_audio_duration(audio_path) or 30.0
        per_step_pauses = [total_audio_duration / len(all_steps)] * len(all_steps) if all_steps else []

    # Concatenate per-step audio files into one final track
    audio_path = str(temp_dir / "narration.mp3")
    if len(audio_segments) > 1 and all(audio_segments):
        await _concat_audio(audio_segments, audio_path)
    elif len(audio_segments) == 1 and audio_segments[0]:
        import shutil as _sh
        _sh.copy(audio_segments[0], audio_path)

    print(f"   🕐 Per-step timing: {[f'{p:.1f}s' for p in per_step_pauses]}")
    print(f"   ⏱️  Total audio: {total_audio_duration:.1f}s")

    # ── Phase 3: Execute with recording (paced by per-step audio) ──
    print(f"   🎬 Recording with per-step audio timing...")

    video_dir = str(temp_dir / "recording")
    os.makedirs(video_dir, exist_ok=True)

    # ── Phase 3: Record with per-step stability + segment tracking ──
    segments: list[tuple[float, float]] = []
    step_times: list[float] = []
    async with async_playwright() as p:
        cookies = None
        if session_id:
            session_data = await api(f"/api/sessions/{session_id}")
            cookies_raw = session_data.get("cookies") if session_data else None
            if cookies_raw:
                try: cookies = json.loads(cookies_raw)
                except Exception: pass

        context, page, browser = await _launch_context(p, record_dir=video_dir, cookies=cookies)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)

            segments: list[tuple[float, float]] = []  # (start, end) of valid content per step
            recording_start = time.time()

            # Handle intro (step -1): no action, just wait on loaded page
            intro_pause = per_step_pauses[0] if per_step_pauses and narration_segments and narration_segments[0].get("step") == -1 else None
            if intro_pause is not None:
                per_step_pauses.pop(0)  # remove intro, keep only step pauses
                await _wait_for_content_stable(page)
                seg_start = time.time() - recording_start
                print(f"      🎤 Intro: holding for {intro_pause:.1f}s")
                await asyncio.sleep(intro_pause * 0.9)
                seg_end = time.time() - recording_start
                segments.append((seg_start, seg_end))
                print(f"      📐 Intro segment: {seg_start:.1f}s → {seg_end:.1f}s")

            AUTH_KEYWORDS = ["sign in", "login", "google", "auth", "continue with", "allow"]
            demo_steps = [s for s in all_steps if not any(k in s.get("target", "").lower() for k in AUTH_KEYWORDS)]
            if len(demo_steps) < len(all_steps):
                print(f"   🚫 Skipping {len(all_steps) - len(demo_steps)} auth step(s)")

            for i, step in enumerate(demo_steps):
                # Wait for page stability before considering content valid
                await _wait_for_content_stable(page)
                seg_start = time.time() - recording_start

                print(f"      Step {i+1}/{len(all_steps)}: {step.get('action')} → {step.get('target', '')[:60]}")
                await execute_step(page, step, i)
                # Wait for new page to stabilize (if navigation happened)
                await _wait_for_content_stable(page, timeout=10, interval=0.5, stable_count=6)
                # Pause for this step's narration segment duration
                pause = per_step_pauses[i] if i < len(per_step_pauses) else per_step_pauses[-1]
                pause *= 0.9
                print(f"      ⏸  Holding for {pause:.1f}s (narration for this step)")
                await asyncio.sleep(pause)

                seg_end = time.time() - recording_start
                segments.append((seg_start, seg_end))
                print(f"      📐 Segment {i+1}: {seg_start:.1f}s → {seg_end:.1f}s")

            await context.close()
            if browser: await browser.close()

            # Find recording (wait briefly for file finalization)
            await asyncio.sleep(1)
            video_files = sorted(Path(video_dir).glob("*.webm"), key=lambda p: p.stat().st_size)
            if not video_files:
                raise Exception("No recording found")
            # Pick the largest file (actual content, not about:blank)
            recording_path = str(video_files[-1])
            print(f"   🎥 Recording: {recording_path} ({len(video_files)} files)")

        except Exception as e:
            await context.close()
            if browser: await browser.close()
            raise e

    # ── Phase 4: Trim segments + SRT + Assembly ──
    # First: trim recording to only valid segments, concat them
    trimmed_path = str(temp_dir / "trimmed.mp4")
    if segments and len(segments) > 0:
        print(f"   ✂️  Trimming {len(segments)} valid segments...")
        # Extract each segment with -ss/-to, then concat (compatible with all ffmpeg)
        seg_files = []
        for i, (start, end) in enumerate(segments):
            seg_file = str(temp_dir / f"seg_{i}.mp4")
            seg_cmd = ["ffmpeg", "-y", "-ss", str(start), "-to", str(end),
                       "-i", recording_path,
                       "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                       "-an", seg_file]
            sp = await asyncio.create_subprocess_exec(*seg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await sp.wait()
            if sp.returncode == 0:
                seg_files.append(seg_file)

        if seg_files:
            # Concat all segments
            list_path = str(temp_dir / "concat.txt")
            temp_dir.mkdir(parents=True, exist_ok=True)  # ensure directory exists
            with open(list_path, "w") as f:
                for sf in seg_files:
                    f.write(f"file '{sf}'\n")
            concat_cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                          "-i", list_path, "-c", "copy", trimmed_path]
            cp = await asyncio.create_subprocess_exec(*concat_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await cp.wait()
            if cp.returncode == 0:
                recording_path = trimmed_path
                # Recalculate step_times for trimmed timeline
                trimmed_step_times = []
                offset = 0.0
                for start, end in segments:
                    trimmed_step_times.append(offset)
                    offset += (end - start)
                step_times = trimmed_step_times
                print(f"   ✅ Segments trimmed ({offset:.1f}s total)")
            else:
                print(f"   ⚠️  Concat failed, using full recording")
        else:
            print(f"   ⚠️  Segment extraction failed, using full recording")
    else:
        print(f"   ⚠️  No segments tracked, using full recording")

    srt_path = None
    if narration.strip():
        try:
            srt_path = str(temp_dir / "subtitles.srt")
            _generate_srt(narration, srt_path, duration=total_audio_duration, step_times=step_times)
            print(f"   📝 Subtitles (synced to {total_audio_duration:.1f}s TTS)")
        except Exception as e:
            print(f"   ⚠️  SRT error: {e}")

    final_path = str(temp_dir / "final.mp4")
    has_subs = srt_path and Path(srt_path).exists()
    has_audio = audio_path and Path(audio_path).exists()

    if has_audio:
        print(f"   🎬 Assembling trimmed video + audio + subtitles...")
        cmd = ["ffmpeg", "-y", "-i", recording_path, "-i", audio_path]
        if has_subs:
            cmd += ["-vf", f"subtitles={srt_path}:force_style='FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=1.5'"]
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", "-b:a", "128k", final_path]
    else:
        print(f"   🎬 Converting trimmed video + subtitles...")
        cmd = ["ffmpeg", "-y", "-i", recording_path]
        if has_subs:
            cmd += ["-vf", f"subtitles={srt_path}:force_style='FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=1.5'"]
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an", final_path]

    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode() if stderr else "?"
        print(f"   ⚠️  ffmpeg error: {err[:200]}")
        final_path = recording_path  # fallback

    # ── Upload ──
    print(f"   📤 Uploading to R2...")
    with open(final_path, "rb") as f:
        await api(f"/api/jobs/{job_id}/video", "PUT", data=f.read())

    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)
    print(f"   🎉 Job {job_id} complete!")
    print(f"   📺 {WORKER_URL}/api/video/{job_id}")
    return


async def _process_job_error(job_id: str, e: Exception):
    print(f"   ❌ Job failed: {e}")
    try:
        await api(f"/api/jobs/{job_id}/status", "PUT", json_data={
            "status": "error", "error": str(e)[:500],
        })
    except Exception:
        pass


async def _detect_blank_end(video_path: str) -> float:
    """Use ffmpeg blackdetect to find where blank/white frames end. Returns trim start time."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", video_path,
            "-vf", "blackdetect=d=0.3:pix_th=0.15",
            "-an", "-f", "null", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        import re
        # ffmpeg outputs: black_start:0 black_end:2.5 black_duration:2.5
        matches = re.findall(r'black_end:([\d.]+)', stderr.decode() if stderr else "")
        if matches:
            return max(0, float(matches[-1]) - 0.3)  # slight overlap to be safe
    except Exception:
        pass
    return 0.0


async def _tts_single(text: str, output_path: str):
    """Generate TTS for a single text segment."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "edge_tts",
        "--voice", "en-US-AriaNeural",
        "--text", text,
        "--write-media", output_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()


async def _get_audio_duration(path: str) -> float | None:
    """Get audio file duration via ffprobe."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        return float(out.decode().strip())
    except Exception:
        return None


async def _concat_audio(input_paths: list[str], output_path: str):
    """Concatenate multiple audio files with ffmpeg concat demuxer."""
    list_path = output_path + ".list.txt"
    with open(list_path, "w") as f:
        for p in input_paths:
            f.write(f"file '{p}'\n")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", output_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()


def _parse_vtt_for_steps(vtt_path: str, num_steps: int, total_duration: float) -> list[float]:
    """Parse edge-tts VTT subtitle file. Distribute cue timings across steps.
    Returns list of pause durations, one per step."""
    import re
    try:
        with open(vtt_path) as f:
            content = f.read()
    except Exception:
        return [total_duration / num_steps] * num_steps

    # Extract cue timestamps: 00:00:01.234 --> 00:00:03.456
    cues = re.findall(r'(\d+:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d+:\d{2}:\d{2}\.\d{3})', content)
    if not cues:
        return [total_duration / num_steps] * num_steps

    def _to_sec(ts: str) -> float:
        h, m, s = ts.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)

    # Map cues to steps proportionally
    total_cues = len(cues)
    cues_per_step = max(1, total_cues // num_steps)
    pauses = []

    for step_i in range(num_steps):
        start_idx = step_i * cues_per_step
        end_idx = min(start_idx + cues_per_step, total_cues) - 1
        if start_idx < total_cues and end_idx < total_cues:
            seg_start = _to_sec(cues[start_idx][0])
            seg_end = _to_sec(cues[end_idx][1])
            pauses.append(seg_end - seg_start)
        else:
            pauses.append(total_duration / num_steps)

    return pauses


async def _wait_for_content_stable(page, timeout: int = 20, interval: float = 1.0, stable_count: int = 10):
    """Wait until page text content stops changing — API data has rendered and settled."""
    import hashlib
    start = time.time()
    last_hash = ""
    stable = 0
    while time.time() - start < timeout:
        try:
            body = (await page.text_content("body")) or ""
            # Also count interactive elements as a sanity check
            btn = await page.locator("button:visible").count()
            link = await page.locator("a:visible").count()
            h = hashlib.md5((body + str(btn) + str(link)).encode()).hexdigest()
            if h == last_hash and btn + link > 0:
                stable += 1
                if stable >= stable_count:
                    return
            else:
                stable = 0
                last_hash = h
        except Exception:
            pass
        await asyncio.sleep(interval)


def _generate_srt(text: str, output_path: str, duration: float = 30, step_times: list[float] | None = None):
    """Generate SRT subtitles synced to step timestamps when available."""
    words = text.split()
    if not words:
        return

    # Split narration into ~10-word chunks
    words_per_chunk = max(1, len(words) // max(1, (len(words) + 9) // 10))
    chunks = []
    for i in range(0, len(words), words_per_chunk):
        chunk = " ".join(words[i:i + words_per_chunk])
        if chunk:
            chunks.append(chunk)

    def _fmt(sec: float) -> str:
        ms = int((sec % 1) * 1000)
        total = int(sec)
        h, m = divmod(total, 3600)
        m, s = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    # Map chunks to step timestamps if available
    if step_times and len(step_times) > 0:
        # Distribute chunks across step boundaries
        times = list(step_times) + [duration]  # add video end
        chunks_per_step = max(1, len(chunks) // len(step_times))
        with open(output_path, "w") as f:
            chunk_idx = 0
            for step_i in range(len(step_times)):
                t_start = times[step_i]
                t_end = times[step_i + 1]
                step_chunks = min(chunks_per_step, len(chunks) - chunk_idx)
                if step_i == len(step_times) - 1:
                    step_chunks = len(chunks) - chunk_idx  # last step gets remainder
                for j in range(step_chunks):
                    if chunk_idx >= len(chunks):
                        break
                    seg_dur = (t_end - t_start) / step_chunks
                    start = t_start + j * seg_dur
                    end = min(start + seg_dur, t_end)
                    f.write(f"{chunk_idx + 1}\n")
                    f.write(f"{_fmt(start)} --> {_fmt(end)}\n")
                    f.write(f"{chunks[chunk_idx]}\n\n")
                    chunk_idx += 1
    else:
        # Fallback: even spacing
        total_chunks = len(chunks)
        chunk_secs = duration / total_chunks if total_chunks > 0 else 4
        with open(output_path, "w") as f:
            for i, chunk in enumerate(chunks):
                start = i * chunk_secs
                end = min(start + chunk_secs, duration)
                f.write(f"{i + 1}\n")
                f.write(f"{_fmt(start)} --> {_fmt(end)}\n")
                f.write(f"{chunk}\n\n")


# ── Main loop ───────────────────────────────────────────
async def main():
    print("=" * 50)
    print("🔄 DemoAgent Runner started")
    print(f"   Worker: {WORKER_URL}")
    print(f"   Persistent profile: {'✅ ' + PROFILE_DIR if USE_PERSISTENT else '❌ not found'}")
    print("=" * 50)

    # Start WebSocket server for remote browser
    WS_PORT = int(os.getenv("WS_PORT", "8765"))
    import websockets
    from browser_server import handle_ws

    async def ws_handler(ws):
        await handle_ws(ws, WORKER_URL)

    ws_server = await websockets.serve(ws_handler, "0.0.0.0", WS_PORT)
    print(f"   🌐 Browser WebSocket: ws://0.0.0.0:{WS_PORT}")

    try:
        while True:
            try:
                await handle_login_sessions()
                job = await api("/api/jobs/next")
                if job and job.get("id"):
                    await process_job(job)
                else:
                    await asyncio.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Main loop error: {e}")
                await asyncio.sleep(POLL_INTERVAL)
    finally:
        ws_server.close()
        await ws_server.wait_close()
        print("\n👋 Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())

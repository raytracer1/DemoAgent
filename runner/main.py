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

# ── Config ─────────────────────────────────────────────
WORKER_URL = os.getenv("WORKER_URL", "http://localhost:8787")
POLL_INTERVAL = 3  # seconds between polls
VIEWPORT = {"width": 1280, "height": 720}

# Temp dir for video processing
TMP_ROOT = Path(tempfile.gettempdir()) / "demo-agent"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

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
async def handle_login_sessions():
    """Poll for sessions that need manual login."""
    while True:
        try:
            session = await api("/api/sessions/next-pending")
            if session and session.get("id"):
                print(f"\n🔐 Login needed: {session['url']}")
                await do_login(session)
            else:
                # No pending sessions, break and move to job polling
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
            label = await page.locator(f"label[for='{label_id}']").first.text_content()
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
            await page.goto(value or target, wait_until="networkidle", timeout=timeout)
            await asyncio.sleep(1)

        elif action == "click":
            el = await find_element(page, target)
            if el:
                await el.click(timeout=timeout)
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

    async with async_playwright() as p:
        context_kwargs = {
            "viewport": VIEWPORT,
        }

        # If session exists, load cookies
        cookies_loaded = False
        if session_id:
            session_data = await api(f"/api/sessions/{session_id}")
            cookies_raw = session_data.get("cookies") if session_data else None
            if cookies_raw:
                try:
                    _ = json.loads(cookies_raw)  # validate JSON
                    cookies_loaded = True
                except Exception:
                    pass

        # Record video
        video_dir = str(temp_dir / "recording")
        os.makedirs(video_dir, exist_ok=True)
        context_kwargs["record_video_dir"] = video_dir
        context_kwargs["record_video_size"] = VIEWPORT

        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(**context_kwargs)

        # Load cookies if available
        if cookies_loaded:
            cookies = json.loads(cookies_raw)
            await context.add_cookies(cookies)
            print(f"   🍪 Loaded login cookies")

        page = await context.new_page()

        try:
            # ── Step 1: Extract elements ──
            if status == "extracting":
                print(f"   📄 Navigating to {url}...")
                await page.goto(url, wait_until="networkidle", timeout=15000)
                await asyncio.sleep(2)

                print(f"   🔍 Extracting page elements...")
                elements = await extract_page_elements(page)

                print(f"   📤 Sending elements to Worker for planning...")
                await api(f"/api/jobs/{job_id}/elements", "PUT", json_data={
                    "url": url,
                    "elements": elements,
                })

                # Wait for Worker to generate plan
                for _ in range(30):
                    await asyncio.sleep(2)
                    updated = await api(f"/api/jobs/{job_id}")
                    if updated.get("status") == "ready" and updated.get("plan"):
                        job["plan"] = updated["plan"]
                        job["status"] = "ready"
                        break
                    if updated.get("status") == "error":
                        raise Exception(f"Plan generation failed: {updated.get('error')}")

                if not job.get("plan"):
                    raise Exception("Plan generation timed out")

                plan = job["plan"]
                print(f"   📋 Plan: {len(plan)} steps generated")

            else:
                # Job already has plan
                plan = json.loads(job.get("plan", "[]")) if isinstance(job.get("plan"), str) else (job.get("plan") or [])

            if not plan:
                raise Exception("No execution plan available")

            # ── Step 2: Execute plan ──
            await api(f"/api/jobs/{job_id}/status", "PUT", json_data={"status": "running"})
            print(f"   ▶️  Executing {len(plan)} steps...")

            # Navigate to start URL first (first step might be a click)
            if url not in page.url:
                await page.goto(url, wait_until="networkidle", timeout=15000)
                await asyncio.sleep(1)

            step_results = []
            for i, step in enumerate(plan):
                print(f"      Step {i+1}/{len(plan)}: {step.get('action')} → {step.get('target', '')[:50]}")
                result = await execute_step(page, step, i)
                step_results.append(result)
                if result["status"] == "error":
                    print(f"      ⚠️  {result['error']}")

            print(f"   ✅ Plan executed: {sum(1 for r in step_results if r['status']=='ok')}/{len(plan)} steps OK")

            # ── Step 3: Generate narration ──
            await api(f"/api/jobs/{job_id}/status", "PUT", json_data={"status": "narrating"})
            print(f"   🎤 Generating narration...")
            narration_resp = await api(f"/api/jobs/{job_id}/narration", "POST")
            narration = narration_resp.get("narration", "")
            print(f"   📝 Narration: {narration[:80]}...")

            # ── Step 4: Close browser (finalizes video) ──
            await context.close()
            await browser.close()

            # Find the recorded video
            video_files = sorted(Path(video_dir).glob("*.webm"))
            if not video_files:
                raise Exception("No video recording found")
            recording_path = str(video_files[-1])
            print(f"   🎥 Recording: {recording_path}")

            # ── Step 5: TTS (edge-tts) ──
            audio_path = str(temp_dir / "narration.mp3")
            if narration.strip():
                try:
                    print(f"   🔊 Generating TTS...")
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "edge_tts",
                        "--voice", "en-US-AriaNeural",
                        "--text", narration,
                        "--write-media", audio_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.wait()
                    if proc.returncode != 0:
                        print(f"   ⚠️  TTS failed, using silent video")
                        audio_path = None
                except Exception as e:
                    print(f"   ⚠️  TTS error: {e}")
                    audio_path = None
            else:
                audio_path = None

            # ── Step 5.5: Generate SRT subtitles ──
            srt_path = None
            if narration.strip():
                try:
                    srt_path = str(temp_dir / "subtitles.srt")
                    _generate_srt(narration, srt_path)
                    print(f"   📝 Subtitles generated")
                except Exception as e:
                    print(f"   ⚠️  SRT error: {e}")

            # ── Step 6: Assemble video (ffmpeg) ──
            final_path = str(temp_dir / "final.mp4")
            if audio_path and Path(audio_path).exists():
                print(f"   🎬 Assembling video with audio + subtitles...")
                has_subs = srt_path and Path(srt_path).exists()
                vf = f"subtitles={srt_path}:force_style='FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=1.5'" if has_subs else None
                cmd = ["ffmpeg", "-y",
                    "-i", recording_path,
                    "-i", audio_path,
                ]
                if vf:
                    cmd += ["-vf", vf]
                cmd += [
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    "-shortest",
                    final_path,
                ]
            else:
                print(f"   🎬 Converting video with subtitles (no audio)...")
                has_subs = srt_path and Path(srt_path).exists()
                vf = f"subtitles={srt_path}:force_style='FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=1.5'" if has_subs else None
                cmd = ["ffmpeg", "-y",
                    "-i", recording_path,
                ]
                if vf:
                    cmd += ["-vf", vf]
                cmd += [
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-an",
                    final_path,
                ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode() if stderr else "unknown"
                print(f"   ⚠️  ffmpeg error: {err[:200]}")
                # Fallback: simple convert without subtitles
                print(f"   🔄 Retrying without subtitles...")
                fallback = ["ffmpeg", "-y", "-i", recording_path, "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an", final_path]
                fp = await asyncio.create_subprocess_exec(*fallback, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, ferr = await fp.communicate()
                if fp.returncode != 0:
                    final_path = recording_path

            # ── Step 7: Upload to Worker/R2 ──
            print(f"   📤 Uploading video to R2...")
            with open(final_path, "rb") as f:
                await api(f"/api/jobs/{job_id}/video", "PUT", data=f.read())
            print(f"   ✅ Upload complete!")

            # ── Cleanup ──
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

            print(f"   🎉 Job {job_id} complete!")
            print(f"   📺 {WORKER_URL}/api/video/{job_id}")

        except Exception as e:
            print(f"   ❌ Job failed: {e}")
            await api(f"/api/jobs/{job_id}/status", "PUT", json_data={
                "status": "error",
                "error": str(e)[:500],
            })
            try:
                await context.close()
                await browser.close()
            except Exception:
                pass


def _generate_srt(text: str, output_path: str, chunk_secs: int = 4):
    """Generate an SRT subtitle file from narration text."""
    words = text.split()
    if not words:
        return

    # Split into chunks of ~10 words each
    words_per_chunk = max(1, len(words) // max(1, (len(words) + 9) // 10))
    chunks = []
    for i in range(0, len(words), words_per_chunk):
        chunk = " ".join(words[i:i + words_per_chunk])
        if chunk:
            chunks.append(chunk)

    def _fmt(sec: int) -> str:
        h, m = divmod(sec, 3600)
        m, s = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d},000"

    with open(output_path, "w") as f:
        for i, chunk in enumerate(chunks):
            start = i * chunk_secs
            end = start + chunk_secs
            f.write(f"{i + 1}\n")
            f.write(f"{_fmt(start)} --> {_fmt(end)}\n")
            f.write(f"{chunk}\n\n")


# ── Main loop ───────────────────────────────────────────
async def main():
    print("=" * 50)
    print("🔄 DemoAgent Runner started")
    print(f"   Worker: {WORKER_URL}")
    print("=" * 50)

    while True:
        try:
            # Check for login sessions first
            await handle_login_sessions()

            # Poll for jobs
            job = await api("/api/jobs/next")
            if job and job.get("id"):
                await process_job(job)
            else:
                # No jobs, wait and poll
                await asyncio.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n👋 Shutting down...")
            break
        except Exception as e:
            print(f"Main loop error: {e}")
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())

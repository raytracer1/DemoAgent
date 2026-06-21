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

        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=VIEWPORT)
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()

        try:
            print(f"   📄 Navigating to {url}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

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
            await browser.close()

            if not all_steps:
                raise Exception("No steps discovered")

            print(f"\n   ✅ Plan discovered: {len(all_steps)} steps total")
            for s in all_steps:
                print(f"      {s['index']+1}: {s['action']} → {s['target'][:60]}")

        except Exception as e:
            await context.close()
            await browser.close()
            raise e

    # ── Phase 2: Narration + TTS (before recording) ──
    await api(f"/api/jobs/{job_id}/status", "PUT", json_data={"status": "narrating"})
    print(f"   🎤 Generating narration...")
    narration_resp = await api(f"/api/jobs/{job_id}/narration", "POST")
    narration = narration_resp.get("narration", "")
    print(f"   📝 Narration: {narration[:80]}...")

    audio_path = str(temp_dir / "narration.mp3")
    subs_path = str(temp_dir / "narration.vtt")
    audio_duration = 30.0  # fallback
    per_step_pauses: list[float] = []  # exact pause per step from subtitle timing

    if narration.strip():
        try:
            print(f"   🔊 Generating TTS with word timestamps...")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "edge_tts",
                "--voice", "en-US-AriaNeural",
                "--text", narration,
                "--write-media", audio_path,
                "--write-subtitles", subs_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode == 0:
                adur_proc = await asyncio.create_subprocess_exec(
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                adur_out, _ = await adur_proc.communicate()
                audio_duration = float(adur_out.decode().strip())
                print(f"   ⏱️  TTS: {audio_duration:.1f}s")

                # Parse VTT subtitles to get per-step timing
                per_step_pauses = _parse_vtt_for_steps(subs_path, len(all_steps), audio_duration)
                print(f"   🕐 Per-step timing: {[f'{p:.1f}s' for p in per_step_pauses]}")
            else:
                print(f"   ⚠️  TTS failed"); audio_path = None
        except Exception as e:
            print(f"   ⚠️  TTS error: {e}"); audio_path = None
    else:
        audio_path = None

    # ── Phase 3: Execute with recording (paced by exact audio timing) ──
    if not per_step_pauses:
        per_step_pauses = [audio_duration / len(all_steps)] * len(all_steps) if all_steps else []
    print(f"   🎬 Recording with per-step audio timing...")

    video_dir = str(temp_dir / "recording")
    os.makedirs(video_dir, exist_ok=True)

    # ── Warm-up: preload page so recording starts on loaded page ──
    async with async_playwright() as p:
        warm_browser = await p.chromium.launch(headless=True)
        warm_ctx = await warm_browser.new_context(viewport=VIEWPORT)
        warm_page = await warm_ctx.new_page()
        await warm_page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await _wait_for_content_stable(warm_page)
        await warm_ctx.close()
        await warm_browser.close()

    # ── Phase 3: Record with preloaded cache ──
    async with async_playwright() as p:
        cookies = None
        if session_id:
            session_data = await api(f"/api/sessions/{session_id}")
            cookies_raw = session_data.get("cookies") if session_data else None
            if cookies_raw:
                try: cookies = json.loads(cookies_raw)
                except Exception: pass

        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=video_dir,
            record_video_size=VIEWPORT,
        )
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()

        try:
            # Navigate (fast — cached from warm-up)
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await _wait_for_content_stable(page)

            step_times: list[float] = []
            recording_start = time.time()

            for i, step in enumerate(all_steps):
                step_times.append(time.time() - recording_start)
                print(f"      Step {i+1}/{len(all_steps)}: {step.get('action')} → {step.get('target', '')[:60]}")
                await execute_step(page, step, i)
                # Pause for this step's narration segment duration
                pause = per_step_pauses[i] if i < len(per_step_pauses) else per_step_pauses[-1]
                pause *= 0.9  # slight overlap for natural flow
                print(f"      ⏸  Holding for {pause:.1f}s (narration for this step)")
                await asyncio.sleep(pause)

            await context.close()
            await browser.close()

            # Find recording
            video_files = sorted(Path(video_dir).glob("*.webm"))
            if not video_files:
                raise Exception("No recording found")
            recording_path = str(video_files[-1])
            print(f"   🎥 Recording: {recording_path}")

        except Exception as e:
            await context.close()
            await browser.close()
            raise e

    # ── Phase 4: SRT + Assembly ──
    srt_path = None
    if narration.strip():
        try:
            srt_path = str(temp_dir / "subtitles.srt")
            _generate_srt(narration, srt_path, duration=audio_duration, step_times=step_times)
            print(f"   📝 Subtitles (synced to {audio_duration:.1f}s TTS)")
        except Exception as e:
            print(f"   ⚠️  SRT error: {e}")

    final_path = str(temp_dir / "final.mp4")
    has_subs = srt_path and Path(srt_path).exists()
    has_audio = audio_path and Path(audio_path).exists()

    if has_audio:
        print(f"   🎬 Assembling video + audio + subtitles...")
        cmd = ["ffmpeg", "-y", "-i", recording_path, "-i", audio_path]
        if has_subs:
            cmd += ["-vf", f"subtitles={srt_path}:force_style='FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=1.5'"]
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", "-b:a", "128k", final_path]
    else:
        print(f"   🎬 Converting video + subtitles...")
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


async def _wait_for_content_stable(page, timeout: int = 30, interval: float = 0.8, stable_count: int = 20):
    """Wait until interactive elements stop appearing — API content has rendered."""
    start = time.time()
    last_count = -1
    stable = 0
    while time.time() - start < timeout:
        try:
            # Count interactive elements (buttons, links, inputs)
            btn = await page.locator("button:visible").count()
            link = await page.locator("a:visible").count()
            inp = await page.locator("input:visible, textarea:visible, select:visible").count()
            total = btn + link + inp
            if total == last_count and total > 0:
                stable += 1
                if stable >= stable_count:
                    return  # DOM settled with real elements
            else:
                stable = 0
                last_count = total
        except Exception:
            pass
        await asyncio.sleep(interval)
    # timeout — proceed anyway


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

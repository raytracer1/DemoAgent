chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'startDemo') {
    runDemo(msg).then(sendResponse).catch(e => sendResponse({ error: e.message }));
    return true;
  }
});

async function runDemo({ tabId, goal, streamId, deepseekKey }) {
  // ── Step 1: Read DOM from content script ──
  const elements = await sendToTab(tabId, { action: 'extractElements' });

  // ── Step 2: LLM plan + narration in parallel ──
  const [plan, narration] = await Promise.all([
    generatePlan(elements, goal, deepseekKey),
    generateNarration(goal, deepseekKey),
  ]);

  // ── Step 3: Create offscreen for recording ──
  await createOffscreen();
  await chrome.runtime.sendMessage({ action: 'startRecording', streamId, narration });

  // ── Step 4: Execute steps on target tab ──
  for (const step of plan) {
    await sendToTab(tabId, { action: 'execute', step });
    await sleep(2000);
  }

  // ── Step 5: Stop recording, get blob ──
  const { blob } = await chrome.runtime.sendMessage({ action: 'stopRecording' });
  await closeOffscreen();

  // ── Step 6: Upload to R2 ──
  const id = crypto.randomUUID();
  const workerUrl = 'https://demo-agent-worker.zhengbijun123.workers.dev';
  const videoBlob = await (await fetch(blob)).blob();
  await fetch(`${workerUrl}/api/jobs/${id}/video`, {
    method: 'PUT',
    headers: { 'Content-Type': 'video/webm' },
    body: videoBlob,
  });

  return { videoUrl: `${workerUrl}/api/video/${id}` };
}

async function sendToTab(tabId, msg) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, msg, (resp) => {
      chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve(resp);
    });
  });
}

let _offscreenReady = false;
async function createOffscreen() {
  if (_offscreenReady) return;
  // Check if already exists
  const existing = await chrome.runtime.getContexts({
    contextTypes: [chrome.runtime.ContextType.OFFSCREEN_DOCUMENT],
  });
  if (existing.length > 0) { _offscreenReady = true; return; }

  await chrome.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: [chrome.offscreen.Reason.USER_MEDIA],
    justification: 'tabCapture recording',
  });
  _offscreenReady = true;
}

async function closeOffscreen() {
  try { await chrome.offscreen.closeDocument(); } catch(e) {}
  _offscreenReady = false;
}

async function generatePlan(elements, goal, apiKey) {
  const text = elements.map(e => `[${e.id}] ${e.tag} "${e.text}"`).join('\n');
  return callLLM(apiKey, [
    { role: 'system', content: 'Browser automation. Output ONLY JSON array: [{"id":<id>,"action":"click|type|select","value":"optional"}]. No other text.' },
    { role: 'user', content: `Elements:\n${text.slice(0, 5000)}\n\nGoal: ${goal}\nGenerate actions:` },
  ]).then(r => { const m = (r||'').match(/\[[\s\S]*?\]/); return m ? JSON.parse(m[0]) : []; });
}

async function generateNarration(goal, apiKey) {
  return callLLM(apiKey, [
    { role: 'system', content: 'Marketing copywriter. Write 30-45s product demo voiceover (75-100 words). Exciting tone. Plain text.' },
    { role: 'user', content: `Goal: ${goal}\nWrite voiceover:` },
  ]);
}

async function callLLM(apiKey, messages) {
  const resp = await fetch('https://api.deepseek.com/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` },
    body: JSON.stringify({ model: 'deepseek-chat', messages, temperature: 0.3, max_tokens: 2000 }),
  });
  const data = await resp.json();
  return data.choices[0].message.content?.trim() || '';
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

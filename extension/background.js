console.log("DemoAgent background started");
let state = { recording: false, status: '', videoUrl: null };

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'getState') { sendResponse(state); }
  else if (msg.action === 'startDemo') {
    runDemo(msg).catch(e => {
      console.error('runDemo failed:', e);
      state = { recording: false, status: 'Failed: ' + e.message, videoUrl: null };
      updateBadge();
    });
    sendResponse({ ok: true });
  } else if (msg.action === 'stopDemo') {
    stopEarly = true;
    sendResponse({ ok: true });
  }
  return true;
});

function updateBadge() {
  if (state.recording) {
    chrome.action.setBadgeText({ text: '●' });
    chrome.action.setBadgeBackgroundColor({ color: '#ff4757' });
  } else {
    chrome.action.setBadgeText({ text: '' });
  }
}

let stopEarly = false;

async function runDemo({ tabId, goal, streamId, deepseekKey }) {
  console.log("runDemo starting", { tabId, goal });
  stopEarly = false;
  state = { recording: true, status: 'Extracting...', videoUrl: null };
  updateBadge();

  try { await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] }); }
  catch (e) { console.log('Inject:', e.message); }

  let elements = await sendToTab(tabId, { action: 'extractElements' });
  console.log('Extracted:', elements?.length);

  const allSteps = [];
  for (let r = 1; r <= 5 && !stopEarly; r++) {
    const plan = await generatePlan(elements, goal, deepseekKey, allSteps);
    const f = plan.filter(s => !['sign in','login','google','auth','allow','wait','scroll'].some(k => (s.action||'').includes(k) || (s.text||'').toLowerCase().includes(k)));
    if (!f.length) break;
    let ch = false;
    for (const s of f) {
      if (stopEarly) break;
      console.log(`Step: ${s.action} "${s.text}"`);
      state.status = `${s.action} "${(s.text||'').slice(0,30)}"`;
      const pre = (await sendToTab(tabId, { action: 'getUrl' })) || '';
      await sendToTab(tabId, { action: 'execute', step: s });
      allSteps.push(s);
      await sleep(2000);
      console.log(`URL: ${pre} -> checking...`);
      if (((await sendToTab(tabId, { action: 'getUrl' })) || '') !== pre) { ch = true; break; }
    }
    if (stopEarly) break;
    if (ch) elements = await sendToTab(tabId, { action: 'extractElements' });
  }
  if (stopEarly) { state = { recording: false, status: 'Stopped', videoUrl: null }; updateBadge(); return; }
  if (!allSteps.length) { state = { recording: false, status: 'No steps', videoUrl: null }; updateBadge(); return; }
  console.log(`Plan: ${allSteps.length} steps`);

  // Re-execute for recording
  for (let i = 0; i < allSteps.length && !stopEarly; i++) {
    state.status = `Rec ${i+1}/${allSteps.length}`;
    await sendToTab(tabId, { action: 'execute', step: allSteps[i] });
    await sleep(2000);
  }

  state = { recording: false, status: 'Complete', videoUrl: null };
  updateBadge();
  console.log('Done');
}

async function sendToTab(tabId, msg) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, msg, (resp) => {
      chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve(resp);
    });
  });
}

async function generatePlan(elements, goal, apiKey, history=[]) {
  const text = elements.map(e => `[${e.id}] ${e.tag} "${e.text}"`).join('\n');
  const done = history.length ? `Done: ${JSON.stringify(history.map(s=>({a:s.action,t:s.text})))}\nDo NOT repeat.` : '';
  const resp = await callDeepSeek(apiKey, [
    { role:'system', content:'Output ONLY JSON array: [{"text":"<exact>","action":"click|type|select","value":"optional"}]. text MUST match element text. Fill forms, submit at end. Skip auth.' },
    { role:'user', content:`Elements:\n${text.slice(0,5000)}\nGoal: ${goal}\n${done}\nGenerate 2-4 actions:` },
  ]);
  try { const m = (resp||'').match(/\[[\s\S]*?\]/); return m ? JSON.parse(m[0]) : []; } catch { return []; }
}

async function generateNarration(goal, steps, apiKey) {
  return callDeepSeek(apiKey, [
    { role:'system', content:'Write 30-45s voiceover (75-100 words). Exciting tone. Plain text.' },
    { role:'user', content:`Goal: ${goal}\nSteps: ${steps.map((s,i)=>`Step ${i}: ${s.action} "${s.text}"`).join('\n')}\nWrite voiceover:` },
  ]);
}

async function callDeepSeek(apiKey, messages) {
  const resp = await fetch('https://api.deepseek.com/v1/chat/completions', {
    method:'POST',
    headers:{'Content-Type':'application/json','Authorization':`Bearer ${apiKey}`},
    body:JSON.stringify({model:'deepseek-chat',messages,temperature:0.3,max_tokens:2000}),
  });
  const data = await resp.json();
  return data.choices[0].message.content?.trim()||'';
}

function sleep(ms){return new Promise(r=>setTimeout(r,ms));}

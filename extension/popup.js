const idleView = document.getElementById('idleView');
const recordingView = document.getElementById('recordingView');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const statusEl = document.getElementById('status');
const apiKeyInput = document.getElementById('apiKeyInput');

let mediaRecorder = null, chunks = [];

// Load saved API key
chrome.storage.local.get('deepseekKey', (d) => { if (d.deepseekKey) apiKeyInput.value = d.deepseekKey; });

chrome.runtime.sendMessage({ action: 'getState' }, (state) => {
  if (state?.recording) { showRecording(); statusEl.textContent = state.status || 'Recording...'; }
});

startBtn.addEventListener('click', async () => {
  const goal = document.getElementById('goalInput').value.trim();
  const apiKey = apiKeyInput.value.trim();
  if (!goal) return;
  if (!apiKey) { statusEl.textContent = 'Enter API key'; return; }
  chrome.storage.local.set({ deepseekKey: apiKey });

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;

  try {
    const streamId = await new Promise((res, rej) => {
      chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id }, (id) => {
        chrome.runtime.lastError ? rej(chrome.runtime.lastError) : res(id);
      });
    });
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
      audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
    });
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9' });chunks = [];
    mediaRecorder.ondataavailable = e => chunks.push(e.data);mediaRecorder.start();

    chrome.runtime.sendMessage({ action: 'startDemo', tabId: tab.id, goal, deepseekKey: apiKey });
    showRecording();startPolling();
  } catch (e) { statusEl.textContent = '❌ ' + e.message; }
});

stopBtn.addEventListener('click', () => { chrome.runtime.sendMessage({ action: 'stopDemo' }); statusEl.textContent = 'Stopping...'; });

let _int = null;
function startPolling() {
  if (_int) clearInterval(_int);
  _int = setInterval(() => {
    chrome.runtime.sendMessage({ action: 'getState' }, (s) => {
      if (chrome.runtime.lastError) return;
      if (!s?.recording) { clearInterval(_int); stopAndUpload(); }
      else statusEl.textContent = s.status || 'Recording...';
    });
  }, 1000);
}

async function stopAndUpload() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.onstop = async () => {
      const blob = new Blob(chunks, { type: 'video/webm' });
      const reader = new FileReader();
      reader.onload = async () => {
        const id = crypto.randomUUID();
        const videoBlob = await (await fetch(reader.result)).blob();
        await fetch(`https://demo-agent-worker.zhengbijun123.workers.dev/api/jobs/${id}/video`, {
          method: 'PUT', headers: { 'Content-Type': 'video/webm' }, body: videoBlob,
        });
        chrome.tabs.create({ url: `https://demo-agent-jade.vercel.app/?video=${encodeURIComponent(`https://demo-agent-worker.zhengbijun123.workers.dev/api/video/${id}`)}` });
        recordingView.style.display = 'none';idleView.style.display = 'block';
      };
      reader.readAsDataURL(blob);
    };
    mediaRecorder.stop();mediaRecorder.stream.getTracks().forEach(t=>t.stop());
  }
}

function showRecording() { idleView.style.display = 'none';recordingView.style.display = 'block'; }

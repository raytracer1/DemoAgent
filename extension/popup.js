const idleView = document.getElementById('idleView');
const recordingView = document.getElementById('recordingView');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const statusEl = document.getElementById('status');

let mediaRecorder = null;
let chunks = [];

chrome.runtime.sendMessage({ action: 'getState' }, (state) => {
  if (state?.recording) { showRecording(); statusEl.textContent = state.status || 'Recording...'; }
});

startBtn.addEventListener('click', async () => {
  const goal = document.getElementById('goalInput').value.trim();
  if (!goal) return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;

  try {
    // Get capture stream in popup (has user gesture)
    const streamId = await new Promise((res, rej) => {
      chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id }, (id) => {
        chrome.runtime.lastError ? rej(chrome.runtime.lastError) : res(id);
      });
    });

    const stream = await navigator.mediaDevices.getUserMedia({
      video: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
      audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
    });

    mediaRecorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9' });
    chunks = [];
    mediaRecorder.ondataavailable = e => chunks.push(e.data);
    mediaRecorder.start();
    console.log('Recording started');

    // Tell background to start automation
    chrome.runtime.sendMessage({
      action: 'startDemo',
      tabId: tab.id, goal, streamId,
      deepseekKey: 'DEEPSEEK_API_KEY_PLACEHOLDER',
    });

    showRecording();
    startPolling();
  } catch (e) {
    statusEl.textContent = '❌ ' + e.message;
    console.error('Start error:', e);
  }
});

stopBtn.addEventListener('click', () => {
  chrome.runtime.sendMessage({ action: 'stopDemo' });
  statusEl.textContent = 'Stopping...';
});

let _interval = null;
function startPolling() {
  if (_interval) clearInterval(_interval);
  _interval = setInterval(() => {
    chrome.runtime.sendMessage({ action: 'getState' }, (state) => {
      if (chrome.runtime.lastError) return;
      if (!state?.recording) {
        clearInterval(_interval);
        // Stop recording, upload
        stopAndUpload(state);
      } else {
        statusEl.textContent = state.status || 'Recording...';
      }
    });
  }, 1000);
}

async function stopAndUpload(state) {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.onstop = async () => {
      const blob = new Blob(chunks, { type: 'video/webm' });
      const reader = new FileReader();
      reader.onload = async () => {
        const base64 = reader.result;
        const id = crypto.randomUUID();
        const workerUrl = 'https://demo-agent-worker.zhengbijun123.workers.dev';
        const videoBlob = await (await fetch(base64)).blob();
        await fetch(`${workerUrl}/api/jobs/${id}/video`, {
          method: 'PUT', headers: { 'Content-Type': 'video/webm' }, body: videoBlob,
        });
        const videoUrl = `${workerUrl}/api/video/${id}`;
        if (state?.videoUrl || state?.status === 'Complete' || true) {
          chrome.tabs.create({ url: "https://demo-agent-jade.vercel.app/?video=" + encodeURIComponent(videoUrl) });
        }
        recordingView.style.display = 'none';
        idleView.style.display = 'block';
      };
      reader.readAsDataURL(blob);
    };
    mediaRecorder.stop();
    mediaRecorder.stream.getTracks().forEach(t => t.stop());
  }
}

function showRecording() {
  idleView.style.display = 'none';
  recordingView.style.display = 'block';
}

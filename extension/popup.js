const startBtn = document.getElementById('startBtn');
const statusEl = document.getElementById('status');
const videoPlayer = document.getElementById('videoPlayer');

startBtn.addEventListener('click', async () => {
  startBtn.disabled = true;
  const goal = document.getElementById('goalInput').value.trim();
  if (!goal) { startBtn.disabled = false; return; }
  setStatus('🤖 Running demo...');

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) throw new Error('No active tab');

    const streamId = await new Promise((res, rej) => {
      chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id }, (id) => {
        chrome.runtime.lastError ? rej(chrome.runtime.lastError) : res(id);
      });
    });

    const resp = await chrome.runtime.sendMessage({
      action: 'startDemo',
      tabId: tab.id,
      goal,
      streamId,
      deepseekKey: 'DEEPSEEK_API_KEY_PLACEHOLDER',
    });

    if (resp?.videoUrl) {
      videoPlayer.src = resp.videoUrl;
      videoPlayer.style.display = 'block';
      setStatus('✅ Complete!');
    } else {
      setStatus('❌ ' + (resp?.error || 'Failed'));
    }
  } catch (e) {
    setStatus('❌ ' + e.message);
  }
  startBtn.disabled = false;
});

function setStatus(msg) { statusEl.textContent = msg; }

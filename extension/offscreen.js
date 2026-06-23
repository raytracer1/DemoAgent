chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'startRecording') {
    startRecording(msg.streamId, msg.narration).then(sendResponse);
    return true;
  }
  if (msg.action === 'stopRecording') {
    stopRecording().then(sendResponse);
    return true;
  }
});

let mediaRecorder = null;
let chunks = [];

async function startRecording(streamId, narration) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: {
        mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId },
      },
      audio: {
        mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId },
      },
    });

    mediaRecorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9' });
    chunks = [];
    mediaRecorder.ondataavailable = e => chunks.push(e.data);
    mediaRecorder.start();

    if (narration) {
      const utterance = new SpeechSynthesisUtterance(narration);
      utterance.rate = 0.9;
      speechSynthesis.speak(utterance);
    }

    return { ok: true };
  } catch (e) {
    return { error: e.message };
  }
}

async function stopRecording() {
  if (!mediaRecorder) return { error: 'not recording' };
  return new Promise(resolve => {
    mediaRecorder.onstop = () => {
      const blob = new Blob(chunks, { type: 'video/webm' });
      const reader = new FileReader();
      reader.onload = () => resolve({ blob: reader.result });
      reader.readAsDataURL(blob);
    };
    mediaRecorder.stop();
    mediaRecorder.stream.getTracks().forEach(t => t.stop());
  });
}

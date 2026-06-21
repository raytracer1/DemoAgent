const WORKER = process.env.NEXT_PUBLIC_WORKER_URL || 'https://demo-agent-worker.zhengbijun123.workers.dev';

export async function createSession(url: string, cookies?: string) {
  const resp = await fetch(`${WORKER}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, cookies: cookies || undefined }),
  });
  return resp.json();
}

export async function createJob(url: string, goal: string, sessionId?: string | null) {
  const resp = await fetch(`${WORKER}/api/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, goal, session_id: sessionId || null }),
  });
  return resp.json();
}

export async function getJob(jobId: string) {
  const resp = await fetch(`${WORKER}/api/jobs/${jobId}`);
  return resp.json();
}

export function videoUrl(jobId: string) {
  return `${WORKER}/api/video/${jobId}`;
}

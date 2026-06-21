'use client';

import { useState, useRef, useCallback } from 'react';
import { getJob, videoUrl } from '@/lib/api';

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8765';
const WORKER = process.env.NEXT_PUBLIC_WORKER_URL || 'https://demo-agent-worker.zhengbijun123.workers.dev';

export default function Home() {
  const [url, setUrl] = useState('https://i-was-there-psi.vercel.app/');
  const [goal, setGoal] = useState('create a video in world cup 2026');
  const [status, setStatus] = useState('');
  const [statusType, setStatusType] = useState<'idle' | 'active' | 'done' | 'error'>('idle');
  const [jobId, setJobId] = useState<string | null>(null);
  const [narration, setNarration] = useState('');
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(false);
  const [browserLoading, setBrowserLoading] = useState(false);
  const [browserUrl, setBrowserUrl] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval>>();
  const wsRef = useRef<WebSocket>();
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const connectBrowser = useCallback(() => {
    if (!url.trim()) return;
    setBrowserLoading(true);
    setStatus('Connecting...');
    setStatusType('active');

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;
    const img = new Image();

    img.onload = () => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      canvas.getContext('2d')?.drawImage(img, 0, 0);
      setBrowserLoading(false);
    };

    ws.onopen = () => ws.send(JSON.stringify({ action: 'connect', url: url.trim() }));

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'ready') {
        setConnected(true); setBrowserUrl(msg.url); setStatus(''); setStatusType('idle');
      } else if (msg.type === 'frame' && msg.data) {
        img.src = 'data:image/jpeg;base64,' + msg.data;
      } else if (msg.type === 'url_changed') {
        setBrowserUrl(msg.url);
      } else if (msg.type === 'demo_ready') {
        setConnected(false); ws.close(); triggerDemo(msg.cookies, msg.url);
      } else if (msg.type === 'error') {
        setError(msg.message); setStatusType('error');
      }
    };

    ws.onclose = () => setConnected(false);
  }, [url]);

  const sendClick = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!wsRef.current || !connected || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const x = Math.round((e.clientX - rect.left) * (1280 / rect.width));
    const y = Math.round((e.clientY - rect.top) * (720 / rect.height));
    wsRef.current.send(JSON.stringify({ action: 'click', x, y }));
  }, [connected]);

  const startDemo = useCallback(() => {
    if (!wsRef.current || !connected) return;
    wsRef.current.send(JSON.stringify({ action: 'start_demo' }));
    setStatus('🤖 AI taking over...'); setStatusType('active');
  }, [connected]);

  const triggerDemo = useCallback(async (cookiesStr: string, siteUrl: string) => {
    setStatus('Creating job...');
    try {
      const r1 = await fetch(`${WORKER}/api/sessions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: siteUrl, cookies: cookiesStr }),
      });
      const { session_id } = await r1.json();

      const r2 = await fetch(`${WORKER}/api/jobs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: siteUrl, goal: goal.trim(), session_id }),
      });
      const { job_id } = await r2.json();
      setJobId(job_id);

      pollRef.current = setInterval(async () => {
        try {
          const job = await getJob(job_id);
          if (job.status === 'extracting') { setStatus('📄 Extracting elements...'); setStatusType('active'); }
          else if (job.status === 'planning') { setStatus('🧠 AI planning...'); setStatusType('active'); }
          else if (job.status === 'ready') { setStatus('▶️  Executing...'); setStatusType('active'); }
          else if (job.status === 'running') { setStatus('🎥 Recording...'); setStatusType('active'); }
          else if (job.status === 'narrating') { setStatus('🎤 Generating narration...'); setStatusType('active'); }
          else if (job.status === 'done') {
            clearInterval(pollRef.current);
            setStatus('✅ Complete'); setStatusType('done');
            if (job.narration) setNarration(job.narration);
          } else if (job.status === 'error') {
            clearInterval(pollRef.current);
            setStatus('❌ Failed'); setStatusType('error');
            setError(job.error || 'Unknown');
          }
        } catch {}
      }, 2000);
    } catch (e: any) { setError(e.message); setStatusType('error'); }
  }, [goal]);

  const dotColors = { active: 'bg-purple-500 animate-pulse', done: 'bg-emerald-400', error: 'bg-red-500', idle: 'bg-neutral-600' };

  return (
    <main className="max-w-[1320px] mx-auto px-5 py-10">
      <h1 className="text-[28px] font-bold text-white mb-1">🎬 DemoAgent</h1>
      <p className="text-[#888] text-[15px] mb-8">Connect to a website, log in, then AI generates your demo video</p>

      {/* Input Row */}
      <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-xl p-6 mb-5">
        <div className="flex gap-3 items-center">
          <input
            className="flex-[2] px-3.5 py-2.5 text-[15px] bg-[#111] border border-[#333] rounded-lg text-[#e0e0e0] outline-none focus:border-purple-500"
            type="url" value={url} onChange={e => setUrl(e.target.value)} placeholder="Target URL"
          />
          <input
            className="flex-[2] px-3.5 py-2.5 text-[15px] bg-[#111] border border-[#333] rounded-lg text-[#e0e0e0] outline-none focus:border-purple-500"
            type="text" value={goal} onChange={e => setGoal(e.target.value)} placeholder="Demo goal"
          />
          {!connected ? (
            <button className="shrink-0 px-5 py-2.5 text-sm font-semibold rounded-lg bg-purple-600 text-white hover:bg-purple-500" onClick={connectBrowser}>🔗 Connect</button>
          ) : (
            <button className="shrink-0 px-5 py-2.5 text-sm font-semibold rounded-lg bg-emerald-500 text-black hover:bg-emerald-400" onClick={startDemo}>🚀 Start Demo</button>
          )}
        </div>
        {connected && <p className="mt-2.5 text-[13px] text-emerald-400">● Connected — {browserUrl}</p>}
      </div>

      {/* Remote Browser */}
      <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-xl p-6 mb-5">
        <h2 className="text-base text-[#aaa] uppercase tracking-wide mb-3.5">
          {connected ? '🌐 Remote Browser (click to interact)' : '🌐 Browser Preview'}
        </h2>
        {browserLoading && (
          <div className="py-16 text-center text-sm text-[#555] bg-[#111] rounded-lg border border-dashed border-[#2a2a2a]">
            ⏳ Launching remote browser... this may take a few seconds
          </div>
        )}
        {!connected && !browserLoading && !status && (
          <div className="py-16 text-center text-sm text-[#555] bg-[#111] rounded-lg border border-dashed border-[#2a2a2a]">
            Click "Connect" to open a remote browser session
          </div>
        )}
        <canvas
          ref={canvasRef}
          onClick={sendClick}
          className={`w-full rounded-lg border border-[#2a2a2a] cursor-crosshair ${connected && !browserLoading ? '' : 'hidden'}`}
        />
      </div>

      {/* Status & Results */}
      <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-xl p-6 mb-5">
        {status && (
          <div className="flex items-center gap-2 px-3.5 py-2.5 bg-[#111] border border-[#333] rounded-lg text-sm">
            <span className={`w-2 h-2 rounded-full shrink-0 ${dotColors[statusType]}`} />
            <span>{status}</span>
          </div>
        )}
        {error && <div className="mt-3.5 p-3.5 bg-red-950/30 border border-red-900/50 rounded-lg text-sm text-red-400">❌ {error}</div>}
        {jobId && statusType === 'done' && (
          <div className="mt-4"><video className="w-full rounded-lg border border-[#2a2a2a]" src={videoUrl(jobId)} controls /></div>
        )}
        {narration && <div className="mt-3.5 p-3.5 bg-[#111] border border-[#2a2a2a] rounded-lg text-sm leading-relaxed text-[#bbb] whitespace-pre-wrap">📝 {narration}</div>}
      </div>
    </main>
  );
}

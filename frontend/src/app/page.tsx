'use client';

import { useState, useRef, useCallback } from 'react';
import { getJob, videoUrl } from '@/lib/api';

const WORKER = process.env.NEXT_PUBLIC_WORKER_URL || 'https://demo-agent-worker.zhengbijun123.workers.dev';

export default function Home() {
  const [url, setUrl] = useState('https://i-was-there-psi.vercel.app/');
  const [goal, setGoal] = useState('create a video in world cup 2026');
  const [status, setStatus] = useState('');
  const [statusType, setStatusType] = useState<'idle' | 'active' | 'done' | 'error'>('idle');
  const [jobId, setJobId] = useState<string | null>(null);
  const [narration, setNarration] = useState('');
  const [error, setError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval>>();

  const doGenerate = useCallback(async () => {
    if (!url.trim() || !goal.trim()) return;
    setStatus('Creating job...'); setStatusType('active');
    setError(''); setJobId(null); setNarration('');

    try {
      const r1 = await fetch(`${WORKER}/api/sessions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim() }),
      });
      const { session_id } = await r1.json();

      const r2 = await fetch(`${WORKER}/api/jobs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim(), goal: goal.trim(), session_id }),
      });
      const { job_id } = await r2.json();
      setJobId(job_id);

      pollRef.current = setInterval(async () => {
        try {
          const job = await getJob(job_id);
          if (job.status === 'extracting') { setStatus('📄 Extracting page elements...'); setStatusType('active'); }
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
  }, [url, goal]);

  const dotColors = { active: 'bg-purple-500 animate-pulse', done: 'bg-emerald-400', error: 'bg-red-500', idle: 'bg-neutral-600' };

  return (
    <main className="max-w-[720px] mx-auto px-5 py-10">
      <h1 className="text-[28px] font-bold text-white mb-1">🎬 DemoAgent</h1>
      <p className="text-[#888] text-[15px] mb-8">Enter a URL and goal — AI generates your product demo video</p>

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
          <button
            className="shrink-0 px-5 py-2.5 text-sm font-semibold rounded-lg bg-purple-600 text-white hover:bg-purple-500 disabled:opacity-50"
            onClick={doGenerate} disabled={statusType === 'active'}
          >
            {statusType === 'active' ? '⏳ ...' : '🚀 Generate'}
          </button>
        </div>
        {status && (
          <div className="flex items-center gap-2 px-3.5 py-2.5 bg-[#111] border border-[#333] rounded-lg text-sm mt-3">
            <span className={`w-2 h-2 rounded-full shrink-0 ${dotColors[statusType]}`} />
            <span>{status}</span>
          </div>
        )}
        {error && <div className="mt-3 p-3.5 bg-red-950/30 border border-red-900/50 rounded-lg text-sm text-red-400">❌ {error}</div>}
        {narration && <div className="mt-3 p-3.5 bg-[#111] border border-[#2a2a2a] rounded-lg text-sm leading-relaxed text-[#bbb] whitespace-pre-wrap">📝 {narration}</div>}
        {jobId && statusType === 'done' && (
          <div className="mt-4"><video className="w-full rounded-lg border border-[#2a2a2a]" src={videoUrl(jobId)} controls /></div>
        )}
      </div>
    </main>
  );
}

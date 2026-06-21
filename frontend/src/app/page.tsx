'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import { createJob, getJob, videoUrl } from '@/lib/api';

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
  const [browserUrl, setBrowserUrl] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval>>();
  const wsRef = useRef<WebSocket>();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  // ── Remote Browser ──
  const connectBrowser = useCallback(() => {
    if (!url.trim()) return;
    setStatus('连接远程浏览器...');
    setStatusType('active');

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;
    imgRef.current = new Image();

    imgRef.current.onload = () => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      canvas.width = imgRef.current!.naturalWidth;
      canvas.height = imgRef.current!.naturalHeight;
      ctx.drawImage(imgRef.current!, 0, 0);
    };

    ws.onopen = () => {
      ws.send(JSON.stringify({ action: 'connect', url: url.trim() }));
    };

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'ready') {
        setConnected(true);
        setBrowserUrl(msg.url);
        setStatus('');
        setStatusType('idle');
      } else if (msg.type === 'frame') {
        if (imgRef.current && msg.data) {
          imgRef.current.src = 'data:image/jpeg;base64,' + msg.data;
        }
      } else if (msg.type === 'url_changed') {
        setBrowserUrl(msg.url);
      } else if (msg.type === 'demo_ready') {
        setConnected(false);
        ws.close();
        // Trigger demo generation with cookies
        triggerDemo(msg.cookies, msg.url);
      } else if (msg.type === 'error') {
        setError(msg.message);
        setStatusType('error');
      }
    };

    ws.onclose = () => setConnected(false);
  }, [url]);

  const sendClick = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!wsRef.current || !connected) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const scaleX = 1280 / rect.width;
    const scaleY = 720 / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);
    wsRef.current.send(JSON.stringify({ action: 'click', x, y }));
  }, [connected]);

  const startDemo = useCallback(() => {
    if (!wsRef.current || !connected) return;
    wsRef.current.send(JSON.stringify({ action: 'start_demo' }));
    setStatus('🤖 AI 接管中...');
    setStatusType('active');
  }, [connected]);

  const triggerDemo = useCallback(async (cookiesStr: string, siteUrl: string) => {
    setStatus('创建任务...');
    try {
      // Save session with cookies
      const r1 = await fetch(`${WORKER}/api/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: siteUrl, cookies: cookiesStr }),
      });
      const { session_id } = await r1.json();

      // Create job
      const r2 = await fetch(`${WORKER}/api/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: siteUrl, goal: goal.trim(), session_id }),
      });
      const { job_id } = await r2.json();
      setJobId(job_id);

      // Poll for completion
      pollRef.current = setInterval(async () => {
        try {
          const job = await getJob(job_id);
          if (job.status === 'extracting') { setStatus('📄 提取页面元素...'); setStatusType('active'); }
          else if (job.status === 'planning') { setStatus('🧠 AI 规划步骤...'); setStatusType('active'); }
          else if (job.status === 'ready') { setStatus('▶️ 执行操作...'); setStatusType('active'); }
          else if (job.status === 'running') { setStatus('🎥 录制中...'); setStatusType('active'); }
          else if (job.status === 'narrating') { setStatus('🎤 生成旁白...'); setStatusType('active'); }
          else if (job.status === 'done') {
            clearInterval(pollRef.current);
            setStatus('✅ 完成');
            setStatusType('done');
            if (job.narration) setNarration(job.narration);
          } else if (job.status === 'error') {
            clearInterval(pollRef.current);
            setStatus('❌ 失败');
            setStatusType('error');
            setError(job.error || 'Unknown');
          }
        } catch {}
      }, 2000);
    } catch (e: any) {
      setError(e.message);
      setStatusType('error');
    }
  }, [goal]);

  const dotClass = statusType === 'active' ? 'dot active' : statusType === 'done' ? 'dot done' : statusType === 'error' ? 'dot error' : 'dot';

  return (
    <div style={styles.container}>
      <h1 style={styles.title}>🎬 DemoAgent</h1>
      <p style={styles.sub}>Connect to a website, log in, then AI generates your demo video</p>

      <div style={styles.card}>
        <label style={styles.label}>Target Website URL</label>
        <input style={styles.input} type="url" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://example.com" />
        <div style={styles.btnRow}>
          {!connected ? (
            <button style={styles.btnPrimary} onClick={connectBrowser}>🔗 Connect Account</button>
          ) : (
            <button style={styles.btnSuccess} onClick={startDemo}>🚀 Start Demo</button>
          )}
          {connected && <span style={{ ...styles.hint, color: '#00d2a0' }}>● Connected — {browserUrl}</span>}
        </div>
      </div>

      {/* Remote Browser View */}
      <div style={styles.card}>
        <h2 style={styles.cardTitle}>{connected ? '🌐 Remote Browser (click to interact)' : '🌐 Browser Preview'}</h2>
        <canvas
          ref={canvasRef}
          onClick={sendClick}
          style={{ ...styles.canvas, display: connected ? 'block' : 'none' }}
        />
        {!connected && !status && (
          <div style={styles.placeholder}>
            Click "Connect Account" to open a remote browser session
          </div>
        )}
      </div>

      {/* Demo Goal + Results */}
      <div style={styles.card}>
        <h2 style={styles.cardTitle}>Demo Goal</h2>
        <input style={styles.input} type="text" value={goal} onChange={e => setGoal(e.target.value)} placeholder="What should the demo show?" />
        {status && (
          <div style={styles.statusBar}>
            <span className={dotClass} />
            <span>{status}</span>
          </div>
        )}
        {error && <div style={styles.errorBox}>❌ {error}</div>}
        {jobId && statusType === 'done' && (
          <div style={styles.videoWrap}>
            <video style={styles.video} src={videoUrl(jobId)} controls />
          </div>
        )}
        {narration && <div style={styles.narrationBox}>📝 {narration}</div>}
      </div>

      <style jsx>{`
        .dot { width: 8px; height: 8px; border-radius: 50%; background: #555; flex-shrink: 0; display: inline-block; }
        .dot.active { background: #6c5ce7; animation: pulse 1.2s infinite; }
        .dot.done { background: #00d2a0; }
        .dot.error { background: #ff4757; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
      `}</style>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { maxWidth: 1320, margin: '0 auto', padding: '40px 20px' },
  title: { fontSize: 28, marginBottom: 4, color: '#fff' },
  sub: { color: '#888', fontSize: 15, marginBottom: 32 },
  card: { background: '#1a1a1a', border: '1px solid #2a2a2a', borderRadius: 12, padding: 24, marginBottom: 20 },
  cardTitle: { fontSize: 16, marginBottom: 14, color: '#aaa', textTransform: 'uppercase' as const, letterSpacing: 0.5 },
  label: { display: 'block', fontSize: 13, color: '#999', marginBottom: 6, marginTop: 12 },
  input: { width: '100%', padding: '10px 14px', fontSize: 15, background: '#111', border: '1px solid #333', borderRadius: 8, color: '#e0e0e0', outline: 'none' },
  btnRow: { display: 'flex', gap: 10, marginTop: 18, alignItems: 'center' },
  btnPrimary: { padding: '10px 22px', fontSize: 14, fontWeight: 600, border: 'none', borderRadius: 8, cursor: 'pointer', background: '#6c5ce7', color: '#fff' },
  btnSuccess: { padding: '10px 22px', fontSize: 14, fontWeight: 600, border: 'none', borderRadius: 8, cursor: 'pointer', background: '#00d2a0', color: '#000' },
  hint: { fontSize: 13, color: '#888' },
  canvas: { width: '100%', borderRadius: 8, border: '1px solid #2a2a2a', cursor: 'crosshair' },
  placeholder: { padding: 60, textAlign: 'center' as const, color: '#555', fontSize: 14, background: '#111', borderRadius: 8, border: '1px dashed #2a2a2a' },
  statusBar: { display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', background: '#111', border: '1px solid #333', borderRadius: 8, marginTop: 14, fontSize: 14 },
  errorBox: { marginTop: 14, padding: 14, background: '#1f0f0f', border: '1px solid #3f1f1f', borderRadius: 8, fontSize: 14, color: '#ff6b7a' },
  videoWrap: { marginTop: 16 },
  video: { width: '100%', borderRadius: 8, border: '1px solid #2a2a2a' },
  narrationBox: { marginTop: 14, padding: 14, background: '#111', border: '1px solid #2a2a2a', borderRadius: 8, fontSize: 14, lineHeight: 1.6, color: '#bbb', whiteSpace: 'pre-wrap' as const },
};

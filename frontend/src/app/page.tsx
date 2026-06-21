'use client';

import { useState, useRef, useCallback } from 'react';
import { createSession, createJob, getJob, videoUrl } from '@/lib/api';

export default function Home() {
  const [url, setUrl] = useState('https://i-was-there-psi.vercel.app/');
  const [goal, setGoal] = useState('create a video in world cup 2026');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const [statusType, setStatusType] = useState<'idle' | 'active' | 'done' | 'error'>('idle');
  const [jobId, setJobId] = useState<string | null>(null);
  const [narration, setNarration] = useState('');
  const [error, setError] = useState('');
  const [loginMessage, setLoginMessage] = useState('Runner 会自动打开浏览器，登录后自动保存');
  const pollRef = useRef<ReturnType<typeof setInterval>>();

  const doLogin = useCallback(async () => {
    if (!url.trim()) return;
    setLoginMessage('⏳ 等待 Runner 打开浏览器...');
    try {
      const data = await createSession(url);
      setSessionId(data.session_id);
      setLoginMessage('请在打开的浏览器中登录目标网站 (最多 120s)');
    } catch (e: any) {
      setLoginMessage(`❌ ${e.message}`);
    }
  }, [url]);

  const doGenerate = useCallback(async () => {
    if (!url.trim() || !goal.trim()) return;
    setStatus('创建任务...');
    setStatusType('active');
    setError('');
    setJobId(null);
    setNarration('');

    try {
      const { job_id } = await createJob(url, goal, sessionId);
      setJobId(job_id);

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
            setJobId(job_id);
            if (job.narration) setNarration(job.narration);
          } else if (job.status === 'error') {
            clearInterval(pollRef.current);
            setStatus('❌ 失败');
            setStatusType('error');
            setError(job.error || 'Unknown');
          }
        } catch {
          // retry next poll
        }
      }, 2000);
    } catch (e: any) {
      setStatus('❌ 失败');
      setStatusType('error');
      setError(e.message);
    }
  }, [url, goal, sessionId]);

  const dotClass = statusType === 'active' ? 'dot active' : statusType === 'done' ? 'dot done' : statusType === 'error' ? 'dot error' : 'dot';

  return (
    <div style={styles.container}>
      <h1 style={styles.title}>🎬 DemoAgent</h1>
      <p style={styles.sub}>输入 URL 和演示目标，自动生成产品演示视频</p>

      {/* Login */}
      <div style={styles.card}>
        <h2 style={styles.cardTitle}>1️⃣ 登录网站（如需要）</h2>
        <label style={styles.label}>目标网站 URL</label>
        <input style={styles.input} type="url" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://example.com" />
        <div style={styles.btnRow}>
          <button style={styles.btnSecondary} onClick={doLogin}>🔐 登录网站</button>
          <span style={styles.hint}>{loginMessage}</span>
        </div>
      </div>

      {/* Generate */}
      <div style={styles.card}>
        <h2 style={styles.cardTitle}>2️⃣ 生成演示</h2>
        <label style={styles.label}>演示目标</label>
        <input style={styles.input} type="text" value={goal} onChange={e => setGoal(e.target.value)} placeholder="例如：create a video in world cup 2026" />
        <div style={styles.btnRow}>
          <button style={styles.btnPrimary} onClick={doGenerate} disabled={statusType === 'active'}>
            {statusType === 'active' ? '⏳ 处理中...' : '🚀 生成 Demo'}
          </button>
        </div>
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
  container: { maxWidth: 680, margin: '0 auto', padding: '40px 20px' },
  title: { fontSize: 28, marginBottom: 4, color: '#fff' },
  sub: { color: '#888', fontSize: 15, marginBottom: 32 },
  card: { background: '#1a1a1a', border: '1px solid #2a2a2a', borderRadius: 12, padding: 24, marginBottom: 20 },
  cardTitle: { fontSize: 16, marginBottom: 14, color: '#aaa', textTransform: 'uppercase' as const, letterSpacing: 0.5 },
  label: { display: 'block', fontSize: 13, color: '#999', marginBottom: 6, marginTop: 12 },
  input: { width: '100%', padding: '10px 14px', fontSize: 15, background: '#111', border: '1px solid #333', borderRadius: 8, color: '#e0e0e0', outline: 'none' },
  btnRow: { display: 'flex', gap: 10, marginTop: 18, alignItems: 'center' },
  btnPrimary: { padding: '10px 22px', fontSize: 14, fontWeight: 600, border: 'none', borderRadius: 8, cursor: 'pointer', background: '#6c5ce7', color: '#fff' },
  btnSecondary: { padding: '10px 22px', fontSize: 14, fontWeight: 600, border: 'none', borderRadius: 8, cursor: 'pointer', background: '#2a2a2a', color: '#ccc' },
  hint: { fontSize: 13, color: '#888' },
  statusBar: { display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', background: '#111', border: '1px solid #333', borderRadius: 8, marginTop: 14, fontSize: 14 },
  errorBox: { marginTop: 14, padding: 14, background: '#1f0f0f', border: '1px solid #3f1f1f', borderRadius: 8, fontSize: 14, color: '#ff6b7a' },
  videoWrap: { marginTop: 16 },
  video: { width: '100%', borderRadius: 8, border: '1px solid #2a2a2a' },
  narrationBox: { marginTop: 14, padding: 14, background: '#111', border: '1px solid #2a2a2a', borderRadius: 8, fontSize: 14, lineHeight: 1.6, color: '#bbb', whiteSpace: 'pre-wrap' as const },
};

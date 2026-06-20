import { Hono } from 'hono';
import { cors } from 'hono/cors';
import OpenAI from 'openai';

// ── Types ──────────────────────────────────────────────
type Bindings = {
  DB: D1Database;
  VIDEOS: R2Bucket;
  LLM_API_KEY: string;
  LLM_BASE_URL: string;
  LLM_MODEL: string;
};

type Variables = {
  llm: OpenAI;
};

const app = new Hono<{ Bindings: Bindings; Variables: Variables }>();

app.use('*', cors());
app.use('*', async (c, next) => {
  c.set('llm', new OpenAI({ apiKey: c.env.LLM_API_KEY, baseURL: c.env.LLM_BASE_URL }));
  await next();
});

// ── SESSIONS ───────────────────────────────────────────

// POST /api/sessions — create login session
app.post('/api/sessions', async (c) => {
  const { url } = await c.req.json<{ url: string }>();
  const id = crypto.randomUUID();
  await c.env.DB.prepare(
    'INSERT INTO sessions (id, url, cookies, created_at) VALUES (?, ?, NULL, ?)'
  ).bind(id, url, Date.now()).run();
  return c.json({ session_id: id, status: 'pending_login', url });
});

// GET /api/sessions/next-pending — runner polls this
app.get('/api/sessions/next-pending', async (c) => {
  const row = await c.env.DB.prepare(
    'SELECT * FROM sessions WHERE cookies IS NULL ORDER BY created_at ASC LIMIT 1'
  ).first();
  return c.json(row || null);
});

// PUT /api/sessions/:id/cookies — store login cookies
app.put('/api/sessions/:id/cookies', async (c) => {
  const id = c.req.param('id');
  const { cookies } = await c.req.json<{ cookies: string }>();
  await c.env.DB.prepare('UPDATE sessions SET cookies = ? WHERE id = ?')
    .bind(typeof cookies === 'string' ? cookies : JSON.stringify(cookies), id).run();
  return c.json({ ok: true });
});

// ── JOBS ──────────────────────────────────────────────

// POST /api/jobs — create demo job (frontend)
app.post('/api/jobs', async (c) => {
  const { url, goal, session_id } = await c.req.json<{ url: string; goal: string; session_id?: string }>();
  const id = crypto.randomUUID();
  const now = Date.now();
  await c.env.DB.prepare(
    `INSERT INTO jobs (id, session_id, url, goal, status, created_at, updated_at)
     VALUES (?, ?, ?, ?, 'extracting', ?, ?)`
  ).bind(id, session_id || null, url, goal, now, now).run();
  return c.json({ job_id: id, status: 'extracting' });
});

// GET /api/jobs/next — runner polls for work to do
app.get('/api/jobs/next', async (c) => {
  const row = await c.env.DB.prepare(
    `SELECT * FROM jobs WHERE status IN ('extracting', 'ready', 'narrating')
     ORDER BY created_at ASC LIMIT 1`
  ).first();
  return c.json(row || null);
});

// GET /api/jobs/:id — get job detail (frontend polls)
app.get('/api/jobs/:id', async (c) => {
  const id = c.req.param('id');
  const row = await c.env.DB.prepare('SELECT * FROM jobs WHERE id = ?').bind(id).first();
  if (!row) return c.json({ error: 'not found' }, 404);
  // Parse JSON fields
  return c.json({
    ...row,
    plan: row.plan ? JSON.parse(row.plan as string) : null,
  });
});

// ── PLANNING — runner sends elements, Worker calls LLM ──

// PUT /api/jobs/:id/elements — runner uploads extracted page elements
app.put('/api/jobs/:id/elements', async (c) => {
  const id = c.req.param('id');
  const body = await c.req.json<{ url: string; elements: any }>();
  const llm = c.get('llm');
  const model = c.env.LLM_MODEL;

  const prompt = `You are a browser automation planner. Given a URL, user goal, and REAL interactive elements on the page, generate browser actions.

Return ONLY a JSON array. Each step: {"action": "navigate|click|type|select|wait|upload|scroll", "target": "element text/description", "value": "optional value to type or select"}

Maximum 10 steps. target MUST match actual element text from the page elements listed below.

=== PAGE ELEMENTS ===
${JSON.stringify(body.elements, null, 2)}

Generate the execution plan as a JSON array.`;

  try {
    // Fetch the job to get the goal
    const job = await c.env.DB.prepare('SELECT goal FROM jobs WHERE id = ?').bind(id).first();
    const goal = job?.goal || body.url;
    const resp = await llm.chat.completions.create({
      model,
      messages: [
        { role: 'system', content: prompt },
        { role: 'user', content: `URL: ${body.url}\nGoal: ${goal}\n\nGenerate the execution plan as a JSON array.` },
      ],
      temperature: 0.3,
      max_tokens: 2000,
    });

    const text = resp.choices[0].message.content || '';
    // Parse JSON from response
    const jsonMatch = text.match(/\[[\s\S]*\]/);
    const plan = jsonMatch ? JSON.parse(jsonMatch[0]) : [];

    await c.env.DB.prepare(
      `UPDATE jobs SET plan = ?, status = 'ready', updated_at = ? WHERE id = ?`
    ).bind(JSON.stringify(plan), Date.now(), id).run();

    return c.json({ plan });
  } catch (e: any) {
    await c.env.DB.prepare(
      `UPDATE jobs SET status = 'error', error = ?, updated_at = ? WHERE id = ?`
    ).bind(e.message, Date.now(), id).run();
    return c.json({ error: e.message }, 500);
  }
});

// ── NARRATION — Worker generates via LLM ──────────────

// POST /api/jobs/:id/narration — generate narration script
app.post('/api/jobs/:id/narration', async (c) => {
  const id = c.req.param('id');
  const job = await c.env.DB.prepare('SELECT * FROM jobs WHERE id = ?').bind(id).first();
  if (!job) return c.json({ error: 'not found' }, 404);

  const llm = c.get('llm');
  const plan = JSON.parse((job.plan as string) || '[]');
  const stepsDesc = plan.map((s: any, i: number) =>
    `Step ${i + 1}: ${s.action} → "${s.target}" ${s.value ? `(${s.value})` : ''}`
  ).join('\n');

  const resp = await llm.chat.completions.create({
    model: c.env.LLM_MODEL,
    messages: [
      {
        role: 'system',
        content: `You are a product marketing copywriter. Write a voiceover for a 30–45 second product demo video.
- Exciting, professional marketing tone
- About 75–100 words
- Describe what the user achieves
- Plain text, no formatting
Output only the narration script.`,
      },
      {
        role: 'user',
        content: `Product goal: ${job.goal}\n\nDemo steps:\n${stepsDesc}\n\nWrite the voiceover script.`,
      },
    ],
    temperature: 0.7,
    max_tokens: 500,
  });

  const narration = resp.choices[0].message.content?.trim() || '';
  await c.env.DB.prepare(
    `UPDATE jobs SET narration = ?, updated_at = ? WHERE id = ?`
  ).bind(narration, Date.now(), id).run();

  return c.json({ narration });
});

// ── STATUS (runner updates) ───────────────────────────

// PUT /api/jobs/:id/status — runner updates job status
app.put('/api/jobs/:id/status', async (c) => {
  const id = c.req.param('id');
  const { status, error } = await c.req.json<{ status: string; error?: string }>();
  await c.env.DB.prepare(
    `UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?`
  ).bind(status, error || null, Date.now(), id).run();
  return c.json({ ok: true });
});

// ── VIDEO ─────────────────────────────────────────────

// PUT /api/jobs/:id/video — runner uploads final MP4
app.put('/api/jobs/:id/video', async (c) => {
  const id = c.req.param('id');
  const body = await c.req.arrayBuffer();
  const key = `final/${id}/demo.mp4`;

  await c.env.VIDEOS.put(key, body, {
    httpMetadata: { contentType: 'video/mp4' },
  });

  await c.env.DB.prepare(
    `UPDATE jobs SET video_key = ?, status = 'done', updated_at = ? WHERE id = ?`
  ).bind(key, Date.now(), id).run();

  return c.json({ video_key: key, status: 'done' });
});

// GET /api/video/:jobId — serve final video from R2
app.get('/api/video/:jobId', async (c) => {
  const jobId = c.req.param('jobId');
  const job = await c.env.DB.prepare('SELECT video_key FROM jobs WHERE id = ?').bind(jobId).first();
  if (!job?.video_key) return c.json({ error: 'video not found' }, 404);

  const obj = await c.env.VIDEOS.get(job.video_key as string);
  if (!obj) return c.json({ error: 'video file missing' }, 404);

  c.header('Content-Type', 'video/mp4');
  c.header('Cache-Control', 'public, max-age=3600');
  return c.body(obj.body);
});

// ── D1 Schema init ─────────────────────────────────────

app.get('/api/init-db', async (c) => {
  await c.env.DB.prepare(
    `CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY, url TEXT NOT NULL, cookies TEXT, created_at INTEGER NOT NULL)`
  ).run();
  await c.env.DB.prepare(
    `CREATE TABLE IF NOT EXISTS jobs (
      id TEXT PRIMARY KEY, session_id TEXT, url TEXT NOT NULL, goal TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', plan TEXT, narration TEXT,
      video_key TEXT, error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)`
  ).run();
  return c.json({ ok: true, message: 'DB initialized' });
});

export default app;

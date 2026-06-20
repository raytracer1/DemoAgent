CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  cookies TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  url TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  plan TEXT,
  narration TEXT,
  video_key TEXT,
  error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

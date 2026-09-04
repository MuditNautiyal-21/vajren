-- VAJREN memory. One SQLite file, so backup is one file copy and crash
-- consistency is SQLite's problem, not yours.
--   sqlite3 vajren.db < schema.sql

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- episodic --
-- Every task VAJREN has ever run. "What did I ask you on Tuesday?"
CREATE TABLE IF NOT EXISTS episodes (
  id           INTEGER PRIMARY KEY,
  started_at   TEXT NOT NULL DEFAULT (datetime('now')),
  ended_at     TEXT,
  channel      TEXT,                      -- voice | telegram | schedule
  request      TEXT NOT NULL,
  plan         TEXT,
  outcome      TEXT,                      -- success | failed | cancelled | timeout
  error        TEXT,
  trace_ref    TEXT
);
CREATE INDEX IF NOT EXISTS idx_episodes_time ON episodes(started_at DESC);

-- ------------------------------------------------------------------- audit --
-- Append-only. Every tool call, every approval. Never UPDATE or DELETE here.
CREATE TABLE IF NOT EXISTS audit (
  id            INTEGER PRIMARY KEY,
  at            TEXT NOT NULL DEFAULT (datetime('now')),
  episode_id    INTEGER REFERENCES episodes(id),
  tool          TEXT NOT NULL,
  args_json     TEXT NOT NULL,
  tier          TEXT NOT NULL,            -- auto | confirm | forbidden
  approved_by   TEXT,                     -- voice | telegram | null
  heard_phrase  TEXT,                     -- what STT actually returned
  result_json   TEXT,
  verified      INTEGER,                  -- 1 = post-condition passed
  undo_ref      TEXT                      -- trash path, git sha, draft id
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit(at DESC);

-- -------------------------------------------------------------------- jobs --
-- The crash-recovery layer. On boot, scan for state='in_progress'.
CREATE TABLE IF NOT EXISTS jobs (
  id             TEXT PRIMARY KEY,        -- also the idempotency key
  state          TEXT NOT NULL,           -- queued | in_progress | done | failed
  payload_json   TEXT NOT NULL,
  attempts       INTEGER NOT NULL DEFAULT 0,
  next_action    TEXT,
  last_error     TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);

-- ---------------------------------------------------------------- semantic --
-- Facts and preferences about Mudit. Embeddings live in the vec table alongside.
CREATE TABLE IF NOT EXISTS facts (
  id          INTEGER PRIMARY KEY,
  subject     TEXT NOT NULL,              -- 'preferences' | 'work' | a person's name
  fact        TEXT NOT NULL,
  source      TEXT,                       -- 'stated' | 'observed' | 'corrected'
  confidence  REAL DEFAULT 1.0,
  valid_from  TEXT NOT NULL DEFAULT (datetime('now')),
  valid_to    TEXT,                       -- NULL = still true. Supersede, don't delete.
  UNIQUE(subject, fact)
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);

-- ------------------------------------------------------------------ corpus --
-- Indexed files. Contextual chunks (Anthropic pattern: one LLM-written context
-- sentence prepended before embedding) live in chunk_text.
CREATE TABLE IF NOT EXISTS documents (
  id          INTEGER PRIMARY KEY,
  path        TEXT NOT NULL UNIQUE,
  mtime       REAL NOT NULL,
  sha256      TEXT,
  indexed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chunks (
  id           INTEGER PRIMARY KEY,
  document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal      INTEGER NOT NULL,
  chunk_text   TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_text, content='chunks', content_rowid='id'
);

-- Vector tables (requires the sqlite-vec extension to be loaded):
--   CREATE VIRTUAL TABLE vec_chunks USING vec0(chunk_id INTEGER PRIMARY KEY, embedding FLOAT[1024]);
--   CREATE VIRTUAL TABLE vec_facts  USING vec0(fact_id  INTEGER PRIMARY KEY, embedding FLOAT[1024]);

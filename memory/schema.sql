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

-- ------------------------------------------------------------ memory, v2 --
-- What Vajren carries from one conversation to the next. Phase 06.
--
-- ⚠ SIZE IS A DESIGN PROPERTY, not an accident. Mudit: "if it builds its
--   brain on the C drive it will slow the PC." It will not, because it is
--   BOUNDED: raw turns are pruned after `retention_days`, what matters about
--   them survives as facts, and facts are superseded rather than piled up.
--   The whole file stays in the tens of megabytes on a 954 GB SSD. What
--   would slow the PC is 45 GB of models being read off a USB drive on
--   every swap — which is why the brain stays on C: and the models do too.

-- Each request and what came of it. Searchable, so "what did I ask you
-- yesterday about the essay" resolves without loading everything.
CREATE TABLE IF NOT EXISTS turns (
  id           INTEGER PRIMARY KEY,
  at           TEXT NOT NULL DEFAULT (datetime('now')),
  session_id   TEXT NOT NULL,
  episode_id   INTEGER REFERENCES episodes(id),
  request      TEXT NOT NULL,
  outcome      TEXT NOT NULL,             -- what Vajren said at the end
  tools        TEXT NOT NULL DEFAULT '',  -- 'open_url focus_window' — what it DID
  status       TEXT NOT NULL              -- completed | cancelled | failed
);
CREATE INDEX IF NOT EXISTS idx_turns_time ON turns(at DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
  request, outcome, content='turns', content_rowid='id', tokenize='porter'
);
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
  INSERT INTO turns_fts(rowid, request, outcome) VALUES (new.id, new.request, new.outcome);
END;
CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
  INSERT INTO turns_fts(turns_fts, rowid, request, outcome) VALUES ('delete', old.id, old.request, old.outcome);
END;

-- Facts get full-text search too. (The `facts` table above already exists.)
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
  subject, fact, content='facts', content_rowid='id', tokenize='porter'
);
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, subject, fact) VALUES (new.id, new.subject, new.fact);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, subject, fact) VALUES ('delete', old.id, old.subject, old.fact);
END;

-- Learned trust: which SHAPES of action Mudit has approved often enough that
-- asking again is noise. A shape is (tool, pattern) where pattern is the arg
-- with the specifics removed — a folder not a file, a host not a URL.
-- Cancelling once resets the count to zero: trust is earned slowly and lost
-- at once. Tools that can never be here are listed in core/policy.py.
CREATE TABLE IF NOT EXISTS trust (
  tool         TEXT NOT NULL,
  pattern      TEXT NOT NULL,
  approvals    INTEGER NOT NULL DEFAULT 0,   -- consecutive, since last cancel
  cancels      INTEGER NOT NULL DEFAULT 0,   -- lifetime
  granted_at   TEXT,                          -- NULL until earned
  revoked_at   TEXT,                          -- set by "ask me about that again"
  last_at      TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (tool, pattern)
);

-- ------------------------------------------------------- the brain, Phase 11 --
-- Facts were flat strings. This is the part that CONNECTS them: what Vajren has
-- met (entities), how those things relate (edges), and every action it took
-- against them (observations). Recall walks the graph instead of only matching
-- words, so "message Sakshi" lights up WhatsApp and the window it lives in
-- without anyone having written that sentence down.
--
-- ⚠ PROVENANCE IS THE SPINE, not decoration. A memory that grows by itself is
--   a prompt-injection sink with a very long fuse: poison one page, and a
--   sentence from it becomes something Vajren believes and acts on forever.
--   So every row carries where it came from, and core/brain.py enforces in
--   CODE (not in a prompt) that only 'stated' and 'observed' — what Mudit said,
--   and what Vajren did and verified — may ever reach the planner as belief.
--   'read' is recorded, attributed, and never asserted.
--
--   stated    Mudit said it.                                  believable
--   observed  Vajren did it and the post-condition passed.    believable
--   inferred  consolidation saw the same thing N times.       believable, hedged
--   read      it came out of a file, a page, or stdout.       NEVER asserted

CREATE TABLE IF NOT EXISTS entities (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL,            -- person|app|file|folder|host|window|topic
  name         TEXT NOT NULL,            -- canonical: lowercased, for matching
  display      TEXT NOT NULL,            -- as first seen, for speaking
  first_seen   TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen    TEXT NOT NULL DEFAULT (datetime('now')),
  mentions     INTEGER NOT NULL DEFAULT 1,
  merged_into  INTEGER REFERENCES entities(id),   -- set when consolidated away
  UNIQUE(kind, name)
);
CREATE INDEX IF NOT EXISTS idx_entities_seen ON entities(last_seen DESC);

-- A link between two things that showed up in the same piece of work.
-- Weight grows with evidence and decays when it stops being used, so the graph
-- reshapes itself around what Mudit actually does rather than what he did once.
CREATE TABLE IF NOT EXISTS edges (
  id           INTEGER PRIMARY KEY,
  src          INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  dst          INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  rel          TEXT NOT NULL DEFAULT 'with',
  weight       REAL NOT NULL DEFAULT 1.0,
  evidence     INTEGER NOT NULL DEFAULT 1,
  first_seen   TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(src, dst, rel)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src, weight DESC);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst, weight DESC);

-- Every action, against every thing it touched. This is the episodic layer of
-- the graph: `turns` says what was asked, this says what it MET doing it.
CREATE TABLE IF NOT EXISTS observations (
  id           INTEGER PRIMARY KEY,
  at           TEXT NOT NULL DEFAULT (datetime('now')),
  turn_id      INTEGER REFERENCES turns(id),
  episode_id   INTEGER REFERENCES episodes(id),
  entity_id    INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  tool         TEXT NOT NULL DEFAULT '',
  role         TEXT NOT NULL DEFAULT 'context',   -- subject|object|context
  provenance   TEXT NOT NULL DEFAULT 'observed',  -- see the ladder above
  verified     INTEGER,                            -- 1 = post-condition passed
  detail       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_obs_entity ON observations(entity_id, at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_turn ON observations(turn_id);

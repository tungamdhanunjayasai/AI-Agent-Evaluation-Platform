-- Initial PostgreSQL schema for the Agent Evaluation Platform.
-- JSON and timestamp values remain text to preserve the existing Store API.

CREATE TABLE IF NOT EXISTS datasets (
  dataset_version_id TEXT PRIMARY KEY,
  dataset_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  dataset_version_id TEXT NOT NULL REFERENCES datasets(dataset_version_id),
  category TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_configs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  adapter TEXT NOT NULL,
  settings_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY,
  dataset_version_id TEXT NOT NULL,
  split TEXT NOT NULL,
  repetitions INTEGER NOT NULL,
  status TEXT NOT NULL,
  planned_runs INTEGER NOT NULL,
  idempotency_key TEXT UNIQUE,
  manifest_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL REFERENCES experiments(id),
  task_id TEXT NOT NULL REFERENCES tasks(id),
  agent_config_id TEXT NOT NULL REFERENCES agent_configs(id),
  repetition INTEGER NOT NULL,
  execution_status TEXT NOT NULL,
  evaluation_status TEXT NOT NULL,
  attempt_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(experiment_id, task_id, agent_config_id, repetition)
);

CREATE TABLE IF NOT EXISTS attempts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  environment_id TEXT NOT NULL,
  execution_status TEXT NOT NULL,
  stop_reason TEXT,
  final_response TEXT,
  environment_json TEXT NOT NULL,
  hidden_fixture_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  attempt_id TEXT NOT NULL REFERENCES attempts(id),
  sequence_number INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL,
  duration_ms DOUBLE PRECISION,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scores (
  id TEXT PRIMARY KEY,
  attempt_id TEXT NOT NULL REFERENCES attempts(id),
  evaluator TEXT NOT NULL,
  status TEXT NOT NULL,
  score DOUBLE PRECISION,
  reason_codes_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(attempt_id, evaluator)
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
  status TEXT NOT NULL,
  claimed_at TEXT,
  finished_at TEXT,
  error TEXT
);

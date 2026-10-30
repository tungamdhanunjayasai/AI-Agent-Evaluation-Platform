-- Dataset-version scoped task identity and recoverable job state.

ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_task_id_fkey;
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_pkey;
ALTER TABLE tasks ADD CONSTRAINT tasks_pkey PRIMARY KEY (dataset_version_id, id);

ALTER TABLE runs ADD COLUMN IF NOT EXISTS dataset_version_id TEXT;
UPDATE runs AS r
SET dataset_version_id = e.dataset_version_id
FROM experiments AS e
WHERE e.id = r.experiment_id AND r.dataset_version_id IS NULL;
ALTER TABLE runs ALTER COLUMN dataset_version_id SET NOT NULL;
ALTER TABLE runs ADD CONSTRAINT runs_task_dataset_version_fkey
  FOREIGN KEY (dataset_version_id, task_id)
  REFERENCES tasks(dataset_version_id, id);

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS attempts_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_expires_at TEXT;
CREATE INDEX IF NOT EXISTS jobs_claimable_idx ON jobs(status, lease_expires_at, id);

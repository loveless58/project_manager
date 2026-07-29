CREATE TABLE document_artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES organization_runs(id) ON DELETE RESTRICT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    artifact_kind TEXT NOT NULL,
    logical_uri TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    created_at_utc TEXT NOT NULL,
    UNIQUE(run_id, document_id, artifact_kind)
);

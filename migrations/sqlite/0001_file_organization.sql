CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project_code TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(project_code)
);

CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL,
    media_type TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    parser TEXT NOT NULL,
    source_ref_json TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE document_locations (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    binding_id TEXT NOT NULL,
    logical_uri TEXT NOT NULL,
    storage_provider TEXT NOT NULL,
    object_key TEXT NOT NULL,
    is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(document_id, logical_uri)
);

CREATE UNIQUE INDEX document_locations_one_current_per_document
ON document_locations(document_id)
WHERE is_current = 1;

CREATE TABLE document_project_links (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    link_state TEXT NOT NULL CHECK(link_state IN ('candidate', 'confirmed')),
    evidence_type TEXT NOT NULL,
    confidence TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    confirmed_at_utc TEXT,
    UNIQUE(document_id, project_id)
);

CREATE TABLE organization_runs (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    run_state TEXT NOT NULL CHECK(run_state IN ('pending', 'completed', 'failed')),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE organization_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES organization_runs(id) ON DELETE RESTRICT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    proposal_json TEXT NOT NULL,
    item_state TEXT NOT NULL CHECK(item_state IN ('pending', 'confirmed', 'skipped', 'executed', 'failed')),
    target_location_json TEXT,
    created_at_utc TEXT NOT NULL,
    confirmed_at_utc TEXT,
    executed_at_utc TEXT,
    UNIQUE(run_id, document_id)
);

CREATE INDEX document_project_links_project_state
ON document_project_links(project_id, link_state);

CREATE INDEX organization_items_run_state
ON organization_items(run_id, item_state);

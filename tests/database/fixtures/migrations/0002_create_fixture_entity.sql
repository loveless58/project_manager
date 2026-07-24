CREATE TABLE fixture_entity (
    id TEXT PRIMARY KEY,
    value TEXT NOT NULL UNIQUE,
    parent_id TEXT,
    FOREIGN KEY (parent_id) REFERENCES fixture_parent(id)
);

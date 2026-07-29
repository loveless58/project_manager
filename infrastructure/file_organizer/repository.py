"""SQLite repository for the File Organizer Agent's durable business facts."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from contracts.structured_document import validate_structured_document


_LOCATION_KEYS = ("binding_id", "logical_uri", "storage_provider", "object_key")


class FileOrganizationRepository:
    """Keep project, document, location and organization-run facts in SQLite.

    Transaction ownership remains with the caller's ``SqliteUnitOfWork``. This
    class deliberately performs no filesystem work and does not create its own
    connections, so archive code can persist a verified result atomically.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def upsert_document(self, structured_document: Mapping[str, Any]) -> str:
        payload = _normalize_document(structured_document)
        content_hash = payload["content_hash"]
        existing = self._connection.execute(
            "SELECT id FROM documents WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if existing is not None:
            self._connection.execute(
                "UPDATE documents SET source_ref_json = ?, fields_json = ?, "
                "parse_status = ?, parser = ?, updated_at_utc = ? WHERE id = ?",
                (
                    _json(payload["source_ref"]),
                    _json(payload["fields"]),
                    payload["status"],
                    payload["parser"],
                    _utc_now(),
                    existing["id"],
                ),
            )
            return str(existing["id"])

        document_id = _new_id()
        now = _utc_now()
        self._connection.execute(
            "INSERT INTO documents (id, content_hash, schema_version, media_type, "
            "parse_status, parser, source_ref_json, fields_json, created_at_utc, updated_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document_id,
                content_hash,
                payload["schema_version"],
                payload["media_type"],
                payload["status"],
                payload["parser"],
                _json(payload["source_ref"]),
                _json(payload["fields"]),
                now,
                now,
            ),
        )
        return document_id

    def record_location(
        self, document_id: str, location: Mapping[str, Any], *, current: bool
    ) -> str:
        normalized = _validate_location(location)
        existing = self._connection.execute(
            "SELECT id FROM document_locations WHERE document_id = ? AND logical_uri = ?",
            (document_id, normalized["logical_uri"]),
        ).fetchone()
        if current:
            self._connection.execute(
                "UPDATE document_locations SET is_current = 0 "
                "WHERE document_id = ? AND is_current = 1",
                (document_id,),
            )
        if existing is not None:
            self._connection.execute(
                "UPDATE document_locations SET binding_id = ?, storage_provider = ?, "
                "object_key = ?, is_current = ?, recorded_at_utc = ? WHERE id = ?",
                (
                    normalized["binding_id"],
                    normalized["storage_provider"],
                    normalized["object_key"],
                    int(current),
                    _utc_now(),
                    existing["id"],
                ),
            )
            return str(existing["id"])

        location_id = _new_id()
        self._connection.execute(
            "INSERT INTO document_locations "
            "(id, document_id, binding_id, logical_uri, storage_provider, object_key, is_current, recorded_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                location_id,
                document_id,
                normalized["binding_id"],
                normalized["logical_uri"],
                normalized["storage_provider"],
                normalized["object_key"],
                int(current),
                _utc_now(),
            ),
        )
        return location_id

    def locations_for(self, document_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT id, binding_id, logical_uri, storage_provider, object_key, is_current, recorded_at_utc "
            "FROM document_locations WHERE document_id = ? ORDER BY recorded_at_utc, id",
            (document_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "binding_id": row["binding_id"],
                "logical_uri": row["logical_uri"],
                "storage_provider": row["storage_provider"],
                "object_key": row["object_key"],
                "is_current": bool(row["is_current"]),
                "recorded_at_utc": row["recorded_at_utc"],
            }
            for row in rows
        ]

    def create_project(self, name: str, project_code: str | None = None) -> str:
        name = name.strip()
        if not name:
            raise ValueError("project name is required")
        normalized_code = project_code.strip() if project_code else None
        if normalized_code:
            existing = self._connection.execute(
                "SELECT id FROM projects WHERE project_code = ?", (normalized_code,)
            ).fetchone()
            if existing is not None:
                self._connection.execute(
                    "UPDATE projects SET name = ?, updated_at_utc = ? WHERE id = ?",
                    (name, _utc_now(), existing["id"]),
                )
                return str(existing["id"])
        project_id = _new_id()
        now = _utc_now()
        self._connection.execute(
            "INSERT INTO projects (id, name, project_code, created_at_utc, updated_at_utc) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, name, normalized_code, now, now),
        )
        return project_id

    def find_project_candidates(self, fields: Mapping[str, Any]) -> list[dict[str, str | None]]:
        project_code = _as_text(fields.get("project_code"))
        company_name = _as_text(fields.get("company_name"))
        if project_code:
            rows = self._connection.execute(
                "SELECT id, name, project_code FROM projects WHERE project_code = ? ORDER BY name, id",
                (project_code,),
            ).fetchall()
        elif company_name:
            rows = self._connection.execute(
                "SELECT id, name, project_code FROM projects WHERE name = ? ORDER BY name, id",
                (company_name,),
            ).fetchall()
        else:
            rows = []
        return [
            {"id": row["id"], "name": row["name"], "project_code": row["project_code"]}
            for row in rows
        ]

    def link_document_to_project(
        self,
        document_id: str,
        project_id: str,
        link_state: str,
        evidence_type: str,
        confidence: str,
    ) -> str:
        _require_choice(link_state, {"candidate", "confirmed"}, "link state")
        existing = self._connection.execute(
            "SELECT id FROM document_project_links WHERE document_id = ? AND project_id = ?",
            (document_id, project_id),
        ).fetchone()
        now = _utc_now()
        if existing is not None:
            self._connection.execute(
                "UPDATE document_project_links SET link_state = ?, evidence_type = ?, confidence = ?, "
                "confirmed_at_utc = ? WHERE id = ?",
                (link_state, evidence_type, confidence, now if link_state == "confirmed" else None, existing["id"]),
            )
            return str(existing["id"])
        link_id = _new_id()
        self._connection.execute(
            "INSERT INTO document_project_links "
            "(id, document_id, project_id, link_state, evidence_type, confidence, created_at_utc, confirmed_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (link_id, document_id, project_id, link_state, evidence_type, confidence, now, now if link_state == "confirmed" else None),
        )
        return link_id

    def confirm_project_link(self, link_id: str) -> None:
        self._connection.execute(
            "UPDATE document_project_links SET link_state = 'confirmed', confirmed_at_utc = ? WHERE id = ?",
            (_utc_now(), link_id),
        )

    def create_run(self, goal: str) -> str:
        if not goal.strip():
            raise ValueError("organization goal is required")
        run_id = _new_id()
        now = _utc_now()
        self._connection.execute(
            "INSERT INTO organization_runs (id, goal, run_state, created_at_utc, updated_at_utc) "
            "VALUES (?, ?, 'pending', ?, ?)",
            (run_id, goal.strip(), now, now),
        )
        return run_id

    def record_item(self, run_id: str, document_id: str, proposal: Mapping[str, Any]) -> str:
        existing = self._connection.execute(
            "SELECT id FROM organization_items WHERE run_id = ? AND document_id = ?",
            (run_id, document_id),
        ).fetchone()
        if existing is not None:
            self._connection.execute(
                "UPDATE organization_items SET proposal_json = ? WHERE id = ?",
                (_json(proposal), existing["id"]),
            )
            return str(existing["id"])
        item_id = _new_id()
        self._connection.execute(
            "INSERT INTO organization_items "
            "(id, run_id, document_id, proposal_json, item_state, created_at_utc) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (item_id, run_id, document_id, _json(proposal), _utc_now()),
        )
        return item_id

    def confirm_item(self, item_id: str, target_location: Mapping[str, Any]) -> None:
        self._connection.execute(
            "UPDATE organization_items SET item_state = 'confirmed', target_location_json = ?, confirmed_at_utc = ? "
            "WHERE id = ? AND item_state IN ('pending', 'confirmed')",
            (_json(_validate_location(target_location)), _utc_now(), item_id),
        )

    def complete_archive(self, item_id: str, target_location: Mapping[str, Any]) -> None:
        self._connection.execute(
            "UPDATE organization_items SET item_state = 'executed', target_location_json = ?, executed_at_utc = ? "
            "WHERE id = ? AND item_state = 'confirmed'",
            (_json(_validate_location(target_location)), _utc_now(), item_id),
        )

    def archive_context(self, item_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT oi.item_state, oi.target_location_json, oi.document_id, d.content_hash, "
            "dl.binding_id, dl.logical_uri, dl.storage_provider, dl.object_key "
            "FROM organization_items oi JOIN documents d ON d.id = oi.document_id "
            "LEFT JOIN document_locations dl ON dl.document_id = d.id AND dl.is_current = 1 "
            "WHERE oi.id = ?",
            (item_id,),
        ).fetchone()
        if row is None or row["target_location_json"] is None or row["binding_id"] is None:
            return None
        try:
            target = json.loads(row["target_location_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(target, dict):
            return None
        source = {key: row[key] for key in _LOCATION_KEYS}
        return {"item_state": row["item_state"], "document_id": row["document_id"], "content_hash": row["content_hash"], "source_location": source, "target_location": target}


    def item_for_run(self, run_id: str, item_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT item_state, proposal_json FROM organization_items WHERE run_id = ? AND id = ?",
            (run_id, item_id),
        ).fetchone()
        if row is None:
            return None
        try:
            proposal = json.loads(row["proposal_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(proposal, dict):
            return None
        return {"item_state": row["item_state"], "proposal": proposal}

    def skip_item(self, item_id: str) -> None:
        self._connection.execute(
            "UPDATE organization_items SET item_state = 'skipped' WHERE id = ? AND item_state = 'pending'",
            (item_id,),
        )


def _normalize_document(structured_document: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(structured_document)
    if payload.get("status") == "success":
        return validate_structured_document(payload)
    if payload.get("status") != "needs_review":
        raise ValueError("document parse status is unsupported")
    candidate = {
        "schema_version": payload.get("schema_version"),
        "source_ref": payload.get("source_ref"),
        "content_hash": payload.get("content_hash"),
        "media_type": payload.get("media_type"),
        "status": "success",
        "parser": payload.get("parser"),
        "text": payload.get("text", ""),
        "pages": payload.get("pages", []),
        "tables": payload.get("tables", []),
        "fields": payload.get("fields", {}),
    }
    normalized = validate_structured_document(candidate)
    normalized["status"] = "needs_review"
    return normalized


def _new_id() -> str:
    return str(uuid.uuid4())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_location(location: Mapping[str, Any]) -> dict[str, str]:
    normalized = {key: _as_text(location.get(key)) for key in _LOCATION_KEYS}
    if any(not value for value in normalized.values()):
        raise ValueError("location must include binding_id, logical_uri, storage_provider and object_key")
    return normalized


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _require_choice(value: str, allowed: set[str], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"unsupported {label}")

"""File-backed, fail-closed hand-off for explicitly supplied agent judgement."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping

from contracts.agent_judgement import AgentJudgementSchemaError, validate_run_id
from contracts.archive_run_artifacts import (
    ArchiveRunArtifactError,
    strict_json_load,
    validate_agent_judgement_requests_artifact,
    validate_agent_judgement_responses_artifact,
)


RequestPreparer = Callable[[list[str]], dict[str, Any]]
ResponseConsumer = Callable[[str, list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]
RunDirectoryResolver = Callable[[str], str]
ArtifactWriter = Callable[[str, dict[str, Any]], None]


class AgentJudgementGateway:
    """Persist requests and accept only a fully validated response artifact.

    The gateway owns the agent exchange only.  It delegates source processing and
    all business/review artifact construction to explicit owner callbacks.  In
    particular, it has no ledger, archive, feedback, or confirmation authority.
    """

    def __init__(
        self,
        *,
        request_preparer: RequestPreparer | None = None,
        response_consumer: ResponseConsumer | None = None,
        run_dir_resolver: RunDirectoryResolver | None = None,
        artifact_writer: ArtifactWriter | None = None,
    ) -> None:
        self._request_preparer = request_preparer
        self._response_consumer = response_consumer
        self._run_dir_resolver = run_dir_resolver
        self._artifact_writer = artifact_writer or self._atomic_write_json

    def bind(
        self,
        *,
        request_preparer: RequestPreparer,
        response_consumer: ResponseConsumer,
        run_dir_resolver: RunDirectoryResolver,
        artifact_writer: ArtifactWriter,
    ) -> None:
        """Attach the run owner without granting it agent-response bypasses."""
        self._request_preparer = request_preparer
        self._response_consumer = response_consumer
        self._run_dir_resolver = run_dir_resolver
        self._artifact_writer = artifact_writer

    def prepare_run(self, files: list[str]) -> dict[str, Any]:
        """Create the local request artifact after the owner has prepared input."""
        if self._request_preparer is None:
            return self._blocked("", "LLM.CAPABILITY_DISABLED")
        if type(files) is not list or any(type(path) is not str for path in files):
            return self._blocked("", "AGENT_JUDGEMENT.REQUEST_INVALID")
        try:
            prepared = self._request_preparer(list(files))
            if type(prepared) is not dict:
                raise ValueError("request preparation")
            if prepared.get("status") != "ready":
                return dict(prepared)
            run_id = validate_run_id(prepared.get("run_id"))
            run_dir = prepared.get("run_dir")
            requests = prepared.get("requests")
            artifacts = prepared.get("artifacts")
            if (
                type(run_dir) is not str
                or type(requests) is not list
                or type(artifacts) is not dict
            ):
                raise ValueError("request preparation")
            payload = {
                "schema_version": "agent_judgement_requests.v1",
                "run_id": run_id,
                "requests": requests,
            }
            validate_agent_judgement_requests_artifact(payload, run_id=run_id)
            request_path = os.path.join(run_dir, "agent_judgement_requests.json")
            self._artifact_writer(request_path, payload)
        except (AgentJudgementSchemaError, ArchiveRunArtifactError, OSError, TypeError, ValueError):
            return self._blocked("", "AGENT_JUDGEMENT.REQUEST_INVALID")

        result_artifacts = dict(artifacts)
        result_artifacts["agent_judgement_requests"] = request_path
        result_artifacts["run_dir"] = run_dir
        return {
            "schema_version": "agent_judgement_gateway.run.v1",
            "status": "awaiting_agent_judgement",
            "run_id": run_id,
            "request_count": len(requests),
            "artifacts": result_artifacts,
            "boundary": {
                "agent_response_consumed": False,
                "archive_plan_executed": False,
                "confirmed": False,
            },
        }

    def resume_run(self, run_id: str, response_path: str) -> dict[str, Any]:
        """Consume one strict response artifact, or leave the run untouched."""
        if self._run_dir_resolver is None or self._response_consumer is None:
            return self._blocked(str(run_id), "LLM.CAPABILITY_DISABLED")
        if type(response_path) is not str or not response_path:
            return self._blocked(str(run_id), "LLM.CAPABILITY_DISABLED")
        try:
            run_id = validate_run_id(run_id)
            run_dir = self._run_dir_resolver(run_id)
            failure_marker_path = os.path.join(run_dir, "agent_judgement_failed.json")
            if os.path.exists(failure_marker_path):
                failure_marker = strict_json_load(failure_marker_path)
                if failure_marker == {
                    "schema_version": "agent_judgement_failed.v1",
                    "run_id": run_id,
                    "status": "failed",
                    "reason": "AGENT_JUDGEMENT.REQUEST_INVALID",
                }:
                    return self._blocked(run_id, "AGENT_JUDGEMENT.RUN_PREPARATION_FAILED")
                raise ArchiveRunArtifactError("agent failed-run marker")
            request_path = os.path.join(run_dir, "agent_judgement_requests.json")
            request_artifact = strict_json_load(request_path)
            requests = validate_agent_judgement_requests_artifact(
                request_artifact, run_id=run_id,
            )
            if not os.path.isfile(response_path):
                return self._blocked(run_id, "LLM.CAPABILITY_DISABLED")
            response_artifact = strict_json_load(response_path)
            responses = validate_agent_judgement_responses_artifact(
                response_artifact, run_id=run_id, requests=requests,
            )
        except (AgentJudgementSchemaError, ArchiveRunArtifactError, OSError, TypeError, ValueError):
            return self._blocked(str(run_id), "AGENT_JUDGEMENT.RESPONSE_INVALID")

        try:
            return self._response_consumer(run_id, requests, responses)
        except (ArchiveRunArtifactError, OSError, TypeError, ValueError):
            return self._blocked(run_id, "AGENT_JUDGEMENT.RESPONSE_INVALID")

    @staticmethod
    def _blocked(run_id: str, blocked_reason: str) -> dict[str, Any]:
        return {
            "schema_version": "agent_judgement_gateway.run.v1",
            "status": "blocked",
            "run_id": run_id,
            "blocked_reason": blocked_reason,
            "archive_actions": [],
            "boundary": {
                "agent_response_consumed": False,
                "archive_plan_executed": False,
                "confirmed": False,
            },
        }

    @staticmethod
    def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
        encoded = json.dumps(
            payload, ensure_ascii=False, indent=2, allow_nan=False,
        ).encode("utf-8")
        directory = Path(path).parent
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".agent-request-", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


__all__ = ["AgentJudgementGateway"]

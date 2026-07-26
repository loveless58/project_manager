"""File-backed, fail-closed hand-off for explicitly supplied agent judgement."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Callable, Mapping, Sequence

from contracts.agent_judgement import AgentJudgementSchemaError, validate_run_id
from contracts.archive_run_artifacts import (
    ArchiveRunArtifactError,
    strict_json_load,
    validate_agent_judgement_requests_artifact,
    validate_agent_judgement_responses_artifact,
)
from platform_core.path_locality import is_obvious_network_location


RequestPreparer = Callable[[list[str]], dict[str, Any]]
ResponseConsumer = Callable[
    [str, list[dict[str, Any]], list[dict[str, Any]], list[str]], dict[str, Any]
]
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
        response_root: str | None = None,
        protected_roots: Sequence[str] = (),
    ) -> None:
        self._request_preparer = request_preparer
        self._response_consumer = response_consumer
        self._run_dir_resolver = run_dir_resolver
        self._artifact_writer = artifact_writer or self._atomic_write_json
        self._response_root = response_root
        self._protected_roots = tuple(protected_roots)

    def bind(
        self,
        *,
        request_preparer: RequestPreparer,
        response_consumer: ResponseConsumer,
        run_dir_resolver: RunDirectoryResolver,
        response_root: str,
        protected_roots: Sequence[str] = (),
        artifact_writer: ArtifactWriter,
    ) -> None:
        """Attach the run owner without granting it agent-response bypasses."""
        self._request_preparer = request_preparer
        self._response_consumer = response_consumer
        self._run_dir_resolver = run_dir_resolver
        self._artifact_writer = artifact_writer
        self._response_root = response_root
        self._protected_roots = tuple(protected_roots)

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
            if not self._response_root:
                raise ValueError("agent response root")
            Path(self._response_root).mkdir(parents=True, exist_ok=True)
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

    def resume_run(
        self, run_id: str, response_path: str, files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Consume one strict response artifact, or leave the run untouched."""
        if self._run_dir_resolver is None or self._response_consumer is None:
            return self._blocked(str(run_id), "LLM.CAPABILITY_DISABLED")
        if type(response_path) is not str or not response_path:
            return self._blocked(str(run_id), "LLM.CAPABILITY_DISABLED")
        if is_obvious_network_location(response_path):
            return self._blocked(str(run_id), "AGENT_JUDGEMENT.RESPONSE_INVALID")
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
            if (
                type(files) is not list or not files
                or any(type(path) is not str or not path for path in files)
            ):
                return self._blocked(run_id, "AGENT_JUDGEMENT.RUN_INPUT_INVALID")
            response_path = self._validated_response_path(response_path)
            request_path = os.path.join(run_dir, "agent_judgement_requests.json")
            request_artifact = strict_json_load(request_path)
            requests = validate_agent_judgement_requests_artifact(
                request_artifact, run_id=run_id,
            )
            if not os.path.exists(response_path):
                return self._blocked(run_id, "LLM.CAPABILITY_DISABLED")
            response_artifact = strict_json_load(response_path)
            responses = validate_agent_judgement_responses_artifact(
                response_artifact, run_id=run_id, requests=requests,
            )
        except (AgentJudgementSchemaError, ArchiveRunArtifactError, OSError, TypeError, ValueError):
            return self._blocked(str(run_id), "AGENT_JUDGEMENT.RESPONSE_INVALID")

        try:
            return self._response_consumer(run_id, requests, responses, list(files))
        except ArchiveRunArtifactError as exc:
            if str(exc) == "agent judgement run already consumed":
                return self._blocked(run_id, "AGENT_JUDGEMENT.RESPONSE_INVALID")
            if str(exc) == "agent run input invalid":
                return self._blocked(run_id, "AGENT_JUDGEMENT.RUN_INPUT_INVALID")
            raise

    def _validated_response_path(self, raw_path: str) -> str:
        if not self._response_root:
            raise ValueError("agent response root")
        candidate = Path(os.path.abspath(os.fspath(Path(raw_path).expanduser())))
        response_root = Path(os.path.abspath(self._response_root))
        try:
            candidate.relative_to(response_root)
        except ValueError:
            raise ValueError("agent response outside exchange") from None
        self._reject_linked_components(response_root)
        self._reject_linked_components(candidate)
        canonical_root = response_root.resolve()
        canonical_candidate = candidate.resolve()
        try:
            canonical_candidate.relative_to(canonical_root)
        except ValueError:
            raise ValueError("agent response outside exchange") from None
        for raw_root in self._protected_roots:
            protected_root = Path(raw_root).expanduser().resolve()
            try:
                canonical_candidate.relative_to(protected_root)
            except ValueError:
                pass
            else:
                raise ValueError("agent response inside protected storage")
        try:
            details = os.lstat(candidate)
        except FileNotFoundError:
            return str(candidate)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_ISLNK(details.st_mode)
            or getattr(details, "st_file_attributes", 0) & reparse_flag
        ):
            raise ValueError("agent response is not a regular file")
        return str(candidate)

    @staticmethod
    def _reject_linked_components(path: Path) -> None:
        current = Path(path.anchor)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        for part in path.parts[1:]:
            current /= part
            try:
                details = os.lstat(current)
            except FileNotFoundError:
                break
            if (
                stat.S_ISLNK(details.st_mode)
                or getattr(details, "st_file_attributes", 0) & reparse_flag
            ):
                raise ValueError("agent response path contains a linked component")

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

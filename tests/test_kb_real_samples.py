#!/usr/bin/env python3
"""Optional local business-sample regression runner.

The repository stores only a fully synthetic manifest.  Operators may keep a
separate ignored local manifest for manual regression against controlled files.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys
import unittest
from typing import Mapping, Sequence


from skills.document_parse import parse


SAMPLE_ROOT_ENV = "PROJECT_MANAGER_REAL_SAMPLE_ROOT"
SAMPLE_MANIFEST_ENV = "PROJECT_MANAGER_REAL_SAMPLE_MANIFEST"
LOCAL_MANIFEST_PATH = Path(__file__).with_name("fixtures") / "kb_real_samples.local.json"
MANIFEST_SCHEMA_VERSION = "kb.sample_manifest.v1"
ALLOWED_CATEGORIES = frozenset(
    {"招标公告", "投标文件", "合同文件", "报名材料", "其他"}
)


class RealSampleConfigurationError(RuntimeError):
    """The optional local regression runner is configured incorrectly."""


class SampleManifestError(ValueError):
    """A sample manifest does not satisfy the portable manifest contract."""


@dataclass(frozen=True)
class SampleCase:
    relative_path: str
    expected_category: str


@dataclass(frozen=True)
class SampleManifest:
    fixture_kind: str
    contains_real_business_data: bool
    samples: tuple[SampleCase, ...]


def _manifest_path(
    manifest_path: os.PathLike[str] | str | None,
    environ: Mapping[str, str],
) -> Path:
    if manifest_path is not None:
        return Path(manifest_path).expanduser().resolve()
    configured = environ.get(SAMPLE_MANIFEST_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return LOCAL_MANIFEST_PATH.resolve()


def _portable_relative_path(value: object, *, index: int) -> str:
    relative_path = str(value or "").strip()
    if not relative_path:
        raise SampleManifestError(f"samples[{index}].relative_path must not be empty")
    posix_path = PurePosixPath(relative_path)
    windows_path = PureWindowsPath(relative_path)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise SampleManifestError(
            f"samples[{index}].relative_path must be relative"
        )
    normalized_path = relative_path.replace("\\", "/")
    if ".." in PurePosixPath(normalized_path).parts:
        raise SampleManifestError(
            f"samples[{index}].relative_path must not traverse parents"
        )
    return normalized_path


def load_sample_manifest(
    manifest_path: os.PathLike[str] | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> SampleManifest:
    """Load a tracked synthetic or ignored local manifest.

    Missing manifests are an explicit skip so normal CI never depends on local
    business files.  A configured manifest must still pass the same schema and
    relative-path checks as the tracked synthetic fixture.
    """
    environment = os.environ if environ is None else environ
    path = _manifest_path(manifest_path, environment)
    if not path.is_file():
        raise unittest.SkipTest(
            "local sample manifest is absent; configure "
            f"{SAMPLE_MANIFEST_ENV} for the optional regression"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SampleManifestError(f"sample manifest is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SampleManifestError("sample manifest root must be an object")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise SampleManifestError(
            f"sample manifest schema_version must be {MANIFEST_SCHEMA_VERSION}"
        )

    fixture_kind = str(payload.get("fixture_kind", "")).strip()
    contains_business_data = payload.get("contains_real_business_data")
    if fixture_kind not in {"synthetic", "local_business"}:
        raise SampleManifestError(
            "fixture_kind must be synthetic or local_business"
        )
    if not isinstance(contains_business_data, bool):
        raise SampleManifestError("contains_real_business_data must be boolean")
    if fixture_kind == "synthetic" and contains_business_data:
        raise SampleManifestError("synthetic manifest cannot contain business data")
    if fixture_kind == "local_business" and not contains_business_data:
        raise SampleManifestError("local_business manifest must declare business data")

    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise SampleManifestError("samples must be a non-empty array")
    samples = []
    seen_paths = set()
    for index, item in enumerate(raw_samples):
        if not isinstance(item, dict):
            raise SampleManifestError(f"samples[{index}] must be an object")
        relative_path = _portable_relative_path(
            item.get("relative_path"), index=index
        )
        if fixture_kind == "synthetic" and not relative_path.replace(
            "\\", "/"
        ).startswith("synthetic/"):
            raise SampleManifestError(
                f"samples[{index}].relative_path must use synthetic/ prefix"
            )
        if relative_path in seen_paths:
            raise SampleManifestError(
                f"samples[{index}].relative_path must be unique"
            )
        seen_paths.add(relative_path)
        expected_category = str(item.get("expected_category", "")).strip()
        if expected_category not in ALLOWED_CATEGORIES:
            raise SampleManifestError(
                f"samples[{index}].expected_category is unsupported"
            )
        samples.append(SampleCase(relative_path, expected_category))

    return SampleManifest(
        fixture_kind=fixture_kind,
        contains_real_business_data=contains_business_data,
        samples=tuple(samples),
    )


def get_sample_root(environ: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environ is None else environ
    configured = environment.get(SAMPLE_ROOT_ENV, "").strip()
    if not configured:
        raise RealSampleConfigurationError(
            f"set {SAMPLE_ROOT_ENV} to the directory containing local samples"
        )
    root = Path(configured).expanduser().resolve()
    if not root.is_dir():
        raise RealSampleConfigurationError(
            f"{SAMPLE_ROOT_ENV} must be an existing directory: {root}"
        )
    return root


def missing_sample_files(
    sample_root: Path,
    samples: Sequence[SampleCase],
) -> list[SampleCase]:
    return [sample for sample in samples if not (sample_root / sample.relative_path).is_file()]


def _run_samples(sample_root: Path, samples: Sequence[SampleCase]) -> list[str]:
    failures = []
    for sample in samples:
        source_path = sample_root / sample.relative_path
        try:
            result = parse(str(source_path))
            actual = result["business_judgement"]["category"]
        except Exception as exc:  # pragma: no cover - manual provider boundary
            failures.append(f"{sample.relative_path}: EXCEPTION {exc}")
            continue
        if actual != sample.expected_category:
            failures.append(
                f"{sample.relative_path}: expected={sample.expected_category}, actual={actual}"
            )
    return failures


def test_local_business_sample_regression():
    """Run only when an ignored local manifest and root are configured."""
    manifest = load_sample_manifest()
    sample_root = get_sample_root()
    missing = missing_sample_files(sample_root, manifest.samples)
    assert not missing, f"Missing sample files: {len(missing)}/{len(manifest.samples)}"
    failures = _run_samples(sample_root, manifest.samples)
    assert not failures, "\n".join(failures)


def main() -> int:
    try:
        manifest = load_sample_manifest()
    except unittest.SkipTest as exc:
        print(f"SKIP: {exc}")
        return 0
    except SampleManifestError as exc:
        print(f"Manifest error: {exc}", file=sys.stderr)
        return 2

    try:
        sample_root = get_sample_root()
    except RealSampleConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    missing = missing_sample_files(sample_root, manifest.samples)
    if missing:
        print(
            f"Missing sample files: {len(missing)}/{len(manifest.samples)}",
            file=sys.stderr,
        )
        return 1

    failures = _run_samples(sample_root, manifest.samples)
    if failures:
        print(f"Failures: {len(failures)}", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"Local sample result: {len(manifest.samples)}/{len(manifest.samples)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

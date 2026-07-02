"""Runtime discovery for loop package manifests."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List


PACKAGE_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class LoopPackage:
    name: str
    path: Path
    manifest: Dict[str, Any]
    workset_schema: Dict[str, Any]
    trace_contract: Dict[str, Any]

    @property
    def skill_name(self) -> str:
        return self.manifest["skill_interface"]["skill_name"]

    @property
    def version(self) -> str:
        return self.manifest["version"]

    @property
    def expected_tools(self) -> list[str]:
        return list(self.manifest["runtime_tools"])

    @property
    def route_keywords(self) -> list[str]:
        return list(self.manifest.get("route_keywords", []))


def discover_loop_packages(root: Path | None = None) -> Dict[str, LoopPackage]:
    root = Path(root or PACKAGE_ROOT)
    packages: Dict[str, LoopPackage] = {}
    for manifest_path in sorted(root.glob("*/manifest.json")):
        package = _load_package(manifest_path.parent)
        packages[package.name] = package
    return packages


def get_loop_package(name: str, root: Path | None = None) -> LoopPackage:
    packages = discover_loop_packages(root)
    try:
        return packages[name]
    except KeyError as exc:
        available = ", ".join(sorted(packages)) or "<none>"
        raise KeyError(f"Unknown loop package: {name}. Available: {available}") from exc


def route_skill_from_packages(goal: str, default: str = "project_management") -> str:
    packages = discover_loop_packages()
    for package in packages.values():
        for keyword in package.route_keywords:
            if keyword and keyword in goal:
                return package.skill_name
    return default


def validate_all_loop_packages(root: Path | None = None) -> Dict[str, Any]:
    packages = discover_loop_packages(root)
    errors: List[str] = []

    try:
        import main
    except Exception as exc:
        return {"status": "failed", "package_count": len(packages), "errors": [f"cannot import main: {exc}"]}

    runtime_skills = set(main.SKILL_TOOL_MAP)
    package_skills = set(packages)
    missing = sorted(runtime_skills - package_skills)
    extra = sorted(package_skills - runtime_skills)
    if missing:
        errors.append(f"missing loop packages: {missing}")
    if extra:
        errors.append(f"unknown loop packages: {extra}")

    for skill_name, package in packages.items():
        _validate_generic_package(package, errors)
        expected = list(main.SKILL_TOOL_MAP.get(skill_name, []))
        if package.expected_tools != expected:
            errors.append(f"{skill_name} runtime_tools mismatch with SKILL_TOOL_MAP")
            continue
        try:
            actual_tools = main._build_registry_for_skill(skill_name).list_tools()
        except Exception as exc:
            errors.append(f"{skill_name} registry build failed: {exc}")
            continue
        if actual_tools != package.expected_tools:
            errors.append(f"{skill_name} registry drift: expected {package.expected_tools}, got {actual_tools}")

    return {
        "status": "failed" if errors else "pass",
        "package_count": len(packages),
        "packages": sorted(packages),
        "errors": errors,
    }


def _load_package(package_dir: Path) -> LoopPackage:
    manifest = _load_json(package_dir / "manifest.json")
    workset_schema = _load_json(package_dir / manifest["workset_schema"])
    trace_contract = _load_json(package_dir / manifest["trace_contract"])
    return LoopPackage(
        name=manifest["name"],
        path=package_dir,
        manifest=manifest,
        workset_schema=workset_schema,
        trace_contract=trace_contract,
    )


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _validate_generic_package(package: LoopPackage, errors: List[str]) -> None:
    manifest = package.manifest
    if manifest.get("schema_version") != "agentplatform.loop_package.v1":
        errors.append(f"{package.name} schema_version mismatch")
    if manifest.get("name") != package.name:
        errors.append(f"{package.name} manifest name mismatch")
    if manifest.get("skill_interface", {}).get("skill_name") != package.name:
        errors.append(f"{package.name} skill_interface.skill_name mismatch")
    if not package.route_keywords:
        errors.append(f"{package.name} route_keywords must be non-empty")
    if not package.expected_tools:
        errors.append(f"{package.name} runtime_tools must be non-empty")
    for relative_key in ["workset_schema", "trace_contract"]:
        relative_path = manifest.get(relative_key)
        if not relative_path:
            errors.append(f"{package.name} missing {relative_key}")
        elif not (package.path / relative_path).exists():
            errors.append(f"{package.name} {relative_key} path missing: {relative_path}")

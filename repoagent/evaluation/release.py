"""Baseline comparison and release-grade evaluation evidence bundles."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
import subprocess
from uuid import uuid4

from ..evidence import sha256_file, verify_evidence_bundle
from .schema import EVALUATION_RESULT_SCHEMA, digest_path, validate_result_payload


RELEASE_EVIDENCE_SCHEMA = "repoagent.evaluation-release/v1"
_RELEASE_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


def _git(repo_root, *args):
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "git release identity lookup failed")
    return result.stdout.strip()


def _checked_relative(value):
    relative = Path(str(value))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("release manifest path must be safe and relative")
    return relative


def compare_results(baseline, candidate, *, max_pass_rate_drop=0.0):
    baseline = validate_result_payload(baseline)
    candidate = validate_result_payload(candidate)
    baseline_digest = baseline["benchmark"].get("definition_digest")
    candidate_digest = candidate["benchmark"].get("definition_digest")
    if baseline_digest != candidate_digest:
        raise ValueError("cannot compare results from different benchmark definitions")
    if baseline["model"].get("run_kind") != candidate["model"].get("run_kind"):
        raise ValueError("cannot compare different scripted/synthetic/live run kinds")
    baseline_rows = {
        (row["task_id"], row["variant"], row["repetition"]): row
        for row in baseline["rows"]
    }
    candidate_rows = {
        (row["task_id"], row["variant"], row["repetition"]): row
        for row in candidate["rows"]
    }
    if baseline_rows.keys() != candidate_rows.keys():
        raise ValueError("baseline and candidate row identities differ")
    regressions = []
    improvements = []
    for key in sorted(baseline_rows):
        before = baseline_rows[key]["status"] == "pass"
        after = candidate_rows[key]["status"] == "pass"
        if before and not after:
            regressions.append(key)
        elif after and not before:
            improvements.append(key)
    total = len(baseline_rows)
    baseline_rate = sum(row["status"] == "pass" for row in baseline_rows.values()) / total
    candidate_rate = sum(row["status"] == "pass" for row in candidate_rows.values()) / total
    delta = candidate_rate - baseline_rate
    passed = not regressions and delta >= -float(max_pass_rate_drop)
    return {
        "schema": "repoagent.evaluation-comparison/v1",
        "benchmark_digest": baseline_digest,
        "row_n": total,
        "baseline_pass_rate": baseline_rate,
        "candidate_pass_rate": candidate_rate,
        "pass_rate_delta": delta,
        "regressions": [list(key) for key in regressions],
        "improvements": [list(key) for key in improvements],
        "gate": {
            "status": "pass" if passed else "fail",
            "max_pass_rate_drop": float(max_pass_rate_drop),
            "requires_zero_row_regressions": True,
        },
    }


class ReleaseEvidenceBuilder:
    def build(
        self,
        result_path,
        destination,
        *,
        require_clean=True,
        release_tag="",
        repo_root=None,
        benchmark_path=None,
    ):
        result_path = Path(result_path).resolve()
        root = result_path.parent
        payload = validate_result_payload(
            json.loads(result_path.read_text(encoding="utf-8")),
            require_evidence=True,
        )
        if payload.get("schema") != EVALUATION_RESULT_SCHEMA:
            raise ValueError("release input is not an evaluation result")
        source = payload["source"]
        if not re.fullmatch(r"[0-9a-f]{40,64}", str(source.get("commit_sha", ""))):
            raise ValueError("release evidence requires an exact commit SHA")
        if (require_clean or release_tag) and source.get("dirty"):
            raise ValueError("release evidence requires a clean source tree")
        release_tag = str(release_tag)
        if release_tag:
            if not _RELEASE_TAG.fullmatch(release_tag):
                raise ValueError("release tag contains unsupported characters")
            repository = Path(repo_root or ".").resolve()
            if _git(repository, "status", "--porcelain"):
                raise ValueError("release repository must be clean")
            if _git(repository, "rev-parse", "HEAD") != source["commit_sha"]:
                raise ValueError("release repository HEAD does not match evaluation commit")
            tagged_commit = _git(
                repository,
                "rev-parse",
                f"refs/tags/{release_tag}^{{commit}}",
            )
            if tagged_commit != source["commit_sha"]:
                raise ValueError("release tag does not resolve to evaluation commit")
            if benchmark_path is None:
                raise ValueError("tagged release evidence requires the benchmark definition")

        destination = Path(destination)
        if destination.exists():
            raise FileExistsError(f"release destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{destination.name}.tmp-{uuid4().hex}"
        temporary.mkdir()
        try:
            shutil.copyfile(result_path, temporary / "results.json")
            copied = [
                {
                    "path": "results.json",
                    "sha256": sha256_file(temporary / "results.json"),
                    "size_bytes": (temporary / "results.json").stat().st_size,
                }
            ]
            if benchmark_path is not None:
                benchmark_path = Path(benchmark_path).resolve()
                if digest_path(benchmark_path) != payload["benchmark"].get(
                    "definition_digest"
                ):
                    raise ValueError("benchmark file does not match evaluation digest")
                shutil.copyfile(benchmark_path, temporary / "benchmark.json")
                copied.append(
                    {
                        "path": "benchmark.json",
                        "sha256": sha256_file(temporary / "benchmark.json"),
                        "size_bytes": (temporary / "benchmark.json").stat().st_size,
                    }
                )
            seen = set()
            for row in payload["rows"]:
                relative = _checked_relative(row["evidence"].get("bundle", ""))
                if relative in seen:
                    continue
                seen.add(relative)
                source_bundle = (root / relative).resolve()
                if root not in source_bundle.parents or not source_bundle.is_dir():
                    raise ValueError(f"row evidence bundle is unavailable: {relative}")
                target_bundle = temporary / relative
                shutil.copytree(source_bundle, target_bundle)
                for artifact in sorted(item for item in target_bundle.rglob("*") if item.is_file()):
                    copied.append(
                        {
                            "path": artifact.relative_to(temporary).as_posix(),
                            "sha256": sha256_file(artifact),
                            "size_bytes": artifact.stat().st_size,
                        }
                    )
            manifest = {
                "schema": RELEASE_EVIDENCE_SCHEMA,
                "release_tag": release_tag,
                "commit_sha": source["commit_sha"],
                "tree_digest": source.get("tree_digest", ""),
                "benchmark_digest": payload["benchmark"].get("definition_digest", ""),
                "run_kind": payload["model"]["run_kind"],
                "effective_n": payload["aggregates"].get("effective_n"),
                "run_n": payload["aggregates"].get("run_n"),
                "files": copied,
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            temporary.replace(destination)
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        return destination


def verify_release_bundle(path, *, require_tag=True):
    root = Path(path).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != RELEASE_EVIDENCE_SCHEMA:
        raise ValueError("unsupported release evidence schema")
    tag = str(manifest.get("release_tag", ""))
    if require_tag and not _RELEASE_TAG.fullmatch(tag):
        raise ValueError("release evidence is not bound to a valid tag")
    commit_sha = str(manifest.get("commit_sha", ""))
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
        raise ValueError("release evidence has no exact commit identity")
    records = manifest.get("files", [])
    if not records:
        raise ValueError("release evidence manifest contains no files")
    for record in records:
        relative = _checked_relative(record.get("path", ""))
        artifact = root / relative
        if not artifact.is_file():
            raise ValueError(f"release artifact is missing: {relative.as_posix()}")
        if sha256_file(artifact) != record.get("sha256"):
            raise ValueError(f"release checksum mismatch: {relative.as_posix()}")
        if artifact.stat().st_size != int(record.get("size_bytes", -1)):
            raise ValueError(f"release size mismatch: {relative.as_posix()}")
    result = validate_result_payload(
        json.loads((root / "results.json").read_text(encoding="utf-8")),
        require_evidence=True,
    )
    if result["source"].get("commit_sha") != commit_sha:
        raise ValueError("release result and manifest commit identities differ")
    if require_tag and result["source"].get("dirty"):
        raise ValueError("tagged release evidence cannot come from a dirty source")
    if result["benchmark"].get("definition_digest") != manifest.get(
        "benchmark_digest"
    ):
        raise ValueError("release result and manifest benchmark identities differ")
    if require_tag and not (root / "benchmark.json").is_file():
        raise ValueError("tagged release evidence is missing its benchmark definition")
    for row in result["rows"]:
        relative = _checked_relative(row["evidence"].get("bundle", ""))
        verify_evidence_bundle(root / relative)
    return manifest


__all__ = [
    "RELEASE_EVIDENCE_SCHEMA",
    "ReleaseEvidenceBuilder",
    "compare_results",
    "verify_release_bundle",
]

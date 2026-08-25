import json
import subprocess

import pytest

from repoagent.evaluation.release import ReleaseEvidenceBuilder, verify_release_bundle
from repoagent.evaluation.resume import (
    render_resume_claims_markdown,
    resume_claims_from_release,
)
from repoagent.evaluation.schema import EvaluationResult, EvaluationRow, digest_path
from repoagent.release_check import ReleaseCheckError, inspect_release_source
from repoagent.offline_demo import main as demo_main


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repository(root, *, version="1.2.3"):
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "repoagent"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "release")
    _git(root, "tag", f"v{version}")
    return _git(root, "rev-parse", "HEAD")


def _result(root, commit_sha, benchmark):
    evidence = root / "campaign" / "evidence" / "task"
    evidence.mkdir(parents=True)
    (evidence / "manifest.json").write_text(
        json.dumps({"schema": "repoagent.evidence-bundle/v1", "files": []}) + "\n",
        encoding="utf-8",
    )
    result = EvaluationResult(
        experiment={"id": "release", "suite": "contract"},
        source={
            "commit_sha": commit_sha,
            "branch": "main",
            "dirty": False,
            "tree_digest": "sha256:" + "1" * 64,
        },
        environment={"python": "3.12", "lock_digest": "sha256:" + "2" * 64},
        benchmark={
            "id": "runtime-contract",
            "version": 1,
            "definition_digest": digest_path(benchmark),
            "unique_tasks": 1,
        },
        model={"run_kind": "scripted", "provider": "scripted", "model": "fake"},
        design={"variants": ["contract"], "repetitions": 1},
        rows=[
            EvaluationRow(
                "task",
                "contract",
                0,
                "pass",
                evidence={"bundle": "evidence/task"},
            )
        ],
        aggregates={"effective_n": 1, "run_n": 1, "passes": 1, "pass_rate": 1.0},
        limitations=["Scripted contract result; not a coding-quality claim."],
    )
    return result.write(root / "campaign" / "results.json")


def test_release_preflight_requires_clean_tagged_source_and_tracked_lock(tmp_path):
    root = tmp_path / "repository"
    commit = _repository(root)

    report = inspect_release_source(root, tag="v1.2.3")
    assert report["commit_sha"] == commit
    assert report["python"] == ["3.10", "3.11", "3.12"]
    with pytest.raises(ReleaseCheckError, match="package version"):
        inspect_release_source(root, tag="v9.9.9")

    (root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ReleaseCheckError, match="clean"):
        inspect_release_source(root, tag="v1.2.3")


def test_tagged_release_bundle_is_self_contained_verified_and_resume_safe(tmp_path):
    repository = tmp_path / "repository"
    commit = _repository(repository)
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text('{"tasks": []}\n', encoding="utf-8")
    result_path = _result(tmp_path, commit, benchmark)

    bundle = ReleaseEvidenceBuilder().build(
        result_path,
        tmp_path / "release",
        release_tag="v1.2.3",
        repo_root=repository,
        benchmark_path=benchmark,
    )
    manifest = verify_release_bundle(bundle)
    assert manifest["release_tag"] == "v1.2.3"
    assert (bundle / "benchmark.json").is_file()

    claims = resume_claims_from_release(bundle)
    assert claims["workload"]["effective_n"] == 1
    assert claims["model"]["run_kind"] == "scripted"
    markdown = render_resume_claims_markdown(claims)
    assert "1 unique tasks, 1 runs" in markdown
    assert "not a coding-quality claim" in markdown

    (bundle / "results.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        verify_release_bundle(bundle)


def test_tagged_release_cannot_override_dirty_provenance(tmp_path):
    repository = tmp_path / "repository"
    commit = _repository(repository)
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text("{}\n", encoding="utf-8")
    result_path = _result(tmp_path, commit, benchmark)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["source"]["dirty"] = True
    result_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="clean source"):
        ReleaseEvidenceBuilder().build(
            result_path,
            tmp_path / "release",
            require_clean=False,
            release_tag="v1.2.3",
            repo_root=repository,
            benchmark_path=benchmark,
        )


def test_resume_claims_reject_untagged_development_bundle(tmp_path):
    repository = tmp_path / "repository"
    commit = _repository(repository)
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text("{}\n", encoding="utf-8")
    result_path = _result(tmp_path, commit, benchmark)
    bundle = ReleaseEvidenceBuilder().build(result_path, tmp_path / "development")

    assert verify_release_bundle(bundle, require_tag=False)["release_tag"] == ""
    with pytest.raises(ValueError, match="valid tag"):
        resume_claims_from_release(bundle)


def test_offline_demo_runs_contracts_and_verifies_evidence(tmp_path, capsys):
    assert demo_main(["--repo-root", ".", "--output", str(tmp_path / "demo")]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["passes"] == summary["effective_n"] == 12
    assert summary["evidence_bundles"] == 12

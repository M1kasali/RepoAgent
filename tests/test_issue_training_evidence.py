from copy import deepcopy

import pytest

from repoagent.evolver.generator import CandidateGenerator
from repoagent.issue_agent.training_evidence import freeze_observation, reviewed_failure


@pytest.fixture
def state():
    result = {
        "status": "completed", "exit_code": 1, "passed": False,
        "reproduced": True, "output_truncated": False,
        "stderr": "SECRET", "stdout": "SECRET",
    }
    return {
        "case_id": "issue_example", "status": "verification_failed",
        "repository": {"base_revision": "a" * 40, "path": "/private/SECRET"},
        "issue": {"body": "SECRET"},
        "runs": [{"phase": "fix", "status": "completed", "verification": {
            "baseline": deepcopy(result), "candidate": deepcopy(result),
        }}],
    }


def freeze(state, tmp_path, split="training"):
    return freeze_observation(state, output=tmp_path / "observation.json",
                              task_id="task", family="family", split=split)


def approval(envelope):
    return {"schema": "repoagent.issue-training-review/v1", "approved": True,
            "reviewer": "maintainer", "observation_digest": envelope["digest"]}


def test_export_omits_raw_content_and_cannot_overwrite(state, tmp_path):
    freeze(state, tmp_path)
    assert "SECRET" not in (tmp_path / "observation.json").read_text()
    assert (tmp_path / "observation.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        freeze(state, tmp_path)


def test_reviewed_evidence_drives_existing_candidate_generator(state, tmp_path):
    envelope = freeze(state, tmp_path)
    evidence = reviewed_failure(state, envelope, approval(envelope))
    generator = CandidateGenerator({"prompt": lambda items: {
        "repoagent/prompt_prefix.py": b"verify boundary behavior",
    }})
    proposal = generator.generate(label="prompt", base_commit="b" * 40,
                                  evidence=[evidence], repository_reader=lambda _: b"before")
    assert proposal.manifest.evidence_ids == (evidence.evidence_id,)
    assert evidence.artifact_digest == "sha256:" + envelope["digest"]


@pytest.mark.parametrize("change", ["state", "observation", "approval", "digest"])
def test_drift_or_missing_approval_rejected(state, tmp_path, change):
    envelope = freeze(state, tmp_path)
    review = approval(envelope)
    if change == "state":
        state["issue"]["body"] = "changed"
    elif change == "observation":
        envelope["observation"]["family"] = "other"
    elif change == "approval":
        review["approved"] = False
    else:
        review["observation_digest"] = "0" * 64
    with pytest.raises(ValueError):
        reviewed_failure(state, envelope, review)


def test_sealed_failure_cannot_be_used_for_training(state, tmp_path):
    envelope = freeze(state, tmp_path, "sealed")
    with pytest.raises(ValueError, match="sealed"):
        reviewed_failure(state, envelope, approval(envelope))


def test_running_failure_is_not_eligible(state, tmp_path):
    state["runs"][-1]["status"] = "running"
    with pytest.raises(ValueError):
        freeze(state, tmp_path)


def test_cli_export_and_review(state, tmp_path):
    import json
    from repoagent.cli import build_product_parser
    from repoagent.issue_agent.cases import CaseStore
    from repoagent.issue_agent.cli import run

    store = CaseStore(tmp_path / "cases")
    state["schema"] = "repoagent.issue-case/v1"
    # Use the store's canonical identity rules.
    state["case_id"] = "issue_" + "a" * 24
    store.directory(state["case_id"]).mkdir(parents=True)
    store.save(state)
    path = tmp_path / "observation.json"
    common = [state["case_id"], "--store", str(tmp_path / "cases")]
    parser = build_product_parser()
    exported = run(parser.parse_args([
        "issue", "export-feedback", *common, "--output", str(path),
        "--task-id", "task", "--family", "family", "--split", "training",
    ]))
    review = tmp_path / "review.json"
    review.write_text(json.dumps(approval(exported)))
    result = run(parser.parse_args([
        "issue", "training-evidence", *common,
        "--observation", str(path), "--review", str(review),
    ]))
    assert result["task_id"] == "task"
    assert result["artifact_digest"] == "sha256:" + exported["digest"]


@pytest.mark.parametrize("envelope", [{}, {"observation": []}, {"observation": {}}])
def test_malformed_envelope_rejected(state, envelope):
    with pytest.raises(ValueError, match="envelope"):
        reviewed_failure(state, envelope, {})

from copy import deepcopy

import pytest

from repoagent.issue_agent.campaign import (
    freeze_campaign, load_campaign, snapshot_task, training_input, validate_campaign,
)
from repoagent.issue_agent.training_evidence import freeze_observation


def row(name, split):
    return {"task_id": name, "case_id": "issue_" + name, "family": name,
            "split": split, "base_revision": "a" * 40,
            "source_issue": "https://github.com/example/project/issues/" + ("1" if name == "train" else "2"),
            "prompt": "Repair the implementation.", "files": {"main.py": name},
            "behavior_files": ["main.py"], "behavior_checks": [{
                "check_id": "result", "program": "print(42)", "expected_json": "42",
            }]}


@pytest.fixture
def config():
    return {"schema": "repoagent.issue-campaign/v1",
            "tasks": [row("train", "training"), row("heldout", "sealed")]}


def test_frozen_campaign_loads_and_produces_snapshot_contract(config, tmp_path):
    path = tmp_path / "campaign.json"
    manifest = freeze_campaign(config, path)
    loaded = load_campaign(path, expected_digest=manifest["config_digest"])
    task = snapshot_task(loaded["tasks"][0])
    assert task.model_mode == "host"
    assert task.native_tools is True
    assert task.worker_input()["native_tools"] is True
    assert "behavior_checks" not in task.worker_input()
    assert not task.responses
    with pytest.raises(FileExistsError):
        freeze_campaign(config, path)
    with pytest.raises(ValueError, match="changed"):
        load_campaign(path, expected_digest="wrong")


@pytest.mark.parametrize("field", ["case_id", "task_id", "family", "files", "source_issue"])
def test_cross_split_reuse_rejected(config, field):
    config["tasks"][1][field] = deepcopy(config["tasks"][0][field])
    with pytest.raises(ValueError):
        validate_campaign(config)


def test_reviewed_issue_materializes_only_registered_training_task(config, tmp_path):
    candidate = {"status": "completed", "exit_code": 1, "passed": False,
                 "reproduced": True, "output_truncated": False}
    state = {"case_id": "issue_train", "status": "verification_failed",
             "repository": {"base_revision": "a" * 40},
             "issue": {"url": config["tasks"][0]["source_issue"]},
             "runs": [{"phase": "fix", "status": "completed", "verification": {
                 "baseline": candidate, "candidate": candidate}}]}
    envelope = freeze_observation(state, output=tmp_path / "observation.json",
                                  task_id="train", family="train", split="training")
    pinned = validate_campaign(config)["config_digest"]
    review = {"schema": "repoagent.issue-training-review/v1", "approved": True,
              "reviewer": "operator", "observation_digest": envelope["digest"],
              "campaign_digest": pinned}
    evidence, task = training_input(state, envelope, review, campaign=config, expected_digest=pinned)
    assert task.task_id == evidence.task_id == "train"
    config["tasks"][0]["split"] = "sealed"
    config["tasks"][1]["split"] = "training"
    with pytest.raises(ValueError, match="frozen"):
        training_input(state, envelope, review, campaign=config, expected_digest=pinned)

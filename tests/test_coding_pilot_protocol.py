from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from repoagent.evolver.pilot_protocol import (
    freeze_pilot,
    preflight_pilot,
    verify_frozen_pilot,
)
from repoagent.evolver.workspace import _git


@pytest.fixture
def pilot(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    (root / "repoagent").mkdir()
    prefix = root / "repoagent/prompt_prefix.py"
    prefix.write_text("PREFIX = 'baseline'\n")
    _git(root, "add", ".")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-m", "baseline")
    baseline = _git(root, "rev-parse", "HEAD")
    prefix.write_text("PREFIX = 'candidate'\n")
    _git(root, "add", ".")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-m", "candidate")
    candidate = _git(root, "rev-parse", "HEAD")
    config = {
        "schema": "repoagent.coding-pilot-config/v1",
        "experiment_id": "fixture-only",
        "intervention": "prompt",
        "baseline_commit": baseline,
        "candidate_commit": candidate,
        "image_id": "sha256:" + "a" * 64,
        "model": {
            "provider": "fixture",
            "model": "fixture",
            "configuration_digest": "sha256:" + "b" * 64,
            "counter_identity": "fixture-v1",
        },
        "limits": {
            "max_calls": 4,
            "max_input_tokens": 20000,
            "max_output_tokens": 512,
            "max_estimated_cost_usd": 1,
            "timeout_seconds": 60,
        },
        "pricing": {
            "input_per_1m_usd": 1,
            "output_per_1m_usd": 2,
            "cache_read_per_1m_usd": 0,
            "cache_write_per_1m_usd": 0,
            "source": "fixture-only-not-provider-pricing",
        },
        "max_total_cost_usd": 48,
        "tasks": [
            {
                "task_id": f"task_{i}",
                "family": f"family_{i}",
                "split": "training" if i < 12 else "sealed",
                "prompt": f"Fixture {i}: implement solve in solution.py.",
                "files": {"solution.py": "def solve(): raise NotImplementedError\n"},
                "behavior_files": ["solution.py"],
                "behavior_checks": [
                    {
                        "check_id": "answer",
                        "program": "raise RuntimeError('must not run on host')",
                        "expected_json": str(i),
                    }
                ],
            }
            for i in range(24)
        ],
    }
    return root, config


def test_fixed_pair_preflight_has_no_execution_authority(pilot):
    root, config = pilot
    before = deepcopy(config)
    result = preflight_pilot(config, repo_root=root)
    assert config == before
    assert result["status"] == "preflight_only"
    assert result["execution_authorized"] is False
    assert result["reserved_trials"] == 48
    assert result["max_model_calls"] == 192
    assert result["reserved_cost_usd"] == "48"
    assert result["task_counts"] == {"training": 12, "sealed": 12}
    assert "must not run on host" not in json.dumps(result)
    assert result["analysis"]["automatic_promotion"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "family",
        "duplicate_id",
        "duplicate_input",
        "small_split",
        "budget",
        "nan",
        "ref",
        "image",
        "cache_price",
        "mixed_changes",
        "unknown_field",
        "no_checks",
        "model_calls",
    ],
)
def test_preflight_rejects_invalid_design(pilot, defect):
    root, config = pilot
    if defect == "family":
        config["tasks"][-1]["family"] = config["tasks"][0]["family"]
    elif defect == "duplicate_id":
        config["tasks"][-1]["task_id"] = config["tasks"][0]["task_id"]
    elif defect == "duplicate_input":
        config["tasks"][-1]["prompt"] = config["tasks"][0]["prompt"]
    elif defect == "small_split":
        config["tasks"][0]["split"] = "sealed"
    elif defect == "budget":
        config["max_total_cost_usd"] = 47.99
    elif defect == "nan":
        config["max_total_cost_usd"] = float("nan")
    elif defect == "ref":
        config["baseline_commit"] = "HEAD"
    elif defect == "image":
        config["image_id"] = "python:3.12-slim"
    elif defect == "cache_price":
        config["pricing"]["cache_read_per_1m_usd"] = None
    elif defect == "mixed_changes":
        (root / "unrelated.txt").write_text("bad")
        _git(root, "add", ".")
        _git(root, "-c", "commit.gpgsign=false", "commit", "-m", "unrelated")
        config["candidate_commit"] = _git(root, "rev-parse", "HEAD")
    elif defect == "unknown_field":
        config["api_key"] = "not-allowed"
    elif defect == "no_checks":
        config["tasks"][0]["behavior_checks"] = []
    elif defect == "model_calls":
        config["limits"]["max_calls"] = 21
    with pytest.raises(ValueError):
        preflight_pilot(config, repo_root=root)


def test_skills_and_prompt_cannot_be_mixed(pilot):
    root, config = pilot
    _git(root, "checkout", "--detach", config["baseline_commit"])
    (root / "skills/example").mkdir(parents=True)
    (root / "skills/example/SKILL.md").write_text("fixture skill")
    _git(root, "add", ".")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-m", "skill")
    config["candidate_commit"] = _git(root, "rev-parse", "HEAD")
    config["intervention"] = "skills"
    result = preflight_pilot(config, repo_root=root)
    assert result["changed_paths"] == ["skills/example/SKILL.md"]
    config["intervention"] = "prompt"
    with pytest.raises(ValueError, match="isolate"):
        preflight_pilot(config, repo_root=root)


def test_freeze_private_files_no_overwrite_and_digest_binding(pilot, tmp_path):
    root, config = pilot
    source = tmp_path / "config.json"
    source.write_text(json.dumps(config))
    out = tmp_path / "frozen"
    result = freeze_pilot(source, repo_root=root, output_root=out)
    assert verify_frozen_pilot(out, repo_root=root) == result
    assert json.loads((out / "private-config.json").read_text()) == config
    assert (out.stat().st_mode & 0o777) == 0o700
    assert ((out / "private-config.json").stat().st_mode & 0o777) == 0o600
    with pytest.raises(FileExistsError):
        freeze_pilot(source, repo_root=root, output_root=out)
    config["tasks"][0]["behavior_checks"][0]["expected_json"] = "999"
    assert (
        preflight_pilot(config, repo_root=root)["config_digest"]
        != result["config_digest"]
    )
    assert (
        preflight_pilot(config, repo_root=root)["task_descriptors"]
        != result["task_descriptors"]
    )
    (out / "private-config.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="changed"):
        verify_frozen_pilot(out, repo_root=root)


def test_unaffordable_single_call_is_rejected(pilot):
    root, config = pilot
    config["pricing"]["input_per_1m_usd"] = 1000
    with pytest.raises(ValueError, match="one fully reserved"):
        preflight_pilot(config, repo_root=root)


def test_preflight_receipt_cannot_be_silently_changed(pilot, tmp_path):
    root, config = pilot
    source = tmp_path / "config.json"
    source.write_text(json.dumps(config))
    out = tmp_path / "frozen"
    result = freeze_pilot(source, repo_root=root, output_root=out)
    result["execution_authorized"] = True
    (out / "preflight.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="changed"):
        verify_frozen_pilot(out, repo_root=root)


def test_private_inputs_and_output_cannot_enter_snapshot(pilot, tmp_path):
    root, config = pilot
    inside = root / "private.json"
    inside.write_text(json.dumps(config))
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(config))
    for source, output in [(inside, tmp_path / "out"), (outside, root / "out")]:
        with pytest.raises(ValueError, match="outside"):
            freeze_pilot(source, repo_root=root, output_root=output)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        freeze_pilot(
            alias / "private.json", repo_root=root, output_root=tmp_path / "out"
        )


def test_cli_only_prepares_and_never_executes_probes(pilot, tmp_path):
    root, config = pilot
    source = tmp_path / "config.json"
    source.write_text(json.dumps(config))
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1] / "scripts/prepare_coding_pilot.py"
            ),
            "--repo",
            str(root),
            "--config",
            str(source),
            "--output",
            str(tmp_path / "cli-output"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "execution_authorized=false" in result.stdout

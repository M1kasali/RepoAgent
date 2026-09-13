import pytest

from repoagent.call_efficiency import CallEfficiencyEntry
from repoagent.evaluation.accounting import run_accounting_experiment


def test_runtime_accounting_cases_preserve_unknown_cost(tmp_path):
    root = tmp_path / "acceptance"
    result = run_accounting_experiment(root)
    assert result["passed"] is True
    assert result["positive_claim_eligible"] is False
    assert len(result["cases"]) == 7
    assert sum(len(row["calls"]) for row in result["cases"]) == 8
    assert all((root / row["run_directory"]).is_dir() for row in result["cases"])
    assert all(row["run_directory"].startswith(row["case"] + "/runs/")
               for row in result["cases"])
    with pytest.raises(FileExistsError):
        run_accounting_experiment(root)


def test_acceptance_rejects_broken_cost_arithmetic(tmp_path, monkeypatch):
    monkeypatch.setattr(CallEfficiencyEntry, "estimated_cost_usd", property(lambda self: 0.0))
    result = run_accounting_experiment(tmp_path / "broken")
    assert result["passed"] is False
    assert result["cases"][0]["checks"]["unit_cost"] is False

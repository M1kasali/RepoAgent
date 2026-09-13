import pytest

from repoagent.evaluation.context_memory import run_context_memory_experiment
from repoagent.sqlite_memory import SQLiteMemoryBackend


def test_context_and_memory_acceptance(tmp_path):
    root = tmp_path / "result"
    result = run_context_memory_experiment(root)
    assert result["passed"] is True
    assert result["positive_claim_eligible"] is False
    assert result["tight_context_success_count"] == 2
    assert len(result["context"]) == 2
    assert len(result["memory"]) == 3
    with pytest.raises(FileExistsError):
        run_context_memory_experiment(root)


def test_broken_recall_fails_acceptance(tmp_path, monkeypatch):
    async def empty(self, query, **kwargs):
        return []

    monkeypatch.setattr(SQLiteMemoryBackend, "recall", empty)
    result = run_context_memory_experiment(tmp_path / "result")
    assert result["passed"] is False
    assert all(not row["checks"]["shared_fact_present"] for row in result["memory"])

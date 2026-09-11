import json

import pytest

from repoagent.evolver import ModelCandidateProposer
from test_evolver_materialization import repository as repository
from test_evolver_contracts import _evidence
from test_evolver_model_budget import _client, LeafClient


def proposer(repository, tmp_path, output):
    root, proposal, _ = repository
    return ModelCandidateProposer(
        root,
        base_commit=proposal.manifest.base_commit,
        label="prompt",
        paths=["repoagent/prompt_prefix.py"],
        evidence=[_evidence()],
        client=_client(LeafClient(outputs=[output])),
        journal_directory=tmp_path / "generation",
    )


def test_model_output_builds_bounded_manifest_and_preserves_checkout(
    repository, tmp_path
):
    p = proposer(
        repository,
        tmp_path,
        json.dumps({"files": {"repoagent/prompt_prefix.py": "new prompt\n"}}),
    )
    result = p({"base_commit": p.base, "history": []})
    assert result.content["repoagent/prompt_prefix.py"] == b"new prompt\n"
    assert (repository[0] / "repoagent/prompt_prefix.py").read_text() == "old prompt\n"
    assert p.client.evidence()["calls_reserved"] == 1
    assert p.journal.ledger.events()[-1]["payload"]["status"] == "candidate_generated"


@pytest.mark.parametrize(
    "output",
    [
        "{}",
        '{"files":{"protected.txt":"oops"}}',
        '{"files":{"repoagent/prompt_prefix.py":2}}',
        '{"files":{},"files":{}}',
        "not JSON",
        json.dumps({"files": {"repoagent/prompt_prefix.py": "x" * 9000}}),
    ],
)
def test_malformed_and_out_of_scope_candidates_are_not_written(
    repository, tmp_path, output
):
    p = proposer(repository, tmp_path, output)
    with pytest.raises(ValueError):
        p({"base_commit": p.base, "history": []})
    assert (repository[0] / "protected.txt").read_text() == "unchanged"
    assert p.client.evidence()["cost_complete"]

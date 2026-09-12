from dataclasses import replace
from threading import Barrier

import pytest

from repoagent.skill_ranking import BM25, LocalSkillSource, SkillRouter, tokenize
from repoagent.skill_selection import ModelSkillGate, SkillResolver
from repoagent.skills import LocalSkillPool, SkillCatalog, SkillChangeWatcher
from repoagent import FakeModelClient
from repoagent.providers.base import ProviderCancelledError
from repoagent.skill_refs import resolve_skill_refs
from test_skills import build_agent, write_skill


def resolver(root, **kwargs):
    catalog = SkillCatalog({"workspace": root}, env={})
    return SkillResolver(
        catalog, SkillRouter([LocalSkillSource(LocalSkillPool(catalog))]), **kwargs
    )


def test_bm25_handles_chinese_empty_and_term_frequency():
    index = BM25(
        [
            tokenize("pytest pytest failures"),
            tokenize("unrelated"),
            tokenize("代码审查"),
        ]
    )
    assert index.scores(tokenize("pytest"))[0] > 0
    assert index.scores(tokenize("pytest"))[1:] == [0, 0]
    assert index.scores(tokenize("代码"))[2] > 0
    assert BM25([]).scores(["pytest"]) == []
    assert index.scores([]) == [0, 0, 0]


def test_body_only_relevance_is_reference_not_activation(tmp_path):
    write_skill(tmp_path, "review", body="Investigate regression and coverage.")
    result = resolver(tmp_path).resolve("Investigate regression", available_tools=[])
    assert not result.activated
    assert [hit.qualified_id for hit in result.references] == ["workspace/review"]


def test_stopword_match_does_not_offer_reference(tmp_path):
    write_skill(tmp_path, "review", body="Use the checklist.")
    result = resolver(tmp_path).resolve("the", available_tools=[])
    assert not result.activated and not result.references


def test_always_is_separate_and_tool_requirements_are_enforced(tmp_path):
    write_skill(tmp_path, "always", extra="always: true\n")
    write_skill(tmp_path, "pytest", extra="requires_tools: shell\n")
    selection = resolver(tmp_path)
    blocked = selection.resolve("pytest always", available_tools=[])
    assert [hit.qualified_id for hit in blocked.activated] == ["workspace/always"]
    assert blocked.diagnostics["rejected"]["workspace/pytest"]["missing_tools"] == [
        "shell"
    ]
    admitted = selection.resolve("pytest always", available_tools=["shell"])
    assert [hit.qualified_id for hit in admitted.activated] == [
        "workspace/always",
        "workspace/pytest",
    ]


def test_pool_refreshes_index_on_body_change_and_removal(tmp_path):
    directory = write_skill(tmp_path, "review", body="oldword")
    catalog = SkillCatalog({"workspace": tmp_path})
    pool, watcher = LocalSkillPool(catalog), SkillChangeWatcher(catalog)
    assert pool.search("oldword")
    path = directory / "SKILL.md"
    path.write_text(path.read_text().replace("oldword", "newword"))
    watcher.poll()
    assert not pool.search("oldword")
    assert pool.search("newword")
    path.unlink()
    watcher.poll()
    assert not pool.search("newword")


class Source:
    def __init__(self, name, hits, weight=1):
        self.name, self.hits, self.weight = name, hits, weight

    def search(self, query, history, k):
        if isinstance(self.hits, Exception):
            raise self.hits
        return self.hits[:k]


def test_rrf_uses_rank_not_score_scale_and_isolates_source_failure(tmp_path):
    write_skill(tmp_path, "review")
    hit = LocalSkillPool(SkillCatalog({"workspace": tmp_path})).search("review")[0]
    alternate = replace(
        hit, manifest=replace(hit.manifest, source="alternate"), score=1e9
    )
    router = SkillRouter(
        [
            Source("local", [hit, hit], 2),
            Source("alternate", [alternate]),
            Source("failed", RuntimeError("do not log sensitive payload")),
        ]
    )
    hits, diagnostics = router.select("review")
    assert len(hits) == 1
    assert hits[0].qualified_id == "workspace/review"
    assert hits[0].score == pytest.approx(3 / 61)
    assert diagnostics["failed_sources"] == {"failed": "RuntimeError"}
    assert diagnostics["contributing_sources"]["workspace/review"] == [
        "local",
        "alternate",
    ]


@pytest.mark.parametrize(
    "response,gate_state,active",
    [
        ('{"skills": []}', "accepted", []),
        (
            '{"skills": ["unknown", "workspace/pytest", "workspace/pytest"]}',
            "accepted",
            ["workspace/pytest"],
        ),
        (
            '```json\n{"skills": ["workspace/pytest"]}\n```',
            "accepted",
            ["workspace/pytest"],
        ),
        ("not json", "fallback", ["workspace/pytest"]),
        ('{"skills": [5]}', "fallback", ["workspace/pytest"]),
    ],
)
def test_gate_empty_unknown_duplicate_and_malformed_outputs(
    tmp_path, response, gate_state, active
):
    write_skill(tmp_path, "pytest")
    client = FakeModelClient([response])
    result = resolver(tmp_path, gate=ModelSkillGate(client)).resolve(
        "pytest", available_tools=[]
    )
    assert result.diagnostics["gate"] == gate_state
    assert [hit.qualified_id for hit in result.activated] == active
    assert len(client.prompts) == 1
    if gate_state == "accepted" and not active:
        assert not result.references


def test_gate_cannot_reenable_blocked_skill(tmp_path):
    write_skill(tmp_path, "pytest", extra="requires_tools: shell\n")
    client = FakeModelClient(['{"skills": ["workspace/pytest"]}'])
    result = resolver(tmp_path, gate=ModelSkillGate(client)).resolve(
        "pytest", available_tools=[]
    )
    assert not result.activated and not result.references
    assert not client.prompts


def test_runtime_reference_budget_metadata(tmp_path):
    write_skill(
        tmp_path / "skills", "review", body="Investigate regression with SECRET_BODY."
    )
    agent = build_agent(tmp_path, ["<final>Done.</final>"])
    agent.ask("Investigate regression")
    prompt = agent.model_client.prompts[0]
    assert "Skill references" in prompt and "workspace/review" in prompt
    assert "SECRET_BODY" not in prompt
    assert agent.last_prompt_metadata["skills"]["activated_count"] == 0
    assert agent.last_prompt_metadata["skill_selection"]["reference_ids"] == [
        "workspace/review"
    ]


def test_activation_limit_references_remaining_candidates(tmp_path):
    for name in ("first", "second", "third"):
        write_skill(tmp_path, name)
    result = resolver(tmp_path).resolve("first second third", available_tools=[])
    assert len(result.activated) == 2
    assert len(result.references) == 1


@pytest.mark.parametrize("weight", [0, -1, float("nan"), float("inf"), True])
def test_router_rejects_invalid_weights(weight):
    with pytest.raises(ValueError):
        SkillRouter([Source("invalid", [], weight)])


def test_provider_gate_failure_falls_back_but_cancellation_propagates(tmp_path):
    write_skill(tmp_path, "pytest")

    class Gate:
        error = TimeoutError("private detail")

        def select(self, *args):
            raise self.error

    gate = Gate()
    selection = resolver(tmp_path, gate=gate)
    result = selection.resolve("pytest", available_tools=[])
    assert len(result.activated) == 1
    assert result.diagnostics["fallback_reason"] == "TimeoutError"
    gate.error = ProviderCancelledError("cancelled")
    with pytest.raises(ProviderCancelledError):
        selection.resolve("pytest", available_tools=[])


def test_skill_refs_resolve_existing_contained_files_and_preserve_fences(tmp_path):
    directory = tmp_path / "skill"
    (directory / "references").mkdir(parents=True)
    (directory / "references" / "guide.md").write_text("Never read eagerly")
    (tmp_path / "outside.txt").write_text("outside")
    (directory / "references" / "escape").symlink_to(tmp_path / "outside.txt")
    body = (
        "[guide](references/guide.md#section)\n"
        "{baseDir}/references/guide.md\n"
        "{baseDir}/missing.txt\n"
        "{baseDir}/../outside.txt\n"
        "[escape](references/escape)\n"
        "```text\n{baseDir}/references/guide.md\n```\n"
        "~~~\n[guide](references/guide.md)\n~~~\n"
    )
    output = resolve_skill_refs(body, directory)
    assert f"[guide]({directory}/references/guide.md#section)" in output
    assert f"\n{directory}/references/guide.md\n" in output
    assert "{baseDir}/missing.txt" in output
    assert "{baseDir}/../outside.txt" in output
    assert "[escape](references/escape)" in output
    assert "```text\n{baseDir}/references/guide.md\n```" in output
    assert "~~~\n[guide](references/guide.md)\n~~~" in output


def test_runtime_gate_rejection_and_disabled_skills(tmp_path):
    write_skill(tmp_path / "skills", "pytest", body="BODY_MARKER")
    gate_client = FakeModelClient(['{"skills": []}'])
    agent = build_agent(
        tmp_path, ["<final>Done.</final>"], skill_gate=ModelSkillGate(gate_client)
    )
    agent.ask("pytest")
    assert "BODY_MARKER" not in agent.model_client.prompts[0]
    assert not agent.active_skills and not agent.skill_references
    disabled = build_agent(
        tmp_path,
        ["<final>Done.</final>"],
        feature_flags={"skills": False},
        skill_gate=ModelSkillGate(FakeModelClient([])),
    )
    disabled.ask("pytest")
    assert not disabled.active_skills and not disabled.skill_references


def test_empty_query_only_retains_always_skills(tmp_path):
    write_skill(tmp_path, "always", extra="always: true\n")
    write_skill(tmp_path, "pytest")
    result = resolver(tmp_path).resolve(" ", available_tools=[])
    assert [hit.qualified_id for hit in result.activated] == ["workspace/always"]
    assert not result.references


def test_watcher_retries_after_invalid_manifest_is_corrected(tmp_path):
    catalog = SkillCatalog({"workspace": tmp_path})
    watcher = SkillChangeWatcher(catalog)
    directory = write_skill(tmp_path, "review", extra="references: missing.md\n")
    with pytest.raises(ValueError):
        watcher.poll()
    assert catalog.list() == ()
    (directory / "missing.md").write_text("fixed")
    assert watcher.poll()
    assert len(catalog.list()) == 1


def test_router_fans_out_sources_concurrently_and_rejects_invalid_hits(tmp_path):
    barrier = Barrier(2)

    class ConcurrentSource(Source):
        def search(self, query, history, k):
            barrier.wait(timeout=3)
            return []

    router = SkillRouter([ConcurrentSource("one", []), ConcurrentSource("two", [])])
    hits, diagnostics = router.select("review")
    assert not hits and not diagnostics["failed_sources"]
    _, diagnostics = SkillRouter([Source("bad", [object()])]).select("review")
    assert diagnostics["failed_sources"] == {"bad": "ValueError"}


def test_external_content_without_file_activates_but_deleted_local_file_does_not(
    tmp_path,
):
    directory = write_skill(tmp_path, "review", body="Inspect regressions")
    catalog = SkillCatalog({"workspace": tmp_path})
    hit = LocalSkillPool(catalog).search("regressions")[0]
    external = replace(
        hit,
        manifest=replace(hit.manifest, source="external", path=tmp_path / "absent.md"),
    )
    result = SkillResolver(
        catalog, SkillRouter([Source("external", [external])])
    ).resolve(
        "regressions",
        available_tools=[],
    )
    assert result.activated == (replace(external, score=1 / 61),)
    (directory / "SKILL.md").unlink()
    result = SkillResolver(catalog, SkillRouter([Source("local", [hit])])).resolve(
        "review", available_tools=[]
    )
    assert not result.activated and not result.references
    assert result.diagnostics["rejected"] == {"workspace/review": "missing_skill_file"}

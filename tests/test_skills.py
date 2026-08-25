import pytest

from repoagent import (
    FakeModelClient,
    RepoAgent,
    SessionStore,
    SkillCatalog,
    SkillChangeWatcher,
    SkillManifestError,
    WorkspaceContext,
)


def write_skill(root, skill_id, *, body="Run the workflow.", extra=""):
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"id: {skill_id}\n"
        f"name: {skill_id.title()}\n"
        f"description: Workflow for {skill_id} tasks\n"
        "version: 1\n"
        f"{extra}"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return skill_dir


def build_agent(tmp_path, outputs, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return RepoAgent(
        model_client=FakeModelClient(outputs),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        **kwargs,
    )


def test_skill_catalog_discovers_manifest_and_lazy_loads_body_and_reference(tmp_path):
    root = tmp_path / "skills"
    skill_dir = write_skill(
        root,
        "review",
        body="Review version one.",
        extra="references: refs/checklist.md\n",
    )
    (skill_dir / "refs").mkdir()
    (skill_dir / "refs" / "checklist.md").write_text(
        "Check security.\n", encoding="utf-8"
    )
    catalog = SkillCatalog({"workspace": root})

    manifest = catalog.list()[0]
    assert manifest.qualified_id == "workspace/review"
    assert manifest.references == ("refs/checklist.md",)
    assert not hasattr(manifest, "content")

    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace(
            "Review version one.", "Review version two."
        ),
        encoding="utf-8",
    )
    activated = catalog.activate("review", limit=1)
    assert activated[0].content == "Review version two."
    assert catalog.load_reference(manifest, "refs/checklist.md") == "Check security.\n"
    with pytest.raises(SkillManifestError):
        catalog.load_reference(manifest, "../secret.txt")


def test_skill_catalog_applies_requirements_always_and_stable_ranking(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "always", extra="always: true\n")
    write_skill(root, "pytest", body="Run pytest.")
    write_skill(root, "blocked", extra="requires_env: MISSING_SKILL_ENV\n")
    catalog = SkillCatalog({"workspace": root}, env={})

    activated = catalog.activate("pytest failures", limit=3)

    assert [item.qualified_id for item in activated] == [
        "workspace/always",
        "workspace/pytest",
    ]
    blocked = catalog.get("workspace/blocked")
    assert catalog.availability(blocked) == {
        "available": False,
        "missing_bins": [],
        "missing_env": ["MISSING_SKILL_ENV"],
    }


def test_skill_catalog_rejects_invalid_or_missing_references(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "bad", extra="references: ../outside.md\n")

    with pytest.raises(SkillManifestError):
        SkillCatalog({"workspace": root})


def test_skill_change_watcher_poll_refreshes_created_and_changed_manifests(tmp_path):
    root = tmp_path / "skills"
    catalog = SkillCatalog({"workspace": root})
    watcher = SkillChangeWatcher(catalog)
    assert catalog.list() == ()

    skill_dir = write_skill(root, "review", body="Version one")
    assert watcher.poll() is True
    first_digest = catalog.get("workspace/review").digest

    path = skill_dir / "SKILL.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("Version one", "Version two"),
        encoding="utf-8",
    )
    assert watcher.poll() is True
    assert catalog.get("workspace/review").digest != first_digest
    assert watcher.poll() is False
    watcher.stop()


def test_runtime_activates_skill_into_dedicated_context_segment(tmp_path):
    write_skill(tmp_path / "skills", "pytest", body="Inspect tests before patching.")
    client = FakeModelClient(["<final>Done.</final>"])
    agent = build_agent(tmp_path, [])
    agent.model_client = client

    assert agent.ask("Fix the pytest failure") == "Done."

    prompt = client.prompts[0]
    assert "Skills:\n## Pytest [workspace/pytest]" in prompt
    assert "Inspect tests before patching." in prompt
    assert agent.last_prompt_metadata["skills"] == {
        "activated_ids": ["workspace/pytest"],
        "activated_count": 1,
        "raw_tokens": agent.last_prompt_metadata["sections"]["skills"]["raw_tokens"],
        "rendered_tokens": agent.last_prompt_metadata["sections"]["skills"]["rendered_tokens"],
    }


def test_runtime_watcher_discovers_skill_created_after_agent_start(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"])
    write_skill(tmp_path / "skills", "review", body="Review the diff.")

    assert agent.ask("Review this change") == "Done."
    assert "Review the diff." in agent.model_client.prompts[0]

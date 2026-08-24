from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.providers.tool_schema import model_tools_from_registry
from repoagent.tool_contracts import ToolEffect
from repoagent.tools import BASE_TOOL_DEFINITIONS, build_tool_registry


def _agent(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return RepoAgent(
        model_client=FakeModelClient([]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )


def test_registry_has_one_definition_source_plus_runner(tmp_path):
    agent = _agent(tmp_path)
    registry = build_tool_registry(agent.tool_context())

    assert all(set(entry) == {"definition", "run"} for entry in registry.values())
    assert registry["read_file"]["definition"] is BASE_TOOL_DEFINITIONS[
        "read_file"
    ]
    assert registry["run_shell"]["definition"].effect is ToolEffect.EXECUTE
    assert registry["run_shell"]["definition"].requires_approval is True


def test_provider_schema_is_projected_without_reinterpreting_definition(tmp_path):
    agent = _agent(tmp_path)
    model_tools = model_tools_from_registry(agent.tools)

    for model_tool in model_tools:
        definition = agent.tools[model_tool.name]["definition"]
        assert model_tool.description == definition.description
        assert dict(model_tool.parameters) == definition.to_dict()["parameters"]


def test_runtime_validation_uses_definition_defaults_and_constraints(tmp_path):
    agent = _agent(tmp_path)

    assert agent.validate_tool("read_file", {"path": "README.md"}) == {
        "path": "README.md",
        "start": 1,
        "end": 200,
    }
    result = agent.run_tool(
        "read_file", {"path": "README.md", "start": "1"}
    )
    assert "must be integer" in result


def test_prompt_signature_is_definition_identity_based(tmp_path):
    agent = _agent(tmp_path)

    signature = agent.tool_signature()
    definition_ids = {
        name: entry["definition"].definition_id
        for name, entry in agent.tools.items()
    }

    assert signature
    assert len(set(definition_ids.values())) == len(definition_ids)

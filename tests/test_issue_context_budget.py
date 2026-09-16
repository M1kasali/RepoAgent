import asyncio

import pytest

from repoagent import RepoAgent, SessionStore, WorkspaceContext
from repoagent.issue_agent.worker import context_options
from repoagent.providers.base import ModelEvent, ModelResult, ModelUsage


@pytest.mark.parametrize("legacy_cap", [True, False])
def test_native_issue_prompt_exceeding_old_demo_cap(tmp_path, legacy_cap):
    class Provider:
        model = "fixture"
        supports_native_tools = supports_structured_messages = True

        def stream(self, request):
            yield ModelEvent(kind="completed", result=ModelResult(
                text="done", model=self.model, usage=ModelUsage(input_tokens=20000, output_tokens=1)))

    options = context_options()
    if legacy_cap:
        options["context_token_budget"] = 16000
    agent = RepoAgent(model_client=Provider(), workspace=WorkspaceContext.build(tmp_path),
                      session_store=SessionStore(tmp_path / "sessions"),
                      max_provider_calls=1, max_new_tokens=4096,
                      feature_flags={"skills": False, "memory": False}, **options)
    try:
        if legacy_cap:
            with pytest.raises(Exception, match="configured context window"):
                agent.ask("word " * 19000)
        else:
            assert agent.ask("word " * 19000) == "done"
    finally:
        asyncio.run(agent.aclose())

import json
import subprocess

import httpx
import pytest

from repoagent.issue_agent.cases import (
    CaseStore,
    bind_repository,
    fetch_issue,
    issue_identity,
    read_snapshot,
)

URL = "https://github.com/example/project/issues/12"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b/issues/1",
        "https://evil.com/a/b/issues/1",
        "https://github.com/a/b/pull/1",
        URL + "?x=1",
        "https://github.com/../b/issues/1",
    ],
)
def test_reject_unwanted_issue_urls(url):
    with pytest.raises(ValueError):
        issue_identity(url)


def test_bounded_fetch_strips_solution_metadata():
    payload = {
        "html_url": URL,
        "title": "Bug",
        "body": "Symptom",
        "comments": [{"body": "secret solution"}],
    }
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = fetch_issue(URL, client=client)
    assert result["body"] == "Symptom" and "comments" not in result
    assert len(requests) == 1 and requests[0].url.host == "api.github.com"


@pytest.mark.parametrize(
    "payload",
    [
        {"html_url": URL, "title": "Bug", "body": "x" * 128001},
        {"html_url": URL, "title": "Bug", "pull_request": {}},
        {"html_url": URL + "0", "title": "Bug"},
    ],
)
def test_bad_snapshots_fail(tmp_path, payload):
    path = tmp_path / "issue.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        read_snapshot(path, URL)


def test_repository_binding_and_case_roundtrip(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True
        ).strip()

    git("init", "-q")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--allow-empty",
        "-qm",
        "base",
    )
    git("remote", "add", "origin", "https://github.com/example/project.git")
    sha = git("rev-parse", "HEAD")
    bound = bind_repository(repo, sha, "example/project")
    with pytest.raises(ValueError):
        bind_repository(repo, "HEAD", "example/project")
    with pytest.raises(ValueError):
        bind_repository(repo, sha, "example/other")
    store = CaseStore(tmp_path / "cases")
    state = store.create({"url": URL}, bound)
    with store.locked(state["case_id"]) as loaded:
        assert loaded == state
    with pytest.raises(ValueError):
        store.load("../outside")
    with pytest.raises(ValueError):
        CaseStore(repo / "cases").create({"url": URL}, bound)

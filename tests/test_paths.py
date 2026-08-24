from repoagent.paths import workspace_state_root


def test_workspace_state_root_uses_repoagent_for_new_workspace(tmp_path):
    assert workspace_state_root(tmp_path) == tmp_path / ".repoagent"


def test_workspace_state_root_continues_existing_legacy_workspace(tmp_path):
    legacy = tmp_path / ".pico"
    legacy.mkdir()

    assert workspace_state_root(tmp_path) == legacy


def test_workspace_state_root_prefers_repoagent_when_both_exist(tmp_path):
    (tmp_path / ".pico").mkdir()
    preferred = tmp_path / ".repoagent"
    preferred.mkdir()

    assert workspace_state_root(tmp_path) == preferred

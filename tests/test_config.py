"""Config loading/writing tests: enabled-stores opt-in list, nudge
dir_overrides, and the TOML writer round-trip."""

import pytest

from prompt_coach.config import (
    ALL_STORE_KINDS,
    load_config,
    write_config,
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in (
        "PROMPT_COACH_ENABLED_STORES",
        "PROMPT_COACH_NUDGE_MODE",
        "PROMPT_COACH_MODEL_FIT_MODE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_enabled_stores_is_all_four(clean_env, tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.stores.enabled == frozenset(ALL_STORE_KINDS)


def test_enabled_stores_env_override(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("PROMPT_COACH_ENABLED_STORES", "hermes, claude-code")
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.stores.enabled == frozenset({"hermes", "claude-code"})


def test_enabled_stores_toml_override(clean_env, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[stores]\nenabled = ["hermes", "codex"]\n')
    cfg = load_config(path)
    assert cfg.stores.enabled == frozenset({"hermes", "codex"})


def test_unknown_enabled_store_raises(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("PROMPT_COACH_ENABLED_STORES", "hermes,not-a-real-store")
    with pytest.raises(ValueError, match="unknown store"):
        load_config(tmp_path / "nope.toml")


def test_nudge_dir_overrides_from_toml(clean_env, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[nudge]\nmode = "coach"\n\n[nudge.dir_overrides]\n"/x/y" = "off"\n')
    cfg = load_config(path)
    assert cfg.nudge.dir_overrides == {"/x/y": "off"}


def test_nudge_dir_overrides_invalid_mode_raises(clean_env, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[nudge.dir_overrides]\n"/x/y" = "nonsense"\n')
    with pytest.raises(ValueError, match="dir_overrides"):
        load_config(path)


def test_no_dir_overrides_defaults_to_empty_dict(clean_env, tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.nudge.dir_overrides == {}


def test_write_config_round_trip(clean_env, tmp_path):
    original = load_config(tmp_path / "nope.toml")
    import dataclasses

    customized = dataclasses.replace(
        original,
        stores=dataclasses.replace(original.stores, enabled=frozenset({"hermes", "codex"})),
        nudge=dataclasses.replace(
            original.nudge, mode="always", dir_overrides={"/a/b": "off", "/a/b/c": "always"}
        ),
    )
    path = tmp_path / "written.toml"
    write_config(customized, path)
    reloaded = load_config(path)

    assert reloaded.stores.enabled == frozenset({"hermes", "codex"})
    assert reloaded.nudge.mode == "always"
    assert reloaded.nudge.dir_overrides == {"/a/b": "off", "/a/b/c": "always"}
    assert reloaded.llm.base_url == original.llm.base_url


def test_write_config_omits_dir_overrides_table_when_empty(clean_env, tmp_path):
    original = load_config(tmp_path / "nope.toml")
    path = tmp_path / "written.toml"
    write_config(original, path)
    assert "dir_overrides" not in path.read_text()


def test_write_config_creates_parent_dir_with_private_mode(clean_env, tmp_path):
    original = load_config(tmp_path / "nope.toml")
    path = tmp_path / "sub" / "config.toml"
    write_config(original, path)
    assert path.is_file()
    assert (path.parent.stat().st_mode & 0o777) == 0o700

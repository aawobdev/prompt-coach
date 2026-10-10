"""Config loading/writing tests: enabled-stores opt-in list, nudge
dir_overrides, and the TOML writer round-trip."""

import pytest

from prompt_coach.config import (
    ALL_STORE_KINDS,
    APIKeyCommandFailed,
    load_config,
    write_config,
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in (
        "PROMPT_COACH_ENABLED_STORES",
        "PROMPT_COACH_NUDGE_MODE",
        "PROMPT_COACH_MODEL_FIT_MODE",
        "PROMPT_COACH_API_KEY",
        "PROMPT_COACH_API_KEY_CMD",
        "PROMPT_COACH_REASONING",
        "PROMPT_COACH_EXTRA_BODY",
        "PROMPT_COACH_API_BASE",
        "PROMPT_COACH_MODEL",
        "PROMPT_COACH_NUDGE_AUTO_REWRITE",
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


def test_auto_nudge_mode_is_accepted(clean_env, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[nudge]\nmode = "auto"\n')
    assert load_config(path).nudge.mode == "auto"


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


# --- api_key_cmd / reasoning (cloud rewrite model, DECISIONS.md 2026-09-27) ---


def test_api_key_cmd_resolves_lazily_from_stdout(clean_env, tmp_path):
    path = tmp_path / "config.toml"
    marker = tmp_path / "ran"
    path.write_text(f'[llm]\napi_key_cmd = "touch {marker}; echo sk-test-123"\n')
    cfg = load_config(path)
    assert not marker.exists()  # loading config must not run it (every hook loads config)
    assert cfg.llm.resolve_api_key() == "sk-test-123"
    assert marker.exists()


def test_api_key_cmd_wins_over_file_api_key(clean_env, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[llm]\napi_key = "ollama"\napi_key_cmd = "echo from-cmd"\n')
    assert load_config(path).llm.resolve_api_key() == "from-cmd"


def test_env_api_key_wins_over_api_key_cmd(clean_env, monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[llm]\napi_key_cmd = "echo from-cmd"\n')
    monkeypatch.setenv("PROMPT_COACH_API_KEY", "from-env")
    assert load_config(path).llm.resolve_api_key() == "from-env"


def test_no_api_key_cmd_returns_plain_api_key(clean_env, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[llm]\napi_key = "plain"\n')
    assert load_config(path).llm.resolve_api_key() == "plain"


def test_no_endpoint_or_model_by_default(clean_env, tmp_path):
    """No guessed endpoint (a LAN IP or a vendor): unset means the LLM
    features are off until the user configures one."""
    llm = load_config(tmp_path / "nope.toml").llm
    assert (llm.base_url, llm.model, llm.api_key) == ("", "", "")
    assert not llm.configured


def test_configured_needs_both_endpoint_and_model(clean_env, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[llm]\napi_base = "http://localhost:11434/v1"\n')
    assert not load_config(path).llm.configured
    path.write_text('[llm]\napi_base = "http://localhost:11434/v1"\nmodel = "m"\n')
    assert load_config(path).llm.configured


@pytest.mark.parametrize("cmd", ["exit 3", "true", "sleep 5"])
def test_api_key_cmd_failure_raises_without_leaking_output(clean_env, tmp_path, cmd):
    path = tmp_path / "config.toml"
    path.write_text(f'[llm]\napi_key_cmd = "echo secret-ish >&2; {cmd}"\n')
    cfg = load_config(path)
    with pytest.raises(APIKeyCommandFailed) as exc:
        cfg.llm.resolve_api_key(timeout=1.0)
    assert "secret-ish" not in str(exc.value)


def test_extra_body_defaults_to_unset(clean_env, tmp_path):
    assert load_config(tmp_path / "nope.toml").llm.extra_body is None


def test_extra_body_from_toml_and_env(clean_env, monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[llm]\nextra_body = '{\"top_k\": 20}'\n")
    assert load_config(path).llm.extra_body == {"top_k": 20}
    monkeypatch.setenv("PROMPT_COACH_EXTRA_BODY", '{"num_ctx": 32768}')
    assert load_config(path).llm.extra_body == {"num_ctx": 32768}


@pytest.mark.parametrize("raw", ["not json", "[1, 2]"])
def test_extra_body_must_be_a_json_object(clean_env, tmp_path, raw):
    path = tmp_path / "config.toml"
    path.write_text(f"[llm]\nextra_body = '{raw}'\n")
    with pytest.raises(ValueError, match="extra_body"):
        load_config(path)


def test_legacy_reasoning_maps_to_extra_body(clean_env, monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[llm]\nreasoning = false\n")
    assert load_config(path).llm.extra_body == {"reasoning": {"enabled": False}}
    monkeypatch.setenv("PROMPT_COACH_REASONING", "true")
    assert load_config(path).llm.extra_body == {"reasoning": {"enabled": True}}


def test_write_config_migrates_legacy_reasoning(clean_env, tmp_path):
    src = tmp_path / "config.toml"
    src.write_text('[llm]\napi_key_cmd = "pass show \\"x\\" | head -1"\nreasoning = false\n')
    cfg = load_config(src)
    out = tmp_path / "written.toml"
    write_config(cfg, out)
    written = out.read_text()
    assert "reasoning = " not in written
    reloaded = load_config(out)
    assert reloaded.llm.api_key_cmd == cfg.llm.api_key_cmd
    assert reloaded.llm.extra_body == {"reasoning": {"enabled": False}}


def test_write_config_omits_unset_api_key_cmd_and_extra_body(clean_env, tmp_path):
    out = tmp_path / "written.toml"
    write_config(load_config(tmp_path / "nope.toml"), out)
    text = out.read_text()
    assert "api_key_cmd" not in text
    assert "extra_body" not in text


def test_auto_rewrite_defaults_off_and_reads_toml_and_env(clean_env, monkeypatch, tmp_path):
    assert load_config(tmp_path / "nope.toml").nudge.auto_rewrite is False
    path = tmp_path / "config.toml"
    path.write_text("[nudge]\nauto_rewrite = true\n")
    assert load_config(path).nudge.auto_rewrite is True
    monkeypatch.setenv("PROMPT_COACH_NUDGE_AUTO_REWRITE", "false")
    assert load_config(path).nudge.auto_rewrite is False


def test_write_config_round_trips_auto_rewrite(clean_env, tmp_path):
    src = tmp_path / "config.toml"
    src.write_text("[nudge]\nauto_rewrite = true\n")
    out = tmp_path / "written.toml"
    write_config(load_config(src), out)
    assert load_config(out).nudge.auto_rewrite is True

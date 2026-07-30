"""Nudge tests: trigger threshold, once-per-session suppression, hook JSON shape."""

import json

from prompt_coach.config import Config, LLMConfig, ModelFitConfig, NudgeConfig, StoresConfig
from prompt_coach.nudge import (
    _resolve_mode,
    build_response,
    evaluate,
    hook_response_stop,
    should_nudge,
)

_UNREACHABLE = "http://127.0.0.1:9"  # nothing listens here -- deterministic "LLM down"


def make_cfg(tmp_path, mode="coach", base_url=_UNREACHABLE, dir_overrides=None):
    return Config(
        llm=LLMConfig(
            base_url=base_url, model="test-model", api_key="x", allow_remote=False, timeout=5.0
        ),
        stores=StoresConfig(
            hermes_db=tmp_path / "state.db",
            claude_projects_dir=tmp_path / "projects",
            copilot_dir=None,
            codex_dir=None,
        ),
        nudge=NudgeConfig(mode=mode, llm_timeout=2.0, dir_overrides=dir_overrides or {}),
        model_fit=ModelFitConfig(mode="descriptive"),
        cache_dir=tmp_path / "cache",
    )


LONG_WEAK = (
    "please help me redesign the whole reporting pipeline. " * 5
)  # >200 chars, no example/format
LONG_STRONG = (
    "Redesign the reporting pipeline. Output only a markdown table of changes. "
    "Example: | file | change |. " + ("padding " * 20)
)
SHORT = "run it"
SHORT_VAGUE = "just redo everything, it's a mess"  # <200 chars, broad-scope, no constraints
SHORT_VAGUE_BUT_CONSTRAINED = "redo everything but only touch the CSS files"
SHORT_ORDINARY = "add a retry to the fetcher"  # <200 chars, no broad-scope word


def _user_line(content, session="s1", uuid="u-1", ts="2026-07-27T10:00:00.000Z"):
    return json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": content},
            "promptSource": "typed",
            "origin": {"kind": "human"},
            "isSidechain": False,
            "uuid": uuid,
            "timestamp": ts,
            "sessionId": session,
            "cwd": "/p",
        }
    )


def _assistant_line(content="ok", session="s1", uuid="a-1", ts="2026-07-27T10:00:01.000Z"):
    return json.dumps(
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": content},
            "uuid": uuid,
            "timestamp": ts,
            "sessionId": session,
        }
    )


class TestShouldNudge:
    def test_short_prompt_never_nudges(self, tmp_path):
        assert not should_nudge(SHORT, "s1", tmp_path)

    def test_long_weak_prompt_nudges_first_time(self, tmp_path):
        assert should_nudge(LONG_WEAK, "s1", tmp_path)

    def test_long_prompt_with_example_or_format_does_not_nudge(self, tmp_path):
        assert not should_nudge(LONG_STRONG, "s1", tmp_path)

    def test_same_session_only_nudges_once(self, tmp_path):
        assert evaluate(LONG_WEAK, "s1", tmp_path) is not None
        assert evaluate(LONG_WEAK, "s1", tmp_path) is None

    def test_different_sessions_each_get_a_nudge(self, tmp_path):
        assert evaluate(LONG_WEAK, "s1", tmp_path) is not None
        assert evaluate(LONG_WEAK, "s2", tmp_path) is not None


class TestShortVagueScope:
    """D: short (<200 char) prompts with an unconstrained broad-scope word,
    added 2026-07-27 after calibrating against the real corpus (26/1739
    human prompts, 1.5%) -- a separate trigger from the long+unshaped one,
    with its own tip text about scope, not output format."""

    def test_short_broad_scope_no_constraints_nudges(self, tmp_path):
        assert should_nudge(SHORT_VAGUE, "s1", tmp_path)
        tip = evaluate(SHORT_VAGUE, "s1", tmp_path)
        assert tip is not None
        assert "scope" in tip

    def test_short_broad_scope_with_constraints_does_not_nudge(self, tmp_path):
        assert not should_nudge(SHORT_VAGUE_BUT_CONSTRAINED, "s1", tmp_path)

    def test_short_prompt_without_broad_scope_word_does_not_nudge(self, tmp_path):
        assert not should_nudge(SHORT_ORDINARY, "s1", tmp_path)

    def test_shares_once_per_session_gate_with_long_unstructured_trigger(self, tmp_path):
        assert evaluate(LONG_WEAK, "s1", tmp_path) is not None
        assert evaluate(SHORT_VAGUE, "s1", tmp_path) is None  # session already nudged


class TestBuildResponseCoachMode:
    """UserPromptSubmit dispatch, LLM unreachable -- degrades to the old
    tip-only behavior rather than blocking with no way forward."""

    def test_returns_system_message_when_nudging_and_llm_unreachable(self, tmp_path):
        cfg = make_cfg(tmp_path)
        resp = build_response({"prompt": LONG_WEAK, "session_id": "s1"}, cfg)
        assert "systemMessage" in resp
        assert "decision" not in resp

    def test_empty_dict_when_not_nudging(self, tmp_path):
        cfg = make_cfg(tmp_path)
        resp = build_response({"prompt": SHORT, "session_id": "s1"}, cfg)
        assert resp == {}

    def test_missing_prompt_or_session_id_is_empty(self, tmp_path):
        cfg = make_cfg(tmp_path)
        assert build_response({"session_id": "s1"}, cfg) == {}
        assert build_response({"prompt": LONG_WEAK}, cfg) == {}

    def test_blocks_with_rewrite_when_llm_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prompt_coach.nudge._make_llm", lambda cfg: object())
        monkeypatch.setattr("prompt_coach.nudge.rewrite_prompt", lambda prompt, llm: "REWRITTEN")
        cfg = make_cfg(tmp_path)
        resp = build_response({"prompt": LONG_WEAK, "session_id": "s1"}, cfg)
        assert resp["decision"] == "block"
        assert "REWRITTEN" in resp["reason"]
        assert "systemMessage" not in resp

    def test_falls_back_to_tip_when_rewrite_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prompt_coach.nudge._make_llm", lambda cfg: object())
        monkeypatch.setattr("prompt_coach.nudge.rewrite_prompt", lambda prompt, llm: None)
        cfg = make_cfg(tmp_path)
        resp = build_response({"prompt": LONG_WEAK, "session_id": "s1"}, cfg)
        assert "systemMessage" in resp
        assert "decision" not in resp

    def test_still_once_per_session_when_blocking(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prompt_coach.nudge._make_llm", lambda cfg: object())
        monkeypatch.setattr("prompt_coach.nudge.rewrite_prompt", lambda prompt, llm: "REWRITTEN")
        cfg = make_cfg(tmp_path)
        assert build_response({"prompt": LONG_WEAK, "session_id": "s1"}, cfg) != {}
        assert build_response({"prompt": LONG_WEAK, "session_id": "s1"}, cfg) == {}


class TestBuildResponseAlwaysMode:
    def test_ignores_heuristic_and_blocks_ordinary_short_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prompt_coach.nudge._make_llm", lambda cfg: object())
        monkeypatch.setattr("prompt_coach.nudge.rewrite_prompt", lambda prompt, llm: "REWRITTEN")
        cfg = make_cfg(tmp_path, mode="always")
        resp = build_response({"prompt": SHORT_ORDINARY, "session_id": "s1"}, cfg)
        assert resp["decision"] == "block"
        assert "REWRITTEN" in resp["reason"]

    def test_ignores_once_per_session_gate(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prompt_coach.nudge._make_llm", lambda cfg: object())
        monkeypatch.setattr("prompt_coach.nudge.rewrite_prompt", lambda prompt, llm: "REWRITTEN")
        cfg = make_cfg(tmp_path, mode="always")
        payload = {"prompt": SHORT_ORDINARY, "session_id": "s1"}
        assert build_response(payload, cfg)["decision"] == "block"
        assert build_response(payload, cfg)["decision"] == "block"  # still blocks, no gate

    def test_llm_unreachable_lets_prompt_through_silently(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="always")
        resp = build_response({"prompt": SHORT_ORDINARY, "session_id": "s1"}, cfg)
        assert resp == {}  # no block, no message -- not stuck with no way forward

    def test_stop_event_does_nothing_in_always_mode(self, tmp_path):
        transcript = tmp_path / "sess.jsonl"
        transcript.write_text(_user_line(LONG_WEAK) + "\n")
        cfg = make_cfg(tmp_path, mode="always")
        resp = build_response(
            {
                "hook_event_name": "Stop",
                "session_id": "s1",
                "transcript_path": str(transcript),
            },
            cfg,
        )
        assert resp == {}  # already caught pre-submission; nothing left for Stop to do


class TestBuildResponseOffMode:
    def test_never_responds_regardless_of_prompt_or_event(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="off")
        assert build_response({"prompt": LONG_WEAK, "session_id": "s1"}, cfg) == {}
        transcript = tmp_path / "sess.jsonl"
        transcript.write_text(_user_line(LONG_WEAK) + "\n")
        assert (
            build_response(
                {
                    "hook_event_name": "Stop",
                    "session_id": "s1",
                    "transcript_path": str(transcript),
                },
                cfg,
            )
            == {}
        )


class TestHookResponseStop:
    def test_reads_prompt_from_transcript_tail(self, tmp_path):
        transcript = tmp_path / "sess.jsonl"
        transcript.write_text(_user_line(LONG_WEAK) + "\n" + _assistant_line() + "\n")
        resp = hook_response_stop("s1", transcript, tmp_path / "cache")
        assert "systemMessage" in resp
        assert "decision" not in resp  # never blocking

    def test_skips_assistant_lines_to_find_last_human_prompt(self, tmp_path):
        transcript = tmp_path / "sess.jsonl"
        transcript.write_text(
            _user_line(SHORT, uuid="u-1", ts="2026-07-27T10:00:00.000Z")
            + "\n"
            + _assistant_line(uuid="a-1", ts="2026-07-27T10:00:01.000Z")
            + "\n"
            + _user_line(LONG_WEAK, uuid="u-2", ts="2026-07-27T10:00:02.000Z")
            + "\n"
            + _assistant_line(uuid="a-2", ts="2026-07-27T10:00:03.000Z")
            + "\n"
        )
        resp = hook_response_stop("s1", transcript, tmp_path / "cache")
        assert "systemMessage" in resp  # the LONG_WEAK prompt, not the short one before it

    def test_missing_transcript_is_empty_not_an_error(self, tmp_path):
        resp = hook_response_stop("s1", tmp_path / "does-not-exist.jsonl", tmp_path / "cache")
        assert resp == {}

    def test_shares_once_per_session_gate_with_user_prompt_submit(self, tmp_path):
        cache_dir = tmp_path / "cache"
        transcript = tmp_path / "sess.jsonl"
        transcript.write_text(_user_line(LONG_WEAK) + "\n")
        assert evaluate(LONG_WEAK, "s1", cache_dir) is not None
        assert hook_response_stop("s1", transcript, cache_dir) == {}  # already nudged this session


class TestResolveMode:
    """Claude Code hooks merge across scopes rather than override (checked
    live, DECISIONS.md 2026-07-30), so per-directory control lives here."""

    def test_no_overrides_returns_global_mode(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach")
        assert _resolve_mode(cfg, "/any/dir") == "coach"

    def test_no_cwd_returns_global_mode(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach", dir_overrides={"/a": "off"})
        assert _resolve_mode(cfg, None) == "coach"

    def test_exact_match(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach", dir_overrides={"/a/b": "off"})
        assert _resolve_mode(cfg, "/a/b") == "off"

    def test_parent_prefix_matches_subdirectory(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach", dir_overrides={"/a/b": "off"})
        assert _resolve_mode(cfg, "/a/b/sub/dir") == "off"

    def test_sibling_directory_does_not_match(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach", dir_overrides={"/a/b": "off"})
        assert _resolve_mode(cfg, "/a/bee") == "coach"  # prefix string, not a path segment

    def test_longest_matching_prefix_wins(self, tmp_path):
        cfg = make_cfg(
            tmp_path,
            mode="coach",
            dir_overrides={"/a": "off", "/a/b": "always"},
        )
        assert _resolve_mode(cfg, "/a/b/sub") == "always"
        assert _resolve_mode(cfg, "/a/other") == "off"

    def test_trailing_slash_in_config_is_tolerated(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach", dir_overrides={"/a/b/": "off"})
        assert _resolve_mode(cfg, "/a/b") == "off"
        assert _resolve_mode(cfg, "/a/b/sub") == "off"


class TestBuildResponseDirOverrides:
    def test_off_override_suppresses_a_normally_triggering_prompt(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach", dir_overrides={"/p": "off"})
        resp = build_response({"prompt": LONG_WEAK, "session_id": "s1", "cwd": "/p"}, cfg)
        assert resp == {}

    def test_override_applies_only_inside_its_directory(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach", dir_overrides={"/p": "off"})
        resp = build_response({"prompt": LONG_WEAK, "session_id": "s1", "cwd": "/elsewhere"}, cfg)
        assert resp != {}  # global "coach" mode still applies outside the override


class TestHermesPreLlmCall:
    """Hermes's pre_llm_call: same wire protocol Hermes documents as the
    UserPromptSubmit equivalent, but message at extra.user_message and
    context-injection only -- no block/rewrite capability exists there."""

    def _payload(self, prompt, session_id="h1", cwd=None):
        payload = {
            "hook_event_name": "pre_llm_call",
            "session_id": session_id,
            "extra": {"user_message": prompt},
        }
        if cwd is not None:
            payload["cwd"] = cwd
        return payload

    def test_triggering_prompt_returns_context_injection(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach")
        resp = build_response(self._payload(LONG_WEAK), cfg)
        assert "context" in resp
        assert "decision" not in resp and "systemMessage" not in resp

    def test_non_triggering_prompt_is_empty(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach")
        assert build_response(self._payload(SHORT), cfg) == {}

    def test_once_per_session_gate_applies(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach")
        assert build_response(self._payload(LONG_WEAK, session_id="h2"), cfg) != {}
        assert build_response(self._payload(LONG_WEAK, session_id="h2"), cfg) == {}

    def test_off_mode_never_fires(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="off")
        assert build_response(self._payload(LONG_WEAK), cfg) == {}

    def test_always_mode_behaves_like_coach_not_unconditional(self, tmp_path):
        # "always" has no meaningful translation for a context-injection-only
        # hook (nothing to unconditionally block-and-rewrite), so it collapses
        # to the same calibrated-trigger, once-per-session behavior as coach.
        cfg = make_cfg(tmp_path, mode="always")
        assert build_response(self._payload(SHORT), cfg) == {}
        assert build_response(self._payload(LONG_WEAK, session_id="h3"), cfg) != {}

    def test_missing_user_message_is_empty(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach")
        payload = {"hook_event_name": "pre_llm_call", "session_id": "h4", "extra": {}}
        assert build_response(payload, cfg) == {}

    def test_dir_override_respected(self, tmp_path):
        cfg = make_cfg(tmp_path, mode="coach", dir_overrides={"/proj": "off"})
        resp = build_response(self._payload(LONG_WEAK, session_id="h5", cwd="/proj"), cfg)
        assert resp == {}

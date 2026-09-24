"""SessionStart one-line summary (plugin phase 3): content, silence rules,
and that it only ever reads existing state."""

from datetime import UTC, datetime, timedelta

from prompt_coach.cache import CacheDB
from prompt_coach.nudge import _TIP_PASTE, _TIP_SCOPE, _TIP_SHAPE
from prompt_coach.session_summary import build_summary, hook_response_session_start
from prompt_coach.streaks import record_prompt


def _history(cache_dir, prompts=3, nudged_first=True):
    for i in range(prompts):
        record_prompt(cache_dir, f"prompt number {i}", was_nudged=nudged_first and i == 0)


def _audit(cache_dir, *triggers):
    cache = CacheDB(cache_dir / "cache.db")
    for trigger in triggers:
        cache.record_audit_event(session_id="s", mode="auto", original_prompt="x", trigger=trigger)
    cache.close()


def test_silent_with_no_history(tmp_path):
    assert build_summary(tmp_path) is None


def test_fresh_install_creates_no_cache(tmp_path):
    _history(tmp_path)
    build_summary(tmp_path)
    assert not (tmp_path / "cache.db").exists()


def test_streak_and_most_common_gap(tmp_path):
    _history(tmp_path, prompts=4)  # nudged once, then 3 clean
    _audit(tmp_path, _TIP_SHAPE, _TIP_SHAPE, _TIP_SCOPE, "always_mode")
    msg = build_summary(tmp_path)
    assert msg.startswith("prompt-coach: 3 prompts since your last nudge.")
    # always_mode is a mode setting, not a weakness: 2 of 3, not 2 of 4
    assert "no output format or example (2 of 3 nudges)" in msg
    assert "name the shape you want back" in msg


def test_paste_trigger_is_a_category(tmp_path):
    _history(tmp_path)
    _audit(tmp_path, _TIP_PASTE, _TIP_PASTE, _TIP_SHAPE)
    assert "pasting without an ask (2 of 3 nudges)" in build_summary(tmp_path)


def test_singular_prompt(tmp_path):
    _history(tmp_path, prompts=2)
    assert "1 prompt since your last nudge." in build_summary(tmp_path)


def test_never_nudged_wording(tmp_path):
    _history(tmp_path, prompts=5, nudged_first=False)
    assert build_summary(tmp_path) == (
        "prompt-coach: 5 prompts tracked, no nudges yet. No nudges this week."
    )


def test_nudges_older_than_a_week_are_ignored(tmp_path):
    _history(tmp_path)
    _audit(tmp_path, _TIP_SHAPE)
    later = datetime.now(tz=UTC) + timedelta(days=8)
    assert build_summary(tmp_path, now=later).endswith("No nudges this week.")


def test_corrupt_cache_is_not_an_error(tmp_path):
    _history(tmp_path)
    (tmp_path / "cache.db").write_text("not a database")
    assert build_summary(tmp_path).endswith("No nudges this week.")


def test_no_prompt_content_in_the_message(tmp_path):
    record_prompt(tmp_path, "SECRET-PROJECT-NAME rewrite the billing", was_nudged=True)
    _audit(tmp_path, _TIP_SHAPE)
    assert "SECRET" not in build_summary(tmp_path)


class TestHookResponse:
    def test_startup_shows_the_summary_as_a_system_message_only(self, tmp_path):
        _history(tmp_path)
        resp = hook_response_session_start({"source": "startup"}, tmp_path, "auto")
        # systemMessage only: additionalContext or plain stdout would put it
        # into the model's context and cost tokens every session.
        assert set(resp) == {"systemMessage"}

    def test_other_sources_stay_silent(self, tmp_path):
        _history(tmp_path)
        for source in ("resume", "clear", "compact", "fork"):
            assert hook_response_session_start({"source": source}, tmp_path, "auto") == {}

    def test_off_mode_stays_silent(self, tmp_path):
        _history(tmp_path)
        assert hook_response_session_start({"source": "startup"}, tmp_path, "off") == {}

    def test_no_history_stays_silent(self, tmp_path):
        assert hook_response_session_start({"source": "startup"}, tmp_path, "coach") == {}

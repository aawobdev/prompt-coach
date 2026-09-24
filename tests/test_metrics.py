"""Tests for deterministic metrics and scoring."""

from datetime import UTC, datetime

from prompt_coach.analysis.metrics import (
    compute_metrics,
    estimate_tokens,
    has_constraints,
    has_example,
    has_sections,
    has_structured_output,
)
from prompt_coach.analysis.score import score_breakdown, score_prompt
from prompt_coach.models import Prompt, PromptOrigin, SourceKind
from prompt_coach.stores.base import content_hash
from prompt_coach.stores.json_import import JsonImportStore


def make(content: str, session="s1", ref="0", origin=PromptOrigin.HUMAN, second=0):
    return Prompt(
        source=SourceKind.JSON_IMPORT,
        session_id=session,
        message_ref=ref,
        content=content,
        content_hash=content_hash(content),
        timestamp=datetime(2026, 7, 1, 12, 0, second, tzinfo=UTC),
        origin=origin,
    )


class TestComputeMetrics:
    """Coverage for compute_metrics/_segment/estimate_tokens -- deleted
    without replacement by an earlier pass over this file (code review
    2026-09-24): these are still-live production functions feeding the
    example_rate/constraint_rate/structured_output_rate corpus stats that
    the has_* regex fixes below directly change the meaning of, so a
    regression here would previously have gone through CI unnoticed."""

    def test_empty_corpus(self):
        m = compute_metrics([])
        assert m.human is None
        assert m.machine is None
        assert m.overall is None

    def test_single_prompt_exact_values(self):
        p = make("a" * 400)  # 100 estimated tokens
        m = compute_metrics([p])
        seg = m.human
        assert seg.prompt_count == 1
        assert seg.session_count == 1
        assert seg.avg_prompt_tokens == 100.0
        assert seg.median_prompt_tokens == 100.0
        assert seg.prompts_per_session == 1.0
        assert seg.refinement_rate == 0.0
        assert seg.avg_first_message_ratio == 1.0
        assert m.machine is None

    def test_refinement_and_first_message_ratio(self):
        prompts = [
            make("x" * 300, session="s1", ref="0", second=0),
            make("y" * 100, session="s1", ref="1", second=10),
            make("z" * 100, session="s2", ref="0", second=20),
        ]
        seg = compute_metrics(prompts).human
        assert seg.session_count == 2
        assert seg.refinement_rate == 0.5  # s1 refined, s2 did not
        # s1 first-message ratio 300/400 = 0.75; s2 = 1.0; mean 0.875
        assert abs(seg.avg_first_message_ratio - 0.875) < 1e-9

    def test_segmentation_by_origin(self):
        prompts = [
            make("human question"),
            make("TASK: machine spec", session="s2", origin=PromptOrigin.MACHINE),
        ]
        m = compute_metrics(prompts)
        assert m.human.prompt_count == 1
        assert m.machine.prompt_count == 1
        assert m.overall.prompt_count == 2

    def test_signal_rates_on_fixture(self, sample_sessions_path):
        prompts = list(JsonImportStore(sample_sessions_path).iter_prompts())
        m = compute_metrics(prompts)
        # explicit-1 carries example + constraints + structured output; vague-1 none.
        assert 0 < m.human.example_rate < 1
        assert 0 < m.human.constraint_rate <= 1
        assert 0 < m.human.structured_output_rate < 1
        assert m.machine.prompt_count == 1

    def test_estimate_tokens(self):
        assert estimate_tokens("abcd" * 25) == 25.0


class TestHasStructuredOutput:
    def test_json_format(self):
        assert has_structured_output("return as json")

    def test_yaml_format(self):
        assert has_structured_output("Respond in YAML format")

    def test_table_format(self):
        assert has_structured_output("Return a table: item | status")
        assert has_structured_output("as a table")
        assert has_structured_output("markdown table")
        assert has_structured_output("table with columns")

    def test_list_format(self):
        assert has_structured_output("list of items")
        assert has_structured_output("Give me a list showing")

    def test_no_format(self):
        assert not has_structured_output("Can you look at my inbox")

    def test_bare_table_or_column_mentions_are_not_output_format(self):
        """code review 2026-09-24: `table`/`columns`/`tabular` used to be
        bare alternatives, matching any mention of the word, not a request
        for that output shape -- verified live against real content."""
        assert not has_structured_output("I found a bug in my routing table code")
        assert not has_structured_output("the users table has a race condition")
        assert not has_structured_output("check the hash table logic")
        assert not has_structured_output("show columns for name and status")
        assert not has_structured_output("tabular data")


class TestHasConstraints:
    def test_explicit_constraints(self):
        assert has_constraints("you must only use stdlib")
        assert has_constraints("never delete files")

    def test_implicit_list_needs_an_actual_bound(self):
        """code review 2026-09-24: any bullet/numbered line used to count
        as "has constraints", regardless of content -- a bare topic list
        registered the same as a real limit. Real bound language (a
        number+unit, a comparison, or an explicit limit word) still
        matches; a plain list of options does not."""
        assert has_constraints("* CPU usage < 30%\n* idle for 30 days")
        assert has_constraints("1. Reduce CPU usage below 50%\n2. Finish within 2 weeks")
        assert not has_constraints("- resized down\n- stopped\n- migrated")
        assert not has_constraints("1. Check this\n2. Do that")

    def test_no_constraints(self):
        assert not has_constraints("Can you look at my inbox")

    def test_vague_broad_scope_with_a_topic_list_is_still_vague(self):
        """The concrete failure this bug caused: a "revamp everything" ask
        with a bare bullet list of topics (no real constraints) used to
        register as constrained, defeating score.py's vague_scope penalty
        for exactly the prompt shape it exists to catch."""
        text = "Revamp everything about this app:\n- the UI\n- the backend\n- the API"
        assert not has_constraints(text)
        assert score_prompt(text) < 50  # vague_scope penalty must still apply


class TestHasExample:
    """_EXAMPLE had zero coverage before this diff, despite being the one
    regex it changed (code review 2026-09-24)."""

    def test_explicit_markers(self):
        assert has_example("for example, do X")
        assert has_example("like this: foo")
        assert has_example("such as bar")
        assert has_example("example: baz")
        assert has_example("```code```")
        assert has_example("input == output")

    def test_eg_realistic_phrasing(self):
        """A prior fix added `e.g.(?=\\s)` inside the outer \\b-wrapped
        group and it stayed dead for this exact case -- the trailing \\b
        can never fire right after "e.g." (both neighbours are non-word
        characters). Verified by executing both versions directly."""
        assert has_example("please format it, e.g. a short json blob with name and id")
        assert has_example("some text (e.g.)")
        assert has_example("some text e.g., more text")

    def test_no_example(self):
        assert not has_example("just do the thing")
        assert not has_example("e.g.foo")  # glued to a word, not real prose usage


class TestHasSections:
    def test_two_headers(self):
        assert has_sections("Problem: xyz\nExpected: abc")

    def test_goal_and_steps(self):
        assert has_sections("Goal: fix this\nSteps:\n1. do it")

    def test_single_header_not_enough(self):
        assert not has_sections("Problem: xyz")

    def test_no_headers(self):
        assert not has_sections("just a normal prompt")

    def test_any_labeled_section_counts_not_just_a_fixed_wordlist(self):
        """code review 2026-09-24: has_sections used to hardcode a closed
        10-word list (Problem/Expected/Steps/Goal/...) and missed any
        other vocabulary. It now reuses the same general "Label:"/heading
        pattern rubric.py's A4 check already uses."""
        assert has_sections("Root Cause:\nThe timer leaks.\nFix:\nClear it on unmount.")
        assert has_sections("## Investigation\n...\n## Resolution\n...")

    def test_bare_bullets_alone_are_not_sections(self):
        """Deliberately distinct from has_constraints' implicit-list
        signal -- two plain bullets shouldn't earn both bonuses for one
        formatting choice."""
        assert not has_sections("- do this\n- do that")


class TestScorePrompt:
    def test_vague_ask_scores_low(self):
        score = score_prompt("Can you look at my inbox and figure out what to do")
        assert score <= 55

    def test_good_structured_prompt_scores_high(self):
        text = (
            "Check Proxmox VM resource usage. Find VMs that can be:\n"
            "- Resized down (CPU/RAM usage < 30% for 7 days)\n"
            "- Stopped (idle for > 30 days)\n"
            "Return a table: VM ID | name | CPU | RAM | recommendation"
        )
        score = score_prompt(text)
        assert score >= 55, f"Expected >= 55, got {score}"

    def test_backblaze_rewrite_scores_well(self):
        text = (
            "Check my Backblaze email at wobdev inbox. I need:\n"
            "- Any invoices due in the next 30 days\n"
            "- Any expired/cancelled items\n"
            "- Any storage overage warnings\n\n"
            "Return a table: item | status | amount | action needed"
        )
        score = score_prompt(text)
        assert score >= 55, f"Expected >= 55, got {score}"

    def test_plex_rewrite_with_sections_scores_better_than_original(self):
        """Sections help, but still lacks format/examples. Score reflects that."""
        text = (
            'Plex "Recently Added" section isn\'t updating properly.\n\n'
            "Problem: Added 3 movies yesterday. Section shows 1, and it's from 4 days ago. "
            "Refreshing the library metadata doesn't fix it.\n"
            "Expected: New items appear within 5 minutes.\n"
            "Logs: checked PMS logs, no errors around scanning.\n\n"
            "What's the next step to diagnose?"
        )
        score = score_prompt(text)
        # Has structure (sections) but no output format or examples
        assert 15 <= score <= 40, f"Expected 15-40, got {score}"

    def test_kanban_spec_scores_high(self):
        text = (
            "CARD 2 of 2. Wire the already-built dashboard/stale.js into the project cards. "
            "Edit ONLY these files: dashboard/index.html, dashboard/app.js, dashboard/style.css. "
            "Do NOT edit dashboard/stale.js, dashboard/stale.test.mjs or dashboard/wiring.test.mjs.\n"
            "Gate: 'node --test dashboard/wiring.test.mjs' must pass (read it first), "
            "and 'node --test dashboard/stale.test.mjs' must still pass. "
            "Run both when done and report pass/fail counts."
        )
        score = score_prompt(text)
        assert score >= 30, f"Expected >= 30, got {score}"

    def test_long_prompt_starting_with_a_common_word_is_still_penalized(self):
        """The bug a follow-up-message exemption caused, before it was
        removed here (code review 2026-09-24): matching on the first word
        alone ("now"/"check"/"try"/"and"/...) fired on long, fully-formed
        prompts, not just short conversational continuations. This text
        starts with "Now" and has no output format, example, or explicit
        constraint -- it must still take the full penalty."""
        text = (
            "Now go through the whole billing export from last quarter and figure out "
            "which customers churned, why they left based on the notes field, and whether "
            "we could have caught it earlier by looking at usage trends before they "
            "cancelled their subscription entirely."
        )
        score = score_prompt(text)
        assert score < 30, f"Expected a heavily penalized score, got {score}"

    def test_breakdown_structure(self):
        text = (
            "Check Proxmox VM resource usage. Find VMs that can be:\n"
            "- Resized down (CPU/RAM usage < 30% for 7 days)\n"
            "- Stopped (idle for > 30 days)\n"
            "Return a table: VM ID | name | CPU | RAM | recommendation"
        )
        result = score_breakdown(text)
        assert isinstance(result["score"], int)
        assert isinstance(result["bonuses"], list)
        assert isinstance(result["penalties"], list)
        bonus_names = [b[0] for b in result["bonuses"]]
        assert "Output format specified" in bonus_names
        assert "Constraints/boundaries set" in bonus_names

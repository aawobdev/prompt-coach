"""Tests for deterministic metrics and scoring."""

import pytest

from prompt_coach.analysis.metrics import (
    has_constraints,
    has_example,
    has_sections,
    has_structured_output,
)
from prompt_coach.analysis.score import score_breakdown, score_prompt


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

    def test_columns_format(self):
        assert has_structured_output("show columns for name and status")
        assert has_structured_output("tabular data")

    def test_no_format(self):
        assert not has_structured_output("Can you look at my inbox")


class TestHasConstraints:
    def test_explicit_constraints(self):
        assert has_constraints("you must only use stdlib")
        assert has_constraints("never delete files")

    def test_implicit_bullet_list(self):
        assert has_constraints("- resized down\n- stopped\n- migrated")
        assert has_constraints("* CPU usage < 30%\n* idle for 30 days")

    def test_implicit_numbered_list(self):
        assert has_constraints("1. Check this\n2. Do that")

    def test_no_constraints(self):
        assert not has_constraints("Can you look at my inbox")


class TestHasSections:
    def test_two_headers(self):
        assert has_sections("Problem: xyz\nExpected: abc")

    def test_goal_and_steps(self):
        assert has_sections("Goal: fix this\nSteps:\n1. do it")

    def test_single_header_not_enough(self):
        assert not has_sections("Problem: xyz")

    def test_no_headers(self):
        assert not has_sections("just a normal prompt")


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

    def test_follow_up_not_penalised(self):
        """Short follow-ups in Claude Code should not get format/example penalties."""
        text = "yes then wrap up"
        score = score_prompt(text)
        # Base 50, too_short -10 = 40
        assert score == 40

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

"""Tests for deterministic metrics and scoring."""

from datetime import UTC, datetime

import pytest

from prompt_coach.analysis.metrics import (
    authored_text,
    compute_metrics,
    estimate_tokens,
    has_constraints,
    has_example,
    has_sections,
    has_structured_output,
    is_bare_paste,
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
        assert has_structured_output("give me a list of items")
        assert has_structured_output("Give me a list showing")
        assert has_structured_output("can you print the detailed line item list")

    def test_list_mention_without_an_output_verb_is_not_a_format(self):
        """Real corpus 2026-09-24: bare "list of" was about half false
        positives, and pushed those prompts into the >=70 streak band."""
        assert not has_structured_output("Do you have the list of features outstanding?")
        assert not has_structured_output("what's next on the list of fixes")
        assert not has_structured_output("the repo has a list of ALL my projects")

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

    def test_a_bare_percentage_is_not_a_bound(self):
        """Real corpus 2026-09-24: a figure in a bullet is a reading, not a
        limit. It needs a bound next to it."""
        assert not has_constraints("- Related context from the same slides: 72%")
        assert not has_constraints("3. So I'm not 100% sure")
        assert has_constraints("- keep error rate below 2%")
        assert has_constraints("- at most 10% of rows may change")

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
        assert has_example("input == output")

    def test_fence_counts_only_when_cued(self):
        """A fence is where pasted material lives: it's an example only when
        the authored text right before it says so."""
        assert not has_example("```code```")
        assert not has_example("here's the output:\n```\nERROR boom\n```")
        assert has_example('Expected output like this:\n```json\n{"a": 1}\n```')
        assert has_example("in this format:\n```\nname | id\n```")

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
        assert has_sections(
            "Problem: the nightly sync stops after about an hour\n"
            "Expected: it keeps running until the queue is empty"
        )

    def test_goal_and_steps(self):
        assert has_sections("Goal: get the export job passing again\nSteps:\n1. do it")

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

    def test_header_value_is_read_from_its_own_line(self):
        # The label pattern's trailing \s can swallow the newline; the body
        # line below a header must not be mistaken for a 1-3 word value.
        assert has_sections("Root Cause:\nOK.\nFix:\nDone.")

    # Pasted-document shapes from the real corpus (2026-09-24), where about
    # 18 of the 26 prompts credited with sections were pastes. One test per
    # rule, each built so only that rule rejects it.

    def test_form_fields_with_short_values_are_not_sections(self):
        assert not has_sections("CMDKEY: /list\nSamba password: stored\nCount: 5")

    def test_repeated_labels_are_records_not_sections(self):
        # Prose values and distinct names would pass every other rule.
        assert not has_sections(
            "Question: what time is it in Rome right now\n"
            "Answer: it is quarter to four in the afternoon\n"
            "Question: what is the capital of Italy please\n"
            "Answer: the capital of Italy is Rome, of course"
        )

    def test_labels_buried_in_a_long_document_are_not_sections(self):
        contract = "Clause text goes on and on. " * 800
        assert not has_sections(
            "Scope: this agreement covers all the services listed below\n"
            + contract
            + "\nNotes on completeness: this covers everything we agreed on"
        )

    def test_headers_with_nothing_under_them_are_not_sections(self):
        # ipconfig once its dotted detail lines are stripped as output
        assert not has_sections(
            "Wireless LAN adapter WiFi:\n\n   Connection-specific DNS Suffix  . :\n"
            "   IPv4 Address. . . . . . . : 10.0.0.2\n"
            "Ethernet adapter Ethernet 2:\n\n   Connection-specific DNS Suffix  . :\n"
            "   Media State . . . . . . . : Media disconnected"
        )

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

    def test_billing_rewrite_scores_well(self):
        text = (
            "Check the billing emails from my storage provider. I need:\n"
            "- Any invoices due in the next 30 days\n"
            "- Any expired/cancelled items\n"
            "- Any storage overage warnings\n\n"
            "Return a table: item | status | amount | action needed"
        )
        score = score_prompt(text)
        assert score >= 55, f"Expected >= 55, got {score}"

    def test_structured_bug_report_outscores_a_vague_ask(self):
        """Sections, no format or example. Used to be pinned at 15-40, below
        the vague inbox ask: the length-gated absence penalties made a
        careful report cost more than a lazy one (fixed 2026-09-25)."""
        text = (
            'Plex "Recently Added" section isn\'t updating properly.\n\n'
            "Problem: Added 3 movies yesterday. Section shows 1, and it's from 4 days ago. "
            "Refreshing the library metadata doesn't fix it.\n"
            "Expected: New items appear within 5 minutes.\n"
            "Logs: checked PMS logs, no errors around scanning.\n\n"
            "What's the next step to diagnose?"
        )
        vague = score_prompt("Can you look at my inbox and figure out what to do")
        assert score_prompt(text) > vague

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
        constraint -- it must still take the no-success-criteria penalty."""
        text = (
            "Now go through the whole billing export from last quarter and figure out "
            "which customers churned, why they left based on the notes field, and whether "
            "we could have caught it earlier by looking at usage trends before they "
            "cancelled their subscription entirely."
        )
        result = score_breakdown(text)
        assert NO_CRITERIA in [p[0] for p in result["penalties"]]
        assert result["score"] < 50

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


NO_CRITERIA = "No success criteria (format, example or constraint)"


class TestLengthRules:
    """Each case isolates one length rule; see score._factors (2026-09-25)."""

    @staticmethod
    def names(text):
        b = score_breakdown(text)
        return [x[0] for x in b["bonuses"]] + [x[0] for x in b["penalties"]]

    def test_more_detail_never_lowers_the_score(self):
        # Same signals throughout; only neutral detail is added. Before the
        # fix the score fell 50 -> 45 -> 35 -> 25 as the report grew.
        for opener in (
            "Fix the login bug in the auth service.",
            "Fix the login bug in the auth service. Do not change the API.",
        ):
            text, scores = opener, []
            while len(text) < 480:
                scores.append(score_prompt(text))
                text += " The session token expires early after a refresh."
            assert scores == sorted(scores), scores

    def test_no_criteria_penalty_at_any_length(self):
        assert NO_CRITERIA in self.names("look at my inbox please")
        assert NO_CRITERIA in self.names("look at my inbox please. " * 12)

    @pytest.mark.parametrize(
        "text",
        [
            "Summarise the inbox. Return a table: sender | subject | action",
            "Summarise the inbox, for example: 'Bank - statement ready - file it'",
            "Summarise the inbox. Only emails from this week.",
        ],
    )
    def test_any_one_criterion_lifts_the_penalty(self, text):
        assert NO_CRITERIA not in self.names(text)

    def test_short_prompt_gets_too_short_not_both(self):
        assert self.names("fix it") == ["Too short (< 20 chars)"]

    def test_detail_bonus_has_no_upper_cap(self):
        assert "Enough detail (50+ chars)" in self.names("Describe the fault in detail. " * 12)

    def test_pasted_text_is_not_detail(self):
        assert "Enough detail (50+ chars)" not in self.names(TRACEBACK + "\nwhy?")

    def test_bare_paste_leftovers_are_not_detail(self):
        # A PowerShell banner survives authored_text as ~65 chars (real data).
        banner = "PowerShell 7.6.5 Loaded VAULT_ACCESS_TOKEN -> $env:VAULT_ACCESS_TOKEN\n"
        text = banner + TRACEBACK * 3
        assert len(authored_text(text)) >= 50
        assert is_bare_paste(text)
        assert "Enough detail (50+ chars)" not in self.names(text)

    def test_pasted_text_is_not_rambling(self):
        text = "Why does this fail after the upgrade?\n" + TRACEBACK * 4
        assert len(text) > 500
        assert not any(n.startswith("Rambling") for n in self.names(text))

    def test_typed_rambling_still_penalised(self):
        assert any(n.startswith("Rambling") for n in self.names("I think maybe it broke. " * 25))

    def test_too_short_reads_the_whole_prompt(self):
        assert "Too short (< 20 chars)" not in self.names(TRACEBACK + "\nwhy?")

    def test_score_is_the_clamped_sum_of_its_breakdown(self):
        every_bonus = (
            "Task: tidy the docker config.\n"
            "Constraints: only edit compose files, do not restart anything.\n"
            "Example: 'plex - port 32400 - ok'\n"
            "Return a table: service | port | status"
        )
        b = score_breakdown(every_bonus)
        assert 50 + sum(v for _, v in b["bonuses"] + b["penalties"]) > 100
        assert b["score"] == 100
        for text in ("fix it", TRACEBACK, "Summarise the inbox. Only emails from this week."):
            b = score_breakdown(text)
            total = 50 + sum(v for _, v in b["bonuses"] + b["penalties"])
            assert b["score"] == max(0, min(100, total))


TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "app.py", line 42, in <module>\n'
    "    foo()\n"
    "ValueError: bad thing\n"
    "```\n"
    "def foo():\n"
    '    raise ValueError("bad thing")\n'
    "```"
)
SYSTEMCTL = (
    "\u25cf nginx.service - A high performance web server\n"
    "     Loaded: loaded (/lib/systemd/system/nginx.service; enabled)\n"
    "     Active: active (running) since Tue 2026-09-24 10:00:00 UTC; 2h ago\n"
    "   Main PID: 1234 (nginx)\n"
    "      Tasks: 3 (limit: 4915)\n"
    "     Memory: 5.1M\n"
    "        CPU: 120ms"
)


class TestPasteDetection:
    """Pasted material can't earn structure signals it didn't author, and a
    prompt that's mostly paste with little ask is flagged (2026-09-24). The
    original bug: a bare fenced paste scored as a *good* prompt because the
    fence counted as a worked example."""

    def test_authored_text_strips_fences_output_lines_and_kv_dumps(self):
        text = "Why does this fail?\n" + TRACEBACK + "\n" + SYSTEMCTL
        assert authored_text(text).strip() == "Why does this fail?"

    def test_compact_typed_spec_stays_authored(self):
        text = "Goal: ship it\nContext: prod is down\nSteps: roll back\nOutput: a summary"
        assert authored_text(text) == text

    def test_bare_paste_detected(self):
        assert is_bare_paste(TRACEBACK)
        assert is_bare_paste(SYSTEMCTL)
        assert is_bare_paste("```python\n" + "x = compute(a, b)\n" * 6 + "```")
        assert is_bare_paste("fix this\n" + TRACEBACK)

    def test_real_asks_are_not_pastes(self):
        assert not is_bare_paste(
            "Please go through the billing export and work out which customers churned "
            "and why, then summarise it for me in a short table."
        )
        assert not is_bare_paste(
            "This test fails after the upgrade and I've already tried clearing the cache "
            "and pinning the old version. What else could cause it?\n" + TRACEBACK
        )
        assert not is_bare_paste("run it")  # too short to call

    def test_pasted_output_earns_no_structure_signals(self):
        for text in (TRACEBACK, SYSTEMCTL):
            assert not has_example(text)
            assert not has_sections(text)
            assert not has_structured_output(text)
            assert not has_constraints(text)

    def test_repeated_or_lowercase_labels_are_not_sections(self):
        assert not has_sections("Warning: a\nWarning: b\nWarning: c")
        assert not has_sections("concurrency:\n  group: x\nruns-on: y")

    def test_bare_paste_scores_low_and_says_why(self):
        # Before: the fence earned the example bonus and nothing penalised
        # the missing ask, so this scored 55-65. Now it's penalised.
        result = score_breakdown(TRACEBACK)
        assert result["score"] < 40
        assert "Mostly pasted, little instruction" in [p[0] for p in result["penalties"]]


class TestPastedSessionsAndChats:
    """Provenance, not shape: a pasted terminal session or chat log is
    stripped from the authored text between its first and last marker, so a
    tool's help text or a bot's prose reply inside it can't earn structure
    signals (real corpus 2026-09-24: both still earned the sections bonus)."""

    PS_SESSION = (
        "PowerShell 7.6.5\n"
        "PS C:\\Windows\\System32> wsl --help\n"
        "Usage: wsl.exe [Argument] [Options...] [CommandLine]\n"
        "Parameters:\n"
        "    --distribution, -d <Distro> run the specified distribution for this call\n"
        "Remarks: the default distribution is used when no distribution is given at all\n"
        "PS C:\\Windows\\System32>"
    )
    SLACK = (
        "Hermes  [7:30 PM]\n"
        "Let me check both the repo docs and the skills.\n"
        "(edited)\n"
        "[7:31 PM]\n"
        "The pattern: keep one canonical file that both tools read from the repo.\n"
        "For Claude: point CLAUDE.md at AGENTS.md so it loads the same guidance.\n"
        "For Hermes: load the same file through its skills directory on start.\n"
        "(edited)"
    )

    def test_session_between_prompts_is_not_authored(self):
        text = "Why does wsl ignore my distro flag?\n\n" + self.PS_SESSION
        assert authored_text(text).split("\n")[0] == "Why does wsl ignore my distro flag?"
        assert "Usage" not in authored_text(text)
        assert not has_sections(text)

    def test_a_typed_question_after_a_blank_line_stays_authored(self):
        text = self.PS_SESSION + "\nsome trailing output\n\nWhat does that remark mean for me?"
        assert authored_text(text).strip().endswith("What does that remark mean for me?")

    def test_unix_prompt_session(self):
        text = "user@dev:~$ git status\nOn branch main\nnothing to commit"
        assert authored_text(text).strip() == ""
        assert is_bare_paste(text + "\n" + "working tree clean and everything is fine " * 3)

    def test_chat_log_is_not_authored(self):
        text = "Is this the right way to share skills between tools?\n" + self.SLACK
        assert authored_text(text).strip() == "Is this the right way to share skills between tools?"
        assert not has_sections(text)

    def test_whatsapp_export_is_not_authored(self):
        text = "Rough numbers below.\n[19:21, 17/09/2026] Sam: Design: 3 days\nBuild: 5 days"
        assert authored_text(text).strip() == "Rough numbers below."

    def test_powershell_prompt_pasted_mid_line_after_typed_text(self):
        # Real shape (2026-09-25): the paste starts on the typed line, so the
        # line-start marker only saw the closing prompt and the tool's help
        # text stayed "authored" (the prompt scored 65 on an example bonus).
        text = (
            "Seems to work, I switched twice PS C:\\Users\\x\\Downloads> python live.py\n"
            "Usage: live.py [options]\n"
            "Options: for example --verbose, only use this when debugging\n"
            "PS C:\\Users\\x\\Downloads>"
        )
        assert authored_text(text).strip() == "Seems to work, I switched twice"
        assert not has_example(text)
        assert not has_constraints(text)

    def test_powershell_path_in_typed_prose_stays_authored(self):
        # No ">" after the path: a mention, not a prompt.
        text = "Run it from PS C:\\temp and tell me what it prints"
        assert authored_text(text) == text
        # "PS" must start a word: the tail of GPS isn't a prompt.
        text = "Open the GPS C:\\maps> folder and list what is in it"
        assert authored_text(text) == text

    def test_typed_text_mentioning_a_speaker_and_time_stays_authored(self):
        # A chat speaker name has no colon; a typed ask before it does.
        text = "Can you help me fix: Hermes  [9:00 PM]"
        assert authored_text(text) == text

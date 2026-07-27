"""Doc-quality tests: redirect detection, size/structure/staleness scoring,
walk-up discovery, aggregation over a prompt corpus."""

import subprocess
from datetime import UTC, datetime

from prompt_coach.analysis.docs import (
    analyse_docs,
    find_project_docs,
    git_staleness_days,
    is_redirect_stub,
    score_doc,
)
from prompt_coach.models import Prompt, PromptOrigin, SourceKind
from prompt_coach.stores.base import content_hash


def make(cwd, session="s1", ref="0"):
    return Prompt(
        source=SourceKind.CLAUDE_CODE,
        session_id=session,
        message_ref=ref,
        content="some prompt",
        content_hash=content_hash(f"{cwd}:{ref}"),
        timestamp=datetime(2026, 7, 1, tzinfo=UTC),
        origin=PromptOrigin.HUMAN,
        cwd=cwd,
    )


class TestIsRedirectStub:
    def test_short_pointer_is_redirect(self):
        assert is_redirect_stub("See [AGENTS.md](./AGENTS.md) for guidance.")
        assert is_redirect_stub("See AGENTS.md.")

    def test_long_text_is_not_redirect_even_with_doc_name(self):
        text = "AGENTS.md " + ("word " * 40)
        assert not is_redirect_stub(text)

    def test_short_text_without_doc_name_is_not_redirect(self):
        assert not is_redirect_stub("Just a short note about nothing in particular here.")


class TestFindProjectDocs:
    def test_finds_docs_in_cwd_itself(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("guidance")
        found = find_project_docs(str(tmp_path), home=tmp_path.parent)
        assert found == [tmp_path / "AGENTS.md"]

    def test_walks_up_to_project_root(self, tmp_path):
        (tmp_path / "README.md").write_text("root readme")
        sub = tmp_path / "src" / "nested"
        sub.mkdir(parents=True)
        found = find_project_docs(str(sub), home=tmp_path.parent)
        assert found == [tmp_path / "README.md"]

    def test_no_docs_anywhere_returns_empty(self, tmp_path):
        sub = tmp_path / "empty"
        sub.mkdir()
        assert find_project_docs(str(sub), home=tmp_path.parent) == []

    def test_stops_at_home(self, tmp_path):
        home = tmp_path / "home"
        project = home / "projects" / "x"
        project.mkdir(parents=True)
        (tmp_path / "README.md").write_text("outside home, must not be found")
        assert find_project_docs(str(project), home=home) == []

    def test_nonexistent_cwd_returns_empty(self, tmp_path):
        assert find_project_docs(str(tmp_path / "does-not-exist")) == []


class TestScoreDoc:
    def test_sparse_flag_on_short_non_redirect_doc(self, tmp_path):
        path = tmp_path / "AGENTS.md"
        path.write_text("Short doc. " * 5)
        finding = score_doc(path, home=tmp_path.parent)
        assert "sparse" in finding.flags
        assert not finding.is_redirect

    def test_redirect_stub_never_flagged(self, tmp_path):
        path = tmp_path / "CLAUDE.md"
        path.write_text("See AGENTS.md.")
        finding = score_doc(path, home=tmp_path.parent)
        assert finding.is_redirect
        assert finding.flags == ()

    def test_unstructured_flag_when_long_and_no_headers_or_lists(self, tmp_path):
        path = tmp_path / "README.md"
        path.write_text("word " * 200)
        finding = score_doc(path, home=tmp_path.parent)
        assert "unstructured" in finding.flags
        assert "sparse" not in finding.flags

    def test_structured_long_doc_gets_no_structure_flag(self, tmp_path):
        path = tmp_path / "README.md"
        path.write_text("# Heading\n\n- item one\n- item two\n\n" + "word " * 200)
        finding = score_doc(path, home=tmp_path.parent)
        assert "unstructured" not in finding.flags
        assert finding.headers == 1
        assert finding.list_items == 2

    def test_path_shortened_relative_to_home(self, tmp_path):
        home = tmp_path
        project = home / "projects" / "demo"
        project.mkdir(parents=True)
        path = project / "AGENTS.md"
        path.write_text("# Guidance\n\n- rule one\n\n" + "word " * 200)
        finding = score_doc(path, home=home)
        assert finding.path == "~/projects/demo/AGENTS.md"


class TestGitStaleness:
    def test_untracked_file_returns_none(self, tmp_path):
        path = tmp_path / "README.md"
        path.write_text("not in a repo")
        assert git_staleness_days(path) is None

    def test_freshly_committed_file_is_not_stale(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
        path = tmp_path / "README.md"
        path.write_text("fresh")
        subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add readme"], cwd=tmp_path, check=True)
        staleness = git_staleness_days(path)
        assert staleness is not None
        assert staleness < 1.0


class TestAnalyseDocs:
    def test_aggregates_across_unique_cwds_and_counts_missing(self, tmp_path):
        home = tmp_path
        with_docs = home / "projects" / "a"
        with_docs.mkdir(parents=True)
        (with_docs / "AGENTS.md").write_text("Short. " * 5)
        without_docs = home / "projects" / "b"
        without_docs.mkdir(parents=True)

        prompts = [
            make(str(with_docs), ref="1"),
            make(str(with_docs), ref="2"),  # same cwd, doc scored once
            make(str(without_docs), ref="3"),
        ]
        summary = analyse_docs(prompts, home=home)
        assert summary.dirs_checked == 2
        assert summary.dirs_without_docs == 1
        assert len(summary.findings) == 1
        assert "sparse" in summary.findings[0].flags

    def test_prompts_without_cwd_are_ignored(self):
        p = Prompt(
            source=SourceKind.CLAUDE_CODE,
            session_id="s1",
            message_ref="0",
            content="some prompt",
            content_hash=content_hash("no-cwd"),
            timestamp=datetime(2026, 7, 1, tzinfo=UTC),
            origin=PromptOrigin.HUMAN,
            cwd=None,
        )
        summary = analyse_docs([p])
        assert summary.dirs_checked == 0
        assert summary.findings == ()

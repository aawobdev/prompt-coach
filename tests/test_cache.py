"""CacheDB tests: idempotent resync, offset resume, fork dedupe, FTS, llm cache."""

import json
from datetime import UTC, datetime

import pytest

from prompt_coach.cache import CacheDB
from prompt_coach.models import Prompt, PromptOrigin, SourceKind, StoreInfo
from prompt_coach.stores.base import content_hash
from prompt_coach.stores.claude_code import ClaudeCodeStore


def make_prompt(ref: str, content: str, ts_offset: int = 0, **kw) -> Prompt:
    defaults = dict(
        source=SourceKind.JSON_IMPORT,
        session_id="s1",
        message_ref=ref,
        content=content,
        content_hash=content_hash(content),
        timestamp=datetime(2026, 7, 1, 12, 0, ts_offset, tzinfo=UTC),
        origin=PromptOrigin.HUMAN,
    )
    defaults.update(kw)
    return Prompt(**defaults)


class FakeStore:
    """Watermark-path store (no iter_files attribute)."""

    kind = SourceKind.JSON_IMPORT

    def __init__(self, prompts):
        self._prompts = prompts

    def discover(self):
        return StoreInfo(kind=self.kind, path=None, available=True)

    def iter_prompts(self, since=None):
        for p in self._prompts:
            if since is None or p.timestamp >= since:
                yield p


class BrokenStore:
    kind = SourceKind.HERMES

    def discover(self):
        return StoreInfo(kind=self.kind, path=None, available=False, detail="not found")

    def iter_prompts(self, since=None):
        raise AssertionError("must not be called")


@pytest.fixture
def cache(tmp_path):
    db = CacheDB(tmp_path / "cache.db")
    yield db
    db.close()


def test_sync_is_idempotent(cache):
    store = FakeStore([make_prompt("a", "first prompt"), make_prompt("b", "second prompt", 1)])
    s1 = cache.sync([store])
    assert (s1.added, s1.deduped) == (2, 0)
    s2 = cache.sync([store], force=True)
    assert s2.added == 0
    assert s2.deduped == 2
    assert cache.counts()["prompts"] == 2


def test_watermark_skips_old_prompts(cache):
    store = FakeStore([make_prompt("a", "first prompt")])
    cache.sync([store])
    store._prompts.append(make_prompt("b", "newer prompt", 30))
    s = cache.sync([store])
    # Watermark is boundary-inclusive (same-second safety), so at most the
    # boundary row is rescanned; everything older is skipped entirely.
    assert s.scanned <= 2
    assert s.added == 1


def test_fork_dedupe_same_content_and_time_different_session(cache):
    p1 = make_prompt("u-1", "duplicated across fork")
    p2 = make_prompt("u-1", "duplicated across fork", session_id="s2")
    s = cache.sync([FakeStore([p1, p2])])
    assert s.added == 1
    assert s.deduped == 1


def test_unavailable_store_recorded_not_fatal(cache):
    s = cache.sync([BrokenStore(), FakeStore([make_prompt("a", "hello world")])])
    assert s.stores_failed == {"hermes": "not found"}
    assert s.added == 1


def test_filters(cache):
    prompts = [
        make_prompt("a", "human one"),
        make_prompt("b", "TASK: machine one", 5, origin=PromptOrigin.MACHINE),
        make_prompt("c", "human two", 10),
    ]
    cache.sync([FakeStore(prompts)])
    assert len(cache.prompts(origin=PromptOrigin.MACHINE)) == 1
    since = datetime(2026, 7, 1, 12, 0, 8, tzinfo=UTC)
    assert [p.message_ref for p in cache.prompts(since=since)] == ["c"]
    assert len(cache.prompts(source=SourceKind.HERMES)) == 0
    assert len(cache.prompts(limit=2)) == 2


def test_fts_search(cache):
    cache.sync(
        [
            FakeStore(
                [
                    make_prompt("a", "fix the nginx proxy timeout for reports"),
                    make_prompt("b", "write a poem about databases", 1),
                ]
            )
        ]
    )
    hits = cache.search("nginx proxy")
    assert len(hits) == 1
    assert "nginx" in hits[0].content
    assert cache.search('weird "quoted @@ query') == []


def test_claude_file_offset_resume(cache, tmp_path):
    proj = tmp_path / "projects" / "-home-x-p"
    proj.mkdir(parents=True)
    f = proj / "sess.jsonl"

    def cc_line(uuid, text):
        return (
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": text},
                    "promptSource": "typed",
                    "origin": {"kind": "human"},
                    "isSidechain": False,
                    "uuid": uuid,
                    "timestamp": f"2026-06-22T21:39:{uuid[-2:]}.000Z",
                    "sessionId": "sess-1",
                    "cwd": "/home/x/p",
                }
            )
            + "\n"
        )

    f.write_text(cc_line("u-01", "first question"))
    store = ClaudeCodeStore(tmp_path / "projects")

    s1 = cache.sync([store])
    assert s1.added == 1

    # Unchanged file: not rescanned at all.
    s2 = cache.sync([store])
    assert s2.scanned == 0

    # Appended line: only the tail is parsed.
    with open(f, "a") as fh:
        fh.write(cc_line("u-02", "second question"))
    s3 = cache.sync([store])
    assert (s3.scanned, s3.added) == (1, 1)

    # Rewritten (shrunk) file: reparsed from zero, idempotently.
    f.write_text(cc_line("u-01", "first question"))
    s4 = cache.sync([store])
    assert s4.added == 0
    assert cache.counts()["prompts"] == 2


def test_llm_cache_roundtrip(cache):
    assert cache.get_llm("k1") is None
    cache.put_llm("k1", {"answer": 42}, model="m", template_version="v1")
    assert cache.get_llm("k1") == {"answer": 42}
    assert cache.llm_cache_stats()["entries"] == 1


def test_clear(cache):
    cache.sync([FakeStore([make_prompt("a", "hello")])])
    cache.put_llm("k", {}, "m", "v1")
    cache.clear()
    assert cache.counts()["prompts"] == 0
    assert cache.get_llm("k") is None
    assert cache.search("hello") == []

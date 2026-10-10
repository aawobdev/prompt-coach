"""Cost layer: pricing, formula, config and aggregation (TEST-PLAN U1-U9, C3, C10)."""

from datetime import UTC, datetime, timedelta

import pytest

from prompt_coach.analysis.cost import (
    aggregate_sessions,
    group_costs,
    record_cost,
    summarise,
)
from prompt_coach.config import CostConfig, load_config
from prompt_coach.models import SessionUsage, SourceKind, UsageRecord
from prompt_coach.pricing import DEFAULT_PRICES, Price, list_cost_usd, normalise_model, price_for

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def rec(
    *,
    session="s1",
    key="k1",
    model="claude-sonnet-5-5",
    calls=1,
    inp=0,
    out=0,
    read=0,
    write=0,
    write_1h=0,
    source=SourceKind.CLAUDE_CODE,
    ts=T0,
    cwd="/home/x/projects/acme",
    overhead=False,
    store_cost=None,
):
    return UsageRecord(
        source=source,
        session_id=session,
        record_key=key,
        first_ts=ts,
        last_ts=ts,
        model=model,
        calls=calls,
        input_tokens=inp,
        cache_read_tokens=read,
        cache_write_tokens=write,
        cache_write_1h_tokens=write_1h,
        output_tokens=out,
        cwd=cwd,
        overhead=overhead,
        store_cost_usd=store_cost,
    )


@pytest.fixture
def clean_env(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith("PROMPT_COACH_COST_"):
            monkeypatch.delenv(name)


# -- U1/U2 formula ----------------------------------------------------------


def test_formula_matches_hand_computed_value():
    p = Price(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75)
    usd = list_cost_usd(
        p,
        input_tokens=1_000_000,
        output_tokens=200_000,
        cache_read_tokens=5_000_000,
        cache_write_tokens=400_000,
        cache_write_1h_tokens=100_000,
    )
    # 3 + 3 + 1.5 + 1.5 + 0.1M * 6.0 (1h writes at 2x input) = 9.6
    assert usd == pytest.approx(9.6, abs=1e-6)


def test_billed_fraction_and_rate_scale_linearly():
    r = rec(inp=1_000_000)  # sonnet-5-5: $2.00
    base = record_cost(r, CostConfig())
    assert base == pytest.approx(2.0)
    assert record_cost(r, CostConfig(billed_fraction=0.5)) == pytest.approx(1.0)
    assert record_cost(r, CostConfig(billed_fraction=0.7, usd_rate=0.92)) == pytest.approx(
        2.0 * 0.7 * 0.92
    )


def test_per_store_billed_fraction_override():
    cost = CostConfig(billed_fraction=0.5, billed_fraction_by_store={"hermes": 0.25})
    assert record_cost(rec(inp=1_000_000), cost) == pytest.approx(1.0)
    hermes = rec(inp=1_000_000, source=SourceKind.HERMES)
    assert record_cost(hermes, cost) == pytest.approx(0.5)


def test_store_cost_used_when_present():
    """Hermes's own estimate wins over the price table (owner's decision)."""
    r = rec(model="deepseek/deepseek-v4-flash", inp=10_000_000, store_cost=1.25)
    assert record_cost(r, CostConfig(billed_fraction=0.8)) == pytest.approx(1.0)


# -- U3/U4/U5 pricing ---------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["claude-opus-5", "claude-opus-5.0", "claude-opus-4-8@20251101", "claude-opus-4-7"]
)
def test_opus_family_glob(name):
    assert price_for(name, DEFAULT_PRICES) == Price(5.0, 25.0, 0.5, 6.25)


def test_opus_5_5_has_its_own_lower_price():
    assert price_for("claude-opus-5-5", DEFAULT_PRICES).input == 4.0


@pytest.mark.parametrize(
    "a,b",
    [
        ("claude-opus-5.5", "claude-opus-5-5"),
        ("anthropic/claude-sonnet-5.5", "claude-sonnet-5-5"),
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
    ],
)
def test_spellings_get_the_same_price(a, b):
    assert normalise_model(a) == normalise_model(b)
    assert price_for(a, DEFAULT_PRICES) == price_for(b, DEFAULT_PRICES) is not None


@pytest.mark.parametrize("name", ["claude-opus-4-1", "deepseek-v4-flash", "gpt-5.5", None, ""])
def test_unknown_model_has_no_price(name):
    assert price_for(name, DEFAULT_PRICES) is None


def test_unpriced_record_keeps_tokens_and_costs_none():
    r = rec(model="mystery-model", inp=123, out=45)
    assert record_cost(r, CostConfig()) is None
    (s,) = aggregate_sessions([r], CostConfig())
    assert s.est_cost is None
    assert (s.input_tokens, s.output_tokens, s.unpriced_calls) == (123, 45, 1)


def test_user_price_override_tried_before_defaults():
    cost = CostConfig(price_overrides=(("claude-sonnet*", Price(1.0, 1.0, 1.0, 1.0)),))
    assert record_cost(rec(inp=1_000_000), cost) == pytest.approx(1.0)


# -- U6/U7 config -------------------------------------------------------------


def test_cost_config_defaults_are_neutral(clean_env, tmp_path):
    cost = load_config(tmp_path / "nope.toml").cost
    assert (cost.currency, cost.billed_fraction, cost.usd_rate, cost.enabled) == (
        "USD",
        1.0,
        1.0,
        True,
    )


def test_cost_config_env_beats_toml_beats_default(clean_env, monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[cost]\ncurrency = "EUR"\nusd_to_eur = 0.92\nbilled_fraction = 0.7\n')
    cost = load_config(path).cost
    assert (cost.currency, cost.usd_rate, cost.billed_fraction) == ("EUR", 0.92, 0.7)
    monkeypatch.setenv("PROMPT_COACH_COST_BILLED_FRACTION", "0.6")
    monkeypatch.setenv("PROMPT_COACH_COST_USD_RATE", "0.9")
    cost = load_config(path).cost
    assert (cost.usd_rate, cost.billed_fraction) == (0.9, 0.6)


def test_price_table_from_toml(clean_env, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[cost.prices."deepseek-v4-flash*"]\n'
        "input = 0.1\noutput = 0.4\ncache_read = 0.01\ncache_write = 0.1\n"
    )
    cost = load_config(path).cost
    assert price_for("deepseek/deepseek-v4-flash", cost.prices) == Price(0.1, 0.4, 0.01, 0.1)


@pytest.mark.parametrize(
    "body,needle",
    [
        ("[cost]\nbilled_fraction = -0.5\n", "billed_fraction"),
        ('[cost]\nusd_rate = "lots"\n', "usd_rate"),
        (
            '[cost.prices."x*"]\ninput = "cheap"\noutput = 1\ncache_read = 1\ncache_write = 1\n',
            "input",
        ),
        ('[cost.prices."x*"]\ninput = 1\n', "missing"),
    ],
)
def test_bad_cost_config_is_a_clear_error(clean_env, tmp_path, body, needle):
    path = tmp_path / "config.toml"
    path.write_text(body)
    with pytest.raises(ValueError, match=needle):
        load_config(path)


# -- U8/U9 derived and mixed models -------------------------------------------


def test_context_per_call_with_zero_calls_is_zero():
    s = SessionUsage(
        source=SourceKind.HERMES,
        session_id="s",
        model=None,
        api_calls=0,
        input_tokens=10,
        cache_read_tokens=0,
        cache_write_tokens=0,
        output_tokens=0,
        first_ts=T0,
        last_ts=T0,
        cwd=None,
        max_gap_seconds=None,
        models=(),
    )
    assert s.context_per_call == 0


def test_mixed_model_session_costs_each_row_at_its_own_price():
    opus = rec(key="a", model="claude-opus-5", calls=10, inp=1_000_000, source=SourceKind.HERMES)
    sonnet = rec(
        key="b", model="claude-sonnet-4-6", calls=30, inp=1_000_000, source=SourceKind.HERMES
    )
    (s,) = aggregate_sessions([opus, sonnet], CostConfig())
    assert s.est_cost == pytest.approx(5.0 + 3.0)
    assert s.model == "claude-sonnet-4-6"  # most calls
    assert s.models == (("claude-sonnet-4-6", 30), ("claude-opus-5", 10))


def test_overhead_is_folded_in_and_reported():
    main = rec(key="a", inp=1_000_000)
    side = rec(key="b", inp=500_000, overhead=True)
    (s,) = aggregate_sessions([main, side], CostConfig())
    assert s.est_cost == pytest.approx(3.0)
    assert s.overhead_cost == pytest.approx(1.0)
    assert s.overhead_calls == 1


def test_max_gap_only_for_per_call_records():
    calls = [rec(key=str(i), ts=T0 + timedelta(minutes=m)) for i, m in enumerate([0, 2, 9, 10])]
    (s,) = aggregate_sessions(calls, CostConfig())
    assert s.max_gap_seconds == 7 * 60
    (h,) = aggregate_sessions([rec(calls=40, source=SourceKind.HERMES)], CostConfig())
    assert h.max_gap_seconds is None


# -- C3/C10 grouping and lever --------------------------------------------------


def _mixed_records():
    return [
        rec(session="s1", key="a", model="claude-opus-5-5", out=400_000, cwd="/p/api"),
        rec(session="s1", key="b", model="claude-sonnet-5", out=100_000, cwd="/p/api"),
        rec(session="s2", key="c", model="claude-haiku-4-5", out=100_000, cwd="/p/web"),
        rec(
            session="h1",
            key="d",
            model="deepseek-v4-flash",
            calls=5,
            source=SourceKind.HERMES,
            store_cost=0.5,
            ts=T0 + timedelta(days=1),
        ),
        rec(session="h2", key="e", model="local-model", calls=3, source=SourceKind.HERMES),
    ]


@pytest.mark.parametrize("by", ["folder", "model", "store", "day"])
def test_every_grouping_sums_to_the_session_total(by):
    records = _mixed_records()
    session_total = sum(s.est_cost or 0 for s in aggregate_sessions(records, CostConfig()))
    group_total = sum(g.est_cost or 0 for g in group_costs(records, by, CostConfig()))
    assert group_total == pytest.approx(session_total)


def test_lever_is_deterministic_and_picks_the_largest_share():
    records = _mixed_records()
    a = summarise(records, CostConfig())
    b = summarise(list(reversed(records)), CostConfig())
    assert a.lever == b.lever
    assert a.lever.kind == "top_tier_model"  # Opus is $8 of $10, no large sessions
    assert a.lever.share == pytest.approx(0.8)


def test_lever_prefers_long_sessions_when_they_dominate():
    big = [rec(session="big", key=str(i), read=300_000, model="claude-sonnet-5") for i in range(10)]
    summary = summarise(big, CostConfig())
    assert summary.lever.kind == "long_sessions"
    assert summary.large_sessions == 1


def test_no_usage_lists_prompt_sessions_without_usage():
    summary = summarise(
        _mixed_records(),
        CostConfig(),
        prompt_sessions={"codex": {"x", "y"}, "claude-code": {"s1", "zz"}},
    )
    assert summary.no_usage == {"claude-code": 1, "codex": 2}


def test_sessions_sorted_by_cost_with_stable_ties():
    records = [rec(session=sid, key=sid, inp=1_000_000) for sid in ("b", "a", "c")]
    order = [s.session_id for s in aggregate_sessions(records, CostConfig())]
    assert order == ["a", "b", "c"]

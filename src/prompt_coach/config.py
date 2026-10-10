"""Configuration loading: env PROMPT_COACH_* > ~/.config/prompt-coach/config.toml > defaults."""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 -- api_key_cmd runs a user-configured command, by design
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from prompt_coach.models import SourceKind
from prompt_coach.pricing import DEFAULT_PRICES, PRICE_TABLE_DATE, Price

# No default endpoint or model: any OpenAI-compatible endpoint works, and
# guessing one (a LAN IP, a vendor) would send requests somewhere the user
# never chose. Unset = the LLM features are off and everything deterministic
# still runs; `prompt-coach setup` fills these in.
DEFAULT_API_BASE = ""
DEFAULT_MODEL = ""


_FORCE_COLOR_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "PY_COLORS")


class APIKeyCommandFailed(RuntimeError):
    """api_key_cmd failed, timed out, or printed nothing. The message never
    includes the command's output: it may hold a partial secret."""


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    api_key: str
    allow_remote: bool
    timeout: float
    # Shell command that prints the API key, run only when an LLM client is
    # actually built -- never at config load, since every hook call loads
    # config (DECISIONS.md 2026-09-27). Keeps cloud keys out of config.toml:
    # point it at a secrets manager (e.g. `pass show openrouter`).
    api_key_cmd: str = ""
    # Extra JSON merged into every chat-completions request body, for
    # provider-specific knobs the OpenAI schema lacks. None = send nothing.
    # e.g. {"reasoning": {"enabled": false}} stops an OpenRouter reasoning
    # model spending its whole token budget thinking (measured 2026-09-27).
    extra_body: dict | None = None

    @property
    def configured(self) -> bool:
        """An endpoint and model are both set. Unset = deterministic-only."""
        return bool(self.base_url and self.model)

    def resolve_api_key(self, timeout: float = 10.0) -> str:
        if not self.api_key_cmd:
            return self.api_key
        # The key must come back as plain text. A forced-colour environment
        # (FORCE_COLOR, even empty) made a secrets CLI colourise its JSON and
        # the key pipeline exit 4, silently disabling the LLM (2026-10-10).
        cmd_env = {k: v for k, v in os.environ.items() if k not in _FORCE_COLOR_VARS}
        cmd_env["NO_COLOR"] = "1"
        try:
            result = subprocess.run(  # noqa: S602 -- user's own configured command
                self.api_key_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=cmd_env,
            )
        except subprocess.TimeoutExpired:
            raise APIKeyCommandFailed(f"api_key_cmd timed out after {timeout}s") from None
        key = result.stdout.strip()
        if result.returncode != 0:
            raise APIKeyCommandFailed(f"api_key_cmd exited with status {result.returncode}")
        if not key:
            raise APIKeyCommandFailed("api_key_cmd printed nothing")
        return key


# The four live, auto-discovered stores (ChatGPT export and JSON import are
# manual `import`-command stores, never part of the default sync set, so
# they have no enable/disable concept here).
ALL_STORE_KINDS = (
    SourceKind.HERMES.value,
    SourceKind.CLAUDE_CODE.value,
    SourceKind.COPILOT.value,
    SourceKind.CODEX.value,
    SourceKind.COPILOT_CLI.value,
)


@dataclass(frozen=True)
class StoresConfig:
    hermes_db: Path
    claude_projects_dir: Path
    copilot_dir: Path | None  # None = probe default candidates (/mnt/c, ~/.config)
    codex_dir: Path | None  # None = probe default candidates (/mnt/c, ~/.codex)
    # Opt-in, not opt-out (DECISIONS.md 2026-07-29): a store present on disk
    # is only used if named here. Default is all four, preserving the
    # pre-existing implicit "everything present is used" behavior.
    enabled: frozenset[str] = field(default_factory=lambda: frozenset(ALL_STORE_KINDS))
    copilot_cli_dir: Path | None = None  # None = probe (/mnt/c, ~/.copilot)


# "coach": today's default -- only the calibrated weak-prompt triggers fire,
#   at most once per session, and the hook blocks with an LLM rewrite offered
#   (falls back to the old non-blocking tip if the LLM is unreachable).
# "auto": same calibrated triggers as coach, but the improvement is applied
#   automatically -- no block, no accept/reject, and no LLM call: the hook
#   injects an additionalContext directive telling the session model itself
#   to refine the prompt before answering (it has more context and a
#   stronger model than any local rewriter -- see nudge.py's docstring).
#   No once-per-session gate: there's no interruption to ration, so every
#   triggered prompt gets improved. Near-zero latency.
# "always": every UserPromptSubmit is blocked and rewritten by the LLM,
#   regardless of quality or session history -- an explicit opt-in, since it
#   adds LLM latency to every single prompt. If the LLM is unreachable, the
#   prompt is let through unmodified rather than blocking with no way out.
# "off": nudge never fires (UserPromptSubmit or Stop).
NUDGE_MODES = ("coach", "auto", "always", "hint", "off")
DEFAULT_NUDGE_MODE = "coach"


@dataclass(frozen=True)
class NudgeConfig:
    mode: str
    llm_timeout: float  # bounded well below the hook's own timeout in settings.json
    # Path -> mode, config.toml-only (DECISIONS.md 2026-07-30): a mapping has
    # no clean flat env-var form. nudge.py resolves the longest matching
    # path prefix from the hook's cwd, falling back to `mode` above.
    dir_overrides: dict[str, str] = field(default_factory=dict)
    # Auto mode only (2026-09-27): when true, a triggered prompt is rewritten
    # by the [llm] model and the rewrite goes straight to the session model
    # (no block). False keeps the LLM-free self-refine directive. Any rewrite
    # failure falls back to that directive.
    auto_rewrite: bool = False
    # Opt-in (2026-10-10): append a one-line session-size warning (calls,
    # context, cost per reply) when the current session is over the A15
    # limits, at most once every `cost_warning_every` prompts. Off = no
    # lookup at all, so no added latency.
    cost_warning: bool = False
    cost_warning_every: int = 20


# "off": model-fit analysis never runs.
# "descriptive" (default): flag prompts that look mismatched against the
#   model that handled them, no suggested replacement -- stays on the
#   "coach the prompter, not the prompt" side of the line.
# "prescriptive": also suggest a specific better-fit model, but only ever
#   one already observed/installed for that same store (see DECISIONS.md
#   2026-07-29 -- available models are derived empirically, never a
#   hardcoded catalog).
MODEL_FIT_MODES = ("off", "descriptive", "prescriptive")
DEFAULT_MODEL_FIT_MODE = "descriptive"


@dataclass(frozen=True)
class ModelFitConfig:
    mode: str


@dataclass(frozen=True)
class CostConfig:
    """`[cost]`: turns token usage into estimated spend (DECISIONS.md 2026-10-10).
    Defaults are neutral (USD, list price); a user who knows their billing
    sets billed_fraction and currency."""

    enabled: bool = True
    currency: str = "USD"
    usd_rate: float = 1.0  # USD -> currency; set by hand, never looked up (no network)
    billed_fraction: float = 1.0  # share of list price actually billed
    billed_fraction_by_store: dict[str, float] = field(default_factory=dict)
    price_overrides: tuple[tuple[str, Price], ...] = ()  # from config, tried first
    price_table_date: str = PRICE_TABLE_DATE
    context_warn_tokens: int = 100_000
    calls_warn: int = 100
    idle_minutes: float = 5.0

    @property
    def prices(self) -> tuple[tuple[str, Price], ...]:
        return self.price_overrides + DEFAULT_PRICES

    def fraction_for(self, source: str) -> float:
        return self.billed_fraction_by_store.get(source, self.billed_fraction)


@dataclass(frozen=True)
class Config:
    llm: LLMConfig
    stores: StoresConfig
    nudge: NudgeConfig
    model_fit: ModelFitConfig
    cache_dir: Path
    cost: CostConfig = field(default_factory=CostConfig)


def _config_file() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "~/.config")
    return Path(xdg).expanduser() / "prompt-coach" / "config.toml"


def _cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "~/.cache")
    return Path(xdg).expanduser() / "prompt-coach"


def load_config(path: Path | None = None) -> Config:
    """Build the effective config. `path` overrides the default config file location."""
    file_cfg: dict = {}
    cfg_path = path or _config_file()
    if cfg_path.is_file():
        with open(cfg_path, "rb") as f:
            file_cfg = tomllib.load(f)

    llm_cfg = file_cfg.get("llm", {})
    stores_cfg = file_cfg.get("stores", {})
    nudge_cfg = file_cfg.get("nudge", {})

    def env(name: str, fallback: str) -> str:
        return os.environ.get(f"PROMPT_COACH_{name}", fallback)

    base_url = env("API_BASE", llm_cfg.get("api_base", DEFAULT_API_BASE))
    model = env("MODEL", llm_cfg.get("model", DEFAULT_MODEL))
    api_key = env("API_KEY", llm_cfg.get("api_key", ""))
    # An explicit PROMPT_COACH_API_KEY beats the command (handy for one-off
    # runs); the command beats a plain api_key in the file.
    api_key_cmd = (
        ""
        if "PROMPT_COACH_API_KEY" in os.environ
        else env("API_KEY_CMD", llm_cfg.get("api_key_cmd", ""))
    )
    extra_body = _load_extra_body(env("EXTRA_BODY", llm_cfg.get("extra_body", "")))
    if extra_body is None:
        # Legacy (pre-2026-10-10) `reasoning = true|false`: the OpenRouter
        # field it used to hard-code. write_config() saves it as extra_body.
        reasoning_raw = env("REASONING", "")
        legacy = llm_cfg.get("reasoning")
        if reasoning_raw:
            legacy = reasoning_raw.lower() in ("1", "true", "yes")
        if legacy is not None:
            extra_body = {"reasoning": {"enabled": bool(legacy)}}
    allow_remote = env(
        "ALLOW_REMOTE", "true" if llm_cfg.get("allow_remote", False) else "false"
    ).lower() in ("1", "true", "yes")
    timeout = float(env("TIMEOUT", str(llm_cfg.get("timeout", 120.0))))

    hermes_db = Path(
        env("HERMES_DB", stores_cfg.get("hermes_db", "~/.hermes/state.db"))
    ).expanduser()
    claude_dir = Path(
        env("CLAUDE_PROJECTS", stores_cfg.get("claude_projects_dir", "~/.claude/projects"))
    ).expanduser()
    copilot_raw = env("COPILOT_DIR", stores_cfg.get("copilot_dir", ""))
    copilot_dir = Path(copilot_raw).expanduser() if copilot_raw else None
    codex_raw = env("CODEX_DIR", stores_cfg.get("codex_dir", ""))
    codex_dir = Path(codex_raw).expanduser() if codex_raw else None
    copilot_cli_raw = env("COPILOT_CLI_DIR", stores_cfg.get("copilot_cli_dir", ""))
    copilot_cli_dir = Path(copilot_cli_raw).expanduser() if copilot_cli_raw else None
    cache_dir = Path(env("CACHE_DIR", str(_cache_dir()))).expanduser()

    enabled_raw = env("ENABLED_STORES", "")
    enabled_list = (
        [s.strip() for s in enabled_raw.split(",") if s.strip()]
        if enabled_raw
        else stores_cfg.get("enabled", list(ALL_STORE_KINDS))
    )
    unknown = sorted(set(enabled_list) - set(ALL_STORE_KINDS))
    if unknown:
        raise ValueError(
            f"unknown store(s) in enabled list: {unknown}; must be from {ALL_STORE_KINDS}"
        )
    enabled_stores = frozenset(enabled_list)

    nudge_mode = env("NUDGE_MODE", nudge_cfg.get("mode", DEFAULT_NUDGE_MODE))
    if nudge_mode not in NUDGE_MODES:
        raise ValueError(f"nudge mode {nudge_mode!r} must be one of {NUDGE_MODES}")
    nudge_llm_timeout = float(env("NUDGE_LLM_TIMEOUT", str(nudge_cfg.get("llm_timeout", 20.0))))
    auto_rewrite = env(
        "NUDGE_AUTO_REWRITE", "true" if nudge_cfg.get("auto_rewrite", False) else "false"
    ).lower() in ("1", "true", "yes")
    cost_warning = env(
        "NUDGE_COST_WARNING", "true" if nudge_cfg.get("cost_warning", False) else "false"
    ).lower() in ("1", "true", "yes")
    cost_warning_every = int(
        _non_negative(
            "nudge.cost_warning_every",
            env("NUDGE_COST_WARNING_EVERY", nudge_cfg.get("cost_warning_every", 20)),
        )
    )

    dir_overrides_raw = nudge_cfg.get("dir_overrides", {})
    if not isinstance(dir_overrides_raw, dict):
        raise ValueError("nudge.dir_overrides must be a table of path -> mode")
    for override_path, override_mode in dir_overrides_raw.items():
        if override_mode not in NUDGE_MODES:
            raise ValueError(
                f"nudge.dir_overrides[{override_path!r}] mode {override_mode!r}"
                f" must be one of {NUDGE_MODES}"
            )
    dir_overrides = dict(dir_overrides_raw)

    model_fit_cfg = file_cfg.get("model_fit", {})
    model_fit_mode = env("MODEL_FIT_MODE", model_fit_cfg.get("mode", DEFAULT_MODEL_FIT_MODE))
    if model_fit_mode not in MODEL_FIT_MODES:
        raise ValueError(f"model_fit mode {model_fit_mode!r} must be one of {MODEL_FIT_MODES}")

    return Config(
        llm=LLMConfig(
            base_url=base_url,
            model=model,
            api_key=api_key,
            allow_remote=allow_remote,
            timeout=timeout,
            api_key_cmd=api_key_cmd,
            extra_body=extra_body,
        ),
        stores=StoresConfig(
            hermes_db=hermes_db,
            claude_projects_dir=claude_dir,
            copilot_dir=copilot_dir,
            codex_dir=codex_dir,
            enabled=enabled_stores,
            copilot_cli_dir=copilot_cli_dir,
        ),
        nudge=NudgeConfig(
            mode=nudge_mode,
            llm_timeout=nudge_llm_timeout,
            dir_overrides=dir_overrides,
            auto_rewrite=auto_rewrite,
            cost_warning=cost_warning,
            cost_warning_every=max(cost_warning_every, 1),
        ),
        model_fit=ModelFitConfig(mode=model_fit_mode),
        cache_dir=cache_dir,
        cost=_load_cost(file_cfg.get("cost", {}), env),
    )


def _non_negative(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"{name} must be a number, got {value!r}")
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {value!r}") from None
    if number < 0:
        raise ValueError(f"{name} must not be negative, got {value!r}")
    return number


def _load_cost(cost_cfg: dict, env) -> CostConfig:
    """`[cost]` from config.toml, with PROMPT_COACH_COST_* env overrides for the
    flat keys. Bad numbers fail here with the key named, not mid-report."""
    defaults = CostConfig()

    def num(key: str, default: float) -> float:
        return _non_negative(f"cost.{key}", env(f"COST_{key.upper()}", cost_cfg.get(key, default)))

    # `usd_to_eur` is accepted as a spelling of usd_rate (the original spec's name).
    rate_default = cost_cfg.get("usd_to_eur", defaults.usd_rate)
    by_store_raw = cost_cfg.get("billed_fraction_by_store", {})
    if not isinstance(by_store_raw, dict):
        raise ValueError("cost.billed_fraction_by_store must be a table of store -> fraction")
    prices_raw = cost_cfg.get("prices", {})
    if not isinstance(prices_raw, dict):
        raise ValueError('cost.prices must be tables like [cost.prices."claude-opus*"]')
    overrides = []
    for pattern, fields in prices_raw.items():
        if not isinstance(fields, dict):
            raise ValueError(f"cost.prices.{pattern!r} must be a table")
        missing = {"input", "output", "cache_read", "cache_write"} - set(fields)
        if missing:
            raise ValueError(f"cost.prices.{pattern!r} is missing {sorted(missing)}")
        values = {k: _non_negative(f"cost.prices.{pattern!r}.{k}", v) for k, v in fields.items()}
        overrides.append(
            (
                pattern,
                Price(
                    input=values["input"],
                    output=values["output"],
                    cache_read=values["cache_read"],
                    cache_write=values["cache_write"],
                    cache_write_1h=values.get("cache_write_1h"),
                ),
            )
        )
    return CostConfig(
        enabled=env("COST_ENABLED", str(cost_cfg.get("enabled", True))).lower()
        in ("1", "true", "yes"),
        currency=str(env("COST_CURRENCY", cost_cfg.get("currency", defaults.currency))),
        usd_rate=num("usd_rate", rate_default),
        billed_fraction=num("billed_fraction", defaults.billed_fraction),
        billed_fraction_by_store={
            str(k): _non_negative(f"cost.billed_fraction_by_store.{k}", v)
            for k, v in by_store_raw.items()
        },
        price_overrides=tuple(overrides),
        price_table_date=str(cost_cfg.get("price_table_date", defaults.price_table_date)),
        context_warn_tokens=int(num("context_warn_tokens", defaults.context_warn_tokens)),
        calls_warn=int(num("calls_warn", defaults.calls_warn)),
        idle_minutes=num("idle_minutes", defaults.idle_minutes),
    )


def _load_extra_body(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"llm.extra_body must be a JSON object: {exc}") from None
    if not isinstance(value, dict):
        raise ValueError("llm.extra_body must be a JSON object")
    return value or None


def config_file_path() -> Path:
    """Public accessor for where config.toml lives -- `setup` needs to know
    where to write."""
    return _config_file()


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_config(cfg: Config, path: Path) -> None:
    """Hand-formatted TOML writer, round-trip-safe with load_config() above.
    tomllib (stdlib) is read-only, and this schema is simple enough (flat
    keys, one list, one nested table) not to justify a new TOML-writing
    dependency (DECISIONS.md 2026-07-29)."""
    lines = [
        "[llm]",
        f"api_base = {_toml_str(cfg.llm.base_url)}",
        f"model = {_toml_str(cfg.llm.model)}",
        f"api_key = {_toml_str(cfg.llm.api_key)}",
        f"allow_remote = {str(cfg.llm.allow_remote).lower()}",
        f"timeout = {cfg.llm.timeout}",
    ]
    if cfg.llm.api_key_cmd:
        lines.append(f"api_key_cmd = {_toml_str(cfg.llm.api_key_cmd)}")
    if cfg.llm.extra_body:
        body = json.dumps(cfg.llm.extra_body)
        # A TOML literal string keeps the JSON hand-editable (no escaped
        # quotes) whenever it holds no single quote or newline.
        if "'" in body or "\n" in body:
            lines.append(f"extra_body = {_toml_str(body)}")
        else:
            lines.append(f"extra_body = '{body}'")
    lines += [
        "",
        "[stores]",
        f"hermes_db = {_toml_str(str(cfg.stores.hermes_db))}",
        f"claude_projects_dir = {_toml_str(str(cfg.stores.claude_projects_dir))}",
    ]
    if cfg.stores.copilot_dir is not None:
        lines.append(f"copilot_dir = {_toml_str(str(cfg.stores.copilot_dir))}")
    if cfg.stores.codex_dir is not None:
        lines.append(f"codex_dir = {_toml_str(str(cfg.stores.codex_dir))}")
    if cfg.stores.copilot_cli_dir is not None:
        lines.append(f"copilot_cli_dir = {_toml_str(str(cfg.stores.copilot_cli_dir))}")
    enabled_items = ", ".join(_toml_str(s) for s in sorted(cfg.stores.enabled))
    lines.append(f"enabled = [{enabled_items}]")
    lines += [
        "",
        "[nudge]",
        f"mode = {_toml_str(cfg.nudge.mode)}",
        f"llm_timeout = {cfg.nudge.llm_timeout}",
    ]
    if cfg.nudge.auto_rewrite:
        lines.append("auto_rewrite = true")
    if cfg.nudge.cost_warning:
        lines.append("cost_warning = true")
    if cfg.nudge.cost_warning_every != 20:
        lines.append(f"cost_warning_every = {cfg.nudge.cost_warning_every}")
    if cfg.nudge.dir_overrides:
        lines += ["", "[nudge.dir_overrides]"]
        lines += [
            f"{_toml_str(d)} = {_toml_str(m)}" for d, m in sorted(cfg.nudge.dir_overrides.items())
        ]
    lines += [
        "",
        "[model_fit]",
        f"mode = {_toml_str(cfg.model_fit.mode)}",
    ]
    lines += _cost_lines(cfg.cost)
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text("\n".join(lines))


def _cost_lines(cost: CostConfig) -> list[str]:
    """Only what differs from the defaults, so a config written by `setup`
    keeps whatever [cost] the user set and nothing else."""
    default = CostConfig()
    flat: list[str] = []
    for key in (
        "enabled",
        "currency",
        "usd_rate",
        "billed_fraction",
        "price_table_date",
        "context_warn_tokens",
        "calls_warn",
        "idle_minutes",
    ):
        value = getattr(cost, key)
        if value == getattr(default, key):
            continue
        if isinstance(value, bool):
            flat.append(f"{key} = {str(value).lower()}")
        elif isinstance(value, str):
            flat.append(f"{key} = {_toml_str(value)}")
        else:
            flat.append(f"{key} = {value}")
    tables: list[str] = []
    if cost.billed_fraction_by_store:
        tables += ["", "[cost.billed_fraction_by_store]"]
        tables += [
            f"{_toml_str(k)} = {v}" for k, v in sorted(cost.billed_fraction_by_store.items())
        ]
    for pattern, price in cost.price_overrides:
        tables += [
            "",
            f"[cost.prices.{_toml_str(pattern)}]",
            f"input = {price.input}",
            f"output = {price.output}",
            f"cache_read = {price.cache_read}",
            f"cache_write = {price.cache_write}",
        ]
        if price.cache_write_1h is not None:
            tables.append(f"cache_write_1h = {price.cache_write_1h}")
    return (["", "[cost]", *flat] if flat else []) + tables

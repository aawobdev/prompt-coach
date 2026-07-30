"""Configuration loading: env PROMPT_COACH_* > ~/.config/prompt-coach/config.toml > defaults."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from prompt_coach.models import SourceKind

DEFAULT_API_BASE = "http://192.168.1.123:11434/v1"
# The derived tag has num_ctx 32768 baked in; the base qwen3-coder:30b tag
# runs at the Ollama server default (4096), which truncates pattern payloads.
DEFAULT_MODEL = "qwen3-coder-30b:latest"


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    api_key: str
    allow_remote: bool
    timeout: float


# The four live, auto-discovered stores (ChatGPT export and JSON import are
# manual `import`-command stores, never part of the default sync set, so
# they have no enable/disable concept here).
ALL_STORE_KINDS = (
    SourceKind.HERMES.value,
    SourceKind.CLAUDE_CODE.value,
    SourceKind.COPILOT.value,
    SourceKind.CODEX.value,
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


# "coach": today's default -- only the calibrated weak-prompt triggers fire,
#   at most once per session, and the hook blocks with an LLM rewrite offered
#   (falls back to the old non-blocking tip if the LLM is unreachable).
# "always": every UserPromptSubmit is blocked and rewritten by the LLM,
#   regardless of quality or session history -- an explicit opt-in, since it
#   adds LLM latency to every single prompt. If the LLM is unreachable, the
#   prompt is let through unmodified rather than blocking with no way out.
# "off": nudge never fires (UserPromptSubmit or Stop).
NUDGE_MODES = ("coach", "always", "off")
DEFAULT_NUDGE_MODE = "coach"


@dataclass(frozen=True)
class NudgeConfig:
    mode: str
    llm_timeout: float  # bounded well below the hook's own timeout in settings.json
    # Path -> mode, config.toml-only (DECISIONS.md 2026-07-30): a mapping has
    # no clean flat env-var form. nudge.py resolves the longest matching
    # path prefix from the hook's cwd, falling back to `mode` above.
    dir_overrides: dict[str, str] = field(default_factory=dict)


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
class Config:
    llm: LLMConfig
    stores: StoresConfig
    nudge: NudgeConfig
    model_fit: ModelFitConfig
    cache_dir: Path


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
    api_key = env("API_KEY", llm_cfg.get("api_key", "ollama"))
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
        ),
        stores=StoresConfig(
            hermes_db=hermes_db,
            claude_projects_dir=claude_dir,
            copilot_dir=copilot_dir,
            codex_dir=codex_dir,
            enabled=enabled_stores,
        ),
        nudge=NudgeConfig(
            mode=nudge_mode, llm_timeout=nudge_llm_timeout, dir_overrides=dir_overrides
        ),
        model_fit=ModelFitConfig(mode=model_fit_mode),
        cache_dir=cache_dir,
    )


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
        "",
        "[stores]",
        f"hermes_db = {_toml_str(str(cfg.stores.hermes_db))}",
        f"claude_projects_dir = {_toml_str(str(cfg.stores.claude_projects_dir))}",
    ]
    if cfg.stores.copilot_dir is not None:
        lines.append(f"copilot_dir = {_toml_str(str(cfg.stores.copilot_dir))}")
    if cfg.stores.codex_dir is not None:
        lines.append(f"codex_dir = {_toml_str(str(cfg.stores.codex_dir))}")
    enabled_items = ", ".join(_toml_str(s) for s in sorted(cfg.stores.enabled))
    lines.append(f"enabled = [{enabled_items}]")
    lines += [
        "",
        "[nudge]",
        f"mode = {_toml_str(cfg.nudge.mode)}",
        f"llm_timeout = {cfg.nudge.llm_timeout}",
    ]
    if cfg.nudge.dir_overrides:
        lines += ["", "[nudge.dir_overrides]"]
        lines += [
            f"{_toml_str(d)} = {_toml_str(m)}" for d, m in sorted(cfg.nudge.dir_overrides.items())
        ]
    lines += [
        "",
        "[model_fit]",
        f"mode = {_toml_str(cfg.model_fit.mode)}",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text("\n".join(lines))

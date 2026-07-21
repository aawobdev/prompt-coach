"""Configuration loading: env PROMPT_COACH_* > ~/.config/prompt-coach/config.toml > defaults."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class StoresConfig:
    hermes_db: Path
    claude_projects_dir: Path
    copilot_dir: Path | None  # None = probe default candidates (/mnt/c, ~/.config)
    codex_dir: Path | None  # None = probe default candidates (/mnt/c, ~/.codex)


@dataclass(frozen=True)
class Config:
    llm: LLMConfig
    stores: StoresConfig
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
        ),
        cache_dir=cache_dir,
    )

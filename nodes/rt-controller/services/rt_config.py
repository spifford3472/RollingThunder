"""
RollingThunder configuration helpers for logger foundation.

This module intentionally stays small and focused:
- load app.json
- expose runtime version helpers
- expose logging directory helper

It does not validate the entire application config schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

APP_CONFIG_PATH = Path("/opt/rollingthunder/config/app.json")
DEFAULT_LOG_DIR = "/opt/rollingthunder/data/logs"


def load_app_config(path: str | Path = APP_CONFIG_PATH) -> Dict[str, Any]:
    """
    Load the RollingThunder application config from JSON.

    Raises:
        FileNotFoundError: if the config file does not exist
        ValueError: if the JSON is invalid or does not decode to an object
    """
    config_path = Path(path)

    with config_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"App config must decode to a JSON object: {config_path}")

    return data


def _get_runtime_version_parts(config: Dict[str, Any]) -> tuple[int, int, int]:
    """
    Extract runtimeVersion.major/minor/build as integers.

    Raises:
        ValueError: if runtimeVersion is missing or malformed
    """
    rv = config.get("runtimeVersion")
    if not isinstance(rv, dict):
        raise ValueError("Missing or invalid runtimeVersion object in app.json")

    try:
        major = int(rv["major"])
        minor = int(rv["minor"])
        build = int(rv["build"])
    except KeyError as exc:
        raise ValueError(f"Missing runtimeVersion field: {exc.args[0]}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError("runtimeVersion major/minor/build must be integer-like") from exc

    return major, minor, build


def get_log_dir(config: Dict[str, Any] | None = None) -> str:
    """
    Return configured logging.log_dir or the default path.
    """
    if config is None:
        config = load_app_config()

    logging_cfg = config.get("logging")
    if not isinstance(logging_cfg, dict):
        return DEFAULT_LOG_DIR

    log_dir = logging_cfg.get("log_dir")
    if not isinstance(log_dir, str) or not log_dir.strip():
        return DEFAULT_LOG_DIR

    return log_dir.strip()


def get_runtime_version(config: Dict[str, Any] | None = None) -> str:
    """
    Return full runtime version as '<major>.<minor>.<build>'.
    """
    if config is None:
        config = load_app_config()

    major, minor, build = _get_runtime_version_parts(config)
    return f"{major}.{minor}.{build}"


def get_program_version(config: Dict[str, Any] | None = None) -> str:
    """
    Return program version as '<major>.<minor>'.

    This is intentionally distinct from the full runtime/application version.
    """
    if config is None:
        config = load_app_config()

    major, minor, _build = _get_runtime_version_parts(config)
    return f"{major}.{minor}"


if __name__ == "__main__":
    cfg = load_app_config()
    print("runtime_version =", get_runtime_version(cfg))
    print("program_version =", get_program_version(cfg))
    print("log_dir         =", get_log_dir(cfg))
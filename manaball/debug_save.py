"""Persistence for the single free-match preset."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("manaball.debug_save")
FORMAT_VERSION = 3
SUPPORTED_FORMAT_VERSIONS = {1, 2, 3}
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = PROJECT_ROOT / "data" / "save" / "debug_match_last.json"


def load_debug_match_settings(path: Path | None = None) -> dict[str, Any] | None:
    """Load and minimally validate the last preset; absence is not an error."""

    target = path or DEFAULT_PATH
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"自由試合設定JSONを読み込めません: {error}") from error
    if not isinstance(payload, dict) or payload.get("format_version") not in SUPPORTED_FORMAT_VERSIONS:
        raise ValueError("自由試合設定JSONの形式バージョンが不正です")
    required = ("player_team_size", "enemy_team_size", "player_slots", "enemy_slots", "match_settings")
    if any(key not in payload for key in required):
        raise ValueError("自由試合設定JSONの基本構造が不正です")
    if not isinstance(payload["player_slots"], list) or not isinstance(payload["enemy_slots"], list):
        raise ValueError("自由試合設定JSONの編成枠が不正です")
    if not isinstance(payload["match_settings"], dict):
        raise ValueError("自由試合設定JSONの試合設定が不正です")
    return payload


def save_debug_match_settings(payload: dict[str, Any], path: Path | None = None) -> Path:
    """Atomically save one confirmed preset without damaging the previous file."""

    target = path or DEFAULT_PATH
    document = dict(payload)
    document["format_version"] = FORMAT_VERSION
    document["saved_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    except (OSError, TypeError, ValueError) as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        LOGGER.error("自由試合設定JSONを保存できません: %s", error)
        raise ValueError(f"自由試合設定JSONを保存できません: {error}") from error
    return target

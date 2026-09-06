"""Separated quest, field, layout, and reusable match-setting data."""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import MAX_PARTY_SIZE, MAX_TEAM_SIZE


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = PROJECT_ROOT / "data"
CSV_ROOT = DATA_ROOT / "csv"
LOGGER = logging.getLogger(__name__)

RULE_PURIFICATION = 1
RULE_RECAPTURE = 2
RULE_RITUAL = 3
SUPPORTED_RULE_TYPES = {RULE_PURIFICATION, RULE_RECAPTURE, RULE_RITUAL}


@dataclass(frozen=True)
class FieldSetting:
    field_id: str
    name: str
    layout_id: str
    background_path: str = ""
    bgm_path: str = ""
    description: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class FieldLayout:
    layout_id: str
    width: int
    height: int
    cell_data: tuple[dict[str, Any], ...]
    ally_goal_cells: tuple[tuple[int, int], ...]
    enemy_goal_cells: tuple[tuple[int, int], ...]
    ball_start_cell: tuple[int, int]
    ally_start_cells: tuple[tuple[int, int], ...]
    enemy_start_cells: tuple[tuple[int, int], ...]
    ally_restart_cells: tuple[tuple[int, int], ...]
    enemy_restart_cells: tuple[tuple[int, int], ...]

    @property
    def blocked_cells(self) -> tuple[tuple[int, int], ...]:
        return tuple(
            (int(cell["x"]), int(cell["y"]))
            for cell in self.cell_data
            if not bool(cell.get("walkable", True))
        )


@dataclass(frozen=True)
class MatchSetting:
    match_setting_id: str
    name: str
    ally_field_count: int
    enemy_field_count: int
    ally_party_limit: int
    enemy_party_limit: int
    ally_cost_limit: int
    enemy_cost_limit: int
    score_to_win: int
    max_turns: int
    substitution_enabled: bool
    substitution_count_per_score: int
    injury_enabled: bool
    injury_penalty_per_marker: float
    injury_penalty_floor: float
    bench_recovery_count: int
    restart_rule: str
    draw_rule: str
    enabled: bool = True


@dataclass(frozen=True)
class QuestSetting:
    quest_id: str
    name: str
    field_id: str
    match_setting_id: str
    enemy_group_id: str
    clear_condition_id: str
    failure_condition_id: str
    reward_id: str
    first_clear_reward_id: str
    unlock_condition_id: str
    recommended_level: int
    recommended_cost: int
    bgm_override_path: str
    description: str
    rule_type: int = RULE_PURIFICATION
    required_hold_turns: int = 0
    initial_ball_holder: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class BattleSetup:
    quest: QuestSetting
    field: FieldSetting
    layout: FieldLayout
    match: MatchSetting
    bgm_path: str


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _bool(value: str, default: bool = True) -> bool:
    return default if not str(value).strip() else str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _cells(value: Any) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list):
        return ()
    return tuple((int(item[0]), int(item[1])) for item in value if isinstance(item, list) and len(item) == 2)


def load_field_settings(path: Path | None = None) -> dict[str, FieldSetting]:
    path = path or CSV_ROOT / "field_settings.csv"
    return {
        row["field_id"].strip(): FieldSetting(
            row["field_id"].strip(), row.get("field_name", "").strip(), row.get("layout_id", "").strip(),
            row.get("background_path", "").strip(), row.get("bgm_path", "").strip(),
            row.get("description", "").strip(), _bool(row.get("enabled", "true")),
        )
        for row in _rows(path) if row.get("field_id", "").strip()
    }


def load_field_layouts(path: Path | None = None) -> dict[str, FieldLayout]:
    path = path or DATA_ROOT / "field_layouts.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, FieldLayout] = {}
    for item in raw.get("layouts", []):
        layout_id = str(item.get("layout_id", "")).strip()
        if not layout_id:
            continue
        result[layout_id] = FieldLayout(
            layout_id, int(item["width"]), int(item["height"]), tuple(item.get("cell_data", [])),
            _cells(item.get("ally_goal_cells")), _cells(item.get("enemy_goal_cells")),
            tuple(item.get("ball_start_cell", (0, 0))), _cells(item.get("ally_start_cells")),
            _cells(item.get("enemy_start_cells")), _cells(item.get("ally_restart_cells")),
            _cells(item.get("enemy_restart_cells")),
        )
    return result


def load_match_settings(path: Path | None = None) -> dict[str, MatchSetting]:
    path = path or CSV_ROOT / "match_rules.csv"
    result = {}
    for row in _rows(path):
        setting_id = row.get("match_setting_id", "").strip()
        if not setting_id:
            continue
        result[setting_id] = MatchSetting(
            setting_id, row.get("match_setting_name", "").strip(),
            _int(row.get("ally_field_count", ""), 3), _int(row.get("enemy_field_count", ""), 3),
            _int(row.get("ally_party_limit", "")), _int(row.get("enemy_party_limit", "")),
            _int(row.get("ally_cost_limit", "")), _int(row.get("enemy_cost_limit", "")),
            _int(row.get("score_to_win", ""), 2), _int(row.get("max_turns", "")),
            _bool(row.get("substitution_enabled", "false"), False),
            _int(row.get("substitution_count_per_score", ""), 1),
            _bool(row.get("injury_enabled", "false"), False),
            _float(row.get("injury_penalty_per_marker", ""), 0.2),
            _float(row.get("injury_penalty_floor", ""), 0.4),
            _int(row.get("bench_recovery_count", ""), 1), row.get("restart_rule", "conceding_select").strip(),
            row.get("draw_rule", "draw").strip(), _bool(row.get("enabled", "true")),
        )
    return result


def load_quests(path: Path | None = None) -> dict[str, QuestSetting]:
    path = path or CSV_ROOT / "quests.csv"
    result = {}
    for row in _rows(path):
        quest_id = row.get("quest_id", "").strip()
        if quest_id:
            raw_rule_type = row.get("rule_type", "").strip()
            rule_type = _int(raw_rule_type, RULE_PURIFICATION)
            if rule_type not in SUPPORTED_RULE_TYPES:
                LOGGER.error(
                    "未対応のルール種別です: quest=%s wave=1 rule_type=%s",
                    quest_id, raw_rule_type,
                )
                raise ValueError(f"クエスト {quest_id} のルール種別 {raw_rule_type} は未対応です")
            required_hold_turns = _int(row.get("required_hold_turns", ""), 0)
            if rule_type == RULE_RITUAL and required_hold_turns <= 0:
                LOGGER.warning(
                    "儀式維持ターン数が不正なため3へ補正します: quest=%s wave=1 value=%s",
                    quest_id, row.get("required_hold_turns", ""),
                )
                required_hold_turns = 3
            result[quest_id] = QuestSetting(
                quest_id, row.get("quest_name", "").strip(), row.get("field_id", "").strip(),
                row.get("match_setting_id", "").strip(), row.get("enemy_group_id", "").strip(),
                row.get("clear_condition_id", "win").strip() or "win", row.get("failure_condition_id", "").strip(),
                row.get("reward_id", "").strip(), row.get("first_clear_reward_id", "").strip(),
                row.get("unlock_condition_id", "").strip(), _int(row.get("recommended_level", "")),
                _int(row.get("recommended_cost", "")), row.get("bgm_override_path", "").strip(),
                row.get("description", "").strip(), rule_type, required_hold_turns,
                row.get("initial_ball_holder", "").strip(), _bool(row.get("enabled", "true")),
            )
    return result


def load_battle_setup(quest_id: str) -> BattleSetup:
    quests = load_quests()
    quest = quests.get(quest_id)
    if quest is None or not quest.enabled:
        raise ValueError(f"クエストID {quest_id} が存在しないか無効です")
    field = load_field_settings().get(quest.field_id)
    if field is None or not field.enabled:
        raise ValueError(f"フィールドID {quest.field_id} が存在しないか無効です")
    layout = load_field_layouts().get(field.layout_id)
    if layout is None:
        raise ValueError(f"レイアウトID {field.layout_id} が存在しません")
    match = load_match_settings().get(quest.match_setting_id)
    if match is None or not match.enabled:
        raise ValueError(f"試合設定ID {quest.match_setting_id} が存在しないか無効です")
    validate_setup(layout, match)
    return BattleSetup(quest, field, layout, match, quest.bgm_override_path or field.bgm_path)


def validate_setup(layout: FieldLayout, match: MatchSetting) -> None:
    for label, count in (("味方", match.ally_field_count), ("敵", match.enemy_field_count)):
        if not 1 <= count <= MAX_TEAM_SIZE:
            raise ValueError(f"{label}出場人数は1～{MAX_TEAM_SIZE}人で指定してください: {count}")
    for label, limit in (("味方", match.ally_party_limit), ("敵", match.enemy_party_limit)):
        if not 1 <= limit <= MAX_PARTY_SIZE:
            raise ValueError(f"{label}パーティー人数上限は1～{MAX_PARTY_SIZE}人で指定してください: {limit}")
    if match.ally_field_count > match.ally_party_limit or match.enemy_field_count > match.enemy_party_limit:
        raise ValueError("出場人数がパーティー人数上限を超えています")
    for label, cells, count in (
        ("味方", layout.ally_start_cells, match.ally_field_count),
        ("敵", layout.enemy_start_cells, match.enemy_field_count),
        ("味方リスタート", layout.ally_restart_cells, match.ally_field_count),
        ("敵リスタート", layout.enemy_restart_cells, match.enemy_field_count),
    ):
        if len(cells) < count:
            raise ValueError(f"{label}初期配置が {count - len(cells)} 人分不足しています")
    required = (*layout.ally_goal_cells, *layout.enemy_goal_cells, layout.ball_start_cell,
                *layout.ally_start_cells, *layout.enemy_start_cells)
    if len(set(layout.ally_start_cells + layout.enemy_start_cells)) != len(layout.ally_start_cells + layout.enemy_start_cells):
        raise ValueError("初期配置座標が重複しています")
    for cell in required:
        if not (0 <= cell[0] < layout.width and 0 <= cell[1] < layout.height):
            raise ValueError(f"レイアウト座標 {cell} がフィールド範囲外です")
        if cell in layout.blocked_cells:
            raise ValueError(f"必須座標 {cell} が進入不可マスです")
    if not layout.ally_goal_cells or not layout.enemy_goal_cells:
        raise ValueError("ゴール位置が設定されていません")


def evaluate_clear_condition(quest: QuestSetting, result: dict[str, Any]) -> bool:
    """Evaluate the initial win condition from result data, leaving room for later conditions."""
    if quest.clear_condition_id in {"", "win"}:
        return result.get("winner") == "player"
    return False

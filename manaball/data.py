"""CSV loading and match construction for Mana's Ball."""

from __future__ import annotations

import csv
import logging
import math
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .ai import (
    AI_LEVEL_DEFAULT,
    AI_SETTING_KEYS,
    AI_VALUE_DEFAULT,
    DEFAULT_AI_PROFILE_ID,
    AIProfile,
    clamp_ai_value,
    default_ai_values,
    effective_ai_values,
    safe_ai_profile,
)
from .core import (
    BALL_HOLD_SKILL_BASE_SLOTS,
    COMMAND_GROUPS,
    Character,
    ClassDefinition,
    ElementDefinition,
    MatchConfig,
    MatchGame,
    MAX_PARTY_SIZE,
    MAX_TEAM_SIZE,
    NORMAL_SKILL_BASE_SLOTS,
    Skill,
    SkillEffect,
)


LOGGER = logging.getLogger("manaball.data")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_ROOT = PROJECT_ROOT / "data" / "csv"


@dataclass(frozen=True)
class RosterCharacter:
    """Read-only character master data used by the party setup screen."""

    char_id: str
    name: str
    class_id: str
    class_name: str
    element_id: str
    element_name: str
    max_hp: int
    max_mana: int
    stamina: int
    physical: int
    magic: int
    power: int
    speed: int
    technique: int
    physical_skill_level: int
    magic_skill_level: int
    move_range: int
    cost: int
    skills: tuple[str, ...]
    default_ai_profile_id: str
    default_ai_level: int
    ai_role_id: str
    csv_order: int


@dataclass(frozen=True)
class EnemyMaster:
    """Validated enemy master data before it is converted to match characters."""

    enemy_id: str
    enemy_type_id: str
    name: str
    element_id: str
    class_id: str
    image_id: str
    max_hp: int
    max_mana: int
    physical: int
    magic: int
    power: int
    speed: int
    technique: int
    stamina: int
    physical_skill_level: int
    magic_skill_level: int
    move_range: int
    primary_system: str
    skills: tuple[str, ...]
    default_ai_profile_id: str
    default_ai_level: int
    ai_role_id: str
    enabled: bool
    notes: str
    validation_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class EnemyGroup:
    """One three-member enemy team definition."""

    enemy_group_id: str
    name: str
    enemy_ids: tuple[str, ...]
    enemy_scale: float
    group_ai_profile_id: str
    group_ai_level: int | None
    slot_ai_profile_ids: tuple[str, ...]
    slot_ai_levels: tuple[int | None, ...]
    slot_ai_role_ids: tuple[str, ...]
    display_order: int
    enabled: bool
    ui_selectable: bool
    description: str
    validation_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class EnemyGroupMatchInputs:
    """Adapter output consumed by the existing character/match factory."""

    group: EnemyGroup
    enemy_ids: tuple[str, ...]
    rows: dict[str, dict[str, str]]
    skill_overrides: dict[str, tuple[str, ...]]
    ai_profile_ids: tuple[str, ...]
    ai_levels: tuple[int, ...]
    metadata: dict[str, dict[str, Any]]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _as_bool(value: str, default: bool) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_bool_with_warning(value: str, default: bool, field: str) -> bool:
    if not value:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    LOGGER.warning("%s が不正なため既定値 %s を使用します: %r", field, default, value)
    return default


def _default_command_group(action_type: str, purpose_tag: str) -> str:
    if purpose_tag == "attack" or action_type in {"physical", "magic", "breakthrough"}:
        return "attack"
    if purpose_tag == "movement" or action_type == "movement":
        return "move"
    if purpose_tag in {"pass", "pass_disrupt", "acquisition"} or action_type in {"pass", "steal"}:
        return "ball"
    if action_type == "reaction":
        return "wait"
    return "skill"


def _as_int(value: str, default: int, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        LOGGER.warning("%s が不正なため既定値 %s を使用します: %r", field, default, value)
        return default


def _as_float(value: str, default: float, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        LOGGER.warning("%s が不正なため既定値 %s を使用します: %r", field, default, value)
        return default


def _legacy_int(value: str | None, default: int = 0) -> int:
    """Read an optional removed column without warning when it is absent."""
    try:
        return int(value) if value is not None and str(value).strip() else default
    except (TypeError, ValueError):
        LOGGER.warning("旧互換値が不正なため %s を使用します: %r", default, value)
        return default


def _skill_slots(row: dict[str, str], count: int = 7) -> tuple[str, ...]:
    slots_present = any(f"skill_slot_{index}" in row for index in range(1, count + 1))
    raw = (
        ((row.get(f"skill_slot_{index}") or "").strip() for index in range(1, count + 1))
        if slots_present else ((item or "").strip() for item in (row.get("skills") or "").split("|"))
    )
    standard_actions = {"normal_attack", "normal_physical_attack", "normal_magic_attack", "normal_pass"}
    skills = tuple(item for item in raw if item and item not in standard_actions)
    if len(skills) != len(set(skills)):
        raise ValueError("スキルスロットに同じスキルIDが重複しています")
    return skills


def _primary_system(row: dict[str, str]) -> str:
    configured = (row.get("primary_system") or "").strip()
    if configured in {"physical", "magic"}:
        return configured
    raw_skills = [
        (row.get(f"skill_slot_{index}") or "").strip()
        for index in range(1, 8)
    ]
    if "normal_magic_attack" in raw_skills:
        return "magic"
    if "normal_physical_attack" in raw_skills:
        return "physical"
    magic = _legacy_int(row.get("magic"))
    power = _legacy_int(row.get("power"))
    return "magic" if magic > power else "physical"


def validate_skill_loadout(owner_id: str, skill_ids: tuple[str, ...], skills: dict[str, Skill]) -> None:
    """Validate the shared six normal plus one ball-hold equipment structure."""
    if len(skill_ids) != len(set(skill_ids)):
        raise ValueError(f"キャラクター {owner_id} のスキルが重複しています")
    invalid = [sid for sid in skill_ids if sid not in skills or not skills[sid].enabled or skills[sid].validation_error]
    if invalid:
        raise ValueError(f"キャラクター {owner_id} のスキルが不正です: {', '.join(invalid)}")
    normal_count = sum(skills[sid].equip_slot == "normal" for sid in skill_ids)
    ball_hold_count = sum(skills[sid].equip_slot == "ball_hold" for sid in skill_ids)
    if normal_count > NORMAL_SKILL_BASE_SLOTS:
        raise ValueError(f"キャラクター {owner_id}: 通常スキルは{NORMAL_SKILL_BASE_SLOTS}個まで装備できます。")
    if ball_hold_count > BALL_HOLD_SKILL_BASE_SLOTS:
        raise ValueError(f"キャラクター {owner_id}: ボール保持スキルは{BALL_HOLD_SKILL_BASE_SLOTS}個まで装備できます。")


def _position(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        x_text, y_text = value.split(":", 1)
        return int(x_text), int(y_text)
    except (AttributeError, TypeError, ValueError):
        LOGGER.warning("位置が不正なため既定値 %s を使用します: %r", default, value)
        return default


def load_config(path: Path | None = None) -> MatchConfig:
    path = path or CSV_ROOT / "match_settings.csv"
    values = {row.get("key", "").strip(): row.get("value", "").strip() for row in _read_csv(path)}
    default = MatchConfig()
    positions_player = [
        _position(item, (3, index + 1))
        for index, item in enumerate(values.get("player_positions", "3:1|3:2|3:3").split("|"))
        if item
    ]
    positions_enemy = [
        _position(item, (9, index + 1))
        for index, item in enumerate(values.get("enemy_positions", "9:1|9:2|9:3").split("|"))
        if item
    ]
    config = MatchConfig(
        field_width=_as_int(values.get("field_width", ""), default.field_width, "field_width"),
        field_height=_as_int(values.get("field_height", ""), default.field_height, "field_height"),
        left_goal_x_start=_as_int(values.get("left_goal_x_start", ""), default.left_goal_x_start, "left_goal_x_start"),
        left_goal_x_end=_as_int(values.get("left_goal_x_end", ""), default.left_goal_x_end, "left_goal_x_end"),
        right_goal_x_start=_as_int(values.get("right_goal_x_start", ""), default.right_goal_x_start, "right_goal_x_start"),
        right_goal_x_end=_as_int(values.get("right_goal_x_end", ""), default.right_goal_x_end, "right_goal_x_end"),
        goal_y_start=_as_int(values.get("goal_y_start", ""), default.goal_y_start, "goal_y_start"),
        goal_y_end=_as_int(values.get("goal_y_end", ""), default.goal_y_end, "goal_y_end"),
        team_size=_as_int(values.get("team_size", ""), default.team_size, "team_size"),
        target_score=_as_int(values.get("target_score", ""), default.target_score, "target_score"),
        max_rounds=_as_int(values.get("max_rounds", ""), default.max_rounds, "max_rounds"),
        return_rounds=_as_int(values.get("return_rounds", ""), default.return_rounds, "return_rounds"),
        dice_sides=_as_int(values.get("dice_sides", ""), default.dice_sides, "dice_sides"),
        attribute_dice_sides=_as_int(
            values.get("attribute_dice_sides", ""), default.attribute_dice_sides, "attribute_dice_sides"
        ),
        initial_mana=_as_int(values.get("initial_mana", ""), default.initial_mana, "initial_mana"),
        default_max_mana=_as_int(
            values.get("default_max_mana", ""), default.default_max_mana, "default_max_mana"
        ),
        mana_per_turn=_as_int(values.get("mana_per_turn", ""), default.mana_per_turn, "mana_per_turn"),
        ball_mana_bonus=_as_int(
            values.get("ball_mana_bonus", ""), default.ball_mana_bonus, "ball_mana_bonus"
        ),
        reset_mana_after_score=_as_bool(
            values.get("reset_mana_after_score", ""), default.reset_mana_after_score
        ),
        score_hp_recovery_rate=_as_float(
            values.get("score_hp_recovery_rate", ""),
            default.score_hp_recovery_rate,
            "score_hp_recovery_rate",
        ),
        score_mana_recovery_rate=_as_float(
            values.get("score_mana_recovery_rate", ""),
            default.score_mana_recovery_rate,
            "score_mana_recovery_rate",
        ),
        score_stamina_recovery_rate=_as_float(
            values.get("score_stamina_recovery_rate", ""),
            default.score_stamina_recovery_rate,
            "score_stamina_recovery_rate",
        ),
        stamina_per_turn=_as_int(
            values.get("stamina_per_turn", ""), default.stamina_per_turn, "stamina_per_turn"
        ),
        wait_stamina_recovery_rate=_as_float(
            values.get("wait_stamina_recovery_rate", ""),
            default.wait_stamina_recovery_rate,
            "wait_stamina_recovery_rate",
        ),
        ability_min=_as_int(values.get("ability_min", ""), default.ability_min, "ability_min"),
        ability_max=_as_int(values.get("ability_max", ""), default.ability_max, "ability_max"),
        skill_level_min=_as_int(
            values.get("skill_level_min", ""), default.skill_level_min, "skill_level_min"
        ),
        skill_level_max=_as_int(
            values.get("skill_level_max", ""), default.skill_level_max, "skill_level_max"
        ),
        damage_attack_divisor=_as_int(
            values.get("damage_attack_divisor", ""), default.damage_attack_divisor, "damage_attack_divisor"
        ),
        damage_defense_divisor=_as_int(
            values.get("damage_defense_divisor", ""), default.damage_defense_divisor, "damage_defense_divisor"
        ),
        damage_die_sides=_as_int(
            values.get("damage_die_sides", ""), default.damage_die_sides, "damage_die_sides"
        ),
        minimum_damage=_as_int(
            values.get("minimum_damage", ""), default.minimum_damage, "minimum_damage"
        ),
        damage_difference_multiplier=_as_float(
            values.get("damage_difference_multiplier", ""),
            default.damage_difference_multiplier,
            "damage_difference_multiplier",
        ),
        global_damage_multiplier=_as_float(values.get("global_damage_multiplier", ""), default.global_damage_multiplier, "global_damage_multiplier"),
        damage_difference_rate=_as_float(values.get("damage_difference_rate", ""), default.damage_difference_rate, "damage_difference_rate"),
        damage_difference_min=_as_float(values.get("damage_difference_min", ""), default.damage_difference_min, "damage_difference_min"),
        damage_difference_max=_as_float(values.get("damage_difference_max", ""), default.damage_difference_max, "damage_difference_max"),
        healing_multiplier=_as_float(values.get("healing_multiplier", ""), default.healing_multiplier, "healing_multiplier"),
        physical_attack_coefficient=_as_float(values.get("physical_attack_coefficient", ""), default.physical_attack_coefficient, "physical_attack_coefficient"),
        magic_attack_coefficient=_as_float(values.get("magic_attack_coefficient", ""), default.magic_attack_coefficient, "magic_attack_coefficient"),
        physical_defense_coefficient=_as_float(values.get("physical_defense_coefficient", ""), default.physical_defense_coefficient, "physical_defense_coefficient"),
        magic_defense_coefficient=_as_float(values.get("magic_defense_coefficient", ""), default.magic_defense_coefficient, "magic_defense_coefficient"),
        hp_stat_baseline=_as_int(values.get("hp_stat_baseline", ""), default.hp_stat_baseline, "hp_stat_baseline"),
        hp_per_stamina=_as_int(values.get("hp_per_stamina", ""), default.hp_per_stamina, "hp_per_stamina"),
        mp_stat_baseline=_as_int(values.get("mp_stat_baseline", ""), default.mp_stat_baseline, "mp_stat_baseline"),
        mp_per_magic=_as_int(values.get("mp_per_magic", ""), default.mp_per_magic, "mp_per_magic"),
        healing_magic_coefficient=_as_float(values.get("healing_magic_coefficient", ""), default.healing_magic_coefficient, "healing_magic_coefficient"),
        ball_holder_damage_multiplier=_as_float(
            values.get("ball_holder_damage_multiplier", ""),
            default.ball_holder_damage_multiplier,
            "ball_holder_damage_multiplier",
        ),
        normal_cut_base_rate=_as_int(
            values.get("normal_cut_base_rate", ""), default.normal_cut_base_rate, "normal_cut_base_rate"
        ),
        pass_cut_base_rate=_as_int(
            values.get("pass_cut_base_rate", ""), default.pass_cut_base_rate, "pass_cut_base_rate"
        ),
        normal_attack_drop_base_rate=_as_int(values.get("normal_attack_drop_base_rate", ""), default.normal_attack_drop_base_rate, "normal_attack_drop_base_rate"),
        normal_attack_drop_min_rate=_as_int(values.get("normal_attack_drop_min_rate", ""), default.normal_attack_drop_min_rate, "normal_attack_drop_min_rate"),
        normal_attack_drop_max_rate=_as_int(values.get("normal_attack_drop_max_rate", ""), default.normal_attack_drop_max_rate, "normal_attack_drop_max_rate"),
        ball_cut_base_rate=_as_int(values.get("ball_cut_base_rate", ""), default.ball_cut_base_rate, "ball_cut_base_rate"),
        ball_cut_min_rate=_as_int(values.get("ball_cut_min_rate", ""), default.ball_cut_min_rate, "ball_cut_min_rate"),
        ball_cut_max_rate=_as_int(values.get("ball_cut_max_rate", ""), default.ball_cut_max_rate, "ball_cut_max_rate"),
        steal_base_rate=_as_int(values.get("steal_base_rate", ""), default.steal_base_rate, "steal_base_rate"),
        steal_min_rate=_as_int(values.get("steal_min_rate", ""), default.steal_min_rate, "steal_min_rate"),
        steal_max_rate=_as_int(values.get("steal_max_rate", ""), default.steal_max_rate, "steal_max_rate"),
        pass_cut_min_rate=_as_int(values.get("pass_cut_min_rate", ""), default.pass_cut_min_rate, "pass_cut_min_rate"),
        pass_cut_max_rate=_as_int(values.get("pass_cut_max_rate", ""), default.pass_cut_max_rate, "pass_cut_max_rate"),
        injury_gain_percent=_as_int(values.get("injury_gain_percent", ""), default.injury_gain_percent, "injury_gain_percent"),
        injury_recovery_percent=_as_int(values.get("injury_recovery_percent", ""), default.injury_recovery_percent, "injury_recovery_percent"),
        injury_max_percent=_as_int(values.get("injury_max_percent", ""), default.injury_max_percent, "injury_max_percent"),
        ability_rate_multiplier=_as_int(
            values.get("ability_rate_multiplier", ""),
            default.ability_rate_multiplier,
            "ability_rate_multiplier",
        ),
        success_rate_min=_as_int(
            values.get("success_rate_min", ""), default.success_rate_min, "success_rate_min"
        ),
        success_rate_max=_as_int(
            values.get("success_rate_max", ""), default.success_rate_max, "success_rate_max"
        ),
        base_pass_range=_as_int(
            values.get("base_pass_range", ""), default.base_pass_range, "base_pass_range"
        ),
        pass_distance_grace=_as_int(
            values.get("pass_distance_grace", ""), default.pass_distance_grace, "pass_distance_grace"
        ),
        pass_distance_penalty=_as_int(
            values.get("pass_distance_penalty", ""),
            default.pass_distance_penalty,
            "pass_distance_penalty",
        ),
        prohibit_possession_damage=_as_bool(
            values.get("prohibit_possession_damage", ""), default.prohibit_possession_damage
        ),
        speed_move_fast_threshold=_as_int(
            values.get("speed_move_fast_threshold", ""),
            default.speed_move_fast_threshold,
            "speed_move_fast_threshold",
        ),
        speed_move_slow_threshold=_as_int(
            values.get("speed_move_slow_threshold", ""),
            default.speed_move_slow_threshold,
            "speed_move_slow_threshold",
        ),
        speed_move_step=_as_int(
            values.get("speed_move_step", ""), default.speed_move_step, "speed_move_step"
        ),
        defend_multiplier=_as_float(
            values.get("defend_multiplier", ""), default.defend_multiplier, "defend_multiplier"
        ),
        shield_multiplier=_as_float(
            values.get("shield_multiplier", ""), default.shield_multiplier, "shield_multiplier"
        ),
        keep_bonus=_as_int(values.get("keep_bonus", ""), default.keep_bonus, "keep_bonus"),
        steal_bonus=_as_int(values.get("steal_bonus", ""), default.steal_bonus, "steal_bonus"),
        steal_damage_multiplier=_as_float(
            values.get("steal_damage_multiplier", ""),
            default.steal_damage_multiplier,
            "steal_damage_multiplier",
        ),
        lateral_pass_limit=_as_int(
            values.get("lateral_pass_limit", ""), default.lateral_pass_limit, "lateral_pass_limit"
        ),
        ai_heal_threshold=_as_float(
            values.get("ai_heal_threshold", ""), default.ai_heal_threshold, "ai_heal_threshold"
        ),
        ai_mp_recovery_threshold=_as_float(values.get("ai_mp_recovery_threshold", ""), default.ai_mp_recovery_threshold, "ai_mp_recovery_threshold"),
        ai_min_mp_recovery_rate=_as_float(values.get("ai_min_mp_recovery_rate", ""), default.ai_min_mp_recovery_rate, "ai_min_mp_recovery_rate"),
        ai_pass_history_limit=_as_int(values.get("ai_pass_history_limit", ""), default.ai_pass_history_limit, "ai_pass_history_limit"),
        ai_allow_active_buff_reuse=_as_bool(values.get("ai_allow_active_buff_reuse", ""), default.ai_allow_active_buff_reuse),
        ai_position_repeat_limit=_as_int(values.get("ai_position_repeat_limit", ""), default.ai_position_repeat_limit, "ai_position_repeat_limit"),
        action_intro_ms=_as_int(
            values.get("action_intro_ms", ""), default.action_intro_ms, "action_intro_ms"
        ),
        move_step_ms=_as_int(values.get("move_step_ms", ""), default.move_step_ms, "move_step_ms"),
        judgement_ms=_as_int(values.get("judgement_ms", ""), default.judgement_ms, "judgement_ms"),
        result_ms=_as_int(values.get("result_ms", ""), default.result_ms, "result_ms"),
        skip_speed_multiplier=_as_int(values.get("skip_speed_multiplier", ""), default.skip_speed_multiplier, "skip_speed_multiplier"),
        skip_action_intro_ms=_as_int(values.get("skip_action_intro_ms", ""), default.skip_action_intro_ms, "skip_action_intro_ms"),
        skip_move_step_ms=_as_int(values.get("skip_move_step_ms", ""), default.skip_move_step_ms, "skip_move_step_ms"),
        skip_move_total_max_ms=_as_int(values.get("skip_move_total_max_ms", ""), default.skip_move_total_max_ms, "skip_move_total_max_ms"),
        skip_result_ms=_as_int(values.get("skip_result_ms", ""), default.skip_result_ms, "skip_result_ms"),
        skip_score_hold_ms=_as_int(values.get("skip_score_hold_ms", ""), default.skip_score_hold_ms, "skip_score_hold_ms"),
        player_ids=tuple(item for item in values.get("player_ids", "10|11|12").split("|") if item),
        enemy_ids=tuple(item for item in values.get("enemy_ids", "20|21|22").split("|") if item),
        enemy_ai_profile_ids=tuple(
            item.strip() for item in values.get("enemy_ai_profile_ids", "").split("|")
        ),
        default_enemy_group_id=values.get("default_enemy_group_id", "").strip(),
        player_positions=tuple(positions_player),
        enemy_positions=tuple(positions_enemy),
        ball_position=_position(values.get("ball_position", "6:2"), default.ball_position),
    )
    updates: dict[str, Any] = {}
    for field in (
        "team_size", "target_score", "max_rounds", "dice_sides",
        "attribute_dice_sides",
    ):
        if getattr(config, field) <= 0:
            safe_value = getattr(default, field)
            LOGGER.warning("%s が0以下のため安全な既定値 %s へ補正します", field, safe_value)
            updates[field] = safe_value
    for field in (
        "skip_speed_multiplier", "skip_action_intro_ms", "skip_move_step_ms",
        "skip_move_total_max_ms", "skip_result_ms", "skip_score_hold_ms",
    ):
        if getattr(config, field) <= 0:
            safe_value = getattr(default, field)
            LOGGER.warning("%s が0以下のため安全な既定値 %s へ補正します", field, safe_value)
            updates[field] = safe_value
    if not (0.0 < config.defend_multiplier <= 1.0):
        updates["defend_multiplier"] = default.defend_multiplier
        LOGGER.warning("defend_multiplier を既定値へ補正します")
    if config.ball_holder_damage_multiplier <= 0:
        updates["ball_holder_damage_multiplier"] = default.ball_holder_damage_multiplier
        LOGGER.warning("ball_holder_damage_multiplier が0以下のため既定値1.5へ補正します")
    if not (0.0 < config.shield_multiplier <= 1.0):
        updates["shield_multiplier"] = default.shield_multiplier
        LOGGER.warning("shield_multiplier を既定値へ補正します")
    if config.return_rounds < 0:
        updates["return_rounds"] = default.return_rounds
    for field in ("ai_pass_history_limit", "ai_position_repeat_limit"):
        if getattr(config, field) <= 0:
            updates[field] = getattr(default, field)
            LOGGER.warning("%s が0以下のため既定値へ補正します", field)
    for field in (
        "score_hp_recovery_rate",
        "score_mana_recovery_rate",
        "score_stamina_recovery_rate",
        "wait_stamina_recovery_rate",
        "ai_heal_threshold",
        "ai_mp_recovery_threshold", "ai_min_mp_recovery_rate",
    ):
        value = getattr(config, field)
        if not 0.0 <= value <= 1.0:
            updates[field] = getattr(default, field)
            LOGGER.warning("%s を0～1の既定値へ補正します", field)
    for field in (
        "ability_min", "ability_max", "damage_attack_divisor", "damage_defense_divisor",
        "damage_die_sides", "minimum_damage", "damage_difference_multiplier",
        "ability_rate_multiplier", "base_pass_range", "pass_distance_penalty",
    ):
        if getattr(config, field) <= 0:
            updates[field] = getattr(default, field)
            LOGGER.warning("%s が0以下のため既定値へ補正します", field)
    if config.ability_max < config.ability_min:
        updates["ability_min"] = default.ability_min
        updates["ability_max"] = default.ability_max
        LOGGER.warning("能力値範囲が不正なため既定値へ補正します")
    if not (0 <= config.success_rate_min <= config.success_rate_max <= 100):
        updates["success_rate_min"] = default.success_rate_min
        updates["success_rate_max"] = default.success_rate_max
        LOGGER.warning("成功率の上下限が不正なため既定値へ補正します")
    for field in ("normal_cut_base_rate", "pass_cut_base_rate"):
        if not 0 <= getattr(config, field) <= 100:
            updates[field] = getattr(default, field)
            LOGGER.warning("%s が不正なため既定値へ補正します", field)
    if config.pass_distance_grace < 0:
        updates["pass_distance_grace"] = default.pass_distance_grace
    if config.skill_level_min < 0 or config.skill_level_max < config.skill_level_min:
        updates["skill_level_min"] = default.skill_level_min
        updates["skill_level_max"] = default.skill_level_max
        LOGGER.warning("スキルレベル範囲が不正なため既定値へ補正します")
    if config.stamina_per_turn < 0:
        updates["stamina_per_turn"] = default.stamina_per_turn
    if config.speed_move_step < 0 or config.speed_move_fast_threshold <= config.speed_move_slow_threshold:
        updates["speed_move_fast_threshold"] = default.speed_move_fast_threshold
        updates["speed_move_slow_threshold"] = default.speed_move_slow_threshold
        updates["speed_move_step"] = default.speed_move_step
        LOGGER.warning("スピードによる移動力段階設定を既定値へ補正します")
    if (
        config.field_width > 0
        and config.field_height > 0
        and not (
            0 <= config.ball_position[0] < config.field_width
            and 0 <= config.ball_position[1] < config.field_height
        )
    ):
        updates["ball_position"] = (config.field_width // 2, config.field_height // 2)
        LOGGER.warning("ボール初期位置をフィールド中央へ補正します")
    return replace(config, **updates) if updates else config


def load_classes(path: Path | None = None) -> dict[str, ClassDefinition]:
    """Deprecated compatibility hook; class masters are no longer loaded."""
    return {}


def load_elements(path: Path | None = None) -> dict[str, ElementDefinition]:
    """Deprecated compatibility hook; element masters are no longer loaded."""
    return {}


def _as_ai_value(value: str, field: str) -> int:
    raw = (value or "").strip()
    if not raw:
        return AI_VALUE_DEFAULT
    parsed = _as_int(raw, AI_VALUE_DEFAULT, field)
    clamped = clamp_ai_value(parsed)
    if clamped != parsed:
        LOGGER.warning("%s=%s をAI設定範囲0～10へ補正します", field, parsed)
    return clamped


def _as_ai_level(value: str, field: str, *, blank: int | None = AI_LEVEL_DEFAULT) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return blank
    parsed = _as_int(raw, AI_LEVEL_DEFAULT, field)
    clamped = min(10, max(1, parsed))
    if clamped != parsed:
        LOGGER.warning("%s=%s をAIレベル範囲1～10へ補正します", field, parsed)
    return clamped


def load_ai_profiles(path: Path | None = None) -> dict[str, AIProfile]:
    path = path or CSV_ROOT / "ai_profiles.csv"
    fallback = AIProfile(
        DEFAULT_AI_PROFILE_ID,
        "標準型",
        "すべてのAI設定値が標準の共通プロフィール",
        default_ai_values(),
    )
    if not path.exists():
        LOGGER.warning("AIプロフィールCSVがないため標準AIのみ使用します: %s", path)
        return {fallback.ai_profile_id: fallback}
    rows = _read_csv(path)
    if rows and not set(AI_SETTING_KEYS).issubset(rows[0]):
        LOGGER.warning("旧形式のAIプロフィールCSVを検出したため組み込み標準AIを使用します: %s", path)
        return {fallback.ai_profile_id: fallback}
    profiles: dict[str, AIProfile] = {}
    for row_number, row in enumerate(rows, 2):
        profile_id = row.get("ai_profile_id", "").strip()
        if not profile_id:
            LOGGER.warning("ai_profiles.csv %s 行目はID不足のため無効です", row_number)
            continue
        if profile_id in profiles:
            LOGGER.warning("AIプロフィールIDが重複したため後続行を無視します: %s", profile_id)
            continue
        values = {
            key: _as_ai_value(row.get(key, ""), f"ai_profiles.{profile_id}.{key}")
            for key in AI_SETTING_KEYS
        }
        if not _as_bool(row.get("enabled", "true"), True):
            continue
        profiles[profile_id] = AIProfile(
            ai_profile_id=profile_id,
            name=row.get("ai_profile_name", "").strip() or profile_id,
            description=row.get("description", "").strip(),
            values=values,
        )
    if DEFAULT_AI_PROFILE_ID not in profiles:
        LOGGER.warning("標準AIプロフィールがないため組み込み標準AIを追加します")
        profiles[DEFAULT_AI_PROFILE_ID] = fallback
    return profiles


def _enemy_integer(row: dict[str, str], field: str, context: str, errors: list[str]) -> int:
    raw = row.get(field, "").strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        errors.append(f"{field} は整数で指定してください")
        LOGGER.error("%s の %s が不正です: %r", context, field, raw)
        return 0


def load_enemy_masters(
    path: Path | None = None,
    *,
    config: MatchConfig | None = None,
    classes: dict[str, ClassDefinition] | None = None,
    elements: dict[str, ElementDefinition] | None = None,
    skills: dict[str, Skill] | None = None,
    ai_profiles: dict[str, AIProfile] | None = None,
) -> dict[str, EnemyMaster]:
    """Load enemy masters while retaining invalid rows for group-level rejection."""

    path = path or CSV_ROOT / "enemies.csv"
    if not path.exists():
        LOGGER.warning("敵マスターCSVがありません: %s", path)
        return {}
    config = config or load_config()
    classes = classes or load_classes()
    elements = elements or load_elements()
    skills = skills or load_skills(config=config)
    ai_profiles = ai_profiles or load_ai_profiles()
    result: dict[str, EnemyMaster] = {}
    numeric_fields = ("max_hp", "max_mana", "magic", "power", "speed", "technique", "stamina")
    for row_number, row in enumerate(_read_csv(path), 2):
        enemy_id = row.get("enemy_id", "").strip()
        context = f"enemies.csv {row_number} 行目" if not enemy_id else f"敵 {enemy_id}"
        errors: list[str] = []
        if not enemy_id:
            LOGGER.error("%s は enemy_id がないため無効です", context)
            continue
        if enemy_id in result:
            LOGGER.error("敵マスターIDが重複したため後続行を無視します: %s", enemy_id)
            continue
        name = row.get("name", "").strip()
        if not name:
            errors.append("name が必要です")
        values = {field: _enemy_integer(row, field, context, errors) for field in numeric_fields}
        values["move_range"] = _legacy_int(row.get("move_range"), 3)
        for field in ("max_hp", "max_mana"):
            if values[field] < 1:
                errors.append(f"{field} は1以上で指定してください")
        values["physical"] = _legacy_int(row.get("physical"), 0)
        values["physical_skill_level"] = _legacy_int(row.get("physical_skill_level"), 0)
        values["magic_skill_level"] = _legacy_int(row.get("magic_skill_level"), 0)
        for field in ("magic", "power", "speed", "technique", "stamina"):
            if not config.ability_min <= values[field] <= config.ability_max:
                errors.append(f"{field} は{config.ability_min}～{config.ability_max}で指定してください")
        if values["move_range"] < 0:
            values["move_range"] = 3
        primary_system = row.get("primary_system", "").strip()
        class_id = row.get("class_id", "").strip()
        element_id = row.get("element_id", "").strip()
        skill_ids = tuple(
            row.get(f"skill_slot_{slot}", "").strip()
            for slot in range(1, 4)
            if row.get(f"skill_slot_{slot}", "").strip()
        )
        if len(set(skill_ids)) != len(skill_ids):
            errors.append("スキルスロットに同じスキルIDが重複しています")
        for skill_id in skill_ids:
            skill = skills.get(skill_id)
            if skill is None or not skill.enabled or skill.validation_error:
                errors.append(f"スキル {skill_id} が存在しないか無効です")
                continue
            if skill.category == "normal":
                errors.append(f"標準行動 {skill_id} はスキルスロットへ設定できません")
        default_ai_profile_id = row.get("default_ai_profile_id", "").strip()
        if default_ai_profile_id and default_ai_profile_id not in ai_profiles:
            LOGGER.warning("%s のAIプロフィール %s は未登録のため標準AIへフォールバックします", context, default_ai_profile_id)
        result[enemy_id] = EnemyMaster(
            enemy_id=enemy_id,
            enemy_type_id=row.get("enemy_type_id", "").strip(),
            name=name,
            element_id=element_id,
            class_id=class_id,
            image_id=row.get("image_id", "").strip(),
            max_hp=values["max_hp"],
            max_mana=values["max_mana"],
            physical=values["physical"],
            magic=values["magic"],
            power=values["power"],
            speed=values["speed"],
            technique=values["technique"],
            stamina=values["stamina"],
            physical_skill_level=values["physical_skill_level"],
            magic_skill_level=values["magic_skill_level"],
            move_range=values["move_range"],
            primary_system=primary_system,
            skills=skill_ids,
            default_ai_profile_id=default_ai_profile_id or DEFAULT_AI_PROFILE_ID,
            default_ai_level=_as_ai_level(row.get("default_ai_level", ""), f"{context}.default_ai_level"),
            ai_role_id=(row.get("ai_role_id") or "").strip(),
            enabled=_as_bool(row.get("enabled", "true"), True),
            notes=row.get("notes", "").strip(),
            validation_errors=tuple(errors),
        )
    return result


def load_enemy_groups(
    path: Path | None = None,
    *,
    ai_profiles: dict[str, AIProfile] | None = None,
) -> dict[str, EnemyGroup]:
    """Load enemy groups up to the shared party-size limit and validate group-local values."""

    path = path or CSV_ROOT / "enemy_groups.csv"
    if not path.exists():
        LOGGER.warning("敵グループCSVがありません: %s", path)
        return {}
    ai_profiles = ai_profiles or load_ai_profiles()
    result: dict[str, EnemyGroup] = {}
    for row_number, row in enumerate(_read_csv(path), 2):
        group_id = row.get("enemy_group_id", "").strip()
        context = f"enemy_groups.csv {row_number} 行目" if not group_id else f"敵グループ {group_id}"
        errors: list[str] = []
        if not group_id:
            LOGGER.error("%s は enemy_group_id がないため無効です", context)
            continue
        if group_id in result:
            LOGGER.error("敵グループIDが重複したため後続行を無視します: %s", group_id)
            continue
        name = row.get("enemy_group_name", "").strip()
        if not name:
            errors.append("enemy_group_name が必要です")
        available_slots = [slot for slot in range(1, MAX_PARTY_SIZE + 1) if f"enemy_{slot}_id" in row]
        enemy_ids = tuple((row.get(f"enemy_{slot}_id") or "").strip() for slot in available_slots)
        enemy_ids = tuple(enemy_id for enemy_id in enemy_ids if enemy_id)
        if not enemy_ids:
            errors.append("enemy_1_id 以降に1人以上の敵IDが必要です")
        scale_raw = row.get("enemy_scale", "").strip()
        if not scale_raw:
            enemy_scale = 1.0
        else:
            try:
                enemy_scale = float(scale_raw)
            except ValueError:
                enemy_scale = 0.0
                errors.append("enemy_scale は数値で指定してください")
        if not math.isfinite(enemy_scale) or enemy_scale <= 0:
            errors.append("enemy_scale は0より大きい値が必要です")
        display_order_raw = row.get("display_order", "").strip()
        try:
            display_order = int(display_order_raw) if display_order_raw else row_number
        except ValueError:
            display_order = row_number
            errors.append("display_order は整数で指定してください")
        group_ai = row.get("group_ai_profile_id", "").strip()
        group_level = _as_ai_level(row.get("group_ai_level", ""), f"{context}.group_ai_level", blank=None)
        slot_ai = tuple((row.get(f"enemy_{slot}_ai_profile_id") or "").strip() for slot in available_slots[:len(enemy_ids)])
        slot_levels = tuple(_as_ai_level(row.get(f"enemy_{slot}_ai_level", ""), f"{context}.enemy_{slot}_ai_level", blank=None) for slot in available_slots[:len(enemy_ids)])
        slot_roles = tuple((row.get(f"enemy_{slot}_ai_role_id") or "").strip() for slot in available_slots[:len(enemy_ids)])
        for profile_id in (group_ai, *slot_ai):
            if profile_id and profile_id not in ai_profiles:
                LOGGER.warning("%s のAIプロフィール %s は未登録のため直前の設定へフォールバックします", context, profile_id)
        result[group_id] = EnemyGroup(
            enemy_group_id=group_id,
            name=name,
            enemy_ids=enemy_ids,
            enemy_scale=enemy_scale,
            group_ai_profile_id=group_ai,
            group_ai_level=group_level,
            slot_ai_profile_ids=slot_ai,
            slot_ai_levels=slot_levels,
            slot_ai_role_ids=slot_roles,
            display_order=display_order,
            enabled=_as_bool(row.get("enabled", "true"), True),
            ui_selectable=_as_bool(row.get("ui_selectable", "true"), True),
            description=row.get("description", "").strip(),
            validation_errors=tuple(errors),
        )
    return result


def resolve_enemy_ai_profile_id(
    master_profile_id: str,
    group_profile_id: str,
    slot_profile_id: str,
    ai_profiles: dict[str, AIProfile],
) -> str:
    """Resolve standard < master < group < slot AI precedence in one place."""

    selected = DEFAULT_AI_PROFILE_ID
    for profile_id in (master_profile_id, group_profile_id, slot_profile_id):
        if not profile_id:
            continue
        if profile_id in ai_profiles:
            selected = profile_id
        else:
            LOGGER.warning("未登録の敵AIプロフィール %s を無視し、%s を使用します", profile_id, selected)
    return selected


def build_enemy_group_match_inputs(
    enemy_group_id: str,
    *,
    config: MatchConfig,
    classes: dict[str, ClassDefinition],
    elements: dict[str, ElementDefinition],
    skills: dict[str, Skill],
    ai_profiles: dict[str, AIProfile],
    enemy_path: Path | None = None,
    group_path: Path | None = None,
    enemy_masters: dict[str, EnemyMaster] | None = None,
    enemy_groups: dict[str, EnemyGroup] | None = None,
) -> EnemyGroupMatchInputs:
    """Validate one group and adapt it to the existing character CSV shape."""

    requested_id = (enemy_group_id or "").strip()
    if not requested_id:
        raise ValueError("敵グループIDが空欄のため試合を開始できません")
    masters = enemy_masters if enemy_masters is not None else load_enemy_masters(
        enemy_path,
        config=config,
        classes=classes,
        elements=elements,
        skills=skills,
        ai_profiles=ai_profiles,
    )
    groups = enemy_groups if enemy_groups is not None else load_enemy_groups(
        group_path,
        ai_profiles=ai_profiles,
    )
    group = groups.get(requested_id)
    if group is None:
        raise ValueError(f"敵グループID {requested_id} が存在しません")
    if not group.enabled:
        raise ValueError(f"敵グループ {requested_id} は無効です")
    if group.validation_errors:
        raise ValueError(f"敵グループ {requested_id} のデータが不正です: " + "; ".join(group.validation_errors))

    instance_ids: list[str] = []
    rows: dict[str, dict[str, str]] = {}
    skill_overrides: dict[str, tuple[str, ...]] = {}
    profile_ids: list[str] = []
    ai_levels: list[int] = []
    metadata: dict[str, dict[str, Any]] = {}
    scalable_fields = ("max_hp", "max_mana", "magic", "power", "speed", "technique", "stamina")
    for slot, master_id in enumerate(group.enemy_ids, 1):
        master = masters.get(master_id)
        if master is None:
            raise ValueError(f"敵グループ {requested_id} の敵{slot} ID {master_id} が存在しません")
        if not master.enabled:
            raise ValueError(f"敵グループ {requested_id} の敵 {master_id} は無効です")
        if master.validation_errors:
            raise ValueError(f"敵 {master_id} のデータが不正です: " + "; ".join(master.validation_errors))
        instance_id = f"enemy:{requested_id}:{slot}"
        instance_ids.append(instance_id)
        scaled = {
            field: max(1, int(getattr(master, field) * group.enemy_scale + 0.5))
            for field in scalable_fields
        }
        scaled["physical"] = config.ability_min
        for field in ("magic", "power", "speed", "technique", "stamina"):
            scaled[field] = min(config.ability_max, max(config.ability_min, scaled[field]))
        profile_id = resolve_enemy_ai_profile_id(
            master.default_ai_profile_id,
            group.group_ai_profile_id,
            group.slot_ai_profile_ids[slot - 1],
            ai_profiles,
        )
        profile_ids.append(profile_id)
        ai_levels.append(group.slot_ai_levels[slot - 1] or group.group_ai_level or master.default_ai_level or AI_LEVEL_DEFAULT)
        rows[instance_id] = {
            "char_id": instance_id,
            "name": master.name,
            "element_id": master.element_id,
            "class_id": master.class_id,
            **{field: str(value) for field, value in scaled.items()},
            "physical_skill_level": str(master.physical_skill_level),
            "magic_skill_level": str(master.magic_skill_level),
            "move_range": str(master.move_range),
            "pass_range": str(config.base_pass_range),
            "primary_system": master.primary_system,
            "default_ai_profile_id": master.default_ai_profile_id,
            "default_ai_level": str(master.default_ai_level),
            "ai_role_id": group.slot_ai_role_ids[slot - 1] or master.ai_role_id,
            "skills": "|".join(master.skills),
            # Legacy fields remain required by Character but are derived, not enemy master data.
            "attack": str(scaled["power"]),
            "defense": str(scaled["stamina"]),
            "accuracy": str(scaled["technique"]),
            "evasion": str(scaled["speed"]),
            "magic_defense": str(scaled["stamina"]),
            "pass_power": "0",
            "pass_cut": "0",
            "ball_keep": "0",
            "ball_cut": "0",
            "breakthrough": str(scaled["power"]),
            "breakthrough_defense": str(scaled["stamina"]),
        }
        skill_overrides[instance_id] = master.skills
        metadata[instance_id] = {
            "enemy_master_id": master.enemy_id,
            "enemy_type_id": master.enemy_type_id,
            "enemy_group_id": group.enemy_group_id,
            "enemy_group_slot": slot,
            "enemy_scale": group.enemy_scale,
            "image_id": master.image_id,
        }
    return EnemyGroupMatchInputs(
        group=group,
        enemy_ids=tuple(instance_ids),
        rows=rows,
        skill_overrides=skill_overrides,
        ai_profile_ids=tuple(profile_ids),
        ai_levels=tuple(ai_levels),
        metadata=metadata,
    )


def load_valid_enemy_group_match_inputs(
    *,
    config: MatchConfig | None = None,
    classes: dict[str, ClassDefinition] | None = None,
    elements: dict[str, ElementDefinition] | None = None,
    skills: dict[str, Skill] | None = None,
    ai_profiles: dict[str, AIProfile] | None = None,
    enemy_path: Path | None = None,
    group_path: Path | None = None,
) -> list[EnemyGroupMatchInputs]:
    """Return enabled, fully validated groups in stable display order for UI use."""

    config = config or load_config()
    classes = classes or load_classes()
    elements = elements or load_elements()
    skills = skills or load_skills(config=config)
    ai_profiles = ai_profiles or load_ai_profiles()
    masters = load_enemy_masters(
        enemy_path,
        config=config,
        classes=classes,
        elements=elements,
        skills=skills,
        ai_profiles=ai_profiles,
    )
    groups = load_enemy_groups(group_path, ai_profiles=ai_profiles)
    valid: list[EnemyGroupMatchInputs] = []
    for group in sorted(groups.values(), key=lambda item: item.display_order):
        if not group.enabled or not group.ui_selectable:
            continue
        try:
            valid.append(build_enemy_group_match_inputs(
                group.enemy_group_id,
                config=config,
                classes=classes,
                elements=elements,
                skills=skills,
                ai_profiles=ai_profiles,
                enemy_masters=masters,
                enemy_groups=groups,
            ))
        except ValueError as error:
            LOGGER.warning("敵グループ %s を選択候補から除外します: %s", group.enemy_group_id, error)
    return valid


def load_character_roster(
    config: MatchConfig | None = None,
    classes: dict[str, ClassDefinition] | None = None,
    elements: dict[str, ElementDefinition] | None = None,
    path: Path | None = None,
) -> list[RosterCharacter]:
    """Load all valid character masters without creating match state."""

    config = config or load_config()
    classes = classes or load_classes()
    elements = elements or load_elements()
    path = path or CSV_ROOT / "characters.csv"
    roster: list[RosterCharacter] = []
    defaults = {
        "max_hp": 25,
        "max_mana": config.default_max_mana,
        "stamina": 6,
        "magic": 5,
        "power": 6,
        "speed": 5,
        "technique": 6,
        "move_range": 3,
    }
    for csv_order, row in enumerate(_read_csv(path)):
        char_id = row.get("char_id", "").strip()
        name = row.get("name", "").strip()
        if not char_id or not name:
            LOGGER.warning("characters.csv %s 行目はIDまたは名前不足のため編成対象外です", csv_order + 2)
            continue
        class_id = row.get("class_id", "").strip()
        element_id = row.get("element_id", "0").strip() or "0"
        class_definition = classes.get(class_id, ClassDefinition(class_id or "0", "不明"))
        element_definition = elements.get(element_id, elements.get("0", ElementDefinition("0", "不明")))

        def value(field: str, *, minimum: int = 0, maximum: int | None = None) -> int:
            raw = row.get(field, "")
            if field == "stamina" and not str(raw).strip():
                raw = row.get("max_stamina", "")
            parsed = _as_int(raw, defaults[field], f"roster.{char_id}.{field}") if str(raw).strip() else defaults[field]
            bounded = max(minimum, parsed)
            return min(maximum, bounded) if maximum is not None else bounded

        roster.append(
            RosterCharacter(
                char_id=char_id,
                name=name,
                class_id=class_definition.class_id,
                class_name=class_definition.name,
                element_id=element_definition.element_id,
                element_name=element_definition.name,
                max_hp=value("max_hp", minimum=1),
                max_mana=value("max_mana", minimum=1),
                stamina=value("stamina", minimum=config.ability_min, maximum=config.ability_max),
                physical=_legacy_int(row.get("physical")),
                magic=value("magic", minimum=config.ability_min, maximum=config.ability_max),
                power=value("power", minimum=config.ability_min, maximum=config.ability_max),
                speed=value("speed", minimum=config.ability_min, maximum=config.ability_max),
                technique=value("technique", minimum=config.ability_min, maximum=config.ability_max),
                physical_skill_level=_legacy_int(row.get("physical_skill_level")),
                magic_skill_level=_legacy_int(row.get("magic_skill_level")),
                move_range=value("move_range"),
                cost=max(0, _as_int(row.get("cost", "1") or "1", 1, f"roster.{char_id}.cost")),
                skills=_skill_slots(row),
                default_ai_profile_id=row.get("default_ai_profile_id", DEFAULT_AI_PROFILE_ID).strip()
                or DEFAULT_AI_PROFILE_ID,
                default_ai_level=_as_ai_level(row.get("default_ai_level", ""), f"roster.{char_id}.default_ai_level"),
                ai_role_id=(row.get("ai_role_id") or "").strip(),
                csv_order=csv_order,
            )
        )
    return roster


SUPPORTED_SKILL_EFFECTS = {
    "damage", "modify_stat", "heal_hp", "recover_mp",
    "prepare_reaction", "drop_ball", "steal_ball",
    "force_move", "pass", "guard", "modify_move_range",
}
MAX_SKILL_EFFECTS = 5


def load_skill_effects(path: Path | None = None) -> dict[str, tuple[SkillEffect, ...]]:
    path = path or CSV_ROOT / "skill_effects.csv"
    if not path.exists():
        LOGGER.warning("skill_effects.csv がないため既存スキル互換モードで起動します")
        return {}
    grouped: dict[str, list[SkillEffect]] = {}
    invalid_ids: set[str] = set()
    for row_number, row in enumerate(_read_csv(path), 2):
        skill_id = row.get("skill_id", "").strip()
        effect_type = row.get("effect_type", "").strip()
        timing = row.get("timing", "success").strip() or "success"
        effect_target = row.get("target", "selected").strip() or "selected"
        order = _as_int(row.get("effect_order", ""), 0, f"skill_effects.{row_number}.effect_order")
        if (
            not skill_id
            or effect_type not in SUPPORTED_SKILL_EFFECTS
            or timing not in {"use", "success", "failure", "end", "reaction_success", "reaction_failure"}
            or effect_target not in {"self", "selected"}
            or not 1 <= order <= MAX_SKILL_EFFECTS
        ):
            LOGGER.error("skill_effects.csv %s 行目が不正なためスキルを無効化します", row_number)
            if skill_id:
                invalid_ids.add(skill_id)
            continue
        numeric_values = {
            "base_value": _as_int(row.get("base_value", ""), 0, f"{skill_id}.base_value"),
            "duration": _as_int(row.get("duration", ""), 0, f"{skill_id}.duration"),
            "distance": _as_int(row.get("distance", ""), 0, f"{skill_id}.distance"),
        }
        rate1 = _as_float(row.get("rate1", ""), 0.0, f"{skill_id}.rate1")
        rate2 = _as_float(row.get("rate2", ""), 0.0, f"{skill_id}.rate2")
        stat1 = row.get("stat1", "").strip()
        stat2 = row.get("stat2", "").strip()
        aux1 = row.get("aux1", "").strip()
        aux2 = row.get("aux2", "").strip()
        valid_stats = {"", "physical", "magic", "power", "speed", "technique", "stamina"}
        effect_configuration_valid = True
        if effect_type == "modify_stat":
            effect_configuration_valid = aux1 in valid_stats - {""} and aux2 in {"add", "subtract", "percent"}
        elif effect_type == "modify_move_range":
            effect_configuration_valid = aux2 in {"add", "subtract"} and numeric_values["base_value"] > 0
        elif effect_type == "force_move":
            effect_configuration_valid = numeric_values["distance"] > 0 and aux1 in {"", "away"}
        elif effect_type == "pass":
            effect_configuration_valid = numeric_values["distance"] > 0
        if (
            any(value < 0 for value in numeric_values.values())
            or rate1 < 0
            or rate2 < 0
            or stat1 not in valid_stats
            or stat2 not in valid_stats
            or not effect_configuration_valid
        ):
            LOGGER.error("スキル %s の効果設定が不正なため無効化します", skill_id)
            invalid_ids.add(skill_id)
            continue
        grouped.setdefault(skill_id, []).append(SkillEffect(
            skill_id=skill_id,
            effect_order=order,
            timing=timing,
            effect_type=effect_type,
            target=effect_target,
            condition=row.get("condition", "always").strip() or "always",
            base_value=numeric_values["base_value"],
            stat1=stat1,
            rate1=rate1,
            stat2=stat2,
            rate2=rate2,
            duration=numeric_values["duration"],
            distance=numeric_values["distance"],
            aux1=aux1,
            aux2=aux2,
        ))
    result: dict[str, tuple[SkillEffect, ...]] = {}
    for skill_id, effects in grouped.items():
        orders = [effect.effect_order for effect in effects]
        if skill_id in invalid_ids or len(effects) > MAX_SKILL_EFFECTS or len(set(orders)) != len(orders):
            LOGGER.error("スキル %s の効果数または効果順が不正です", skill_id)
            result[skill_id] = ()
            continue
        result[skill_id] = tuple(sorted(effects, key=lambda effect: effect.effect_order))
    for skill_id in invalid_ids:
        result.setdefault(skill_id, ())
    return result


def load_skills(
    path: Path | None = None,
    config: MatchConfig | None = None,
    effects_path: Path | None = None,
) -> dict[str, Skill]:
    path = path or CSV_ROOT / "skills.csv"
    config = config or MatchConfig()
    skills: dict[str, Skill] = {}
    effects_by_skill = load_skill_effects(effects_path)
    for row_number, row in enumerate(_read_csv(path), 2):
        skill_id = row.get("skill_id", "").strip()
        name = row.get("name", "").strip()
        if not skill_id or not name:
            LOGGER.warning("skills.csv %s 行目は必須項目不足のため無効です", row_number)
            continue
        action_type = row.get("action_type", "").strip()
        activation = row.get("activation", "active").strip() or "active"
        category = row.get("category", "basic").strip() or "basic"
        tie_rule = row.get("tie_rule", "defender").strip() or "defender"
        purpose_tag = row.get("purpose_tag", "").strip()
        equip_slot_raw = (row.get("equip_slot") or "").strip().lower()
        if not equip_slot_raw:
            equip_slot = "ball_hold" if purpose_tag == "ball_hold" else "normal"
            LOGGER.warning("スキル %s の equip_slot が空欄のため %s へ互換分類します", skill_id, equip_slot)
        else:
            equip_slot = equip_slot_raw
        command_group_raw = row.get("command_group", "").strip()
        if not command_group_raw:
            command_group = _default_command_group(action_type, purpose_tag)
            if "command_group" not in row:
                LOGGER.warning(
                    "スキル %s の command_group がないため %s へ補完します",
                    skill_id,
                    command_group,
                )
        elif command_group_raw == "pass":
            command_group = "ball"
            LOGGER.warning(
                "スキル %s の旧 command_group=pass を ball へ補正します",
                skill_id,
            )
        else:
            command_group = command_group_raw
        usable_after_move = _as_bool_with_warning(
            row.get("usable_after_move", ""),
            True,
            f"{skill_id}.usable_after_move",
        )
        display_order_raw = row.get("display_order", "").strip()
        display_order = (
            _as_int(display_order_raw, row_number, f"{skill_id}.display_order")
            if display_order_raw
            else row_number
        )
        skill_system = row.get("skill_system", "").strip() or (
            "magic" if action_type in {"magic", "heal", "pass", "reaction"} else "physical"
        )
        skill_type = row.get("skill_type", "").strip()
        legacy_mana_cost = _as_int(row.get("mana_cost", ""), 0, f"{skill_id}.mana_cost")
        resource_cost = _as_int(
            row.get("resource_cost", ""), legacy_mana_cost, f"{skill_id}.resource_cost"
        )
        resource_type = row.get("resource_type", "").strip() or ("mana" if legacy_mana_cost > 0 else "none")
        if resource_type == "stamina":
            LOGGER.warning("スキル %s の旧スタミナ消費を消費なしへ移行します", skill_id)
            resource_type = "none"
            resource_cost = 0
        range_value = _as_int(row.get("range", ""), 0, f"{skill_id}.range")
        min_range = _as_int(row.get("min_range", ""), 0, f"{skill_id}.min_range")
        cooldown = _as_int(row.get("cooldown", ""), 0, f"{skill_id}.cooldown")
        max_uses = _as_int(row.get("max_uses", ""), 0, f"{skill_id}.max_uses")
        allowed_states = tuple(
            item.strip() for item in row.get("allowed_state", "always").split("|") if item.strip()
        ) or ("always",)
        actor_primary = row.get("actor_primary_stat", "").strip()
        actor_secondary = row.get("actor_secondary_stat", "").strip()
        defender_primary = row.get("defender_primary_stat", "").strip() or actor_primary
        defender_secondary = row.get("defender_secondary_stat", "").strip() or actor_secondary
        ball_effect_type = (row.get("ball_effect_type") or "").strip().lower() or "none"
        ball_effect_base_rate = _as_int(row["ball_effect_base_rate"], 0, f"{skill_id}.ball_effect_base_rate") if row.get("ball_effect_base_rate") else 0
        ball_effect_rate_per_diff = _as_int(row["ball_effect_rate_per_diff"], 5, f"{skill_id}.ball_effect_rate_per_diff") if row.get("ball_effect_rate_per_diff") else 5
        ball_effect_min_rate = _as_int(row["ball_effect_min_rate"], 0, f"{skill_id}.ball_effect_min_rate") if row.get("ball_effect_min_rate") else 0
        default_ball_max = 70 if ball_effect_type == "cut" else 90 if ball_effect_type == "drop" else 0
        ball_effect_max_rate = _as_int(row["ball_effect_max_rate"], default_ball_max, f"{skill_id}.ball_effect_max_rate") if row.get("ball_effect_max_rate") else default_ball_max
        ball_actor_stat = (row.get("ball_effect_actor_stat") or "").strip()
        ball_defender_stat = (row.get("ball_effect_defender_stat") or "").strip()
        ball_hold_scope = (row.get("ball_hold_scope") or "").strip()
        ball_hold_range_raw = row.get("ball_hold_range") or ""
        ball_hold_range = _as_int(ball_hold_range_raw, -1, f"{skill_id}.ball_hold_range") if ball_hold_range_raw else -1
        ball_hold_include_self = _as_bool_with_warning(
            row.get("ball_hold_include_self") or "", True, f"{skill_id}.ball_hold_include_self"
        )
        errors: list[str] = []
        if equip_slot not in {"normal", "ball_hold"}:
            errors.append(f"equip_slot={equip_slot}")
        effect_mode = row.get("effect_mode", "legacy").strip() or "legacy"
        effects = effects_by_skill.get(skill_id, ())
        if effect_mode not in {"legacy", "common"}:
            errors.append(f"effect_mode={effect_mode}")
        if command_group not in COMMAND_GROUPS:
            errors.append(f"command_group={command_group}")
        if effect_mode == "common" and not 1 <= len(effects) <= MAX_SKILL_EFFECTS:
            errors.append("共通効果数は1～5件必要")
        if resource_cost < 0 or range_value < 0 or min_range < 0 or cooldown < 0 or max_uses < 0:
            errors.append("消費MP・射程・クールタイム・使用回数に負数")
        if min_range > range_value:
            errors.append("最小射程が最大射程を超過")
        if activation not in {"active", "reaction", "reaction_wait", "automatic"}:
            errors.append(f"activation={activation}")
        if category not in {"normal", "single", "basic", "advanced"}:
            errors.append(f"category={category}")
        valid_target_types = {
            "self", "self_or_ally", "ally", "enemy", "adjacent_enemy", "adjacent_enemy_holder",
            "self_or_adjacent_ally",
        }
        if row.get("target_type", "").strip() not in valid_target_types:
            errors.append(f"target_type={row.get('target_type', '').strip()}")
        if tie_rule not in {"actor", "defender"}:
            errors.append(f"tie_rule={tie_rule}")
        if skill_system not in {"physical", "magic", "common"}:
            errors.append(f"skill_system={skill_system}")
        if skill_type not in {"power", "magic", "speed", "technique", "stamina", ""}:
            errors.append(f"skill_type={skill_type}")
        if resource_type not in {"mana", "none"}:
            errors.append(f"resource_type={resource_type}")
        invalid_states = set(allowed_states) - {"possession", "acquisition", "contest", "always"}
        if invalid_states:
            errors.append("allowed_state=" + "|".join(sorted(invalid_states)))
        if activation == "automatic" and purpose_tag == "ball_hold":
            if ball_hold_scope not in {"self", "aura"}:
                errors.append(f"ball_hold_scope={ball_hold_scope or '未設定'}")
            if ball_hold_range < 0 or (ball_hold_scope == "self" and ball_hold_range != 0):
                errors.append(f"ball_hold_range={ball_hold_range}")
            if not effects or any(effect.effect_type not in {"modify_stat", "modify_move_range"} for effect in effects):
                errors.append("ボール保持スキルの効果種別不正")
        elif ball_hold_scope:
            errors.append("保持スキル以外にball_hold_scopeが設定済み")
        if ball_effect_type not in {"none", "drop", "cut"}:
            LOGGER.warning("スキル %s のボール効果 %r が不正なため無効化します", skill_id, ball_effect_type)
            ball_effect_type = "none"
        if ball_effect_type != "none":
            if ball_actor_stat not in {"power", "technique"} or ball_defender_stat not in {"power", "technique"}:
                LOGGER.warning("スキル %s のボール効果能力が不正なため無効化します", skill_id)
                ball_effect_type = "none"
            elif ball_effect_min_rate > ball_effect_max_rate:
                LOGGER.warning("スキル %s のボール効果成功率下限が上限を超えるため無効化します", skill_id)
                ball_effect_type = "none"
            elif ball_effect_type == "cut" and range_value >= 2:
                LOGGER.warning("スキル %s の射程2以上のカット効果を無効化します", skill_id)
                ball_effect_type = "none"
        valid_primary = {"power", "magic", "speed", "technique", "stamina"}
        valid_secondary = valid_primary | {""}
        if effect_mode == "legacy" and skill_system != "common" and (actor_primary not in valid_primary or actor_secondary not in valid_secondary):
            errors.append("行動側能力不足")
        if row.get("use_dice", "").strip() and _as_bool(row.get("use_dice", ""), False):
            if defender_primary not in valid_primary or defender_secondary not in valid_secondary:
                errors.append("防御側能力不足")
        validation_error = "; ".join(errors)
        if validation_error:
            LOGGER.error("スキル %s を使用不可にします: %s", skill_id, validation_error)
        required_level_raw = _as_int(row.get("required_level", ""), 0, f"{skill_id}.required_level")
        required_level = min(config.skill_level_max, max(config.skill_level_min, required_level_raw))
        if required_level != required_level_raw:
            LOGGER.warning(
                "スキル %s の required_level=%s を%s～%sへ補正します",
                skill_id,
                required_level_raw,
                config.skill_level_min,
                config.skill_level_max,
            )
        skills[skill_id] = Skill(
            skill_id=skill_id,
            name=name,
            description=row.get("description", "").strip(),
            activation=activation,
            mana_cost=legacy_mana_cost,
            target_type=row.get("target_type", "").strip(),
            range=max(0, range_value),
            reference_stat=row.get("reference_stat", "").strip(),
            use_dice=_as_bool(row.get("use_dice", ""), False),
            base_effect=_as_int(row.get("base_effect", ""), 0, f"{skill_id}.base_effect"),
            success_effect=row.get("success_effect", "").strip(),
            failure_effect=row.get("failure_effect", "").strip(),
            additional_effect=row.get("additional_effect", "").strip(),
            cooldown=max(0, cooldown),
            usable_with_ball=_as_bool(row.get("usable_with_ball", ""), False),
            usable_without_ball=_as_bool(row.get("usable_without_ball", ""), True),
            steal_bonus=_as_int(row.get("steal_bonus", ""), 0, f"{skill_id}.steal_bonus"),
            pass_bonus=_as_int(row.get("pass_bonus", ""), 0, f"{skill_id}.pass_bonus"),
            defense_multiplier=_as_float(
                row.get("defense_multiplier", ""), 1.0, f"{skill_id}.defense_multiplier"
            ),
            round_limit=_as_int(row.get("round_limit", ""), 0, f"{skill_id}.round_limit"),
            defense_stat=row.get("defense_stat", "").strip(),
            action_type=action_type,
            element_id=row.get("element_id", "").strip(),
            uses_attribute=_as_bool(row.get("uses_attribute", ""), False),
            ends_action=_as_bool(row.get("ends_action", ""), True),
            skill_system=skill_system,
            skill_type=skill_type,
            required_level=required_level,
            allowed_states=allowed_states,
            resource_type=resource_type,
            resource_cost=max(0, resource_cost),
            actor_primary_stat=actor_primary,
            actor_secondary_stat=actor_secondary,
            defender_primary_stat=defender_primary,
            defender_secondary_stat=defender_secondary,
            validation_error=validation_error,
            category=category,
            min_range=max(0, min_range),
            max_uses=max(0, max_uses),
            purpose_tag=purpose_tag,
            enabled=_as_bool(row.get("enabled", "true"), True),
            tie_rule=tie_rule,
            effect_mode=effect_mode,
            effects=effects,
            command_group=command_group if command_group in COMMAND_GROUPS else "skill",
            usable_after_move=usable_after_move,
            display_order=display_order,
            ball_effect_type=ball_effect_type,
            ball_effect_base_rate=min(100, max(0, ball_effect_base_rate)),
            ball_effect_actor_stat=ball_actor_stat,
            ball_effect_defender_stat=ball_defender_stat,
            ball_effect_rate_per_diff=ball_effect_rate_per_diff,
            ball_effect_min_rate=min(100, max(0, ball_effect_min_rate)),
            ball_effect_max_rate=min(100, max(0, ball_effect_max_rate)),
            ball_hold_scope=ball_hold_scope,
            ball_hold_range=max(0, ball_hold_range),
            ball_hold_include_self=ball_hold_include_self,
            equip_slot=equip_slot if equip_slot in {"normal", "ball_hold"} else equip_slot,
        )
    return skills


def load_characters(
    config: MatchConfig,
    classes: dict[str, ClassDefinition] | None = None,
    elements: dict[str, ElementDefinition] | None = None,
    path: Path | None = None,
    player_ids: tuple[str, ...] | None = None,
    enemy_ids: tuple[str, ...] | None = None,
    skill_overrides: dict[str, tuple[str, ...]] | None = None,
    ai_profiles: dict[str, AIProfile] | None = None,
    enemy_ai_profile_ids: tuple[str, ...] | None = None,
    enemy_ai_levels: tuple[int, ...] | None = None,
    character_overrides: dict[str, dict[str, int]] | None = None,
    additional_rows: dict[str, dict[str, str]] | None = None,
    character_metadata: dict[str, dict[str, Any]] | None = None,
) -> list[Character]:
    path = path or CSV_ROOT / "characters.csv"
    rows = {row.get("char_id", "").strip(): row for row in _read_csv(path)}
    rows.update(additional_rows or {})
    characters: list[Character] = []
    classes = classes or {}
    elements = elements or {"0": ElementDefinition("0", "無属性")}
    selected_player_ids = player_ids if player_ids is not None else config.player_ids
    selected_enemy_ids = enemy_ids if enemy_ids is not None else config.enemy_ids
    selected_enemy_ai_profile_ids = (
        enemy_ai_profile_ids if enemy_ai_profile_ids is not None else config.enemy_ai_profile_ids
    )
    player_count = config.player_team_size or config.team_size
    enemy_count = config.enemy_team_size or config.team_size
    if len(selected_player_ids) < player_count or len(selected_enemy_ids) < enemy_count:
        if player_count == enemy_count:
            raise ValueError(f"味方・敵はそれぞれ {player_count} 人以上選択してください")
        raise ValueError(f"味方{player_count}人・敵{enemy_count}人以上を選択してください")
    player_party_limit = config.player_party_limit or MAX_PARTY_SIZE
    enemy_party_limit = config.enemy_party_limit or MAX_PARTY_SIZE
    if len(selected_player_ids) > player_party_limit or len(selected_enemy_ids) > enemy_party_limit:
        raise ValueError(
            f"パーティー人数上限を超えています（味方{player_party_limit}人・敵{enemy_party_limit}人）"
        )
    if len(set(selected_player_ids)) != len(selected_player_ids):
        raise ValueError("同じ味方キャラクターは複数編成できません")
    if len(set(selected_enemy_ids)) != len(selected_enemy_ids):
        raise ValueError("同じ敵キャラクターは複数編成できません")
    skill_overrides = skill_overrides or {}
    ai_profiles = ai_profiles or load_ai_profiles()
    character_overrides = character_overrides or {}
    character_metadata = character_metadata or {}
    team_specs = (
        ("player", selected_player_ids, config.player_positions, player_count),
        ("enemy", selected_enemy_ids, config.enemy_positions, enemy_count),
    )
    defaults: dict[str, int] = {
        "max_hp": 25,
        "max_mana": config.default_max_mana,
        "stamina": 6,
        "power": 6,
        "attack": 6,
        "defense": 5,
        "technique": 6,
        "speed": 5,
        "magic": 5,
        "move_range": 3,
        "pass_range": 3,
    }
    override_fields = {"max_hp", "max_mana", "physical", "magic", "power", "speed", "technique", "stamina"}
    for team, ids, positions, team_count in team_specs:
        if len(ids) < team_count or len(positions) < team_count:
            raise ValueError(f"{team} の参加者IDまたは初期位置が参加人数未満です")
        for index, char_id in enumerate(ids):
            row = rows.get(char_id)
            if row is None or not row.get("name", "").strip():
                LOGGER.error("参加キャラクター %s の必須データがないため試合を開始できません", char_id)
                raise ValueError(f"キャラクター {char_id} のデータが不足しています")
            values: dict[str, Any] = {}
            metadata = character_metadata.get(char_id, {})
            for field, fallback in defaults.items():
                raw = row.get(field, "")
                if field == "stamina" and not str(raw).strip():
                    raw = row.get("max_stamina", "")
                if not str(raw).strip() and field in {"max_hp", "max_mana", "power", "magic", "speed", "technique", "stamina"}:
                    LOGGER.warning("キャラクター %s の %s がないため既定値 %s を使用します", char_id, field, fallback)
                values[field] = _as_int(raw, fallback, f"character.{char_id}.{field}") if str(raw).strip() else fallback
            runtime_id = (
                f"enemy:{char_id}:{index + 1}"
                if team == "enemy" and char_id in selected_player_ids
                else char_id
            )
            override = character_overrides.get(
                f"{team}:{index + 1}",
                character_overrides.get(runtime_id, character_overrides.get(char_id, {})),
            )
            invalid_override_fields = set(override) - override_fields
            if invalid_override_fields:
                raise ValueError(f"キャラクター {char_id} の一時能力項目が不正です")
            for field, override_value in override.items():
                values[field] = int(override_value)
            def positive_resource(field: str) -> int:
                value = values[field]
                if value <= 0:
                    LOGGER.warning("キャラクター %s の %s=%s を最低値1へ補正します", char_id, field, value)
                    return 1
                return value

            max_hp = positive_resource("max_hp")
            max_mana = positive_resource("max_mana")
            on_bench = index >= team_count
            position = positions[index] if not on_bench else None
            initial_position = positions[index] if index < len(positions) else positions[index % team_count]
            class_id = row.get("class_id", "").strip()
            element_id = row.get("element_id", "0").strip() or "0"
            class_definition = classes.get(class_id, ClassDefinition(class_id or "0", "職種なし"))
            element_definition = elements.get(element_id, elements.get("0", ElementDefinition("0", "無属性")))
            default_ai_profile_id = row.get("default_ai_profile_id", DEFAULT_AI_PROFILE_ID).strip() or DEFAULT_AI_PROFILE_ID
            profile_id = ""
            if team == "enemy" and index < len(selected_enemy_ai_profile_ids):
                profile_id = selected_enemy_ai_profile_ids[index].strip() or default_ai_profile_id
            elif team == "enemy":
                profile_id = default_ai_profile_id
            profile = safe_ai_profile(ai_profiles, profile_id, f"キャラクター {char_id}") if team == "enemy" else None
            ai_values = effective_ai_values(profile) if profile else default_ai_values()
            ai_level = (_as_ai_level(str(enemy_ai_levels[index]), f"character.{char_id}.ai_level")
                        if team == "enemy" and enemy_ai_levels is not None and index < len(enemy_ai_levels)
                        else _as_ai_level(row.get("default_ai_level", ""), f"character.{char_id}.default_ai_level"))

            def stat(field: str, fallback_field: str) -> int:
                raw = row.get(field, "").strip()
                fallback = values[fallback_field]
                if not raw and field not in {"accuracy", "evasion", "magic_defense", "breakthrough", "breakthrough_defense"}:
                    LOGGER.warning(
                        "キャラクター %s の %s がないため %s=%s を使用します",
                        char_id,
                        field,
                        fallback_field,
                        fallback,
                    )
                return max(1, _as_int(raw, fallback, f"character.{char_id}.{field}")) if raw else max(1, fallback)

            def modifier(field: str) -> int:
                raw = row.get(field, "").strip()
                return _as_int(raw, 0, f"character.{char_id}.{field}") if raw else 0

            def base_stat(field: str) -> int:
                value = values[field]
                clamped = min(config.ability_max, max(config.ability_min, value))
                if clamped != value:
                    LOGGER.warning(
                        "キャラクター %s の %s=%s を能力範囲 %s～%s へ補正します",
                        char_id,
                        field,
                        value,
                        config.ability_min,
                        config.ability_max,
                    )
                return clamped

            def skill_level(field: str) -> int:
                value = values[field]
                clamped = min(config.skill_level_max, max(config.skill_level_min, value))
                if clamped != value:
                    LOGGER.warning(
                        "キャラクター %s の %s=%s をスキルレベル範囲へ補正します",
                        char_id,
                        field,
                        value,
                    )
                return clamped

            stamina_value = base_stat("stamina")
            magic_value = base_stat("magic")
            max_hp = max(1, max_hp + (stamina_value - config.hp_stat_baseline) * config.hp_per_stamina)
            max_mana = max(1, max_mana + (magic_value - config.mp_stat_baseline) * config.mp_per_magic)

            characters.append(
                Character(
                    char_id=runtime_id,
                    name=row["name"].strip(),
                    team=team,
                    class_id=class_definition.class_id,
                    class_name=class_definition.name,
                    element_id=element_definition.element_id,
                    element_name=element_definition.name,
                    max_hp=max_hp,
                    hp=max_hp,
                    max_mana=max_mana,
                    mana=min(config.initial_mana, max_mana),
                    stamina=stamina_value,
                    physical=_legacy_int(row.get("physical")),
                    power=base_stat("power"),
                    physical_skill_level=_legacy_int(row.get("physical_skill_level")),
                    magic_skill_level=_legacy_int(row.get("magic_skill_level")),
                    attack=max(0, values["attack"]),
                    defense=max(0, values["defense"]),
                    accuracy=stat("accuracy", "attack"),
                    evasion=stat("evasion", "defense"),
                    technique=base_stat("technique"),
                    speed=base_stat("speed"),
                    magic=magic_value,
                    magic_defense=stat("magic_defense", "defense"),
                    pass_power=modifier("pass_power"),
                    pass_cut=modifier("pass_cut"),
                    pass_cut_range=max(0, min(max(config.field_width, config.field_height), _as_int(
                        row.get("pass_cut_range", "1") or "1", 1, f"character.{char_id}.pass_cut_range"
                    ))),
                    ball_keep=modifier("ball_keep"),
                    ball_cut=modifier("ball_cut"),
                    breakthrough=stat("breakthrough", "attack"),
                    breakthrough_defense=stat("breakthrough_defense", "defense"),
                    move_range=max(0, values["move_range"]),
                    pass_range=max(0, values["pass_range"]),
                    primary_system=_primary_system(row),
                    initial_position=initial_position,
                    position=position,
                    default_ai_profile_id=default_ai_profile_id if team == "enemy" else "",
                    ai_profile_id=profile.ai_profile_id if profile else "",
                    ai_profile_name=profile.name if profile else "",
                    ai_profile_description=profile.description if profile else "",
                    ai_level=ai_level if team == "enemy" else AI_LEVEL_DEFAULT,
                    ai_role_id=(row.get("ai_role_id") or "").strip(),
                    ai_settings=ai_values,
                    ai_profile_source=("enemy_slot" if team == "enemy" and metadata.get("enemy_group_slot") else "enemy_master" if team == "enemy" else "role_mapping"),
                    skills=skill_overrides.get(
                        f"{team}:{index + 1}",
                        skill_overrides.get(
                            runtime_id,
                            skill_overrides.get(
                                char_id,
                                _skill_slots(row),
                            ),
                        ),
                    ),
                    base_character_id=char_id,
                    enemy_master_id=str(metadata.get("enemy_master_id", "")),
                    enemy_type_id=str(metadata.get("enemy_type_id", "")),
                    enemy_group_id=str(metadata.get("enemy_group_id", "")),
                    enemy_group_slot=int(metadata.get("enemy_group_slot", 0)),
                    enemy_scale=float(metadata.get("enemy_scale", 1.0)),
                    image_id=str(metadata.get("image_id", "")),
                    cost=max(0, _as_int(row.get("cost", "1") or "1", 1, f"character.{char_id}.cost")),
                    on_bench=on_bench,
                )
            )
    return characters


def create_match(
    seed: int | None = None,
    player_ids: tuple[str, ...] | None = None,
    enemy_ids: tuple[str, ...] | None = None,
    skill_overrides: dict[str, tuple[str, ...]] | None = None,
    enemy_ai_profile_ids: tuple[str, ...] | None = None,
    enemy_ai_levels: tuple[int, ...] | None = None,
    config_overrides: dict[str, Any] | None = None,
    character_overrides: dict[str, dict[str, int]] | None = None,
    enemy_group_id: str | None = None,
    enemy_master_path: Path | None = None,
    enemy_group_path: Path | None = None,
    quest_id: str | None = None,
    report_policy: str = "test",
    report_root: Path | None = None,
) -> MatchGame:
    """Load project data and create a fresh match."""

    config = load_config()
    setup = None
    if quest_id is not None:
        from .battle_setup import load_battle_setup

        setup = load_battle_setup(quest_id)
        layout = setup.layout
        rule = setup.match
        ally_goal_x = [cell[0] for cell in layout.ally_goal_cells]
        enemy_goal_x = [cell[0] for cell in layout.enemy_goal_cells]
        goal_y = [cell[1] for cell in (*layout.ally_goal_cells, *layout.enemy_goal_cells)]
        config = replace(
            config,
            field_width=layout.width,
            field_height=layout.height,
            left_goal_x_start=min(ally_goal_x),
            left_goal_x_end=max(ally_goal_x),
            right_goal_x_start=min(enemy_goal_x),
            right_goal_x_end=max(enemy_goal_x),
            goal_y_start=min(goal_y),
            goal_y_end=max(goal_y),
            player_team_size=rule.ally_field_count,
            enemy_team_size=rule.enemy_field_count,
            player_party_limit=rule.ally_party_limit,
            enemy_party_limit=rule.enemy_party_limit,
            player_cost_limit=rule.ally_cost_limit,
            enemy_cost_limit=rule.enemy_cost_limit,
            target_score=rule.score_to_win,
            turn_limit=rule.max_turns,
            substitution_enabled=rule.substitution_enabled,
            substitution_count_per_score=rule.substitution_count_per_score,
            injury_enabled=rule.injury_enabled,
            injury_penalty_per_marker=rule.injury_penalty_per_marker,
            injury_penalty_floor=rule.injury_penalty_floor,
            bench_recovery_count=rule.bench_recovery_count,
            restart_rule=rule.restart_rule,
            player_positions=layout.ally_start_cells,
            enemy_positions=layout.enemy_start_cells,
            ball_position=layout.ball_start_cell,
            blocked_cells=layout.blocked_cells,
            field_id=setup.field.field_id,
            layout_id=layout.layout_id,
            match_setting_id=rule.match_setting_id,
            quest_id=setup.quest.quest_id,
            rule_type=setup.quest.rule_type,
            required_hold_turns=setup.quest.required_hold_turns,
            initial_ball_holder=setup.quest.initial_ball_holder,
            background_path=setup.field.background_path,
            bgm_path=setup.bgm_path,
        )
        if enemy_group_id is None:
            enemy_group_id = setup.quest.enemy_group_id
    if config_overrides:
        unknown = set(config_overrides) - set(MatchConfig.__dataclass_fields__)
        if unknown:
            raise ValueError("試合設定項目が不正です: " + ", ".join(sorted(unknown)))
        config = replace(config, **config_overrides)
    classes = load_classes()
    elements = load_elements()
    ai_profiles = load_ai_profiles()
    skills = load_skills(config=config)
    enemy_inputs: EnemyGroupMatchInputs | None = None
    if enemy_group_id is not None:
        if enemy_ids is not None:
            raise ValueError("敵グループIDと敵キャラクターIDは同時に指定できません")
        if enemy_ai_profile_ids is not None:
            raise ValueError("敵グループ使用時は個別の敵AIプロフィールを別指定できません")
        if enemy_ai_levels is not None:
            raise ValueError("敵グループ使用時は個別の敵AIレベルを別指定できません")
        enemy_inputs = build_enemy_group_match_inputs(
            enemy_group_id,
            config=config,
            classes=classes,
            elements=elements,
            skills=skills,
            ai_profiles=ai_profiles,
            enemy_path=enemy_master_path,
            group_path=enemy_group_path,
        )
        enemy_ids = enemy_inputs.enemy_ids
        enemy_ai_profile_ids = enemy_inputs.ai_profile_ids
        enemy_ai_levels = enemy_inputs.ai_levels
        required_enemy_count = config.enemy_team_size or config.team_size
        if len(enemy_ids) < required_enemy_count:
            raise ValueError(
                f"敵グループ {enemy_group_id} の人数 {len(enemy_ids)} が必要人数 {required_enemy_count} 人に不足しています"
            )
        desired_enemy_count = required_enemy_count
        if config.debug_mode or quest_id is not None:
            enemy_party_limit = config.enemy_party_limit or MAX_PARTY_SIZE
            if len(enemy_ids) > enemy_party_limit:
                raise ValueError(
                    f"敵グループ {enemy_group_id} の人数 {len(enemy_ids)} がパーティー上限 {enemy_party_limit} 人を超えています"
                )
            desired_enemy_count = len(enemy_ids)
        enemy_ids = enemy_ids[:desired_enemy_count]
        enemy_ai_profile_ids = enemy_ai_profile_ids[:desired_enemy_count]
        enemy_ai_levels = enemy_ai_levels[:desired_enemy_count]
    required_player_count = config.player_team_size or config.team_size
    if player_ids is None and len(config.player_ids) < required_player_count:
        roster_ids = tuple(member.char_id for member in load_character_roster(config, classes, elements))
        player_ids = roster_ids[:required_player_count]
    selected_ids = set((player_ids or config.player_ids) + (enemy_ids or config.enemy_ids))
    selected_skill_overrides = {
        char_id: tuple(skill_ids)
        for char_id, skill_ids in (skill_overrides or {}).items()
        if char_id in selected_ids or char_id.startswith("player:") or char_id.startswith("enemy:")
    }
    if enemy_inputs is not None:
        selected_skill_overrides.update(enemy_inputs.skill_overrides)
    for char_id, skill_ids in selected_skill_overrides.items():
        validate_skill_loadout(char_id, skill_ids, skills)
    characters = load_characters(
        config,
        classes,
        elements,
        player_ids=player_ids,
        enemy_ids=enemy_ids,
        skill_overrides=selected_skill_overrides,
        ai_profiles=ai_profiles,
        enemy_ai_profile_ids=enemy_ai_profile_ids,
        enemy_ai_levels=enemy_ai_levels,
        character_overrides=character_overrides,
        additional_rows=enemy_inputs.rows if enemy_inputs else None,
        character_metadata=enemy_inputs.metadata if enemy_inputs else None,
    )
    for character in characters:
        validate_skill_loadout(character.char_id, character.skills, skills)
    return MatchGame(
        config,
        characters,
        skills,
        classes,
        elements,
        ai_profiles=ai_profiles,
        rng=random.Random(seed),
        seed=seed,
        report_policy=report_policy,
        report_root=report_root,
    )


def create_enemy_team(enemy_group_id: str, seed: int | None = None) -> list[Character]:
    """Create independent existing Character instances for one enemy group."""

    game = create_match(seed=seed, enemy_group_id=enemy_group_id)
    return sorted(
        (character for character in game.characters.values() if character.team == "enemy"),
        key=lambda character: character.enemy_group_slot,
    )

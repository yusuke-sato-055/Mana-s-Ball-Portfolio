"""Rules engine for the turn-based 3-on-3 Mana's Ball match."""

from __future__ import annotations

import logging
import math
import random
import copy
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from .ai import AIProfile, AI_LEVEL_DEFAULT, AI_SETTINGS_BY_KEY, AI_VALUE_DEFAULT, DEFAULT_AI_PROFILE_ID, clamp_ai_level, effective_ai_values
from .reporting import MatchReportCollector


PLAYER = "player"
ENEMY = "enemy"
TEAMS = (PLAYER, ENEMY)
MAX_TEAM_SIZE = 6
MAX_PARTY_SIZE = 7
Position = tuple[int, int]
LOGGER = logging.getLogger("manaball.match")
MATCH_STATE_POSSESSION = "possession"
MATCH_STATE_ACQUISITION = "acquisition"
MATCH_STATE_CONTEST = "contest"
MATCH_STATE_LABELS = {
    MATCH_STATE_POSSESSION: "保持状態",
    MATCH_STATE_ACQUISITION: "奪取状態",
    MATCH_STATE_CONTEST: "争奪状態",
    "always": "常時",
}
AI_ROLE_NAMES = {"power": "パワー型", "technique": "テクニック型", "support": "支援型"}
BALL_STATE_NAMES = {
    "self_possession": "自分保持", "ally_possession": "味方保持",
    "enemy_possession": "敵保持", "loose_ball": "ルーズボール",
}
END_REASON_SCORE = "score"
END_REASON_TURN_LIMIT = "turn_limit"
END_REASON_RETIRE = "retire"
END_REASON_DEBUG_INTERRUPT = "debug_interrupt"
END_REASON_LABELS = {
    END_REASON_SCORE: "通常終了",
    END_REASON_TURN_LIMIT: "ターン制限終了",
    END_REASON_RETIRE: "リタイア",
    END_REASON_DEBUG_INTERRUPT: "デバッグ中断",
}
COMMAND_GROUPS = ("attack", "move", "ball", "skill", "wait")
NORMAL_SKILL_BASE_SLOTS = 6
BALL_HOLD_SKILL_BASE_SLOTS = 1
COMMAND_GROUP_LABELS = {
    "attack": "攻撃",
    "move": "移動",
    "ball": "ボール",
    "skill": "スキル",
    "wait": "待機",
}
STAT_LABELS = {
    "magic": "魔法",
    "power": "パワー",
    "speed": "スピード",
    "technique": "テクニック",
    "stamina": "スタミナ",
}
COMBAT_STATS = ("power", "magic", "speed", "technique", "stamina")
RULE_PURIFICATION = 1
RULE_RECAPTURE = 2
RULE_RITUAL = 3


@dataclass(frozen=True)
class MatchConfig:
    field_width: int = 13
    field_height: int = 5
    left_goal_x_start: int = 0
    left_goal_x_end: int = 2
    right_goal_x_start: int = 10
    right_goal_x_end: int = 12
    goal_y_start: int = 1
    goal_y_end: int = 3
    team_size: int = 3
    player_team_size: int = 0
    enemy_team_size: int = 0
    player_party_limit: int = 0
    enemy_party_limit: int = 0
    player_cost_limit: int = 0
    enemy_cost_limit: int = 0
    target_score: int = 2
    max_rounds: int = 8  # 旧設定との互換用。現在はターン周期による終了なし。
    turn_limit: int = 0
    debug_mode: bool = False
    return_rounds: int = 2  # 旧設定との互換用。現在は得点時に復帰する。
    dice_sides: int = 6
    attribute_dice_sides: int = 3
    initial_mana: int = 0
    default_max_mana: int = 5
    mana_per_turn: int = 1
    ball_mana_bonus: int = 1
    reset_mana_after_score: bool = False
    score_hp_recovery_rate: float = 0.5
    score_mana_recovery_rate: float = 0.5
    score_stamina_recovery_rate: float = 0.5  # 旧設定読込の互換用。試合中は使用しない。
    stamina_per_turn: int = 1  # 旧設定読込の互換用。試合中は使用しない。
    wait_stamina_recovery_rate: float = 0.2  # 旧設定読込の互換用。試合中は使用しない。
    ability_min: int = 1
    ability_max: int = 10
    skill_level_min: int = 0
    skill_level_max: int = 6
    damage_attack_divisor: int = 2
    damage_defense_divisor: int = 3
    damage_die_sides: int = 6
    minimum_damage: int = 1
    damage_difference_multiplier: float = 0.5
    global_damage_multiplier: float = 0.6
    damage_difference_rate: float = 0.05
    damage_difference_min: float = -0.25
    damage_difference_max: float = 0.25
    healing_multiplier: float = 0.65
    physical_attack_coefficient: float = 2.0
    magic_attack_coefficient: float = 2.0
    physical_defense_coefficient: float = 1.5
    magic_defense_coefficient: float = 1.5
    hp_stat_baseline: int = 5
    hp_per_stamina: int = 5
    mp_stat_baseline: int = 5
    mp_per_magic: int = 1
    healing_magic_coefficient: float = 1.0
    ball_holder_damage_multiplier: float = 1.5
    normal_cut_base_rate: int = 45
    pass_cut_base_rate: int = 30
    normal_attack_drop_base_rate: int = 20
    normal_attack_drop_min_rate: int = 15
    normal_attack_drop_max_rate: int = 25
    ball_cut_base_rate: int = 45
    ball_cut_min_rate: int = 35
    ball_cut_max_rate: int = 55
    steal_base_rate: int = 55
    steal_min_rate: int = 45
    steal_max_rate: int = 65
    pass_cut_min_rate: int = 25
    pass_cut_max_rate: int = 45
    injury_gain_percent: int = 20
    injury_recovery_percent: int = 10
    injury_max_percent: int = 80
    ability_rate_multiplier: int = 5
    success_rate_min: int = 5
    success_rate_max: int = 95
    base_pass_range: int = 3
    pass_distance_grace: int = 3
    pass_distance_penalty: int = 1
    prohibit_possession_damage: bool = True
    speed_move_fast_threshold: int = 8
    speed_move_slow_threshold: int = 3
    speed_move_step: int = 1
    defend_multiplier: float = 0.5
    shield_multiplier: float = 0.5
    keep_bonus: int = 2
    steal_bonus: int = 2
    steal_damage_multiplier: float = 0.5
    lateral_pass_limit: int = 99  # 旧設定との互換用。現在は方向制限なし。
    ai_heal_threshold: float = 0.5
    ai_mp_recovery_threshold: float = 0.5
    ai_min_mp_recovery_rate: float = 0.25
    ai_pass_history_limit: int = 2
    ai_allow_active_buff_reuse: bool = False
    ai_position_repeat_limit: int = 2
    action_intro_ms: int = 500
    move_step_ms: int = 180
    judgement_ms: int = 1100
    result_ms: int = 750
    skip_speed_multiplier: int = 20
    skip_action_intro_ms: int = 40
    skip_move_step_ms: int = 16
    skip_move_total_max_ms: int = 100
    skip_result_ms: int = 40
    skip_score_hold_ms: int = 800
    player_ids: tuple[str, ...] = ("10", "11", "12")
    enemy_ids: tuple[str, ...] = ("20", "21", "22")
    enemy_ai_profile_ids: tuple[str, ...] = ("attack", "defense", "steal")
    default_enemy_group_id: str = ""
    player_positions: tuple[Position, ...] = ((3, 1), (3, 2), (3, 3))
    enemy_positions: tuple[Position, ...] = ((9, 1), (9, 2), (9, 3))
    ball_position: Position = (6, 2)
    substitution_enabled: bool = False
    substitution_count_per_score: int = 1
    injury_enabled: bool = False
    injury_penalty_per_marker: float = 0.2
    injury_penalty_floor: float = 0.4
    bench_recovery_count: int = 1
    restart_rule: str = "conceding_select"
    field_id: str = ""
    layout_id: str = ""
    match_setting_id: str = ""
    quest_id: str = ""
    rule_type: int = RULE_PURIFICATION
    required_hold_turns: int = 0
    initial_ball_holder: str = ""
    background_path: str = ""
    bgm_path: str = ""
    blocked_cells: tuple[Position, ...] = ()


@dataclass(frozen=True)
class ClassDefinition:
    class_id: str
    name: str
    description: str = ""
    signature_skill: str = ""


@dataclass(frozen=True)
class ElementDefinition:
    element_id: str
    name: str
    strong_against: str = ""
    weak_against: str = ""


@dataclass(frozen=True)
class SkillEffect:
    skill_id: str
    effect_order: int
    timing: str
    effect_type: str
    target: str = "selected"
    condition: str = "always"
    base_value: int = 0
    stat1: str = ""
    rate1: float = 0.0
    stat2: str = ""
    rate2: float = 0.0
    duration: int = 0
    distance: int = 0
    aux1: str = ""
    aux2: str = ""


@dataclass(frozen=True)
class Skill:
    skill_id: str
    name: str
    description: str
    activation: str
    mana_cost: int
    target_type: str
    range: int
    reference_stat: str
    use_dice: bool
    base_effect: int
    success_effect: str
    failure_effect: str
    additional_effect: str
    cooldown: int
    usable_with_ball: bool
    usable_without_ball: bool
    steal_bonus: int = 0
    pass_bonus: int = 0
    defense_multiplier: float = 1.0
    round_limit: int = 0
    defense_stat: str = ""
    action_type: str = ""
    element_id: str = ""
    uses_attribute: bool = False
    ends_action: bool = True
    skill_system: str = "common"
    skill_type: str = ""
    required_level: int = 0
    allowed_states: tuple[str, ...] = ("always",)
    resource_type: str = "none"
    resource_cost: int = 0
    actor_primary_stat: str = ""
    actor_secondary_stat: str = ""
    defender_primary_stat: str = ""
    defender_secondary_stat: str = ""
    validation_error: str = ""
    category: str = "basic"
    min_range: int = 0
    max_uses: int = 0
    purpose_tag: str = ""
    enabled: bool = True
    tie_rule: str = "defender"
    effect_mode: str = "legacy"
    effects: tuple[SkillEffect, ...] = ()
    command_group: str = "skill"
    usable_after_move: bool = True
    display_order: int = 0
    ball_effect_type: str = "none"
    ball_effect_base_rate: int = 0
    ball_effect_actor_stat: str = ""
    ball_effect_defender_stat: str = ""
    ball_effect_rate_per_diff: int = 5
    ball_effect_min_rate: int = 0
    ball_effect_max_rate: int = 0
    ball_hold_scope: str = ""
    ball_hold_range: int = 0
    ball_hold_include_self: bool = True
    equip_slot: str = "normal"


@dataclass
class Character:
    char_id: str
    name: str
    team: str
    class_id: str
    class_name: str
    element_id: str
    element_name: str
    max_hp: int
    hp: int
    max_mana: int
    mana: int
    stamina: int
    physical: int
    power: int
    physical_skill_level: int
    magic_skill_level: int
    attack: int
    defense: int
    accuracy: int
    evasion: int
    technique: int
    speed: int
    magic: int
    magic_defense: int
    pass_power: int
    pass_cut: int
    pass_cut_range: int
    ball_keep: int
    ball_cut: int
    breakthrough: int
    breakthrough_defense: int
    move_range: int
    pass_range: int
    primary_system: str
    initial_position: Position
    position: Position | None
    default_ai_profile_id: str = DEFAULT_AI_PROFILE_ID
    ai_profile_id: str = DEFAULT_AI_PROFILE_ID
    ai_profile_name: str = "標準型"
    ai_profile_description: str = ""
    ai_level: int = AI_LEVEL_DEFAULT
    ai_role_id: str = ""
    ai_role_name: str = "自動判定"
    ai_settings: dict[str, int] = field(default_factory=dict)
    ai_profile_source: str = "fallback"
    skills: tuple[str, ...] = ()
    normal_skill_base_slots: int = NORMAL_SKILL_BASE_SLOTS
    normal_skill_current_slots: int = NORMAL_SKILL_BASE_SLOTS
    ball_hold_skill_base_slots: int = BALL_HOLD_SKILL_BASE_SLOTS
    ball_hold_skill_current_slots: int = BALL_HOLD_SKILL_BASE_SLOTS
    base_character_id: str = ""
    enemy_master_id: str = ""
    enemy_type_id: str = ""
    enemy_group_id: str = ""
    enemy_group_slot: int = 0
    enemy_scale: float = 1.0
    image_id: str = ""
    off_field: bool = False
    acted: bool = False
    defending: bool = False
    keeping: bool = False
    disabled: bool = False
    waiting: bool = False
    return_rounds: int = 0
    extra_action: bool = False
    shield_used_round: int = 0
    temporary_effects: dict[str, Any] = field(default_factory=dict)
    skill_cooldowns: dict[str, int] = field(default_factory=dict)
    skill_use_counts: dict[str, int] = field(default_factory=dict)
    reaction_skill: dict[str, Any] | None = None
    cost: int = 1
    on_bench: bool = False
    injury_markers: int = 0
    injury_markers_gained: int = 0
    injury_markers_recovered: int = 0
    injury_rate: int = 0
    knockout_round: int = 0
    appeared_this_round: bool = False
    was_benched_last_restart: bool = False
    is_benched_at_restart_start: bool = False
    restart_first_actor: bool = False


@dataclass
class Ball:
    holder_id: str | None = None
    loose_position: Position | None = None
    last_holder_id: str | None = None
    last_action: str = "initial"


@dataclass
class ActionResult:
    success: bool
    message: str
    consumed: bool = False
    damage: int = 0
    healing: int = 0
    ball_changed: bool = False
    extra_action: bool = False
    scored: bool = False
    match_ended: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionCandidate:
    action_id: str
    name: str
    description: str
    command_group: str
    source_type: str
    usable: bool
    reason: str
    target_type: str
    min_range: int
    max_range: int
    resource_cost: int
    resource_type: str
    cooldown: int
    display_order: int
    usable_after_move: bool
    skill_id: str = ""


@dataclass(frozen=True)
class AIActionPlan:
    destination: Position
    action_type: str
    purpose: str
    priority: int
    score: float
    target_id: str = ""
    skill_id: str = ""
    system: str = ""
    base_score: float = 0.0
    main_ai_key: str = ""
    profile_adjustment: float = 0.0
    mp_adjustment: float = 0.0
    risk_adjustment: float = 0.0
    decision_ball_state: str = "unknown"
    ball_context_adjustment: float = 0.0
    ball_context_reason: str = ""
    team_ball_role: str = "none"
    movement_purpose: str = "none"
    movement_context_adjustment: float = 0.0
    movement_context_reason: str = ""
    main_action_purpose: str = ""
    main_action_context_adjustment: float = 0.0
    main_action_context_reason: str = ""
    is_recovery_path_clear_action: bool = False
    ball_distance_before: int | None = None
    ball_distance_after: int | None = None


@dataclass(frozen=True)
class MovePlan:
    """A validated move which has not changed match state yet."""

    actor_id: str
    origin: Position
    destination: Position
    path: tuple[Position, ...]


@dataclass
class PendingMove:
    """The small state snapshot needed to cancel one arrived move."""

    plan: MovePlan
    ball_holder_id: str | None
    ball_loose_position: Position | None
    ball_last_holder_id: str | None
    ball_last_action: str
    acted: bool
    waiting: bool
    provisional_pickup: bool = False


def manhattan(first: Position, second: Position) -> int:
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def straight_line_cells(start: Position, end: Position) -> list[Position]:
    """Return grid cells crossed by a direct line, including both endpoints."""

    x, y = start
    end_x, end_y = end
    delta_x = end_x - x
    delta_y = end_y - y
    count_x = abs(delta_x)
    count_y = abs(delta_y)
    sign_x = 0 if delta_x == 0 else (1 if delta_x > 0 else -1)
    sign_y = 0 if delta_y == 0 else (1 if delta_y > 0 else -1)
    step_x = step_y = 0
    cells = [(x, y)]
    while step_x < count_x or step_y < count_y:
        left = (1 + 2 * step_x) * count_y
        right = (1 + 2 * step_y) * count_x
        if left == right:
            x += sign_x
            y += sign_y
            step_x += 1
            step_y += 1
        elif left < right:
            x += sign_x
            step_x += 1
        else:
            y += sign_y
            step_y += 1
        cells.append((x, y))
    return cells


class MatchGame:
    """State and rules for one match. UI code only calls public methods here."""

    def __init__(
        self,
        config: MatchConfig,
        characters: Iterable[Character],
        skills: dict[str, Skill],
        classes: dict[str, ClassDefinition] | None = None,
        elements: dict[str, ElementDefinition] | None = None,
        ai_profiles: dict[str, AIProfile] | None = None,
        rng: random.Random | None = None,
        seed: int | None = None,
        report_policy: str = "test",
        report_root: Any = None,
    ) -> None:
        self.config = config
        self.seed = seed
        self.characters = {character.char_id: character for character in characters}
        self.skills = skills
        self.classes = classes or {}
        self.elements = elements or {}
        self.ai_profiles = ai_profiles or {}
        self.rng = rng or random.Random()
        self.ball = Ball(loose_position=config.ball_position)
        self.scores = {PLAYER: 0, ENEMY: 0}
        self.round = 1
        self.turn_order: list[str] = []
        self.turn_index = 0
        self.match_over = False
        self.winner: str | None = None
        self.end_reason = ""
        self.events: list[str] = []
        self.last_result: ActionResult | None = None
        self.scorers: list[tuple[str, str]] = []
        self.stats = {"skill_uses": 0, "steals": 0, "passes": 0, "interceptions": 0}
        self.prepared_move: MovePlan | None = None
        self.pending_move: PendingMove | None = None
        self.restart_team: str | None = None
        self.restart_prepared = False
        self.substitutions_this_restart = {PLAYER: 0, ENEMY: 0}
        self.final_injury_summary: dict[str, int] = {}
        self.ai_pass_history: list[dict[str, Any]] = []
        self.ai_action_history: dict[str, list[dict[str, Any]]] = {}
        self.ai_pending_plans: dict[str, AIActionPlan] = {}
        self.loose_ball_recovery_actor_id: dict[str, str] = {PLAYER: "", ENEMY: ""}
        self.loose_ball_recovery_score: dict[str, float] = {PLAYER: 0.0, ENEMY: 0.0}
        self.loose_ball_recovery_reason: dict[str, str] = {PLAYER: "", ENEMY: ""}
        self.loose_ball_recovery_position: dict[str, Position | None] = {PLAYER: None, ENEMY: None}
        self.ball_hold_events: list[dict[str, Any]] = []
        self._ball_hold_state: dict[tuple[str, str], dict[str, Any]] = {}
        self._action_hold_references: list[dict[str, Any]] = []
        self._collect_hold_references = False
        self.ritual_hold_turns = 0
        self._ritual_had_possession = False
        self.report = MatchReportCollector(report_policy, report_root)
        self._validate_initial_state()
        for character in self.characters.values():
            self.ai_role(character)
            self._ensure_ai_profile(character)
        self.report.start(self)
        self._initialize_rule_ball_holder()
        player_count = len(self.active_characters(PLAYER))
        enemy_count = len(self.active_characters(ENEMY))
        mode = "デバッグ" if config.debug_mode else "通常"
        self._log(f"{player_count}対{enemy_count}マッチを開始しました / {mode}モード")
        self.start_round(initial=True)

    def _validate_initial_state(self) -> None:
        config = self.config
        if config.field_width <= 0 or config.field_height <= 0:
            raise ValueError("フィールドサイズは1以上である必要があります")
        if not (0 <= config.goal_y_start <= config.goal_y_end < config.field_height):
            raise ValueError("ゴール範囲がフィールド外です")
        if not (
            0 <= config.left_goal_x_start <= config.left_goal_x_end < config.field_width
            and 0 <= config.right_goal_x_start <= config.right_goal_x_end < config.field_width
        ):
            raise ValueError("ゴール範囲がフィールド外です")
        if config.left_goal_x_end >= config.right_goal_x_start:
            raise ValueError("左右のゴール範囲が重複しています")
        if config.target_score <= 0:
            raise ValueError("勝利得点が不正です")
        expected_counts = {
            PLAYER: config.player_team_size or config.team_size,
            ENEMY: config.enemy_team_size or config.team_size,
        }
        if any(not 1 <= count <= MAX_TEAM_SIZE for count in expected_counts.values()):
            raise ValueError(f"参加人数は各チーム1～{MAX_TEAM_SIZE}人で指定してください")
        if config.turn_limit < 0:
            raise ValueError("ターン制限は0以上で指定してください")
        for team in TEAMS:
            team_members = [character for character in self.characters.values() if character.team == team]
            count = len([character for character in team_members if not character.on_bench])
            if count != expected_counts[team]:
                raise ValueError(f"{team} の参加人数が {expected_counts[team]} 人ではありません")
            party_limit = config.player_party_limit if team == PLAYER else config.enemy_party_limit
            if party_limit > 0 and len(team_members) > party_limit:
                raise ValueError(f"{team} のパーティー人数 {len(team_members)} が上限 {party_limit} 人を超えています")
            cost_limit = config.player_cost_limit if team == PLAYER else config.enemy_cost_limit
            total_cost = sum(max(0, character.cost) for character in team_members)
            if cost_limit > 0 and total_cost > cost_limit:
                raise ValueError(f"{team} の総コスト {total_cost} が上限 {cost_limit} を超えています")
        occupied: set[Position] = set()
        for character in self.characters.values():
            if character.team not in TEAMS:
                raise ValueError(f"{character.name} のチーム設定が不正です")
            if character.on_bench:
                if character.position is not None:
                    raise ValueError(f"{character.name} のベンチ状態と位置が矛盾しています")
                continue
            if character.position is None or not self.in_bounds(character.position):
                raise ValueError(f"{character.name} の初期位置が不正です")
            if character.position in config.blocked_cells:
                raise ValueError(f"{character.name} の初期位置 {character.position} は進入不可です")
            if self.is_goal_cell(character.position):
                raise ValueError(f"{character.name} の初期位置 {character.position} がゴール領域内です")
            if character.position in occupied:
                raise ValueError(f"初期位置 {character.position} が重複しています")
            occupied.add(character.position)
            unknown = [skill_id for skill_id in character.skills if skill_id not in self.skills]
            if unknown:
                LOGGER.warning("%s の不明なスキルを無効化します: %s", character.name, unknown)
                character.skills = tuple(skill_id for skill_id in character.skills if skill_id in self.skills)
        if not self.in_bounds(self.config.ball_position):
            LOGGER.warning("ボール初期位置が不正なためフィールド中央へ補正します")
            self.ball.loose_position = (config.field_width // 2, config.field_height // 2)

    def _log(self, message: str) -> None:
        self.events.append(message)
        LOGGER.info(message)

    def _begin_report_action(
        self,
        action_name: str,
        actor_id: str | None = None,
        target_id: str | None = None,
        command_group: str = "",
    ) -> None:
        self._action_hold_references = []
        self._collect_hold_references = True
        self.report.begin_action(self, action_name, actor_id, target_id, command_group)

    def _end_report_action(self, result: ActionResult) -> None:
        self._refresh_ball_hold_state("行動後状態更新")
        if self._action_hold_references:
            unique = {(item["target_id"], item["stat"]): item for item in self._action_hold_references}
            result.details["ball_hold_modifiers"] = list(unique.values())
        self._collect_hold_references = False
        self.report.record_action(self, result)

    def _discard_report_action(self) -> None:
        self._collect_hold_references = False
        self._action_hold_references = []
        self.report.discard_pending()

    def full_log_text(self) -> str:
        return "\n".join(self.events) if self.events else "ログはありません。"

    @property
    def current_actor(self) -> Character | None:
        if self.match_over or self.restart_team or not self.turn_order or self.turn_index >= len(self.turn_order):
            return None
        return self.characters.get(self.turn_order[self.turn_index])

    @property
    def ball_position(self) -> Position | None:
        if self.ball.holder_id:
            holder = self.characters.get(self.ball.holder_id)
            return holder.position if holder and not holder.off_field else self.ball.loose_position
        return self.ball.loose_position

    @property
    def ball_team(self) -> str | None:
        holder = self.characters.get(self.ball.holder_id or "")
        return holder.team if holder else None

    def in_bounds(self, position: Position) -> bool:
        return 0 <= position[0] < self.config.field_width and 0 <= position[1] < self.config.field_height

    def opponent(self, team: str) -> str:
        return ENEMY if team == PLAYER else PLAYER

    def attack_direction(self, team: str) -> int:
        own = self.own_goal_cells(team)
        target = self.target_goal_cells(team)
        if not own or not target:
            return 0
        own_center = sum(position[0] for position in own) / len(own)
        target_center = sum(position[0] for position in target) / len(target)
        return 1 if target_center > own_center else -1 if target_center < own_center else 0

    def character_at(self, position: Position) -> Character | None:
        return next(
            (character for character in self.characters.values() if not character.off_field and character.position == position),
            None,
        )

    def active_characters(self, team: str | None = None) -> list[Character]:
        return [
            character
            for character in self.characters.values()
            if not character.on_bench and not character.off_field and character.position is not None
            and (team is None or character.team == team)
        ]

    def is_ball_holder(self, character_or_id: Character | str) -> bool:
        char_id = character_or_id.char_id if isinstance(character_or_id, Character) else character_or_id
        return self.ball.holder_id == char_id

    def set_ball_holder(self, char_id: str, action: str) -> bool:
        character = self.characters.get(char_id)
        if character is None or character.off_field or character.position is None:
            self._log(f"ボール保持者 {char_id} が不正なため設定できません")
            return False
        previous = self.ball.holder_id
        if previous:
            self.ball.last_holder_id = previous
        self.ball.holder_id = char_id
        self.ball.loose_position = None
        self.ball.last_action = action
        if previous != char_id:
            self._log_ball_hold_transition(previous, char_id, action)
            self._refresh_ball_hold_state(action)
            self._handle_ball_state_change()
        return previous != char_id

    def drop_ball(self, position: Position, action: str) -> None:
        previous = self.ball.holder_id
        self.ball.last_holder_id = previous
        self.ball.holder_id = None
        self.ball.loose_position = position
        self.ball.last_action = action
        if previous:
            self._log_ball_hold_transition(previous, None, action)
            self._refresh_ball_hold_state(action)
        self._handle_ball_state_change()

    @property
    def rule_name(self) -> str:
        return {
            RULE_PURIFICATION: "浄化運搬",
            RULE_RECAPTURE: "マナ奪還",
            RULE_RITUAL: "儀式維持",
        }.get(self.config.rule_type, "不明")

    @property
    def victory_condition_text(self) -> str:
        if self.config.rule_type == RULE_RECAPTURE:
            return "勝利条件：敵からマナボールを奪還する"
        if self.config.rule_type == RULE_RITUAL:
            return f"勝利条件：マナボールを{self.config.required_hold_turns}ターン維持する"
        return "勝利条件：マナボールを浄化地点へ運ぶ"

    def _resolve_initial_holder(self) -> Character | None:
        selector = self.config.initial_ball_holder.strip()
        expected_team = ENEMY if self.config.rule_type == RULE_RECAPTURE else PLAYER
        if selector and selector in self.characters:
            candidate = self.characters[selector]
            return candidate if candidate.team == expected_team and not candidate.on_bench else None
        if selector:
            prefix, separator, raw_index = selector.partition(":")
            team = {"ally": PLAYER, "player": PLAYER, "enemy": ENEMY}.get(prefix.lower())
            if separator and team == expected_team:
                try:
                    index = int(raw_index) - 1
                except ValueError:
                    index = -1
                candidates = self.active_characters(team)
                if 0 <= index < len(candidates):
                    return candidates[index]
            return None
        candidates = self.active_characters(expected_team)
        return candidates[0] if candidates else None

    def _initialize_rule_ball_holder(self) -> None:
        if self.config.rule_type == RULE_PURIFICATION and not self.config.initial_ball_holder:
            return
        holder = self._resolve_initial_holder()
        if holder is None:
            raise ValueError(
                f"初期ボール保持者を取得できません: quest={self.config.quest_id or '-'} "
                f"wave=1 rule={self.config.rule_type} holder={self.config.initial_ball_holder or '(default)'}"
            )
        self.set_ball_holder(holder.char_id, "rule_initial")

    def _handle_ball_state_change(self) -> None:
        if self.match_over:
            return
        if self.config.rule_type == RULE_RECAPTURE and self.ball_team == PLAYER:
            self._log("マナボールを奪還しました")
            self.finish_match(winner=PLAYER)
            return
        if self.config.rule_type == RULE_RITUAL and self.ball_team != PLAYER:
            if self.ritual_hold_turns:
                self._log("儀式維持が中断されました")
            self.ritual_hold_turns = 0
            self._ritual_had_possession = False
        elif self.config.rule_type == RULE_RITUAL and not self._ritual_had_possession:
            self._ritual_had_possession = True
            if self.ball.last_action != "rule_initial":
                self._log("儀式維持を再開します")

    def _update_ritual_at_round_end(self) -> None:
        if self.config.rule_type != RULE_RITUAL or self.match_over:
            return
        if self.ball_team == PLAYER:
            self.ritual_hold_turns += 1
            self._ritual_had_possession = True
            self._log(f"儀式維持：{self.ritual_hold_turns}／{self.config.required_hold_turns}ターン")
            if self.ritual_hold_turns >= self.config.required_hold_turns:
                self._log("儀式が完了しました")
                self.finish_match(winner=PLAYER)
        else:
            self.ritual_hold_turns = 0
            self._ritual_had_possession = False

    def ball_hold_skills(self, holder: Character | None = None) -> list[Skill]:
        if self.match_over or self.restart_team is not None:
            return []
        holder = holder or self.characters.get(self.ball.holder_id or "")
        if holder is None or holder.off_field or holder.position is None or not self.is_ball_holder(holder):
            return []
        return [
            skill for skill_id in holder.skills
            if (skill := self.skills.get(skill_id)) is not None
            and skill.enabled and not skill.validation_error
            and skill.activation == "automatic" and skill.purpose_tag == "ball_hold"
        ]

    def ball_hold_modifier_details(self, character: Character) -> dict[str, Any]:
        """Derive passive holder modifiers from current ball and board state."""
        holder = self.characters.get(self.ball.holder_id or "")
        values: dict[str, list[tuple[int, str, str]]] = {}
        if (
            holder is None or holder.off_field or holder.position is None
            or character.off_field or character.position is None or character.team != holder.team
        ):
            return {"modifiers": {}, "sources": {}}
        for skill in self.ball_hold_skills(holder):
            if skill.ball_hold_scope == "self":
                applies = character.char_id == holder.char_id
            else:
                applies = manhattan(holder.position, character.position) <= skill.ball_hold_range
                if character.char_id == holder.char_id and not skill.ball_hold_include_self:
                    applies = False
            if not applies:
                continue
            for effect in skill.effects:
                if effect.effect_type not in {"modify_stat", "modify_move_range"}:
                    continue
                stat = "move_range" if effect.effect_type == "modify_move_range" else (effect.aux1 or effect.stat1)
                value = -abs(effect.base_value) if effect.aux2 == "subtract" else effect.base_value
                values.setdefault(stat, []).append((value, skill.skill_id, skill.name))
        modifiers: dict[str, int] = {}
        sources: dict[str, list[dict[str, Any]]] = {}
        for stat, items in values.items():
            positive = max((value for value, _, _ in items if value > 0), default=0)
            negative = min((value for value, _, _ in items if value < 0), default=0)
            modifiers[stat] = positive + negative
            sources[stat] = [{"value": value, "skill_id": skill_id, "skill_name": name} for value, skill_id, name in items]
        return {"modifiers": modifiers, "sources": sources}

    def _refresh_ball_hold_state(self, reason: str) -> None:
        current: dict[tuple[str, str], dict[str, Any]] = {}
        holder = self.characters.get(self.ball.holder_id or "")
        for target in self.characters.values():
            details = self.ball_hold_modifier_details(target)
            for stat, adopted in details["modifiers"].items():
                base = int(getattr(target, stat, 0) or 0)
                state = {
                    "holder_id": holder.char_id if holder else "", "holder_name": holder.name if holder else "",
                    "target_id": target.char_id, "target_name": target.name, "stat": stat,
                    "base_value": base, "candidates": details["sources"].get(stat, []),
                    "adopted_value": adopted, "effective_value": base + adopted,
                }
                current[(target.char_id, stat)] = state
        for key, state in current.items():
            previous = self._ball_hold_state.get(key)
            if previous == state:
                continue
            event_type = "適用" if previous is None else "変更"
            self.ball_hold_events.append({**state, "event": event_type, "reason": reason})
            candidates = "、".join(f"{item['skill_name']} {item['value']:+d}" for item in state["candidates"])
            self._log(f"保持スキル{event_type}: 対象={state['target_name']} / 能力={STAT_LABELS.get(state['stat'], state['stat'])} / 基礎値={state['base_value']} / 候補={candidates} / 採用値={state['adopted_value']:+d} / 実効値={state['effective_value']} / 理由={reason}")
        for key, previous in self._ball_hold_state.items():
            if key not in current:
                self.ball_hold_events.append({**previous, "event": "解除", "reason": reason})
                self._log(f"保持スキル解除: 発動者={previous['holder_name']} / 対象={previous['target_name']} / 能力={STAT_LABELS.get(previous['stat'], previous['stat'])} / 解除前補正={previous['adopted_value']:+d} / 理由={reason}")
        self._ball_hold_state = current

    def ball_hold_modifier(self, character: Character, stat: str) -> int:
        return int(self.ball_hold_modifier_details(character)["modifiers"].get(stat, 0))

    def ball_hold_status(self, character: Character) -> dict[str, Any]:
        details = self.ball_hold_modifier_details(character)
        holder = self.characters.get(self.ball.holder_id or "")
        return {
            **details,
            "holder_id": holder.char_id if holder else None,
            "holder_name": holder.name if holder else "",
            "active_skills": [skill.name for skill in self.ball_hold_skills(holder)],
        }

    def _log_ball_hold_transition(self, previous_id: str | None, current_id: str | None, action: str) -> None:
        previous = self.characters.get(previous_id or "")
        current = self.characters.get(current_id or "")
        if previous:
            names = [
                self.skills[skill_id].name for skill_id in previous.skills
                if skill_id in self.skills and self.skills[skill_id].activation == "automatic"
                and self.skills[skill_id].purpose_tag == "ball_hold"
            ]
            if names:
                self._log(f"ボール保持スキル解除: {previous.name} / {' / '.join(names)} / 契機={action}")
        if current:
            skills = self.ball_hold_skills(current)
            if skills:
                summary = []
                for skill in skills:
                    effects = []
                    for effect in skill.effects:
                        stat = "移動力" if effect.effect_type == "modify_move_range" else STAT_LABELS.get(effect.aux1 or effect.stat1, effect.aux1 or effect.stat1)
                        value = -abs(effect.base_value) if effect.aux2 == "subtract" else effect.base_value
                        effects.append(f"{stat}{value:+d}")
                    summary.append(f"{skill.name}({'/'.join(effects)})")
                self._log(f"ボール保持スキル発動: {current.name} / {' / '.join(summary)} / 契機={action}")

    def loose_ball_drop_position(self, origin: Position) -> Position | None:
        """Choose a reproducible, unoccupied legal cell around an impact point."""
        def legal(position: Position) -> bool:
            return (
                self.in_bounds(position)
                and position not in self.config.blocked_cells
                and self.character_at(position) is None
            )

        nearby = [
            (origin[0] + dx, origin[1] + dy)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if (dx or dy) and legal((origin[0] + dx, origin[1] + dy))
        ]
        if nearby:
            return nearby[self.roll_die(len(nearby)) - 1]
        fallback = [
            (x, y)
            for x in range(self.config.field_width)
            for y in range(self.config.field_height)
            if legal((x, y))
        ]
        fallback.sort(key=lambda cell: (manhattan(origin, cell), cell[0], cell[1]))
        return fallback[0] if fallback else None

    def ball_effect_preview(self, actor_id: str, target_id: str, skill_id: str) -> dict[str, Any]:
        skill = self.skills.get(skill_id)
        actor = self.characters.get(actor_id)
        target = self.characters.get(target_id)
        if not skill or not actor or not target or skill.ball_effect_type == "none":
            return {"active": False, "effect_type": "none"}
        actor_value = self.effective_stat(actor, skill.ball_effect_actor_stat)
        defender_value = self.effective_stat(target, skill.ball_effect_defender_stat)
        difference = actor_value - defender_value
        correction = difference * skill.ball_effect_rate_per_diff
        final_rate = min(skill.ball_effect_max_rate, max(skill.ball_effect_min_rate, skill.ball_effect_base_rate + correction))
        return {
            "active": self.is_ball_holder(target),
            "effect_type": skill.ball_effect_type,
            "actor_stat": skill.ball_effect_actor_stat,
            "defender_stat": skill.ball_effect_defender_stat,
            "actor_value": actor_value,
            "defender_value": defender_value,
            "base_rate": skill.ball_effect_base_rate,
            "ability_difference": difference,
            "rate_per_diff": skill.ball_effect_rate_per_diff,
            "ability_rate_bonus": correction,
            "final_rate": final_rate,
        }

    def _resolve_attack_ball_effect(
        self, actor: Character, target: Character, skill: Skill, target_was_holder: bool, origin: Position | None,
    ) -> dict[str, Any] | None:
        if not target_was_holder:
            return None
        previous = target.char_id
        if target.off_field or target.hp <= 0:
            return {
                "effect_type": "drop", "forced_knockout_drop": True, "success": True,
                "previous_holder_id": previous, "final_holder_id": self.ball.holder_id,
                "drop_position": self.ball.loose_position,
            }
        if skill.ball_effect_type == "none":
            return None
        if self.ball.holder_id != target.char_id:
            return None
        preview = self.ball_effect_preview(actor.char_id, target.char_id, skill.skill_id)
        roll = self.roll_die(100)
        success = roll <= int(preview["final_rate"])
        drop_position = None
        if success and skill.ball_effect_type == "cut":
            self.set_ball_holder(actor.char_id, f"{skill.skill_id}:ball_cut")
        elif success and skill.ball_effect_type == "drop" and origin is not None:
            drop_position = self.loose_ball_drop_position(origin)
            if drop_position is None:
                success = False
                LOGGER.warning("%s のボールドロップ先がないため保持状態を維持します", skill.skill_id)
            else:
                self.drop_ball(drop_position, f"{skill.skill_id}:ball_drop")
        result = dict(preview)
        result.update({
            "roll": roll, "success": success, "previous_holder_id": previous,
            "final_holder_id": self.ball.holder_id, "drop_position": drop_position,
            "forced_knockout_drop": False,
        })
        self._log(
            f"攻撃ボール効果: {skill.name} / {skill.ball_effect_type} / "
            f"基礎{skill.ball_effect_base_rate}% / 差{preview['ability_difference']:+d} / "
            f"最終{preview['final_rate']}% / 乱数{roll} / {'成功' if success else '失敗'}"
        )
        return result

    def ensure_ball_consistency(self) -> None:
        holder = self.characters.get(self.ball.holder_id or "")
        if self.ball.holder_id and (holder is None or holder.off_field or holder.position is None):
            fallback = self.ball.loose_position or self.config.ball_position
            LOGGER.warning("無効なボール保持者を検出したためルーズボールへ補正します")
            self.drop_ball(fallback, "consistency_fix")

    def roll_die(self, sides: int) -> int:
        safe_sides = max(1, int(sides or 1))
        try:
            value = int(self.rng.randint(1, safe_sides))
            if not 1 <= value <= safe_sides:
                raise ValueError(value)
            return value
        except Exception:
            fallback = max(1, math.ceil(safe_sides / 2))
            LOGGER.exception("ダイス処理に失敗したため中央値 %s を使用します", fallback)
            return fallback

    def roll(self) -> int:
        return self.roll_die(self.config.dice_sides)

    def attribute_relation(self, attacker_element: str, defender_element: str) -> str:
        if not attacker_element or attacker_element == "0" or not defender_element or defender_element == "0":
            return "none"
        definition = self.elements.get(attacker_element)
        if definition and definition.strong_against == defender_element:
            return "advantage"
        if definition and definition.weak_against == defender_element:
            return "disadvantage"
        return "neutral"

    def team_state(self, team: str) -> str:
        holder_team = self.ball_team
        if holder_team is None:
            return MATCH_STATE_CONTEST
        return MATCH_STATE_POSSESSION if holder_team == team else MATCH_STATE_ACQUISITION

    def team_state_name(self, team: str) -> str:
        return MATCH_STATE_LABELS[self.team_state(team)]

    def ability_components(self, character: Character, primary: str, secondary: str) -> dict[str, Any]:
        actual_primary = primary
        if primary == "best_physical_magic":
            actual_primary = "power"
        primary_value = max(self.config.ability_min, self.effective_stat(character, actual_primary))
        secondary_value = max(self.config.ability_min, self.effective_stat(character, secondary))
        return {
            "primary_key": actual_primary,
            "primary_name": STAT_LABELS.get(actual_primary, actual_primary),
            "primary_value": primary_value,
            "secondary_key": secondary,
            "secondary_name": STAT_LABELS.get(secondary, secondary),
            "secondary_value": secondary_value,
            "total": primary_value + secondary_value,
            "name": f"{STAT_LABELS.get(actual_primary, actual_primary)}×{STAT_LABELS.get(secondary, secondary)}",
        }

    def ability_total(self, character: Character, primary: str, secondary: str) -> int:
        return int(self.ability_components(character, primary, secondary)["total"])

    def effective_stat(self, character: Character, stat: str) -> int:
        base_value = int(getattr(character, stat, 0) or 0)
        value = base_value
        modifiers = character.temporary_effects.get("stat_modifiers", [])
        if isinstance(modifiers, list):
            for modifier in modifiers:
                if not isinstance(modifier, dict) or modifier.get("stat") != stat:
                    continue
                if modifier.get("mode", "add") == "percent":
                    value = math.floor(value * (1.0 + float(modifier.get("value", 0)) / 100.0))
                else:
                    value += int(modifier.get("value", 0))
        hold_details = self.ball_hold_modifier_details(character)
        hold_modifier = int(hold_details["modifiers"].get(stat, 0))
        value += hold_modifier
        if hold_modifier and self._collect_hold_references:
            holder = self.characters.get(self.ball.holder_id or "")
            self._action_hold_references.append({
                "target_id": character.char_id, "target_name": character.name,
                "holder_id": holder.char_id if holder else "", "holder_name": holder.name if holder else "",
                "stat": stat, "base_value": base_value, "candidates": hold_details["sources"].get(stat, []),
                "adopted_value": hold_modifier, "effective_value": value,
            })
        if stat in COMBAT_STATS:
            value = math.floor(value * self.injury_multiplier(character))
            return min(self.config.ability_max, max(self.config.ability_min, value))
        return max(0, value)

    def injury_multiplier(self, character: Character) -> float:
        """Return the runtime-only injury modifier; passive hooks may adjust it later."""
        reduction = min(self.config.injury_max_percent, max(0, character.injury_rate)) / 100.0
        passive_relief = float(character.temporary_effects.get("injury_penalty_relief", 0.0) or 0.0)
        return min(1.0, max(self.config.injury_penalty_floor, 1.0 - reduction + passive_relief))

    def injury_context(self, character: Character | None = None) -> dict[str, Any]:
        """Stable passive-skill connection point for current and cumulative injury state."""
        totals = {}
        for team in TEAMS:
            members = [unit for unit in self.characters.values() if unit.team == team]
            totals[team] = {
                "current": sum(max(0, unit.injury_markers) for unit in members),
                "gained": sum(max(0, unit.injury_markers_gained) for unit in members),
                "recovered": sum(max(0, unit.injury_markers_recovered) for unit in members),
                "field_current": sum(max(0, unit.injury_markers) for unit in members if not unit.on_bench),
            }
        return {
            "self_current": max(0, character.injury_markers) if character else 0,
            "self_gained": max(0, character.injury_markers_gained) if character else 0,
            "self_multiplier": self.injury_multiplier(character) if character else 1.0,
            "teams": totals,
            "field_current": sum(item["field_current"] for item in totals.values()),
            "all_current": sum(item["current"] for item in totals.values()),
            "match_gained": sum(item["gained"] for item in totals.values()),
        }

    @staticmethod
    def _system_name(system: str) -> str:
        return "魔法" if system == "magic" else "物理"

    @staticmethod
    def _valid_system(system: str) -> bool:
        return system in {"physical", "magic"}

    def pass_range_for(self, character: Character, system: str, range_bonus: int = 0) -> int:
        if not self._valid_system(system):
            return 0
        system_value = self.effective_stat(character, "magic" if system == "magic" else "power")
        return max(1, self.config.base_pass_range + max(0, system_value - 1) // 2 + range_bonus)

    def primary_system_for(self, character_or_id: Character | str) -> str | None:
        character = (
            self.characters.get(character_or_id)
            if isinstance(character_or_id, str)
            else character_or_id
        )
        if character is None:
            return None
        if character.primary_system in {"physical", "magic"}:
            return character.primary_system
        return "magic" if self.effective_stat(character, "magic") > self.effective_stat(character, "power") else "physical"

    def basic_attack_skill_id(self, character_or_id: Character | str) -> str | None:
        system = self.primary_system_for(character_or_id)
        if system is None:
            return None
        skill_id = f"normal_{system}_attack"
        skill = self.skills.get(skill_id)
        if skill is None or not skill.enabled or skill.validation_error:
            return None
        return skill_id

    def _success_rate(self, base_rate: int, difference: int, extra: int = 0) -> int:
        rate = base_rate + difference * self.config.ability_rate_multiplier + extra
        return min(self.config.success_rate_max, max(self.config.success_rate_min, rate))

    def _attribute_dice(self, attacker: Character, defender: Character, element_id: str = "") -> dict[str, Any]:
        attack_element = attacker.element_id if element_id in {"", "self"} else element_id
        relation = self.attribute_relation(attack_element, defender.element_id)
        if relation == "none":
            return {"used": False, "bonus": 0, "relation": "none", "rolls": []}
        count = 2 if relation in {"advantage", "disadvantage"} else 1
        rolls = [self.roll_die(self.config.attribute_dice_sides) for _ in range(count)]
        bonus = max(rolls) if relation == "advantage" else min(rolls) if relation == "disadvantage" else rolls[0]
        return {
            "used": True,
            "element_id": attack_element,
            "element_name": self.elements.get(attack_element, ElementDefinition(attack_element, "無属性")).name,
            "defender_element_name": defender.element_name,
            "relation": relation,
            "rolls": rolls,
            "bonus": bonus,
        }

    def contest(
        self,
        action_name: str,
        actor: Character,
        target: Character,
        offense_primary: str,
        offense_secondary: str,
        defense_primary: str,
        defense_secondary: str,
        offense_bonus: int = 0,
        defense_bonus: int = 0,
        uses_attribute: bool = False,
        element_id: str = "",
    ) -> tuple[bool, dict[str, Any]]:
        offense = self.ability_components(actor, offense_primary, offense_secondary)
        defense = self.ability_components(target, defense_primary, defense_secondary)
        offense_value = int(offense["total"])
        defense_value = int(defense["total"])
        offense_roll = self.roll_die(offense_value)
        defense_roll = self.roll_die(defense_value)
        attribute = self._attribute_dice(actor, target, element_id) if uses_attribute else {
            "used": False,
            "bonus": 0,
            "relation": "none",
            "rolls": [],
        }
        offense_total = offense_roll + offense_bonus + attribute["bonus"]
        defense_total = defense_roll + defense_bonus
        success = offense_total > defense_total
        details = {
            "action_name": action_name,
            "actor_id": actor.char_id,
            "actor_name": actor.name,
            "target_id": target.char_id,
            "target_name": target.name,
            "contest": {
                "offense_name": offense["name"],
                "offense_stats": offense,
                "offense_value": offense_value,
                "offense_roll": offense_roll,
                "offense_bonus": offense_bonus,
                "offense_total": offense_total,
                "defense_name": defense["name"],
                "defense_stats": defense,
                "defense_value": defense_value,
                "defense_roll": defense_roll,
                "defense_bonus": defense_bonus,
                "defense_total": defense_total,
                "attribute": attribute,
                "success": success,
            },
        }
        attribute_text = f"+属性{attribute['bonus']}" if attribute["used"] else ""
        self._log(
            f"{action_name}: {actor.name} {offense['name']}D{offense_value}={offense_roll}+補正{offense_bonus}{attribute_text}"
            f"→{offense_total} / {target.name} {defense['name']}D{defense_value}={defense_roll}+補正{defense_bonus}"
            f"→{defense_total} / {'成功' if success else '失敗'}"
        )
        return success, details

    def skill_level(self, character: Character, skill: Skill) -> int:
        if skill.skill_system == "physical":
            return character.physical_skill_level
        if skill.skill_system == "magic":
            return character.magic_skill_level
        return self.config.skill_level_max

    def skill_cost_text(self, skill: Skill) -> str:
        labels = {"mana": "MP", "none": "消費なし"}
        if skill.resource_type == "none" or skill.resource_cost <= 0:
            return "消費なし"
        return f"{labels.get(skill.resource_type, skill.resource_type)}{skill.resource_cost}"

    def _resource_value(self, character: Character, skill: Skill) -> int:
        if skill.resource_type == "mana":
            return character.mana
        return 0

    def _consume_skill_resource(self, character: Character, skill: Skill) -> None:
        if skill.resource_type == "mana":
            character.mana -= skill.resource_cost

    def _advance_temporary_effects(self, character: Character) -> None:
        modifiers = character.temporary_effects.get("stat_modifiers")
        if not isinstance(modifiers, list):
            return
        remaining: list[dict[str, Any]] = []
        for modifier in modifiers:
            if not isinstance(modifier, dict):
                continue
            if modifier.pop("skip_decay", False):
                remaining.append(modifier)
                continue
            modifier["remaining"] = int(modifier.get("remaining", 0)) - 1
            if modifier["remaining"] > 0:
                remaining.append(modifier)
        if remaining:
            character.temporary_effects["stat_modifiers"] = remaining
        else:
            character.temporary_effects.pop("stat_modifiers", None)

    def start_round(self, initial: bool = False) -> None:
        if self.match_over:
            return
        if not initial:
            self._recover_benched_injuries()
        for character in self.characters.values():
            character.acted = False
            character.waiting = False
            character.extra_action = False
            character.appeared_this_round = not character.on_bench and not character.off_field
        active = [character for character in self.active_characters() if not character.disabled]
        tie_values = {character.char_id: self.rng.random() for character in active}
        active.sort(key=lambda character: (-self.effective_stat(character, "speed"), tie_values[character.char_id]))
        forced = next((character for character in active if character.restart_first_actor), None)
        if forced is not None:
            active.remove(forced)
            active.insert(0, forced)
        for character in self.characters.values():
            character.restart_first_actor = False
        self.turn_order = [character.char_id for character in active]
        self.turn_index = 0
        self._log(f"ラウンド {self.round} 開始 / 行動順: " + " → ".join(c.name for c in active))
        if not active:
            self.finish_match()
            return
        self._begin_current_turn()

    def _recover_benched_injuries(self) -> None:
        for character in self.characters.values():
            if character.on_bench and not character.off_field and not character.appeared_this_round:
                before = character.injury_rate
                character.injury_rate = max(0, before - self.config.injury_recovery_percent)
                character.injury_markers = math.ceil(character.injury_rate / max(1, self.config.injury_gain_percent))
                if before != character.injury_rate:
                    character.injury_markers_recovered += 1
                    self._log(f"負傷回復: {character.name} {before}%→{character.injury_rate}%")

    def _begin_current_turn(self) -> None:
        actor = self.current_actor
        if actor is None:
            return
        actor.defending = False
        actor.keeping = False
        actor.temporary_effects.pop("movement_down", None)
        for skill_id in tuple(actor.skill_cooldowns):
            actor.skill_cooldowns[skill_id] = max(0, actor.skill_cooldowns[skill_id] - 1)
            if actor.skill_cooldowns[skill_id] == 0:
                del actor.skill_cooldowns[skill_id]
        if actor.reaction_skill:
            self._log(f"反応待機終了: {actor.name} / {actor.reaction_skill.get('skill_name', '')}")
            actor.reaction_skill = None
        regeneration_before = self.report.capture_state(self)
        gained = self.config.mana_per_turn + (self.config.ball_mana_bonus if self.is_ball_holder(actor) else 0)
        before = actor.mana
        actor.mana = min(actor.max_mana, actor.mana + gained)
        self.report.record_turn_regeneration(
            self,
            actor.char_id,
            actor.name,
            before,
            actor.mana,
            gained,
            "turn_regeneration",
            before_state=regeneration_before,
        )
        self._log(f"{actor.name} の行動開始: MP {before}→{actor.mana} (+{actor.mana - before})")

    def advance_turn(self, actor_id: str | None = None) -> None:
        if self.match_over:
            return
        actor = self.current_actor
        if actor is None:
            return
        if actor_id is not None and actor.char_id != actor_id:
            self._log("現在の行動者と異なるため行動終了を無視しました")
            return
        actor.acted = True
        actor.extra_action = False
        self._advance_temporary_effects(actor)
        self.turn_index += 1
        while self.turn_index < len(self.turn_order):
            next_actor = self.characters[self.turn_order[self.turn_index]]
            if not next_actor.off_field and not next_actor.acted:
                self._begin_current_turn()
                return
            self.turn_index += 1
        self._update_ritual_at_round_end()
        if self.match_over:
            return
        if self.config.turn_limit > 0 and self.round >= self.config.turn_limit:
            self.finish_match(END_REASON_TURN_LIMIT)
            return
        self.round += 1
        self.start_round()

    def zoc_cells(self, moving_team: str) -> set[Position]:
        cells: set[Position] = set()
        for enemy in self.active_characters(self.opponent(moving_team)):
            assert enemy.position is not None
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                cell = (enemy.position[0] + dx, enemy.position[1] + dy)
                if self.in_bounds(cell):
                    cells.add(cell)
        return cells

    def movement_paths(
        self,
        char_id: str,
        steps: int | None = None,
        ignore_zoc: bool = False,
    ) -> dict[Position, list[Position]]:
        character = self.characters.get(char_id)
        if character is None or character.off_field or character.position is None:
            return {}
        limit = self.effective_move_range(character) if steps is None else max(0, steps)
        limit = max(0, limit - int(character.temporary_effects.get("movement_down", 0)))
        start = character.position
        enemies = {c.position for c in self.active_characters(self.opponent(character.team)) if c.position is not None}
        friends = {
            c.position for c in self.active_characters(character.team) if c.char_id != char_id and c.position is not None
        }
        zoc = set() if ignore_zoc else self.zoc_cells(character.team)
        queue: deque[Position] = deque([start])
        distance = {start: 0}
        paths = {start: [start]}
        destinations: dict[Position, list[Position]] = {}
        while queue:
            position = queue.popleft()
            if distance[position] >= limit:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                candidate = (position[0] + dx, position[1] + dy)
                if (
                    not self.in_bounds(candidate) or candidate in self.config.blocked_cells
                    or candidate in enemies or candidate in distance
                ):
                    continue
                distance[candidate] = distance[position] + 1
                paths[candidate] = paths[position] + [candidate]
                if candidate not in friends:
                    destinations[candidate] = paths[candidate]
                if candidate in zoc:
                    continue
                queue.append(candidate)
        return destinations

    def effective_move_range(self, character: Character) -> int:
        adjustment = 0
        speed = self.effective_stat(character, "speed")
        if speed >= self.config.speed_move_fast_threshold:
            adjustment = self.config.speed_move_step
        elif speed <= self.config.speed_move_slow_threshold:
            adjustment = -self.config.speed_move_step
        return max(1, character.move_range + adjustment + self.ball_hold_modifier(character, "move_range"))

    def reachable_positions(self, char_id: str, steps: int | None = None, ignore_zoc: bool = False) -> set[Position]:
        return set(self.movement_paths(char_id, steps, ignore_zoc))

    def prepare_move(
        self,
        char_id: str,
        destination: Position,
        steps: int | None = None,
        ignore_zoc: bool = False,
    ) -> ActionResult:
        if self.prepared_move or self.pending_move:
            return self._result(False, "別の移動が確認待ちです")
        character = self.characters.get(char_id)
        if character is None or character.off_field or character.position is None:
            return self._result(False, "移動できるキャラクターではありません")
        if character.acted:
            return self._result(False, "行動済みのキャラクターは移動できません")
        paths = self.movement_paths(char_id, steps, ignore_zoc)
        if destination not in paths:
            return self._result(False, "移動範囲外、敵占有、味方上での停止、またはZOC通過になるマスです")
        path_list = paths[destination]
        path = tuple(path_list)
        self.prepared_move = MovePlan(char_id, character.position, destination, path)
        details = {
            "action_name": "移動",
            "actor_id": char_id,
            "actor_name": character.name,
            "path": list(path),
            "move_prepared": True,
        }
        return self._result(True, f"{character.name}が移動先へ向かいます", details=details)

    def arrive_prepared_move(self, char_id: str) -> ActionResult:
        plan = self.prepared_move
        character = self.characters.get(char_id)
        if plan is None or plan.actor_id != char_id or character is None or character.position != plan.origin:
            self.prepared_move = None
            return self._result(False, "移動開始時の状態が変化したため移動を反映できません")
        self._begin_report_action("通常移動", char_id, "", "move")
        self.pending_move = PendingMove(
            plan=plan,
            ball_holder_id=self.ball.holder_id,
            ball_loose_position=self.ball.loose_position,
            ball_last_holder_id=self.ball.last_holder_id,
            ball_last_action=self.ball.last_action,
            acted=character.acted,
            waiting=character.waiting,
        )
        self.prepared_move = None
        character.position = plan.destination
        if self.ball.holder_id is None and self.ball.loose_position == plan.destination:
            self.set_ball_holder(char_id, "pending_pickup")
            self.pending_move.provisional_pickup = True
        details = {
            "action_name": "移動",
            "actor_id": char_id,
            "actor_name": character.name,
            "path": list(plan.path),
            "pickup": self.pending_move.provisional_pickup,
        }
        return self._result(True, f"{character.name}が移動しました。次の行動で確定、または移動取消できます", details=details)

    def cancel_pending_move(self, char_id: str) -> ActionResult:
        pending = self.pending_move
        character = self.characters.get(char_id)
        if pending is None or pending.plan.actor_id != char_id or character is None:
            return self._result(False, "取り消せる移動がありません")
        character.position = pending.plan.origin
        character.acted = pending.acted
        character.waiting = pending.waiting
        self.ball.holder_id = pending.ball_holder_id
        self.ball.loose_position = pending.ball_loose_position
        self.ball.last_holder_id = pending.ball_last_holder_id
        self.ball.last_action = pending.ball_last_action
        self.pending_move = None
        self._discard_report_action()
        self._log(f"移動取消: {character.name} / {pending.plan.destination}→{pending.plan.origin}")
        return self._result(True, f"{character.name}の移動を取り消しました")

    def commit_pending_move(self, char_id: str) -> ActionResult:
        pending = self.pending_move
        character = self.characters.get(char_id)
        if pending is None:
            return self._result(True, "確定待ちの移動はありません")
        if pending.plan.actor_id != char_id or character is None or character.position != pending.plan.destination:
            self._discard_report_action()
            return self._result(False, "移動状態が一致しないため確定できません")
        self.pending_move = None
        self._log(f"移動: {character.name} / {'→'.join(map(str, pending.plan.path))}")
        details = {
            "action_name": "移動確定",
            "actor_id": char_id,
            "actor_name": character.name,
            "path": list(pending.plan.path),
        }
        if pending.provisional_pickup:
            self.ball.last_action = "pickup"
            self._log(f"ボール取得: {character.name}")
            details["pickup"] = True
        if (
            self.config.rule_type == RULE_PURIFICATION
            and self.is_ball_holder(character)
            and self.is_opponent_goal(character.team, pending.plan.destination)
        ):
            result = self._score(character)
            result.details.update(details)
            self._end_report_action(result)
            return result
        result = self._result(True, f"{character.name}が移動しました", details=details)
        self._end_report_action(result)
        return result

    def move_character(
        self,
        char_id: str,
        destination: Position,
        steps: int | None = None,
        ignore_zoc: bool = False,
    ) -> ActionResult:
        """Compatibility helper for non-animated callers; UI uses the staged methods."""

        prepared = self.prepare_move(char_id, destination, steps, ignore_zoc)
        if not prepared.success:
            return prepared
        arrived = self.arrive_prepared_move(char_id)
        if not arrived.success:
            return arrived
        return self.commit_pending_move(char_id)

    def is_opponent_goal(self, team: str, position: Position) -> bool:
        x_start, x_end = (
            (self.config.right_goal_x_start, self.config.right_goal_x_end)
            if team == PLAYER
            else (self.config.left_goal_x_start, self.config.left_goal_x_end)
        )
        return x_start <= position[0] <= x_end and self.config.goal_y_start <= position[1] <= self.config.goal_y_end

    def is_goal_cell(self, position: Position) -> bool:
        return (
            self.config.goal_y_start <= position[1] <= self.config.goal_y_end
            and (
                self.config.left_goal_x_start <= position[0] <= self.config.left_goal_x_end
                or self.config.right_goal_x_start <= position[0] <= self.config.right_goal_x_end
            )
        )

    def goal_cells(self, attacking_team: str) -> list[Position]:
        x_start, x_end = (
            (self.config.right_goal_x_start, self.config.right_goal_x_end)
            if attacking_team == PLAYER
            else (self.config.left_goal_x_start, self.config.left_goal_x_end)
        )
        return [
            (x, y)
            for x in range(x_start, x_end + 1)
            for y in range(self.config.goal_y_start, self.config.goal_y_end + 1)
        ]

    def target_goal_cells(self, team: str) -> list[Position]:
        return self.goal_cells(team) if team in TEAMS else []

    def own_goal_cells(self, team: str) -> list[Position]:
        return self.goal_cells(self.opponent(team)) if team in TEAMS else []

    @staticmethod
    def distance_to_cells(position: Position, cells: Iterable[Position]) -> int | None:
        distances = [manhattan(position, cell) for cell in cells]
        return min(distances) if distances else None

    def goal_distance(self, team: str, position: Position, *, own: bool = False) -> int | None:
        cells = self.own_goal_cells(team) if own else self.target_goal_cells(team)
        return self.distance_to_cells(position, cells)

    def movement_direction(self, team: str, origin: Position, destination: Position) -> str:
        before = self.goal_distance(team, origin)
        after = self.goal_distance(team, destination)
        if before is None or after is None:
            return "unknown"
        return "forward" if after < before else "backward" if after > before else "lateral"

    def _score(self, scorer: Character) -> ActionResult:
        self.ai_pass_history.clear()
        self.ai_action_history.clear()
        self.ai_pending_plans.clear()
        self.prepared_move = None
        self.pending_move = None
        state_before = self.report.capture_state(self)
        score_before = dict(self.scores)
        score_position = scorer.position
        self.scores[scorer.team] += 1
        self.scorers.append((scorer.team, scorer.name))
        score_text = f"{self.scores[PLAYER]} - {self.scores[ENEMY]}"
        is_winning_score = self.scores[scorer.team] >= self.config.target_score
        self.report.record_score_event(
            self,
            scorer_id=scorer.char_id,
            scorer_name=scorer.name,
            scoring_team=scorer.team,
            score_before=score_before,
            score_after=dict(self.scores),
            score_position=score_position,
            is_winning_score=is_winning_score,
            score_reset_performed=not is_winning_score,
            state_before=state_before,
            state_after=self.report.capture_state(self),
        )
        self._log(f"得点: {scorer.name} / 現在 {score_text}")
        self._log_ball_hold_transition(scorer.char_id, None, "score")
        if is_winning_score:
            self.finish_match()
            return self._result(True, f"{scorer.name}が得点。試合終了 {score_text}", consumed=True, scored=True, match_ended=True)
        self.restart_team = self.opponent(scorer.team)
        self.restart_prepared = False
        return self._result(
            True,
            f"{scorer.name}が得点。{score_text}",
            consumed=True,
            scored=True,
            details={"conceding_team": self.restart_team, "needs_restart": True},
        )

    def score_holder_in_goal(self, record: bool = True) -> ActionResult | None:
        """Score if the current ball holder is already standing in the opponent goal."""

        if (
            self.config.rule_type != RULE_PURIFICATION
            or self.match_over or self.restart_team is not None or self.pending_move is not None
        ):
            return None
        holder = self.characters.get(self.ball.holder_id or "")
        if holder is None or holder.off_field or holder.position is None:
            return None
        if not self.is_opponent_goal(holder.team, holder.position):
            return None
        if record:
            self._begin_report_action("得点", holder.char_id, "", "score")
        result = self._score(holder)
        result.details.setdefault("action_name", "得点")
        result.details.setdefault("actor_id", holder.char_id)
        result.details.setdefault("actor_name", holder.name)
        if record:
            self._end_report_action(result)
        return result

    def prepare_restart_after_goal(self) -> ActionResult:
        if self.match_over or self.restart_team is None:
            return self._result(False, "得点後の再開準備状態ではありません")
        if self.restart_prepared:
            return self._result(True, "得点後の再配置は完了しています")
        before_state = self.report.capture_state(self)
        self.ai_pass_history.clear()
        self.ai_action_history.clear()
        self.ai_pending_plans.clear()
        self.prepared_move = None
        self.pending_move = None
        scorer_team = self.scorers[-1][0] if self.scorers else ""
        scorer_id = self.ball.holder_id or self.ball.last_holder_id or ""
        self.substitutions_this_restart = {PLAYER: 0, ENEMY: 0}
        for character in self.characters.values():
            character.is_benched_at_restart_start = character.on_bench
        for character in self.characters.values():
            was_knocked_out = character.off_field
            injury_before = character.injury_rate
            hp_recovery = math.ceil(character.max_hp * self.config.score_hp_recovery_rate)
            mana_recovery = math.ceil(character.max_mana * self.config.score_mana_recovery_rate)
            character.hp = hp_recovery if was_knocked_out else min(character.max_hp, character.hp + hp_recovery)
            character.mana = mana_recovery if was_knocked_out else min(character.max_mana, character.mana + mana_recovery)
            character.off_field = False
            character.position = None if character.on_bench else character.initial_position
            character.acted = False
            character.waiting = False
            character.defending = False
            character.keeping = False
            character.disabled = False
            character.return_rounds = 0
            character.knockout_round = 0
            character.extra_action = False
            character.temporary_effects.clear()
            character.reaction_skill = None
            if was_knocked_out:
                self._log(
                    f"得点後復帰: {character.name} / HP {character.hp} / MP {character.mana} / "
                    f"負傷{injury_before}%（維持）"
                )
        self.ball.last_holder_id = self.ball.holder_id
        self.ball.holder_id = None
        self.ball.loose_position = None
        self.ball.last_action = "score_restart_selection"
        self.turn_order = []
        self.turn_index = 0
        self.restart_prepared = True
        self._log("得点後: 初期配置へ戻し、全員の最大HP/MPを設定率で回復しました")
        self.report.record_score_reset(self, before_state, scorer_id, scorer_team)
        return self._result(True, "失点側のボール保持者を選択してください")

    def bench_characters(self, team: str) -> list[Character]:
        return [unit for unit in self.characters.values() if unit.team == team and unit.on_bench]

    def field_characters(self, team: str) -> list[Character]:
        return [unit for unit in self.characters.values() if unit.team == team and not unit.on_bench]

    def can_substitute(self, team: str) -> bool:
        return bool(
            self.restart_prepared
            and self.config.substitution_enabled
            and self.substitutions_this_restart.get(team, 0) < max(0, self.config.substitution_count_per_score)
            and self.bench_characters(team)
            and self.field_characters(team)
        )

    def substitute(self, team: str, field_char_id: str, bench_char_id: str) -> ActionResult:
        if not self.can_substitute(team):
            return self._result(False, "現在は交代できません")
        outgoing = self.characters.get(field_char_id)
        incoming = self.characters.get(bench_char_id)
        if (
            outgoing is None or incoming is None or outgoing.team != team or incoming.team != team
            or outgoing.on_bench or not incoming.on_bench
        ):
            return self._result(False, "交代対象が不正です")
        position = outgoing.initial_position
        outgoing.on_bench = True
        outgoing.position = None
        incoming.on_bench = False
        incoming.initial_position = position
        incoming.position = position
        incoming.off_field = False
        self.substitutions_this_restart[team] += 1
        self._log(f"交代: {outgoing.name} → {incoming.name}")
        return self._result(True, f"{outgoing.name}と{incoming.name}を交代しました")

    def auto_substitute(self, team: str) -> ActionResult:
        if not self.can_substitute(team):
            return self._result(True, "交代しません")
        stable = {char_id: index for index, char_id in enumerate(self.characters)}
        outgoing = max(
            self.field_characters(team),
            key=lambda unit: (unit.injury_markers, 1.0 - unit.hp / max(1, unit.max_hp),
                              1.0 - unit.mana / max(1, unit.max_mana), -stable[unit.char_id]),
        )
        incoming = min(
            self.bench_characters(team),
            key=lambda unit: (unit.injury_markers, -unit.hp / max(1, unit.max_hp),
                              -unit.mana / max(1, unit.max_mana), stable[unit.char_id]),
        )
        if outgoing.injury_markers <= incoming.injury_markers and outgoing.hp >= outgoing.max_hp // 2:
            return self._result(True, "交代しません")
        return self.substitute(team, outgoing.char_id, incoming.char_id)

    def finalize_restart_rosters(self) -> None:
        for character in self.characters.values():
            character.was_benched_last_restart = character.on_bench

    def restart_candidates(self) -> list[Character]:
        if self.restart_team is None:
            return []
        return [
            character for character in self.characters.values()
            if character.team == self.restart_team and not character.off_field and character.position is not None
        ]

    def select_restart_holder(self, char_id: str) -> ActionResult:
        if self.restart_team is None or not self.restart_prepared:
            return self._result(False, "ボール保持者を選択できる状態ではありません")
        candidates = self.restart_candidates()
        character = self.characters.get(char_id)
        if character not in candidates:
            return self._result(False, "そのキャラクターは再開時の保持者にできません")
        team = self.restart_team
        self.set_ball_holder(char_id, "score_restart")
        character.restart_first_actor = True
        self.finalize_restart_rosters()
        self.restart_team = None
        self.restart_prepared = False
        self.round += 1
        self._log(f"得点後再開: {'味方' if team == PLAYER else '敵'} / ボール保持者 {character.name}")
        self.start_round(initial=True)
        return self._result(True, f"{character.name}がボールを持って再開します", ball_changed=True)

    def auto_select_restart_holder(self) -> ActionResult:
        candidates = self.restart_candidates()
        if not candidates:
            return self._result(False, "再開時のボール保持候補がいません")
        stable_order = {char_id: index for index, char_id in enumerate(self.characters)}
        selected = min(
            candidates,
            key=lambda unit: (
                -max(
                    self.effective_stat(unit, "technique") * 2 + self.effective_stat(unit, "power"),
                    self.effective_stat(unit, "technique") * 2 + self.effective_stat(unit, "magic"),
                ),
                unit.injury_markers,
                stable_order[unit.char_id],
            ),
        )
        return self.select_restart_holder(selected.char_id)

    def finish_match(self, reason: str = END_REASON_SCORE, winner: str | None = None) -> None:
        if self.match_over:
            return
        self.match_over = True
        self.end_reason = reason
        player_score = self.scores[PLAYER]
        enemy_score = self.scores[ENEMY]
        self.winner = winner if winner in TEAMS else (
            PLAYER if player_score > enemy_score else ENEMY if enemy_score > player_score else None
        )
        result = "引き分け" if self.winner is None else ("味方の勝利" if self.winner == PLAYER else "敵の勝利")
        reason_name = END_REASON_LABELS.get(reason, reason)
        self._log(f"試合終了: {reason_name} / {result} / {player_score} - {enemy_score} / {self.round}ラウンド")
        self.final_injury_summary = {
            "player_current": sum(unit.injury_markers for unit in self.characters.values() if unit.team == PLAYER),
            "enemy_current": sum(unit.injury_markers for unit in self.characters.values() if unit.team == ENEMY),
            "player_gained": sum(unit.injury_markers_gained for unit in self.characters.values() if unit.team == PLAYER),
            "enemy_gained": sum(unit.injury_markers_gained for unit in self.characters.values() if unit.team == ENEMY),
        }
        self.report.finish(self)
        for character in self.characters.values():
            character.injury_markers = 0
            character.was_benched_last_restart = False
            character.is_benched_at_restart_start = False

    def retire(self, retiring_team: str = PLAYER) -> ActionResult:
        if self.match_over:
            return self._result(False, "試合はすでに終了しています")
        if retiring_team not in TEAMS:
            return self._result(False, "リタイアするチームが不正です")
        self._begin_report_action("リタイア", retiring_team, "", "wait")
        self.prepared_move = None
        self.pending_move = None
        self.restart_team = None
        self.restart_prepared = False
        if self.config.debug_mode:
            self.finish_match(END_REASON_DEBUG_INTERRUPT)
            message = "自由試合を中断しました"
        else:
            self.finish_match(END_REASON_RETIRE, self.opponent(retiring_team))
            message = "試合をリタイアしました"
        result = self._result(True, message, consumed=True, match_ended=True, details={
            "action_name": "リタイア",
            "retiring_team": retiring_team,
            "end_reason": self.end_reason,
        })
        self._end_report_action(result)
        return result

    def result_summary(self) -> dict[str, Any]:
        return {
            "winner": self.winner,
            "draw": self.winner is None,
            "end_reason": self.end_reason,
            "end_reason_name": END_REASON_LABELS.get(self.end_reason, self.end_reason),
            "debug_mode": self.config.debug_mode,
            "player_score": self.scores[PLAYER],
            "enemy_score": self.scores[ENEMY],
            "rounds": self.round,
            "scorers": tuple(self.scorers),
            "injuries": dict(self.final_injury_summary or self.injury_context().get("teams", {})),
            **self.stats,
        }

    def pass_validation(
        self,
        passer_id: str,
        receiver_id: str,
        system: str = "physical",
        range_bonus: int = 0,
    ) -> tuple[bool, str]:
        passer = self.characters.get(passer_id)
        receiver = self.characters.get(receiver_id)
        skill = self.skills.get("normal_pass")
        if passer is None or receiver is None or passer.position is None or receiver.position is None:
            return False, "パス元またはパス先がフィールド上にいません"
        if not self._valid_system(system):
            return False, "パス種別が不正です"
        if passer.acted:
            return False, "行動済みのキャラクターはパスできません"
        if skill and skill.validation_error:
            return False, f"通常パスのデータが不正です: {skill.validation_error}"
        if not self.is_ball_holder(passer):
            return False, "ボールを保持していません"
        if skill and "always" not in skill.allowed_states and self.team_state(passer.team) not in skill.allowed_states:
            return False, f"{self.team_state_name(passer.team)}ではパスできません"
        if passer_id == receiver_id or passer.team != receiver.team or receiver.off_field:
            return False, "生存している味方だけをパス対象にできます"
        distance = manhattan(passer.position, receiver.position)
        pass_range = self.pass_range_for(passer, system, range_bonus)
        if distance > pass_range:
            return False, f"パス距離 {distance} が射程 {pass_range} を超えています"
        return True, "使用可能"

    def pass_preview(
        self,
        passer_id: str,
        receiver_id: str,
        system: str = "physical",
        quick: bool = False,
        range_bonus: int = 0,
        skill_id: str = "",
    ) -> dict[str, Any]:
        passer = self.characters.get(passer_id)
        receiver = self.characters.get(receiver_id)
        if passer is None or receiver is None or passer.position is None or receiver.position is None:
            return {"line": [], "candidates": [], "valid": False, "reason": "パス経路を取得できません"}
        if not self._valid_system(system):
            return {"line": [], "candidates": [], "valid": False, "reason": "パス種別が不正です"}
        line = straight_line_cells(passer.position, receiver.position)
        # The passer's origin never triggers a cut; the receiver cell remains part
        # of the route so defenders covering the target can intercept.
        between = line[1:]
        distance = manhattan(passer.position, receiver.position)
        distance_penalty = max(0, distance - self.config.pass_distance_grace) * self.config.pass_distance_penalty
        skill = self.skills.get(skill_id or ("quick_pass" if quick else "normal_pass"))
        pass_bonus = passer.pass_power + (skill.pass_bonus if quick and skill else 0)
        auxiliary_stat = "magic" if system == "magic" else "power"
        system_value = self.effective_stat(passer, auxiliary_stat)
        system_bonus = system_value
        passer_technique = self.effective_stat(passer, "technique")
        pass_value = passer_technique * 2 + system_bonus + pass_bonus - distance_penalty
        order_index = {char_id: index for index, char_id in enumerate(self.turn_order)}
        candidates: list[dict[str, Any]] = []
        for enemy in self.active_characters(self.opponent(passer.team)):
            assert enemy.position is not None
            if enemy.disabled or not between:
                continue
            reaction_cells: list[tuple[int, Position]] = []
            for index, cell in enumerate(between, start=1):
                dx = abs(enemy.position[0] - cell[0])
                dy = abs(enemy.position[1] - cell[1])
                cut_range = min(max(self.config.field_width, self.config.field_height), max(0, int(enemy.pass_cut_range)))
                reacts = (dx == 0 and dy <= cut_range) or (dy == 0 and dx <= cut_range)
                if reacts:
                    reaction_cells.append((index, cell))
            if not reaction_cells:
                continue
            route_index, reaction_cell = min(reaction_cells, key=lambda item: item[0])
            cut_bonus = enemy.pass_cut
            cut_system_bonus = self.effective_stat(enemy, auxiliary_stat)
            enemy_technique = self.effective_stat(enemy, "technique")
            cut_value = enemy_technique * 2 + cut_system_bonus + cut_bonus
            difference = cut_value - pass_value
            rate = min(
                self.config.pass_cut_max_rate,
                max(self.config.pass_cut_min_rate, self.config.pass_cut_base_rate + difference * self.config.ability_rate_multiplier),
            )
            candidates.append({
                "character_id": enemy.char_id,
                "character_name": enemy.name,
                "route_index": route_index,
                "reaction_cell": reaction_cell,
                "speed": self.effective_stat(enemy, "speed"),
                "turn_order": order_index.get(enemy.char_id, len(order_index)),
                "technique": enemy_technique,
                "system_bonus": cut_system_bonus,
                "pass_cut_bonus": cut_bonus,
                "pass_cut_value": cut_value,
                "ability_difference": difference,
                "base_rate": self.config.pass_cut_base_rate,
                "position_bonus": 0,
                "state_bonus": 0,
                "success_rate": rate,
                "cut_type": "passive",
            })
        def stable_id(value: str) -> tuple[int, str]:
            return (int(value), value) if value.isdigit() else (10**9, value)
        candidates.sort(key=lambda item: (
            int(item["route_index"]),
            -int(item["speed"]),
            int(item["turn_order"]),
            stable_id(str(item["character_id"])),
        ))
        pass_through_rate = 1.0
        for candidate in candidates:
            pass_through_rate *= 1.0 - int(candidate["success_rate"]) / 100.0
        return {
            "valid": bool(line),
            "reason": "使用可能" if line else "パス経路を取得できません",
            "system": system,
            "system_name": self._system_name(system),
            "system_value": system_value,
            "pass_range": self.pass_range_for(passer, system, range_bonus),
            "distance": distance,
            "distance_penalty": distance_penalty,
            "technique": passer_technique,
            "system_bonus": system_bonus,
            "pass_bonus": pass_bonus,
            "pass_value": pass_value,
            "line": line,
            "candidates": candidates,
            "pass_through_rate": round(pass_through_rate * 100, 1),
        }

    def valid_pass_targets(
        self,
        passer_id: str,
        system: str = "physical",
        range_bonus: int = 0,
    ) -> list[Character]:
        passer = self.characters.get(passer_id)
        if passer is None:
            return []
        if not self._valid_system(system):
            return []
        return [
            teammate
            for teammate in self.active_characters(passer.team)
            if teammate.char_id != passer_id
            and self.pass_validation(passer_id, teammate.char_id, system, range_bonus)[0]
        ]

    @staticmethod
    def _effect_of_type(skill: Skill, effect_type: str) -> SkillEffect | None:
        return next((effect for effect in skill.effects if effect.effect_type == effect_type), None)

    def skill_pass_range_bonus(self, skill_id: str) -> int:
        skill = self.skills.get(skill_id)
        effect = self._effect_of_type(skill, "pass") if skill else None
        return max(0, effect.distance) if effect else 0

    def skill_pass_targets(self, passer_id: str, skill_id: str, system: str) -> list[Character]:
        return self.valid_pass_targets(passer_id, system, self.skill_pass_range_bonus(skill_id))

    def skill_pass_preview(
        self,
        passer_id: str,
        receiver_id: str,
        skill_id: str,
        system: str,
    ) -> dict[str, Any]:
        return self.pass_preview(
            passer_id,
            receiver_id,
            system,
            range_bonus=self.skill_pass_range_bonus(skill_id),
            skill_id=skill_id,
        )

    def _resolve_pass_reaction(
        self,
        passer: Character,
        system: str,
        line: list[Position],
    ) -> dict[str, Any] | None:
        between = line[1:-1]
        if not between:
            return None
        order_index = {char_id: index for index, char_id in enumerate(self.turn_order)}
        candidates: list[tuple[int, int, int, float, Character, Position, Skill]] = []
        for character in self.active_characters(self.opponent(passer.team)):
            reaction = character.reaction_skill
            if not reaction or reaction.get("event") != "enemy_pass" or character.disabled or character.position is None:
                continue
            skill = self.skills.get(str(reaction.get("skill_id", "")))
            if skill is None or skill.validation_error:
                continue
            distances = [(manhattan(character.position, cell), index, cell) for index, cell in enumerate(between, start=1)]
            distance, route_index, reaction_cell = min(distances, key=lambda item: (item[0], item[1]))
            if distance > int(reaction.get("range", 0)):
                continue
            auxiliary_stat = "magic" if system == "magic" else "power"
            defense_value = self.effective_stat(character, "technique") * 2 + self.effective_stat(character, auxiliary_stat)
            candidates.append((
                distance,
                -defense_value,
                order_index.get(character.char_id, len(order_index)),
                self.rng.random(),
                character,
                reaction_cell,
                skill,
            ))
        if not candidates:
            return None
        _, _, _, _, defender, reaction_cell, skill = min(candidates, key=lambda item: item[:4])
        pass_success, contest_details = self.contest(
            f"{skill.name}反応",
            passer,
            defender,
            system,
            "technique",
            "physical",
            "technique",
        )
        reaction_success = not pass_success
        defender.reaction_skill = None
        effect_results: list[dict[str, Any]] = []
        timing = "reaction_success" if reaction_success else "reaction_failure"
        for effect in skill.effects:
            if effect.timing == timing:
                effect_results.append(self._apply_common_effect(
                    defender,
                    passer,
                    skill,
                    effect,
                    {"reaction_cell": reaction_cell},
                ))
        self._log(
            f"反応スキル: {defender.name} / {skill.name} / "
            f"{'成功' if reaction_success else '失敗'}"
        )
        return {
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "defender_id": defender.char_id,
            "defender_name": defender.name,
            "reaction_cell": reaction_cell,
            "success": reaction_success,
            "effect_results": effect_results,
            **contest_details,
        }

    def pass_ball(
        self,
        passer_id: str,
        receiver_id: str,
        system: str = "physical",
        quick: bool = False,
        skill_id: str = "",
    ) -> ActionResult:
        passer = self.characters.get(passer_id)
        if passer is None or not self._valid_system(system):
            return self._result(False, "パス種別が不正なためパスできません")
        skill = self.skills.get(skill_id or ("quick_pass" if quick else "normal_pass"))
        range_bonus = self.skill_pass_range_bonus(skill_id) if skill_id else 0
        if skill_id:
            usable, reason = self.can_use_skill(passer_id, skill_id)
            if not usable or skill is None or self._effect_of_type(skill, "pass") is None:
                return self._result(False, reason if not usable else "パススキルデータが不正です")
        valid, reason = self.pass_validation(passer_id, receiver_id, system, range_bonus)
        if not valid:
            return self._result(False, reason)
        passer = self.characters[passer_id]
        receiver = self.characters[receiver_id]
        if quick:
            usable, reason = self.can_use_skill(passer_id, "quick_pass")
            if not usable or skill is None:
                return self._result(False, reason)
        # Calculate from the provisional destination, but do not commit merely by
        # opening/selecting a target.  The shared execution entry point commits
        # immediately before the pass so UI, AI and headless callers behave alike.
        preview = self.pass_preview(
            passer_id,
            receiver_id,
            system,
            quick=quick,
            range_bonus=range_bonus,
            skill_id=skill_id,
        )
        if not preview.get("valid"):
            return self._result(False, str(preview.get("reason", "パス経路を取得できません")))
        move_result: ActionResult | None = None
        if self.action_after_move(passer_id):
            move_result = self.commit_pending_move(passer_id)
            if not move_result.success or move_result.scored:
                return move_result
        self._begin_report_action(skill.name if skill_id and skill else "クイックパス" if quick else "パス", passer_id, receiver_id, "ball")
        if (quick or skill_id) and skill:
            self._consume_skill_resource(passer, skill)
            passer.skill_use_counts[skill.skill_id] = passer.skill_use_counts.get(skill.skill_id, 0) + 1
            if skill.cooldown > 0:
                passer.skill_cooldowns[skill.skill_id] = skill.cooldown
            self.stats["skill_uses"] += 1
        details: dict[str, Any] = {
            "action_name": skill.name if skill_id and skill else "クイックパス" if quick else "パス",
            "actor_id": passer_id,
            "actor_name": passer.name,
            "target_id": receiver_id,
            "target_name": receiver.name,
            **preview,
            "pass_system": system,
            "interceptor": None,
        }
        if move_result:
            details["move_before_pass"] = True
            details["move_path"] = move_result.details.get("path", [])
        if skill_id and skill:
            details.update({
                "skill_id": skill_id,
                "skill_name": skill.name,
                "cooldown": skill.cooldown,
                "use_count": passer.skill_use_counts[skill_id],
                "effect_results": [{
                    "effect_order": self._effect_of_type(skill, "pass").effect_order,
                    "effect_type": "pass",
                    "target_id": receiver_id,
                    "target_name": receiver.name,
                    "range_bonus": range_bonus,
                }],
            })
        reaction = self._resolve_pass_reaction(passer, system, list(preview["line"]))
        if reaction:
            details["reaction_result"] = reaction
            if reaction["success"]:
                details["pass_success"] = False
                details["final_holder_id"] = None
                details["effect_results"] = reaction["effect_results"]
                result = self._result(
                    True,
                    f"{reaction['defender_name']}の{reaction['skill_name']}成功",
                    consumed=True,
                    ball_changed=True,
                    details=details,
                )
                self._end_report_action(result)
                return result
        interceptor: Character | None = None
        results: list[dict[str, Any]] = []
        for candidate in preview["candidates"]:
            candidate_character = self.characters.get(str(candidate["character_id"]))
            if (
                candidate_character is None
                or candidate_character.off_field
                or candidate_character.position is None
                or candidate_character.disabled
            ):
                continue
            roll = self.roll_die(100)
            succeeded = roll <= int(candidate["success_rate"])
            result_data = {**candidate, "roll": roll, "success": succeeded}
            results.append(result_data)
            self._log(
                f"パスカット判定: {candidate_character.name} / 成功率{candidate['success_rate']}% / "
                f"乱数{roll} / {'成功' if succeeded else '失敗'}"
            )
            if succeeded:
                interceptor = candidate_character
                break
        details["pass_cut_results"] = results
        if skill:
            details.update({
                "resource_type": skill.resource_type,
                "resource_cost": skill.resource_cost,
                "required_level": skill.required_level,
            })
        if interceptor is None:
            changed = self.set_ball_holder(receiver_id, skill_id or ("quick_pass" if quick else "pass"))
            details["final_holder_id"] = receiver_id
            details["pass_success"] = True
            self.stats["passes"] += 1
            self._log(f"パス成功: {passer.name}→{receiver.name}")
            scored = self.score_holder_in_goal(record=False)
            if scored:
                scored.details.update(details)
                scored.details["ball_changed"] = changed
                self._end_report_action(scored)
                return scored
            result = self._result(
                True,
                f"{passer.name}から{receiver.name}へパス成功",
                consumed=True,
                ball_changed=changed,
                details=details,
            )
            self._end_report_action(result)
            return result
        changed = self.set_ball_holder(interceptor.char_id, "interception")
        details["interceptor"] = interceptor.name
        details["final_holder_id"] = interceptor.char_id
        details["pass_success"] = False
        self.stats["interceptions"] += 1
        self._log(f"パスカット成功: {interceptor.name} がボールを取得")
        scored = self.score_holder_in_goal(record=False)
        if scored:
            scored.details.update(details)
            scored.details["ball_changed"] = changed
            self._end_report_action(scored)
            return scored
        result = self._result(True, f"{interceptor.name}がパスカット", consumed=True, ball_changed=changed, details=details)
        self._end_report_action(result)
        return result

    def defend(self, char_id: str) -> ActionResult:
        character = self.characters.get(char_id)
        if character is None or character.off_field:
            return self._result(False, "防御できるキャラクターではありません")
        if self.is_ball_holder(character):
            return self._result(False, "ボール保持中は防御を選択できません")
        self._begin_report_action("防御", char_id, "", "wait")
        character.defending = True
        self._log(f"防御: {character.name}")
        result = self._result(True, f"{character.name}が防御状態になりました", consumed=True, details={
            "action_name": "防御", "actor_id": char_id, "actor_name": character.name
        })
        self._end_report_action(result)
        return result

    def keep_ball(self, char_id: str) -> ActionResult:
        character = self.characters.get(char_id)
        if character is None or not self.is_ball_holder(char_id):
            return self._result(False, "ボール保持者だけがボールキープできます")
        self._begin_report_action("ボールキープ", char_id, "", "ball")
        character.keeping = True
        self._log(f"ボールキープ: {character.name} / 保持判定+{self.config.keep_bonus}")
        result = self._result(True, f"{character.name}がボールキープしました", consumed=True, details={
            "action_name": "ボールキープ", "actor_id": char_id, "actor_name": character.name
        })
        self._end_report_action(result)
        return result

    def wait(self, char_id: str) -> ActionResult:
        character = self.characters.get(char_id)
        if character is None or character.off_field:
            return self._result(False, "待機できません")
        if character.acted:
            return self._result(False, "行動済みのキャラクターは待機できません")
        self._begin_report_action("待機", char_id, "", "wait")
        character.waiting = True
        self._log(f"待機: {character.name}")
        result = self._result(True, f"{character.name}が待機しました", consumed=True, details={
            "action_name": "待機", "actor_id": char_id, "actor_name": character.name,
        })
        self._end_report_action(result)
        return result

    def attack_targets(self, attacker_id: str, attack_range: int = 1) -> list[Character]:
        # Compatibility entry point for the old manual attack mode.  Keep it on
        # the same rules path as the CSV-driven basic attack.
        return self.basic_attack_targets(attacker_id)

    def _shield_guard_candidate(self, target: Character) -> Character | None:
        candidates: list[Character] = []
        skill = self.skills.get("shield_guard")
        if not skill:
            return None
        for protector in self.active_characters(target.team):
            if "shield_guard" not in protector.skills:
                continue
            usable, _ = self._skill_prerequisites(protector, skill, allow_reaction=True)
            if not usable:
                continue
            if protector.shield_used_round == self.round or protector.position is None or target.position is None:
                continue
            if manhattan(protector.position, target.position) <= 1:
                candidates.append(protector)
        candidates.sort(key=lambda unit: (
            unit.char_id != target.char_id,
            -self.ability_total(unit, "magic", "power"),
            unit.char_id,
        ))
        return candidates[0] if candidates else None

    def damage_preview(
        self,
        attacker_id: str,
        target_id: str,
        base_power: int | None = None,
        system: str = "physical",
        include_shield: bool = True,
    ) -> dict[str, Any]:
        attacker = self.characters.get(attacker_id)
        target = self.characters.get(target_id)
        if attacker is None or target is None or not self._valid_system(system):
            return {"valid": False, "reason": "ダメージ計算の対象または系統が不正です"}
        if base_power is None:
            skill = self.skills.get("normal_attack")
            base_power = skill.base_effect if skill else 0
        attack_stat = "power" if system == "physical" else "magic"
        attack_coefficient = self.config.physical_attack_coefficient if system == "physical" else self.config.magic_attack_coefficient
        defense_coefficient = self.config.physical_defense_coefficient if system == "physical" else self.config.magic_defense_coefficient
        attack_system_value = self.effective_stat(attacker, attack_stat)
        attack_value = attack_system_value * attack_coefficient
        stamina_component = self.effective_stat(target, "stamina")
        defense_value = stamina_component * defense_coefficient
        difference = attack_value - defense_value
        ability_rate = min(
            self.config.damage_difference_max,
            max(self.config.damage_difference_min, difference * self.config.damage_difference_rate),
        )
        ability_correction = base_power * ability_rate
        basic_damage = max(self.config.minimum_damage, math.floor((base_power + ability_correction) * self.config.global_damage_multiplier))
        predicted_damage = basic_damage
        reductions: list[dict[str, Any]] = []
        if target.defending:
            before = predicted_damage
            predicted_damage = max(
                self.config.minimum_damage,
                math.floor(predicted_damage * self.config.defend_multiplier),
            )
            reductions.append({"name": "防御", "multiplier": self.config.defend_multiplier, "before": before, "after": predicted_damage})
        protector = self._shield_guard_candidate(target) if include_shield else None
        if protector:
            before = predicted_damage
            predicted_damage = max(
                self.config.minimum_damage,
                math.floor(predicted_damage * self.config.shield_multiplier),
            )
            reductions.append({
                "name": "シールドガード",
                "multiplier": self.config.shield_multiplier,
                "before": before,
                "after": predicted_damage,
                "protector_id": protector.char_id,
                "protector_name": protector.name,
            })
        holder_multiplier = self.config.ball_holder_damage_multiplier if self.is_ball_holder(target) else 1.0
        if holder_multiplier != 1.0:
            before = predicted_damage
            predicted_damage = max(self.config.minimum_damage, math.floor(predicted_damage * holder_multiplier))
            reductions.append({
                "name": "ボール保持補正", "multiplier": holder_multiplier,
                "before": before, "after": predicted_damage,
            })
        return {
            "valid": True,
            "system": system,
            "system_name": self._system_name(system),
            "base_power": int(base_power),
            "reference_stat": attack_stat,
            "attack_system_value": attack_system_value,
            "power": self.effective_stat(attacker, "power"),
            "attack_value": attack_value,
            "defense_system_value": stamina_component,
            "stamina": self.effective_stat(target, "stamina"),
            "stamina_component": stamina_component,
            "defense_value": defense_value,
            "ability_difference": difference,
            "difference_multiplier": self.config.damage_difference_multiplier,
            "ability_correction_rate": ability_rate,
            "global_damage_multiplier": self.config.global_damage_multiplier,
            "ability_correction": ability_correction,
            "basic_damage": basic_damage,
            "predicted_damage": predicted_damage,
            "ball_holder_damage_multiplier": holder_multiplier,
            "reductions": reductions,
        }

    def _apply_damage(
        self,
        attacker: Character,
        target: Character,
        base_power: int,
        actor_primary: str,
        actor_secondary: str,
        defender_primary: str,
        defender_secondary: str,
    ) -> tuple[int, int, str | None, dict[str, Any]]:
        system = "magic" if actor_primary == "magic" else "physical"
        calculation = self.damage_preview(
            attacker.char_id,
            target.char_id,
            base_power,
            system,
            include_shield=True,
        )
        shield = self._trigger_shield_guard(target)
        damage = int(calculation["predicted_damage"])
        damage_die = 0
        hp_before = target.hp
        target_was_holder = self.is_ball_holder(target)
        target_position = target.position
        attack_distance = (
            manhattan(attacker.position, target_position)
            if attacker.position is not None and target_position is not None
            else None
        )
        target.hp = max(0, target.hp - damage)
        effective_damage = max(0, hp_before - target.hp)
        knockout_details: dict[str, Any] = {}
        if target.hp <= 0:
            knockout_details = self._knockout(
                target,
                None,
                attacker=attacker,
                attack_distance=attack_distance,
                last_position=target_position,
            )
        calculation["damage"] = damage
        calculation["damage_die"] = 0
        calculation["hp_before"] = hp_before
        calculation["hp_after"] = target.hp
        calculation["effective_damage"] = effective_damage
        calculation["overkill_damage"] = max(0, damage - effective_damage)
        calculation.update(knockout_details)
        return damage, damage_die, shield.name if shield else None, calculation

    def normal_attack(self, attacker_id: str, target_id: str) -> ActionResult:
        attacker = self.characters.get(attacker_id)
        target = self.characters.get(target_id)
        if attacker is None or target is None or target not in self.attack_targets(attacker_id):
            return self._result(False, "隣接する盤面上の敵だけを攻撃できます")
        self._begin_report_action("通常攻撃", attacker_id, target_id, "attack")
        skill = self.skills.get("normal_attack")
        actor_primary = skill.actor_primary_stat if skill else "physical"
        actor_secondary = skill.actor_secondary_stat if skill else "power"
        defender_primary = skill.defender_primary_stat if skill else "physical"
        defender_secondary = skill.defender_secondary_stat if skill else "power"
        details: dict[str, Any] = {
            "action_name": "通常攻撃",
            "actor_id": attacker.char_id,
            "actor_name": attacker.name,
            "target_id": target.char_id,
            "target_name": target.name,
            "automatic_hit": True,
        }
        if skill:
            details.update({
                "resource_type": skill.resource_type,
                "resource_cost": skill.resource_cost,
                "required_level": skill.required_level,
            })
        damage, damage_die, shield, calculation = self._apply_damage(
            attacker,
            target,
            skill.base_effect if skill else 0,
            actor_primary,
            actor_secondary,
            defender_primary,
            defender_secondary,
        )
        details.update({"damage": damage, "damage_die": damage_die, "shield_guard": shield, "damage_calculation": calculation})
        self._log(f"通常攻撃結果: {attacker.name}→{target.name} / ダメージ{damage} / HP {target.hp}/{target.max_hp}")
        result = self._result(
            True,
            f"{target.name}に{damage}ダメージ",
            consumed=True,
            damage=damage,
            ball_changed=bool(calculation.get("ball_drop_position")),
            details=details,
        )
        self._end_report_action(result)
        return result

    def basic_attack_targets(self, attacker_id: str) -> list[Character]:
        skill_id = self.basic_attack_skill_id(attacker_id)
        if skill_id is None or not self.can_use_skill(attacker_id, skill_id)[0]:
            return []
        return self.skill_targets(attacker_id, skill_id)

    def use_basic_attack(self, attacker_id: str, target_id: str) -> ActionResult:
        skill_id = self.basic_attack_skill_id(attacker_id)
        if skill_id is None:
            return self._result(False, "主系統の基本攻撃データがありません")
        return self.use_skill(attacker_id, skill_id, target_id)

    def _trigger_shield_guard(self, target: Character) -> Character | None:
        protector = self._shield_guard_candidate(target)
        if protector is None:
            return None
        skill = self.skills["shield_guard"]
        self._consume_skill_resource(protector, skill)
        protector.shield_used_round = self.round
        self.stats["skill_uses"] += 1
        self._log(f"シールドガード: {protector.name} / {self.skill_cost_text(skill)}")
        return protector

    def _skill_prerequisites(
        self,
        character: Character,
        skill: Skill,
        allow_reaction: bool = False,
    ) -> tuple[bool, str]:
        if not skill.enabled:
            return False, "スキルが無効設定です"
        if skill.validation_error:
            return False, f"スキルデータ不正: {skill.validation_error}"
        if character.off_field or character.position is None:
            return False, "使用者がフィールド上にいません"
        is_common_basic_attack = skill.skill_id in {"normal_physical_attack", "normal_magic_attack"}
        if skill.skill_id not in character.skills and not is_common_basic_attack:
            return False, "このスキルを習得していません"
        if skill.activation not in {"active", "reaction_wait"} and not allow_reaction:
            return False, "反応スキルは条件成立時に自動発動します"
        if character.disabled:
            return False, "行動不能中です"
        if character.acted and not allow_reaction:
            return False, "行動済みです"
        cooldown = character.skill_cooldowns.get(skill.skill_id, 0)
        if cooldown > 0:
            return False, f"クールタイム中（残り{cooldown}）"
        used = character.skill_use_counts.get(skill.skill_id, 0)
        if skill.max_uses > 0 and used >= skill.max_uses:
            return False, f"使用回数上限（{skill.max_uses}回）"
        if skill.activation == "reaction_wait" and character.reaction_skill:
            return False, "別の反応スキルが待機中です"
        if skill.resource_type != "none" and self._resource_value(character, skill) < skill.resource_cost:
            resource_name = "MP"
            return False, f"{resource_name}不足（必要 {skill.resource_cost}）"
        holding = self.is_ball_holder(character)
        state = self.team_state(character.team)
        if (
            skill.command_group != "attack"
            and
            "always" not in skill.allowed_states
            and state not in skill.allowed_states
        ):
            return False, f"{MATCH_STATE_LABELS[state]}では使用できません"
        if holding and not skill.usable_with_ball and skill.command_group != "attack":
            return False, "ボール保持中は使用できません"
        if not holding and not skill.usable_without_ball:
            return False, "ボール非保持中は使用できません"
        return True, "使用可能"

    def can_use_skill(self, char_id: str, skill_id: str) -> tuple[bool, str]:
        character = self.characters.get(char_id)
        skill = self.skills.get(skill_id)
        if character is None:
            return False, "使用者が存在しません"
        if skill is None:
            return False, "スキルデータが不足しています"
        if self.action_after_move(char_id):
            if self._skill_moves_user(skill):
                return False, "移動後は使用できません"
            if not skill.usable_after_move:
                return False, "移動後は使用できません"
        usable, reason = self._skill_prerequisites(character, skill)
        if not usable:
            return False, reason
        effect_types = {effect.effect_type for effect in skill.effects}
        if "heal_hp" in effect_types and skill.target_type == "self" and character.hp >= character.max_hp:
            return False, "HPが最大です"
        if "recover_mp" in effect_types and skill.target_type == "self" and character.mana >= character.max_mana:
            return False, "MPが最大です"
        if "pass" in effect_types and not any(
            self.skill_pass_targets(char_id, skill_id, system)
            for system in ("physical", "magic")
        ):
            return False, "有効なパス対象がいません"
        if "force_move" in effect_types and not self.skill_targets(char_id, skill_id):
            return False, "押し出し先が有効な対象がいません"
        if skill.effect_mode == "common" and not self.skill_targets(char_id, skill_id):
            return False, "射程内に有効な対象がいません"
        if skill_id == "steal" and not self.steal_targets(char_id):
            return False, "隣接する敵ボール保持者がいません"
        if (
            skill_id == "quick_pass"
            and not any(self.valid_pass_targets(char_id, system) for system in ("physical", "magic"))
        ):
            return False, "有効なパス対象がいません"
        if skill_id == "heal" and not self.skill_targets(char_id, "heal"):
            return False, "回復が必要な対象がいません"
        if skill_id == "shadow_step" and not self.reachable_positions(char_id, ignore_zoc=True):
            return False, "移動可能なマスがありません"
        if skill_id in {"push_strike", "elemental_bolt", "breakthrough"} and not self.skill_targets(char_id, skill_id):
            return False, "射程内に有効な対象がいません"
        return True, "使用可能"

    def action_after_move(self, char_id: str) -> bool:
        """Return whether the current action has a provisional move to commit."""

        return bool(self.pending_move and self.pending_move.plan.actor_id == char_id)

    @staticmethod
    def _skill_moves_user(skill: Skill) -> bool:
        """Treat any action that can reposition its user as a move action."""

        if skill.command_group == "move" or skill.action_type in {"movement", "breakthrough"}:
            return True
        return any(
            effect.effect_type == "force_move" and effect.target == "self"
            for effect in skill.effects
        )

    def action_candidates(self, char_id: str, command_group: str) -> list[ActionCandidate]:
        character = self.characters.get(char_id)
        if character is None or command_group not in COMMAND_GROUPS:
            return []
        moved = self.action_after_move(char_id)
        candidates: list[ActionCandidate] = []
        candidates.extend(self._standard_action_candidates(character, command_group, moved))
        skill_ids = list(character.skills)
        if command_group == "attack":
            basic_attack_id = self.basic_attack_skill_id(character)
            skill_ids = [sid for sid in skill_ids if sid not in {"normal_physical_attack", "normal_magic_attack"}]
            if basic_attack_id:
                skill_ids.insert(0, basic_attack_id)
        for index, skill_id in enumerate(skill_ids, start=100):
            if skill_id == "normal_attack":
                continue
            skill = self.skills.get(skill_id)
            if skill is None:
                LOGGER.warning("行動候補のスキルIDが存在しません: %s / %s", character.name, skill_id)
                continue
            if skill.activation == "automatic":
                continue
            if skill.command_group != command_group:
                continue
            usable, reason = self.can_use_skill(char_id, skill_id)
            max_range = skill.range
            if self._effect_of_type(skill, "pass"):
                system = "magic" if skill.actor_secondary_stat == "magic" else "physical"
                max_range = self.pass_range_for(character, system, self.skill_pass_range_bonus(skill_id))
            candidates.append(ActionCandidate(
                action_id=f"skill:{skill_id}",
                name=skill.name,
                description=skill.description,
                command_group=command_group,
                source_type="skill",
                usable=usable,
                reason=reason if not usable else "",
                target_type=skill.target_type,
                min_range=skill.min_range,
                max_range=max_range,
                resource_cost=skill.resource_cost,
                resource_type=skill.resource_type,
                cooldown=character.skill_cooldowns.get(skill_id, 0),
                display_order=skill.display_order or index,
                usable_after_move=skill.usable_after_move,
                skill_id=skill_id,
            ))
        return sorted(candidates, key=lambda item: (item.display_order, item.action_id))

    def _standard_action_candidates(
        self,
        character: Character,
        command_group: str,
        moved: bool,
    ) -> list[ActionCandidate]:
        if character.acted:
            reason = "行動済みです"
        elif character.disabled:
            reason = "行動不能中です"
        else:
            reason = ""
        if command_group == "attack":
            return []
        if command_group == "move":
            reachable = [] if reason or moved else self.reachable_positions(character.char_id)
            usable = bool(reachable)
            return [ActionCandidate(
                action_id="standard:move",
                name="通常移動",
                description="移動可能範囲内のマスへ仮移動する",
                command_group="move",
                source_type="standard",
                usable=usable,
                reason=reason or ("移動済みです" if moved else "" if usable else "移動可能なマスがありません"),
                target_type="cell",
                min_range=0,
                max_range=self.effective_move_range(character),
                resource_cost=0,
                resource_type="none",
                cooldown=0,
                display_order=0,
                usable_after_move=False,
            )]
        if command_group == "ball":
            keep_usable = not reason and self.is_ball_holder(character)
            pass_candidates = []
            for order, system in enumerate(("physical", "magic")):
                targets = [] if reason else self.valid_pass_targets(character.char_id, system)
                pass_usable = bool(targets)
                pass_candidates.append(ActionCandidate(
                    action_id=f"standard:pass:{system}",
                    name="パワーパス" if system == "physical" else "マジックパス",
                    description="テクニックとパワーを使う基本パス" if system == "physical" else "テクニックとマジックを使う基本パス",
                    command_group="ball", source_type="standard", usable=pass_usable,
                    reason=reason or ("" if pass_usable else "有効なパス対象がいません"),
                    target_type="ally", min_range=0, max_range=self.pass_range_for(character, system),
                    resource_cost=0, resource_type="none", cooldown=0,
                    display_order=order, usable_after_move=True,
                ))
            return pass_candidates + [
                ActionCandidate(
                    action_id="standard:keep",
                    name="ボールキープ",
                    description="保持判定を強化して行動を終了する",
                    command_group="ball",
                    source_type="standard",
                    usable=keep_usable,
                    reason=reason or ("" if keep_usable else "ボールを保持していません"),
                    target_type="self",
                    min_range=0,
                    max_range=0,
                    resource_cost=0,
                    resource_type="none",
                    cooldown=0,
                    display_order=10,
                    usable_after_move=True,
                ),
            ]
        if command_group == "wait":
            usable = not reason
            return [ActionCandidate(
                action_id="standard:wait",
                name="通常待機",
                description="行動を終了する",
                command_group="wait",
                source_type="standard",
                usable=usable,
                reason=reason,
                target_type="self",
                min_range=0,
                max_range=0,
                resource_cost=0,
                resource_type="none",
                cooldown=0,
                display_order=0,
                usable_after_move=True,
            )]
        return []

    def steal_targets(self, attacker_id: str) -> list[Character]:
        attacker = self.characters.get(attacker_id)
        holder = self.characters.get(self.ball.holder_id or "")
        if (
            attacker is None or attacker.position is None or self.is_ball_holder(attacker) or holder is None
            or holder.position is None or holder.team == attacker.team or holder.off_field
        ):
            return []
        return [holder] if manhattan(attacker.position, holder.position) <= 1 else []

    def cut_targets(self, attacker_id: str) -> list[Character]:
        attacker = self.characters.get(attacker_id)
        holder = self.characters.get(self.ball.holder_id or "")
        if (
            attacker is None
            or attacker.acted
            or attacker.disabled
            or attacker.off_field
            or attacker.position is None
            or holder is None
            or holder.off_field
            or holder.position is None
            or holder.team == attacker.team
        ):
            return []
        return [holder] if manhattan(attacker.position, holder.position) == 1 else []

    def cut_preview(self, attacker_id: str, target_id: str, system: str) -> dict[str, Any]:
        attacker = self.characters.get(attacker_id)
        target = self.characters.get(target_id)
        if not self._valid_system(system):
            return {"valid": False, "reason": "カット系統が不正です"}
        if attacker is None or target is None or target not in self.cut_targets(attacker_id):
            return {"valid": False, "reason": "隣接する敵ボール保持者だけをカットできます"}
        auxiliary_stat = "magic" if system == "magic" else "power"
        cut_system_bonus = self.effective_stat(attacker, auxiliary_stat)
        keep_system_bonus = self.effective_stat(target, "stamina")
        cut_bonus = attacker.ball_cut
        keep_state_bonus = self.config.keep_bonus if target.keeping else 0
        keep_bonus = target.ball_keep + keep_state_bonus
        attacker_technique = self.effective_stat(attacker, "technique")
        target_technique = self.effective_stat(target, "technique")
        cut_value = attacker_technique * 2 + cut_system_bonus + cut_bonus
        keep_value = target_technique * 2 + keep_system_bonus + keep_bonus
        difference = cut_value - keep_value
        situation_bonus = 0
        rate = self._success_rate(self.config.normal_cut_base_rate, difference, situation_bonus)
        return {
            "valid": True,
            "system": system,
            "system_name": self._system_name(system),
            "actor_id": attacker_id,
            "actor_name": attacker.name,
            "target_id": target_id,
            "target_name": target.name,
            "cut_technique": attacker_technique,
            "cut_system_bonus": cut_system_bonus,
            "cut_bonus": cut_bonus,
            "cut_value": cut_value,
            "keep_technique": target_technique,
            "keep_system_bonus": keep_system_bonus,
            "ball_keep_bonus": target.ball_keep,
            "keep_state_bonus": keep_state_bonus,
            "keep_bonus": keep_bonus,
            "keep_value": keep_value,
            "ability_difference": difference,
            "base_rate": self.config.normal_cut_base_rate,
            "ability_rate_bonus": difference * self.config.ability_rate_multiplier,
            "situation_bonus": situation_bonus,
            "success_rate": rate,
        }

    def cut_ball(self, attacker_id: str, target_id: str, system: str) -> ActionResult:
        self._begin_report_action("通常カット", attacker_id, target_id, "ball")
        preview = self.cut_preview(attacker_id, target_id, system)
        if not preview.get("valid"):
            self._discard_report_action()
            return self._result(False, str(preview.get("reason", "カットできません")))
        roll = self.roll_die(100)
        success = roll <= int(preview["success_rate"])
        changed = self.set_ball_holder(attacker_id, "cut") if success else False
        details = {
            "action_name": f"{preview['system_name']}カット",
            **preview,
            "random_roll": roll,
            "success": success,
            "final_holder_id": attacker_id if success else target_id,
        }
        if success:
            self.stats["steals"] += 1
            self._log(
                f"カット成功: {preview['actor_name']} / 成功率{preview['success_rate']}% / 乱数{roll}"
            )
        else:
            self._log(
                f"カット失敗: {preview['actor_name']} / 成功率{preview['success_rate']}% / 乱数{roll}"
            )
        scored = self.score_holder_in_goal(record=False)
        if scored:
            scored.details.update(details)
            scored.details["ball_changed"] = changed
            self._end_report_action(scored)
            return scored
        result = self._result(
            True,
            "カット成功" if success else "カット失敗",
            consumed=True,
            ball_changed=changed,
            details=details,
        )
        self._end_report_action(result)
        return result

    def use_steal(self, attacker_id: str, target_id: str) -> ActionResult:
        configured = self.skills.get("steal")
        if configured and configured.effect_mode == "common":
            return self.execute_common_skill(attacker_id, "steal", target_id)
        self._begin_report_action("スティール", attacker_id, target_id, "skill")
        attacker = self.characters.get(attacker_id)
        target = self.characters.get(target_id)
        usable, reason = self.can_use_skill(attacker_id, "steal")
        if not usable or attacker is None or target is None or target not in self.steal_targets(attacker_id):
            self._discard_report_action()
            return self._result(False, reason if not usable else "スティール対象が不正です")
        skill = self.skills["steal"]
        self._consume_skill_resource(attacker, skill)
        self.stats["skill_uses"] += 1
        success, details = self.contest(
            "スティール",
            attacker,
            target,
            skill.actor_primary_stat,
            skill.actor_secondary_stat,
            skill.defender_primary_stat,
            skill.defender_secondary_stat,
            offense_bonus=skill.steal_bonus,
            defense_bonus=self.config.keep_bonus if target.keeping else 0,
        )
        changed = False
        if success:
            changed = self.set_ball_holder(attacker_id, "steal")
            self.stats["steals"] += 1
            self._log(f"スティール成功: {attacker.name} がボールを取得")
        else:
            self._log(f"スティール失敗: {target.name} が保持")
        details.update({
            "resource_type": skill.resource_type,
            "resource_cost": skill.resource_cost,
            "required_level": skill.required_level,
        })
        scored = self.score_holder_in_goal(record=False)
        if scored:
            scored.details.update(details)
            scored.details["ball_changed"] = changed
            self._end_report_action(scored)
            return scored
        result = self._result(
            True,
            "スティール成功" if success else "スティール失敗",
            consumed=True,
            ball_changed=changed,
            extra_action=False,
            details=details,
        )
        self._end_report_action(result)
        return result

    def heal(self, healer_id: str, target_id: str) -> ActionResult:
        self._begin_report_action("治癒", healer_id, target_id, "skill")
        healer = self.characters.get(healer_id)
        target = self.characters.get(target_id)
        usable, reason = self.can_use_skill(healer_id, "heal")
        if not usable or healer is None:
            self._discard_report_action()
            return self._result(False, reason)
        skill = self.skills["heal"]
        if target is None or target.off_field or target.position is None or target.team != healer.team:
            self._discard_report_action()
            return self._result(False, "フィールド上の自分または味方だけを回復できます")
        if healer.position is None or manhattan(healer.position, target.position) > skill.range:
            self._discard_report_action()
            return self._result(False, f"ヒールの射程 {skill.range} 外です")
        if target.hp >= target.max_hp:
            self._discard_report_action()
            return self._result(False, "対象のHPは最大です")
        self._consume_skill_resource(healer, skill)
        self.stats["skill_uses"] += 1
        die = self.roll()
        ability = self.ability_components(healer, skill.actor_primary_stat, skill.actor_secondary_stat)
        amount = math.floor((skill.base_effect + int(ability["total"]) // 2 + die) * self.config.healing_multiplier)
        before = target.hp
        target.hp = min(target.max_hp, target.hp + amount)
        actual = target.hp - before
        details = {
            "action_name": "ヒール", "actor_id": healer_id, "actor_name": healer.name,
            "target_id": target_id, "target_name": target.name, "ability": ability,
            "effect_die": die, "healing": actual, "resource_type": skill.resource_type,
            "resource_cost": skill.resource_cost, "required_level": skill.required_level,
            "skill_id": skill.skill_id, "skill_name": skill.name,
            "calculated_hp_recovery": amount,
            "effective_hp_recovery": actual,
            "overheal_hp": max(0, amount - actual),
            "recovery_source": "skill",
        }
        self._log(
            f"ヒール: {healer.name}→{target.name} / {ability['name']}={ability['total']} / "
            f"1D6={die} / HP+{actual} / {self.skill_cost_text(skill)}"
        )
        result = self._result(True, f"{target.name}のHPを{actual}回復", consumed=True, healing=actual, details=details)
        self._end_report_action(result)
        return result

    def _push_destination(self, actor: Character, target: Character) -> Position | None:
        assert actor.position is not None and target.position is not None
        dx = target.position[0] - actor.position[0]
        dy = target.position[1] - actor.position[1]
        if abs(dx) + abs(dy) != 1:
            return None
        destination = (target.position[0] + dx, target.position[1] + dy)
        return destination if self.in_bounds(destination) and self.character_at(destination) is None else None

    def push_strike(self, actor_id: str, target_id: str) -> ActionResult:
        self._begin_report_action("押し込み攻撃", actor_id, target_id, "skill")
        actor = self.characters.get(actor_id)
        target = self.characters.get(target_id)
        usable, reason = self.can_use_skill(actor_id, "push_strike")
        if not usable or actor is None or target is None or target not in self.skill_targets(actor_id, "push_strike"):
            self._discard_report_action()
            return self._result(False, reason if not usable else "対象が不正です")
        skill = self.skills["push_strike"]
        target_was_holder = self.is_ball_holder(target)
        target_origin = target.position
        self._consume_skill_resource(actor, skill)
        self.stats["skill_uses"] += 1
        details: dict[str, Any] = {
            "action_name": "押し込み攻撃", "actor_id": actor_id, "actor_name": actor.name,
            "target_id": target_id, "target_name": target.name, "automatic_hit": True,
            "resource_type": skill.resource_type, "resource_cost": skill.resource_cost,
            "skill_id": skill.skill_id, "skill_name": skill.name,
        }
        damage, die, shield, calculation = self._apply_damage(
            actor, target, skill.base_effect,
            skill.actor_primary_stat, skill.actor_secondary_stat,
            skill.defender_primary_stat, skill.defender_secondary_stat,
        )
        details.update({
            "damage": damage,
            "damage_die": die,
            "shield_guard": shield,
            "damage_calculation": calculation,
        })
        ball_effect = self._resolve_attack_ball_effect(actor, target, skill, target_was_holder, target_origin)
        if ball_effect:
            details["ball_effect"] = ball_effect
        if not target.off_field:
            destination = self._push_destination(actor, target)
            if destination:
                target.position = destination
                details["target_move"] = destination
                self._log(f"押し出し: {target.name}→{destination}")
                scored = self.score_holder_in_goal(record=False)
                if scored:
                    scored.details.update(details)
                    scored.damage = damage
                    self._end_report_action(scored)
                    return scored
        result = self._result(
            True,
            "押し込み攻撃成功",
            consumed=True,
            damage=damage,
            ball_changed=bool(calculation.get("ball_drop_position") or ball_effect),
            details=details,
        )
        self._end_report_action(result)
        return result

    def elemental_bolt(self, actor_id: str, target_id: str) -> ActionResult:
        self._begin_report_action("属性魔法", actor_id, target_id, "skill")
        actor = self.characters.get(actor_id)
        target = self.characters.get(target_id)
        usable, reason = self.can_use_skill(actor_id, "elemental_bolt")
        if not usable or actor is None or target is None or target not in self.skill_targets(actor_id, "elemental_bolt"):
            self._discard_report_action()
            return self._result(False, reason if not usable else "対象が不正です")
        skill = self.skills["elemental_bolt"]
        target_was_holder = self.is_ball_holder(target)
        target_origin = target.position
        self._consume_skill_resource(actor, skill)
        self.stats["skill_uses"] += 1
        details: dict[str, Any] = {
            "action_name": "属性魔法", "actor_id": actor_id, "actor_name": actor.name,
            "target_id": target_id, "target_name": target.name, "automatic_hit": True,
            "resource_type": skill.resource_type, "resource_cost": skill.resource_cost,
            "skill_id": skill.skill_id, "skill_name": skill.name,
        }
        damage, die, shield, calculation = self._apply_damage(
            actor, target, skill.base_effect,
            skill.actor_primary_stat, skill.actor_secondary_stat,
            skill.defender_primary_stat, skill.defender_secondary_stat,
        )
        details.update({
            "damage": damage,
            "damage_die": die,
            "shield_guard": shield,
            "damage_calculation": calculation,
        })
        ball_effect = self._resolve_attack_ball_effect(actor, target, skill, target_was_holder, target_origin)
        if ball_effect:
            details["ball_effect"] = ball_effect
        if not target.off_field:
            target.temporary_effects["movement_down"] = 1
            details["movement_down"] = 1
        result = self._result(
            True,
            "属性魔法成功",
            consumed=True,
            damage=damage,
            ball_changed=bool(calculation.get("ball_drop_position") or ball_effect),
            details=details,
        )
        self._end_report_action(result)
        return result

    def breakthrough_skill(self, actor_id: str, target_id: str) -> ActionResult:
        self._begin_report_action("ブレイクスルー", actor_id, target_id, "skill")
        actor = self.characters.get(actor_id)
        target = self.characters.get(target_id)
        usable, reason = self.can_use_skill(actor_id, "breakthrough")
        if not usable or actor is None or target is None or target not in self.skill_targets(actor_id, "breakthrough"):
            self._discard_report_action()
            return self._result(False, reason if not usable else "対象が不正です")
        skill = self.skills["breakthrough"]
        self._consume_skill_resource(actor, skill)
        self.stats["skill_uses"] += 1
        old_actor_position = actor.position
        old_target_position = target.position
        success, details = self.contest(
            "突破", actor, target,
            skill.actor_primary_stat, skill.actor_secondary_stat,
            skill.defender_primary_stat, skill.defender_secondary_stat,
        )
        details.update({"resource_type": skill.resource_type, "resource_cost": skill.resource_cost})
        details.update({"skill_id": skill.skill_id, "skill_name": skill.name})
        details["contest_success"] = success
        damage = 0
        movement_succeeded = False
        failure_reason = ""
        if success:
            destination = self._push_destination(actor, target)
            if destination is None:
                failure_reason = "移動先が盤外、占有中、または進入不可"
            if not (self.config.prohibit_possession_damage and self.team_state(actor.team) == MATCH_STATE_POSSESSION):
                damage, die, shield, calculation = self._apply_damage(
                    actor, target, skill.base_effect,
                    skill.actor_primary_stat, skill.actor_secondary_stat,
                    skill.defender_primary_stat, skill.defender_secondary_stat,
                )
                details.update({
                    "damage": damage,
                    "damage_die": die,
                    "shield_guard": shield,
                    "damage_calculation": calculation,
                })
            if not target.off_field and destination and old_target_position:
                target.position = destination
                scored = self.score_holder_in_goal(record=False)
                if scored:
                    details["target_move"] = destination
                    details["contest_success"] = True
                    details["target_move_success"] = True
                    details["actor_move_success"] = False
                    details["final_effect_success"] = False
                    details["skill_success"] = False
                    details["effect_failure_reason"] = "対象移動後に得点が成立し、使用者移動は未成立"
                    scored.details.update(details)
                    scored.damage = damage
                    self._end_report_action(scored)
                    return scored
                actor.position = old_target_position
                movement_succeeded = True
                details["target_move"] = destination
                details["actor_move"] = old_target_position
                details["movement_results"] = [
                    {
                        "moved_character_id": actor.char_id,
                        "movement_role": "actor",
                        "movement_type": "skill",
                        "position_before": old_actor_position,
                        "position_after": actor.position,
                        "path": [old_actor_position, actor.position],
                        "distance": 1,
                    },
                    {
                        "moved_character_id": target.char_id,
                        "movement_role": "target",
                        "movement_type": "forced",
                        "position_before": old_target_position,
                        "position_after": target.position,
                        "path": [old_target_position, target.position],
                        "distance": 1,
                    },
                ]
        scored = self.score_holder_in_goal(record=False)
        if scored:
            scored.details.update(details)
            scored.damage = damage
            self._end_report_action(scored)
            return scored
        details["target_move_success"] = movement_succeeded
        details["actor_move_success"] = movement_succeeded
        details["final_effect_success"] = success and movement_succeeded
        details["skill_success"] = details["final_effect_success"]
        details["effect_failure_reason"] = failure_reason
        result = self._result(True, "突破成功" if details["final_effect_success"] else "突破失敗", consumed=True, damage=damage, details=details)
        self._end_report_action(result)
        return result

    def activate_shadow_step(self, actor_id: str) -> ActionResult:
        self._begin_report_action("影渡り", actor_id, actor_id, "skill")
        actor = self.characters.get(actor_id)
        usable, reason = self.can_use_skill(actor_id, "shadow_step")
        if not usable or actor is None:
            self._discard_report_action()
            return self._result(False, reason)
        skill = self.skills["shadow_step"]
        self._consume_skill_resource(actor, skill)
        self.stats["skill_uses"] += 1
        self._log(f"影渡り: {actor.name} / この移動は敵ZOCを無視 / {self.skill_cost_text(skill)}")
        result = self._result(True, "ZOCを無視する移動先を選択してください", consumed=False, details={
            "action_name": "影渡り", "actor_id": actor_id, "actor_name": actor.name,
            "skill_id": skill.skill_id, "skill_name": skill.name,
            "special_move": "ignore_zoc", "resource_type": skill.resource_type,
            "resource_cost": skill.resource_cost,
        })
        self._end_report_action(result)
        return result

    def _generic_skill_targets(self, user: Character, skill: Skill) -> list[Character]:
        if user.position is None:
            return []
        effect_types = {effect.effect_type for effect in skill.effects}
        if "pass" in effect_types:
            system = "magic" if skill.actor_secondary_stat == "magic" else "physical"
            targets = {
                target.char_id: target
                for target in self.skill_pass_targets(user.char_id, skill.skill_id, system)
            }
            return list(targets.values())
        if skill.target_type == "self":
            candidates = [user]
        elif skill.target_type in {"self_or_ally", "ally"}:
            candidates = self.active_characters(user.team)
            if skill.target_type == "ally":
                candidates = [candidate for candidate in candidates if candidate.char_id != user.char_id]
        elif skill.target_type in {"enemy", "adjacent_enemy"}:
            candidates = self.active_characters(self.opponent(user.team))
        elif skill.target_type == "adjacent_enemy_holder":
            candidates = self.steal_targets(user.char_id)
        else:
            return []
        result: list[Character] = []
        max_range = max(skill.min_range, skill.range)
        for candidate in candidates:
            if candidate.off_field or candidate.position is None:
                continue
            distance = manhattan(user.position, candidate.position)
            if not skill.min_range <= distance <= max_range:
                continue
            if "heal_hp" in effect_types and candidate.hp >= candidate.max_hp:
                continue
            if "recover_mp" in effect_types and candidate.mana >= candidate.max_mana:
                continue
            if "force_move" in effect_types and self._push_destination(user, candidate) is None:
                continue
            result.append(candidate)
        return result

    def _effect_amount(self, source: Character, effect: SkillEffect) -> int:
        amount = float(effect.base_value)
        for stat, rate in ((effect.stat1, effect.rate1), (effect.stat2, effect.rate2)):
            if stat:
                amount += self.effective_stat(source, stat) * rate
        if effect.effect_type == "heal_hp":
            amount *= self.config.healing_multiplier
        return max(0, math.floor(amount))

    def _effect_references(self, source: Character, effect: SkillEffect) -> list[dict[str, Any]]:
        return [
            {"stat": stat, "value": self.effective_stat(source, stat), "rate": rate}
            for stat, rate in ((effect.stat1, effect.rate1), (effect.stat2, effect.rate2))
            if stat
        ]

    def _apply_common_damage(
        self,
        source: Character,
        target: Character,
        skill: Skill,
        effect: SkillEffect,
    ) -> tuple[int, dict[str, Any], str | None]:
        system = skill.skill_system if skill.skill_system in {"physical", "magic"} else "physical"
        damage, _, shield, calculation = self._apply_damage(
            source,
            target,
            effect.base_value,
            system,
            "power",
            system,
            "stamina",
        )
        return damage, calculation, shield

    def common_skill_damage_preview(
        self,
        source_id: str,
        target_id: str,
        skill_id: str,
    ) -> dict[str, Any]:
        """Preview a CSV damage effect through the same path used at resolution."""

        skill = self.skills.get(skill_id)
        effect = self._effect_of_type(skill, "damage") if skill else None
        if skill is None or skill.effect_mode != "common" or effect is None:
            return {"valid": False, "reason": "ダメージ効果を持つ共通スキルではありません"}
        system = skill.skill_system if skill.skill_system in {"physical", "magic"} else "physical"
        return self.damage_preview(source_id, target_id, effect.base_value, system)

    def action_preview(self, source_id: str, skill_id: str, target_id: str | None = None) -> dict[str, Any]:
        """Build a side-effect-free confirmation preview from the shared rule data."""

        source = self.characters.get(source_id)
        skill = self.skills.get(skill_id)
        selected = self.characters.get(target_id or source_id)
        if source is None or skill is None or selected is None:
            return {"valid": False, "reason": "使用者、行動、または対象が存在しません"}
        usable, reason = self.can_use_skill(source_id, skill_id)
        target_valid = selected in self.skill_targets(source_id, skill_id)
        if not target_valid:
            usable, reason = False, "対象が不正または射程外です"
        base: dict[str, Any] = {
            "valid": True, "executable": usable, "reason": "" if usable else reason,
            "preview_type": "other", "action_id": skill_id, "action_name": skill.name,
            "actor_id": source_id, "target_id": selected.char_id,
            "self_target": source.char_id == selected.char_id,
            "mana_cost": skill.resource_cost if skill.resource_type == "mana" else 0,
            "mana_after": max(0, source.mana - (skill.resource_cost if skill.resource_type == "mana" else 0)),
            "description": skill.description, "details": [], "confirm_label": "行動を決定",
        }
        damage_effect = self._effect_of_type(skill, "damage")
        if damage_effect is not None:
            damage = self.common_skill_damage_preview(source_id, selected.char_id, skill_id)
            if not damage.get("valid"):
                return {**base, "valid": False, "executable": False, "reason": str(damage.get("reason", "確認できません"))}
            hp_after = max(0, selected.hp - int(damage["predicted_damage"]))
            ball = self.ball_effect_preview(source_id, selected.char_id, skill_id)
            base.update({
                "preview_type": "damage", "title": "攻撃予測", "primary_label": "予測ダメージ",
                "primary_value": int(damage["predicted_damage"]), "before_value": selected.hp,
                "after_value": hp_after, "knockout": hp_after <= 0, "damage": damage,
                "ball_effect": ball if ball.get("active") else None, "confirm_label": "攻撃を決定",
            })
            return base
        modify = self._effect_of_type(skill, "modify_stat")
        if modify is not None:
            stat = modify.aux1 or modify.stat1
            value = -modify.base_value if modify.aux2 == "subtract" else modify.base_value
            before = self.effective_stat(selected, stat)
            existing = [
                item for item in selected.temporary_effects.get("stat_modifiers", [])
                if isinstance(item, dict) and item.get("source_skill_id") == skill_id and item.get("stat") == stat
            ]
            # Resolution replaces the same skill/stat modifier before appending the new value.
            old_value = sum(int(item.get("value", 0)) for item in existing if item.get("mode", "add") == "add")
            after = before - old_value + value
            after = min(self.config.ability_max, max(self.config.ability_min, after))
            weakening = value < 0
            base.update({
                "preview_type": "debuff" if weakening else "buff",
                "title": "弱体予測" if weakening else "強化予測",
                "primary_label": f"{STAT_LABELS.get(stat, stat)}{value:+d}", "primary_value": value,
                "stat": stat, "stat_name": STAT_LABELS.get(stat, stat), "before_value": before,
                "after_value": after, "duration": max(1, modify.duration), "effect_active": bool(existing),
                "stack_rule": "同じ効果は重複せず、効果値と残り回数を更新",
                "confirm_label": "弱体化を決定" if weakening else "強化を決定",
            })
            return base
        return base

    def _apply_common_effect(
        self,
        user: Character,
        selected: Character,
        skill: Skill,
        effect: SkillEffect,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        target = user if effect.target == "self" else selected
        result: dict[str, Any] = {
            "effect_order": effect.effect_order,
            "effect_type": effect.effect_type,
            "target_id": target.char_id,
            "target_name": target.name,
            "references": self._effect_references(user, effect),
        }
        if effect.effect_type == "damage":
            damage, calculation, shield = self._apply_common_damage(user, target, skill, effect)
            result.update({
                "damage": damage,
                "damage_calculation": calculation,
                "shield_guard": shield,
                # Damage effects use the shared physical/magic ability difference.
                # CSV reference rates remain available to non-damage effects only.
                "references": [],
            })
        elif effect.effect_type == "modify_stat":
            stat = effect.aux1 or effect.stat1
            modifiers = target.temporary_effects.setdefault("stat_modifiers", [])
            assert isinstance(modifiers, list)
            modifiers[:] = [
                item for item in modifiers
                if not (isinstance(item, dict) and item.get("source_skill_id") == skill.skill_id and item.get("stat") == stat)
            ]
            value = -effect.base_value if effect.aux2 == "subtract" else effect.base_value
            modifier = {
                "stat": stat,
                "mode": "percent" if effect.aux2 == "percent" else "add",
                "value": value,
                "remaining": max(1, effect.duration),
                "source_skill_id": skill.skill_id,
                "source_skill_name": skill.name,
                "skip_decay": True,
            }
            modifiers.append(modifier)
            result.update({"stat": stat, "value": value, "duration": modifier["remaining"]})
        elif effect.effect_type == "heal_hp":
            amount = self._effect_amount(user, effect)
            before = target.hp
            target.hp = min(target.max_hp, target.hp + amount)
            result["calculated_hp_recovery"] = amount
            result["healing"] = target.hp - before
            result["effective_hp_recovery"] = target.hp - before
            result["overheal_hp"] = max(0, amount - (target.hp - before))
            result["recovery_source"] = "skill"
        elif effect.effect_type == "recover_mp":
            amount = self._effect_amount(user, effect)
            before = target.mana
            target.mana = min(target.max_mana, target.mana + amount)
            result["calculated_mp_recovery"] = amount
            result["mana_recovery"] = target.mana - before
            result["effective_mp_recovery"] = target.mana - before
            result["overheal_mp"] = max(0, amount - (target.mana - before))
            result["recovery_source"] = "skill"
        elif effect.effect_type == "prepare_reaction":
            target.reaction_skill = {
                "skill_id": skill.skill_id,
                "skill_name": skill.name,
                "event": effect.aux1 or "enemy_pass",
                "range": max(0, effect.distance),
                "remaining_uses": max(1, effect.base_value or 1),
            }
            result["reaction"] = copy.deepcopy(target.reaction_skill)
        elif effect.effect_type == "drop_ball":
            position = context.get("reaction_cell") or target.position
            if not isinstance(position, tuple):
                raise ValueError("ボール落下位置を取得できません")
            self.drop_ball(position, skill.skill_id)
            result["ball_drop_position"] = position
            result["ball_changed"] = True
        elif effect.effect_type == "steal_ball":
            changed = self.set_ball_holder(user.char_id, skill.skill_id)
            result.update({"ball_changed": changed, "holder_id": user.char_id})
        elif effect.effect_type == "force_move":
            destination = self._push_destination(user, target)
            if destination is None:
                raise ValueError("押し出し先が無効です")
            origin = target.position
            target.position = destination
            result.update({"origin": origin, "target_move": destination, "distance": effect.distance})
            self._log(f"強制移動: {target.name} {origin}→{destination}")
        elif effect.effect_type == "guard":
            target.defending = True
            result["defending"] = True
        else:
            raise ValueError(f"未実装の効果種別: {effect.effect_type}")
        return result

    def execute_common_skill(
        self,
        user_id: str,
        skill_id: str,
        target_id: str | None = None,
    ) -> ActionResult:
        user = self.characters.get(user_id)
        skill = self.skills.get(skill_id)
        if skill_id == "steal" and skill is not None and not any(
            effect.effect_type == "steal_ball" and effect.timing == "success"
            for effect in skill.effects
        ):
            return self._result(False, "スティール効果の設定を取得できません")
        usable, reason = self.can_use_skill(user_id, skill_id)
        if not usable or user is None or skill is None or skill.effect_mode != "common":
            return self._result(False, reason if not usable else "共通スキルデータが不正です")
        selected = self.characters.get(target_id or user_id)
        if skill_id == "steal" and user is not None and selected is not None:
            if selected.team == user.team:
                return self._result(False, "敵チームのボール保持者だけを対象にできます")
            if selected.disabled or selected.off_field:
                return self._result(False, "対象は戦闘不能です")
            if not self.is_ball_holder(selected):
                return self._result(False, "対象がボールを保持していません")
            if user.position is None or selected.position is None or manhattan(user.position, selected.position) > skill.range:
                return self._result(False, "対象がスティール範囲外です")
        targets = self.skill_targets(user_id, skill_id)
        if selected is None or selected not in targets:
            return self._result(False, "対象が不正または射程外です")
        self._begin_report_action(skill.name, user_id, selected.char_id, skill.command_group)
        character_snapshots = {char_id: copy.deepcopy(character.__dict__) for char_id, character in self.characters.items()}
        ball_snapshot = copy.deepcopy(self.ball.__dict__)
        stats_snapshot = copy.deepcopy(self.stats)
        try:
            target_was_holder = self.is_ball_holder(selected)
            target_origin = selected.position
            self._consume_skill_resource(user, skill)
            user.skill_use_counts[skill_id] = user.skill_use_counts.get(skill_id, 0) + 1
            skill_success = True
            details: dict[str, Any] = {
                "action_name": skill.name,
                "skill_id": skill_id,
                "skill_name": skill.name,
                "actor_id": user_id,
                "actor_name": user.name,
                "target_id": selected.char_id,
                "target_name": selected.name,
                "resource_type": skill.resource_type,
                "resource_cost": skill.resource_cost,
                "category": skill.category,
            }
            if skill.use_dice and skill.activation != "reaction_wait":
                if skill_id == "steal":
                    actor_value = self.effective_stat(user, "technique")
                    target_value = self.effective_stat(selected, "technique")
                    difference = actor_value - target_value
                    rate_bonus = difference * self.config.ability_rate_multiplier
                    rate = min(self.config.steal_max_rate, max(self.config.steal_min_rate, self.config.steal_base_rate + rate_bonus))
                    roll = self.roll_die(100)
                    skill_success = roll <= rate
                    contest_details = {
                        "action_name": skill.name, "actor_id": user.char_id,
                        "actor_name": user.name, "target_id": selected.char_id,
                        "target_name": selected.name, "actor_stat": "technique",
                        "actor_stat_name": "テクニック", "actor_value": actor_value,
                        "target_stat": "technique", "target_stat_name": "テクニック",
                        "target_value": target_value, "ability_difference": difference,
                        "base_rate": self.config.steal_base_rate,
                        "ability_rate_bonus": rate_bonus, "success_rate": rate,
                        "random_roll": roll,
                    }
                else:
                    skill_success, contest_details = self.contest(
                        skill.name, user, selected, skill.actor_primary_stat, skill.actor_secondary_stat,
                        skill.defender_primary_stat, skill.defender_secondary_stat,
                        offense_bonus=skill.steal_bonus,
                        defense_bonus=self.config.keep_bonus if selected.keeping else 0,
                    )
                contest = contest_details.get("contest", {})
                if (
                    skill.tie_rule == "actor"
                    and isinstance(contest, dict)
                    and contest.get("offense_total") == contest.get("defense_total")
                ):
                    skill_success = True
                    contest["success"] = True
                details.update(contest_details)
                details.update({"skill_id": skill_id, "skill_name": skill.name, "category": skill.category})
            effect_results: list[dict[str, Any]] = []
            context: dict[str, Any] = {}
            for effect in skill.effects:
                should_apply = effect.timing in {"use", "always", "end"}
                should_apply = should_apply or (effect.timing == "success" and skill_success)
                should_apply = should_apply or (effect.timing == "failure" and not skill_success)
                if should_apply:
                    effect_results.append(self._apply_common_effect(user, selected, skill, effect, context))
            if skill.cooldown > 0:
                user.skill_cooldowns[skill_id] = skill.cooldown
            self.stats["skill_uses"] += 1
            if any(result.get("holder_id") for result in effect_results):
                self.stats["steals"] += 1
            details["skill_success"] = skill_success
            details["effect_results"] = effect_results
            if skill_id == "steal":
                details["holder_before"] = selected.char_id
                details["holder_after"] = self.ball.holder_id
                details["ball_changed"] = self.ball.holder_id == user.char_id
                if skill_success and self.ball.holder_id != user.char_id:
                    raise RuntimeError("スティール成功後のボール保持者変更に失敗しました")
                self._log(
                    f"スティール{'成功' if skill_success else '失敗'}: "
                    f"{user.name}←{selected.name} / 保持者:{selected.name}→"
                    f"{user.name if skill_success else selected.name}"
                )
            damage = sum(int(result.get("damage", 0)) for result in effect_results)
            healing = sum(int(result.get("healing", 0)) for result in effect_results)
            mana_recovery = sum(int(result.get("mana_recovery", 0)) for result in effect_results)
            first_damage = next((result for result in effect_results if result.get("damage_calculation")), None)
            if first_damage:
                details["damage"] = damage
                details["damage_calculation"] = first_damage["damage_calculation"]
                for key in (
                    "ball_drop_reason", "ball_drop_position", "attack_distance",
                    "previous_ball_holder_id", "final_ball_holder_id",
                ):
                    if key in first_damage["damage_calculation"]:
                        details[key] = first_damage["damage_calculation"][key]
                ball_effect = self._resolve_attack_ball_effect(
                    user, selected, skill, target_was_holder, target_origin
                )
                if ball_effect:
                    details["ball_effect"] = ball_effect
            details["healing"] = healing
            details["mana_recovery"] = mana_recovery
            details["cooldown"] = skill.cooldown
            details["use_count"] = user.skill_use_counts[skill_id]
            self._log(
                f"スキル: {user.name} / {skill.name} / {self.skill_cost_text(skill)} / "
                f"{'成功' if skill_success else '失敗'} / 効果{len(effect_results)}件"
            )
            scored = self.score_holder_in_goal(record=False)
            if scored:
                scored.details.update(details)
                scored.details["ball_changed"] = any(
                    result.get("ball_changed") for result in effect_results
                ) or bool(details.get("ball_drop_position")) or bool(details.get("ball_effect"))
                self._end_report_action(scored)
                return scored
            ball_changed = any(result.get("ball_changed") for result in effect_results) or bool(
                details.get("ball_drop_position") or details.get("ball_effect")
            )
            result = self._result(
                True,
                f"{skill.name}{'成功' if skill_success else '失敗'}",
                consumed=skill.ends_action,
                damage=damage,
                healing=healing,
                ball_changed=ball_changed,
                details=details,
            )
            self._end_report_action(result)
            return result
        except Exception as exc:
            for char_id, snapshot in character_snapshots.items():
                self.characters[char_id].__dict__.clear()
                self.characters[char_id].__dict__.update(snapshot)
            self.ball.__dict__.clear()
            self.ball.__dict__.update(ball_snapshot)
            self.stats = stats_snapshot
            LOGGER.exception("共通スキル %s の実行に失敗したため状態を復元しました", skill_id)
            self._discard_report_action()
            return self._result(False, f"スキル実行エラー: {exc}")

    def skill_targets(self, user_id: str, skill_id: str) -> list[Character]:
        user = self.characters.get(user_id)
        if user is None or user.position is None:
            return []
        skill = self.skills.get(skill_id)
        if skill and skill.effect_mode == "common":
            return self._generic_skill_targets(user, skill)
        if skill_id == "steal":
            return self.steal_targets(user_id)
        if skill_id == "quick_pass":
            system = "magic" if skill.actor_secondary_stat == "magic" else "physical"
            return self.valid_pass_targets(user_id, system)
        if skill_id == "heal":
            skill = self.skills.get(skill_id)
            return [] if not skill else [
                character for character in self.active_characters(user.team)
                if character.position is not None and manhattan(user.position, character.position) <= skill.range
                and character.hp < character.max_hp
            ]
        if skill_id in {"push_strike", "breakthrough"}:
            return [
                target for target in self.active_characters(self.opponent(user.team))
                if target.position is not None and manhattan(user.position, target.position) <= 1
            ]
        if skill_id == "elemental_bolt":
            skill = self.skills.get(skill_id)
            return [] if not skill else [
                target for target in self.active_characters(self.opponent(user.team))
                if target.position is not None and manhattan(user.position, target.position) <= skill.range
            ]
        if skill_id == "shadow_step":
            return [user]
        return []

    def use_skill(
        self,
        user_id: str,
        skill_id: str,
        target_id: str | None = None,
        system: str | None = None,
    ) -> ActionResult:
        skill = self.skills.get(skill_id)
        if skill and skill.effect_mode == "common":
            if self._effect_of_type(skill, "pass"):
                if not target_id or not self._valid_system(system or ""):
                    return self._result(False, "パス対象またはパス系統が不正です")
                return self.pass_ball(user_id, target_id, system or "physical", skill_id=skill_id)
            return self.execute_common_skill(user_id, skill_id, target_id)
        if skill_id == "steal" and target_id:
            return self.use_steal(user_id, target_id)
        if skill_id == "heal" and target_id:
            return self.heal(user_id, target_id)
        if skill_id == "quick_pass" and target_id:
            return self.pass_ball(user_id, target_id, quick=True)
        if skill_id == "push_strike" and target_id:
            return self.push_strike(user_id, target_id)
        if skill_id == "elemental_bolt" and target_id:
            return self.elemental_bolt(user_id, target_id)
        if skill_id == "breakthrough" and target_id:
            return self.breakthrough_skill(user_id, target_id)
        if skill_id == "shadow_step":
            return self.activate_shadow_step(user_id)
        return self._result(False, "対象またはスキルが不正です")

    def _knockout(
        self,
        target: Character,
        ball_recipient: Character | None = None,
        *,
        attacker: Character | None = None,
        attack_distance: int | None = None,
        last_position: Position | None = None,
    ) -> dict[str, Any]:
        recorded_position = last_position or target.position
        if recorded_position is None:
            recorded_position = self.config.ball_position
            LOGGER.error(
                "戦闘不能者 %s の最終位置を取得できないため既定位置 %s へボールを落とします",
                target.char_id,
                recorded_position,
            )
        last_position = recorded_position
        held_ball = self.is_ball_holder(target)
        previous_holder_id = self.ball.holder_id
        target.hp = 0
        # Injury is match-local and intentionally does not alter master/save stats.
        injury_before = target.injury_rate
        target.injury_rate = min(
            self.config.injury_max_percent,
            max(0, target.injury_rate) + self.config.injury_gain_percent,
        )
        target.injury_markers = math.ceil(target.injury_rate / max(1, self.config.injury_gain_percent))
        target.injury_markers_gained += 1
        target.off_field = True
        target.position = None
        target.acted = True
        target.waiting = False
        target.defending = False
        target.keeping = False
        target.disabled = False
        target.knockout_round = self.round
        target.return_rounds = 0
        target.extra_action = False
        target.temporary_effects.clear()
        target.reaction_skill = None
        if held_ball:
            if ball_recipient and not ball_recipient.off_field and ball_recipient.position is not None:
                self.set_ball_holder(ball_recipient.char_id, "knockout_transfer")
                ball_text = f"{ball_recipient.name}へボール移動"
            else:
                drop_action = "ranged_knockout_drop" if attack_distance is not None and attack_distance >= 2 else "knockout_drop"
                drop_position = self.loose_ball_drop_position(last_position)
                if drop_position is None:
                    drop_position = last_position
                    LOGGER.warning("戦闘不能ドロップの配置先がないため最終位置を使用します")
                self.drop_ball(drop_position, drop_action)
                ball_text = f"{drop_position}へボール移動"
                if drop_action == "ranged_knockout_drop":
                    attacker_name = attacker.name if attacker else "不明"
                    self._log(
                        "遠距離撃破ドロップ: "
                        f"理由=遠距離攻撃 / 攻撃者={attacker_name} / 戦闘不能者={target.name} / "
                        f"距離={attack_distance} / 落下位置={drop_position} / "
                        f"変更前保持者={previous_holder_id or 'なし'} / 変更後保持者=なし"
                    )
        else:
            ball_text = "ボール移動なし"
        self._log(
            f"戦闘不能: 攻撃者={attacker.name if attacker else '不明'} / 対象={target.name} / "
            f"ラウンド={self.round} / 負傷{injury_before}%→{target.injury_rate}% / "
            f"保持中={'はい' if held_ball else 'いいえ'} / {ball_text} / 得点後再配置まで離脱"
        )
        return {
            "ball_drop_reason": "ranged_knockout" if held_ball and attack_distance is not None and attack_distance >= 2 else "knockout" if held_ball else "none",
            "ball_drop_position": self.ball.loose_position if held_ball else None,
            "forced_knockout_drop": bool(held_ball and ball_recipient is None),
            "attack_distance": attack_distance,
            "previous_ball_holder_id": previous_holder_id,
            "final_ball_holder_id": self.ball.holder_id,
            "knockout_target_id": target.char_id,
            "knockout_attacker_id": attacker.char_id if attacker else None,
            "knockout_round": self.round,
            "injury_rate_before": injury_before,
            "injury_rate_after": target.injury_rate,
            "target_was_ball_holder": held_ball,
        }

    def return_positions(self, team: str) -> list[Position]:
        own_goal_x = self.config.left_goal_x_end if team == PLAYER else self.config.right_goal_x_start
        nearby_x = own_goal_x + 1 if team == PLAYER else own_goal_x - 1
        center_y = (self.config.goal_y_start + self.config.goal_y_end) / 2
        candidates = [
            (x, y) for x in (own_goal_x, nearby_x) for y in range(self.config.field_height)
            if self.character_at((x, y)) is None
        ]
        candidates.sort(key=lambda position: (abs(position[1] - center_y), abs(position[0] - own_goal_x), position[1]))
        return candidates

    def _result(self, success: bool, message: str, **kwargs: Any) -> ActionResult:
        result = ActionResult(success=success, message=message, **kwargs)
        self.last_result = result
        if not success:
            self._log(f"操作不可: {message}")
        return result

    def ai_value(self, character: Character, key: str) -> int:
        return int(character.ai_settings.get(key, AI_VALUE_DEFAULT))

    def _ai_bias(self, character: Character, key: str, scale: float = 1.0) -> float:
        return (self.ai_value(character, key) - AI_VALUE_DEFAULT) * scale

    @staticmethod
    def _attach_path(result: ActionResult, move_result: ActionResult | None) -> ActionResult:
        if move_result and move_result.details.get("path"):
            result.details["path"] = move_result.details["path"]
            result.details.setdefault("actor_id", move_result.details.get("actor_id"))
        return result

    def ai_ball_state(self, actor: Character) -> str:
        holder = self.characters.get(self.ball.holder_id or "")
        if holder is None:
            return "loose_ball"
        if holder.char_id == actor.char_id:
            return "self_possession"
        return "ally_possession" if holder.team == actor.team else "enemy_possession"

    def ai_role(self, actor: Character) -> str:
        role = actor.ai_role_id.strip().lower()
        if role in AI_ROLE_NAMES:
            return role
        if role:
            LOGGER.warning("%s のAI役割IDが不正なため自動判定します: %s", actor.name, role)
        support_purposes = {"heal", "mp_support"}
        if any(self.skills.get(skill_id) and self.skills[skill_id].purpose_tag in support_purposes for skill_id in actor.skills):
            role = "support"
        elif actor.technique >= actor.power:
            role = "technique"
        else:
            role = "power"
        actor.ai_role_id = role
        actor.ai_role_name = AI_ROLE_NAMES[role]
        return role

    def _ensure_ai_profile(self, actor: Character) -> None:
        """Resolve both teams through the same profile data after role detection."""
        if actor.ai_profile_id and actor.ai_profile_id in self.ai_profiles:
            profile_id = actor.ai_profile_id
            source = actor.ai_profile_source if actor.ai_profile_source != "fallback" else ("enemy_master" if actor.team == ENEMY else "character")
        else:
            role_profile = {"power": "attack", "support": "healer", "technique": "steal"}
            profile_id = role_profile.get(self.ai_role(actor), DEFAULT_AI_PROFILE_ID)
            source = "role_mapping"
            if profile_id not in self.ai_profiles:
                LOGGER.warning("%s の役割対応AIプロフィール %s がないため標準型を使用します", actor.name, profile_id)
                profile_id = DEFAULT_AI_PROFILE_ID
                source = "fallback"
        profile = self.ai_profiles.get(profile_id) or self.ai_profiles.get(DEFAULT_AI_PROFILE_ID)
        if profile is None:
            actor.ai_profile_id = DEFAULT_AI_PROFILE_ID
            actor.ai_profile_name = "標準型"
            actor.ai_profile_source = "fallback"
            return
        actor.ai_profile_id = profile.ai_profile_id
        actor.ai_profile_name = profile.name
        actor.ai_profile_description = profile.description
        actor.ai_settings = effective_ai_values(profile)
        actor.ai_level = clamp_ai_level(getattr(actor, "ai_level", AI_LEVEL_DEFAULT))
        actor.ai_profile_source = source

    def _resolve_loose_ball_recovery(self, team: str) -> str:
        ball = self.ball.loose_position
        if ball is None:
            self.loose_ball_recovery_actor_id[team] = ""
            self.loose_ball_recovery_position[team] = None
            return ""
        existing_id = self.loose_ball_recovery_actor_id.get(team, "")
        existing = self.characters.get(existing_id)
        if self.loose_ball_recovery_position.get(team) == ball and existing and existing.position is not None and not existing.disabled and not existing.off_field:
            return existing_id
        candidates = [unit for unit in self.active_characters(team) if unit.position is not None and not unit.disabled and not unit.off_field]
        preferred = [unit for unit in candidates if self.ai_value(unit, "loose_ball_priority") > 0]
        pool = preferred or candidates
        if not pool:
            self.loose_ball_recovery_actor_id[team] = ""
            return ""
        def suitability(unit: Character) -> tuple[float, int, int, int, str]:
            distance = manhattan(unit.position, ball)
            reachable = distance <= max(0, unit.move_range)
            score = (120 if reachable else 0) - distance * 14 + unit.speed * 3 + unit.technique * 2 + self.ai_value(unit, "loose_ball_priority") * 6 + unit.hp / max(1, unit.max_hp) * 10
            return score, -distance, unit.speed, unit.technique, unit.char_id
        selected = max(pool, key=suitability)
        score = suitability(selected)[0]
        reason = "取得可能性・距離・速度・技術・優先度を比較" if preferred else "全員のルーズボール優先度が0のため最寄り候補へフォールバック"
        self.loose_ball_recovery_actor_id[team] = selected.char_id
        self.loose_ball_recovery_position[team] = ball
        self.loose_ball_recovery_score[team] = score
        self.loose_ball_recovery_reason[team] = reason
        return selected.char_id

    def _ai_priority(self, role: str, state: str, purpose: str) -> int:
        orders = {
            ("power", "self_possession"): ("score", "kill", "attack", "advance", "pass", "buff", "keep", "wait"),
            ("power", "ally_possession"): ("kill", "attack", "guard_owner", "advance", "buff", "wait"),
            ("power", "enemy_possession"): ("holder_kill", "steal", "cut", "holder_attack", "kill", "attack", "block", "approach_holder", "reaction", "heal", "buff", "wait"),
            ("power", "loose_ball"): ("pickup", "approach_ball", "attack", "block", "wait"),
            ("technique", "self_possession"): ("score", "advance", "pass", "safe_move", "keep", "kill", "wait"),
            ("technique", "ally_possession"): ("receive_score", "receive", "safe_move", "steal", "cut", "attack", "guard_owner", "wait"),
            ("technique", "enemy_possession"): ("holder_kill", "steal", "cut", "holder_attack", "kill", "attack", "block", "approach_holder", "reaction", "heal", "buff", "wait"),
            ("technique", "loose_ball"): ("pickup", "approach_ball", "block", "receive", "wait"),
            ("support", "self_possession"): ("score", "pass", "safe_move", "kill", "keep", "wait"),
            ("support", "ally_possession"): ("heal", "buff", "mp_support", "support_move", "debuff", "attack", "wait"),
            ("support", "enemy_possession"): ("holder_kill", "steal", "cut", "holder_attack", "kill", "attack", "block", "approach_holder", "reaction", "heal", "debuff", "buff", "wait"),
            ("support", "loose_ball"): ("pickup", "heal", "buff", "block", "support_move", "wait"),
        }
        order = orders[(role, state)]
        aliases = {"position": "support_move", "defense": "buff", "mp_support": "mp_support"}
        purpose = aliases.get(purpose, purpose)
        try:
            return order.index(purpose)
        except ValueError:
            return len(order) - 1 if purpose == "wait" else max(1, len(order) - 2)

    def _ai_active_modifier(self, target: Character, skill_id: str) -> bool:
        modifiers = target.temporary_effects.get("stat_modifiers", [])
        return isinstance(modifiers, list) and any(
            isinstance(item, dict) and item.get("source_skill_id") == skill_id for item in modifiers
        )

    def _ai_pass_is_return(self, actor: Character, target: Character) -> bool:
        if not self.ai_pass_history or actor.position is None or target.position is None:
            return False
        last = self.ai_pass_history[-1]
        reverse = last.get("passer_id") == target.char_id and last.get("receiver_id") == actor.char_id
        actor_distance = self.goal_distance(actor.team, actor.position)
        target_distance = self.goal_distance(actor.team, target.position)
        progresses = actor_distance is not None and target_distance is not None and target_distance < actor_distance
        target_danger = self._ai_nearby_enemies(target.team, target.position)
        actor_danger = self._ai_nearby_enemies(actor.team, actor.position)
        safer = target_danger < actor_danger
        if reverse and not progresses and not safer:
            return True
        recent = self.ai_pass_history[-max(2, self.config.ai_pass_history_limit):]
        pair = frozenset((actor.char_id, target.char_id))
        return sum(frozenset((item.get("passer_id"), item.get("receiver_id"))) == pair for item in recent) >= 2 and not progresses and not safer

    def _ai_nearby_enemies(self, team: str, position: Position) -> int:
        return sum(
            enemy.position is not None and manhattan(position, enemy.position) <= 1
            for enemy in self.active_characters(self.opponent(team))
        )

    def _ai_position_purpose(self, actor: Character, state: str, origin: Position, destination: Position) -> str:
        holder = self.characters.get(self.ball.holder_id or "")
        if state == "loose_ball":
            return "pickup" if destination == self.ball.loose_position else "approach_ball"
        if state == "self_possession":
            direction = self.movement_direction(actor.team, origin, destination)
            return "advance" if direction == "forward" else "safe_move"
        if state == "ally_possession":
            return "receive" if holder and holder.position and manhattan(destination, holder.position) <= self.config.base_pass_range else "guard_owner"
        return "approach_holder" if holder and holder.position else "block"

    def _ai_position_score(self, actor: Character, state: str, origin: Position, destination: Position) -> float:
        holder = self.characters.get(self.ball.holder_id or "")
        danger = self._ai_nearby_enemies(actor.team, destination)
        score = -danger * (8 - self._ai_bias(actor, "risk_tolerance", 0.05))
        target_distance = self.goal_distance(actor.team, destination)
        own_distance = self.goal_distance(actor.team, destination, own=True)
        if state == "self_possession":
            score -= (target_distance or 0) * (8 + self._ai_bias(actor, "score_priority", 0.08))
        elif state == "loose_ball" and self.ball.loose_position:
            score -= manhattan(destination, self.ball.loose_position) * 10
        elif holder and holder.position:
            score -= manhattan(destination, holder.position) * 7
            if state == "enemy_possession":
                score -= (own_distance or 0) * 2
        direction = self.movement_direction(actor.team, origin, destination)
        score += 18 if direction == "forward" else -30 if direction == "backward" else 0
        if self.ai_role(actor) == "support":
            allies = [unit for unit in self.active_characters(actor.team) if unit.char_id != actor.char_id and unit.position is not None]
            important = holder if holder and holder.team == actor.team and holder.position else min(
                allies, key=lambda unit: unit.hp / max(1, unit.max_hp), default=None
            )
            if important and important.position:
                distance = manhattan(destination, important.position)
                score -= max(0, distance - self.config.base_pass_range) * 12
                if direction == "backward" and distance > manhattan(origin, important.position):
                    score -= 35
        history = self.ai_action_history.get(actor.char_id, [])
        if any(item.get("destination") == destination for item in history[-self.config.ai_position_repeat_limit:]):
            score -= 18
        return score

    def _ai_pass_score(self, actor: Character, target: Character, preview: dict[str, Any],
                       origin: Position, best_move_progress: int, resource_cost: int = 0) -> tuple[float, list[str]]:
        """Score normal and skill passes from the same deterministic preview."""
        level = min(3, max(0, self.ai_value(actor, "pass_judgment_level")))
        current_distance = self.goal_distance(actor.team, origin) or 0
        target_distance = self.goal_distance(actor.team, target.position) or current_distance
        progress = current_distance - target_distance
        through = float(preview.get("pass_through_rate", 100))
        target_danger = self._ai_nearby_enemies(actor.team, target.position)
        actor_danger = self._ai_nearby_enemies(actor.team, origin)
        reasons = [f"前進{progress}", f"通過率{through:.1f}%"]
        score = progress * 9 + self._ai_bias(actor, "pass_priority", 0.9) - resource_cost * 2
        if level >= 1:
            safety_weight = 0.15 + self.ai_value(actor, "ball_safe_move") / 250
            risk_relief = self.ai_value(actor, "risk_tolerance") / 200
            score += (through - 50) * max(0.05, safety_weight - risk_relief)
            score -= target_danger * (7 + self._ai_bias(actor, "ball_safe_move", 0.05))
            score -= len(preview.get("candidates", ())) * 5
        if level >= 2:
            score += (progress - best_move_progress) * (8 - self._ai_bias(actor, "ball_breakthrough", 0.04))
            if not target.acted and not target.disabled:
                score += 16 + self._ai_bias(actor, "ball_ally_link", 0.12)
                reasons.append("受け手未行動")
            if actor_danger >= 2:
                score += self._ai_bias(actor, "ball_danger_pass", 0.25) + 15
                reasons.append("保持者危険")
        if level >= 3 and not target.acted:
            next_positions = self.reachable_positions(target.char_id)
            next_best = min((self.goal_distance(target.team, p) or target_distance for p in next_positions), default=target_distance)
            next_progress = target_distance - next_best
            score += next_progress * 6
            if any(self.is_opponent_goal(target.team, p) for p in next_positions):
                score += 80 + self._ai_bias(actor, "score_priority", 0.5)
                reasons.append("次行動で得点圏")
        if target.position and self.is_opponent_goal(actor.team, target.position):
            score += 500 + self._ai_bias(actor, "score_priority", 1.0)
            reasons.append("即時得点")
        role = self.ai_role(actor)
        if role == "technique" and through >= 70:
            score += 8
        elif role == "support" and target_danger == 0:
            score += 8
        elif role == "power" and best_move_progress >= progress:
            score -= 8
        return score, reasons

    def _build_ai_plans(self, actor: Character, allow_move: bool = True) -> list[AIActionPlan]:
        if actor.position is None:
            return []
        origin = actor.position
        state = self.ai_ball_state(actor)
        if state != "loose_ball":
            for team in TEAMS:
                self.loose_ball_recovery_actor_id[team] = ""
                self.loose_ball_recovery_position[team] = None
        role = self.ai_role(actor)
        recovery_id = self._resolve_loose_ball_recovery(actor.team) if state == "loose_ball" else ""
        enemy_recovery_id = self._resolve_loose_ball_recovery(self.opponent(actor.team)) if state == "loose_ball" else ""
        target_goals = self.target_goal_cells(actor.team)
        own_goals = set(self.own_goal_cells(actor.team))
        if not target_goals or not own_goals or self.attack_direction(actor.team) == 0:
            LOGGER.warning("AIのゴール方向を取得できません: %s / team=%s", actor.name, actor.team)
            return [AIActionPlan(origin, "wait", "wait", 999, -10000)]
        destinations = {origin}
        if allow_move:
            destinations.update(self.reachable_positions(actor.char_id))
        origin_goal_distance = self.goal_distance(actor.team, origin) or 0
        best_move_progress = max(
            (origin_goal_distance - (self.goal_distance(actor.team, position) or origin_goal_distance) for position in destinations),
            default=0,
        )
        plans: list[AIActionPlan] = []

        def ball_context(action: str, purpose: str, destination: Position, target: str, skill_id: str) -> tuple[Any, ...]:
            ball = self.ball.loose_position
            before = manhattan(origin, ball) if ball is not None else None
            after = manhattan(destination, ball) if ball is not None else None
            moved = destination != origin
            movement_purpose = "none"
            movement_adjustment = 0.0
            movement_reason = ""
            main_purpose = purpose or action
            main_adjustment = 0.0
            main_reason = ""
            path_clear = False
            team_role = "none"

            if state == "loose_ball":
                is_recovery = actor.char_id == recovery_id
                team_role = "recovery" if is_recovery else "recovery_support"
                if moved and before is not None and after is not None and after < before:
                    movement_purpose = "pickup" if after == 0 else "recovery_approach"
                    if is_recovery:
                        movement_adjustment = 220.0 if after == 0 else 120.0
                        movement_reason = "回収担当の直接取得移動" if after == 0 else "回収担当のボール接近移動"
                    else:
                        movement_adjustment = 55.0
                        movement_reason = "回収担当の護衛・取得後の受け位置"

                if target == enemy_recovery_id and action in {"skill", "steal", "cut"}:
                    main_purpose = "recovery_interference"
                    main_adjustment = 45.0
                    main_reason = "敵回収担当者への妨害"
                    team_role = "interceptor"
                elif is_recovery and action == "skill" and target in self.characters and ball is not None:
                    target_unit = self.characters[target]
                    route = set(straight_line_cells(origin, ball))
                    on_route = bool(target_unit.position and (target_unit.position in route or manhattan(target_unit.position, ball) <= 1))
                    skill = self.skills.get(skill_id)
                    forces_move = bool(skill and any(effect.effect_type == "force_move" for effect in skill.effects))
                    can_clear = purpose in {"kill", "kill_attack", "holder_kill"} or forces_move
                    if on_route and can_clear:
                        main_purpose = "recovery_path_clear"
                        main_adjustment = 20.0
                        main_reason = "ボール取得経路上の敵を排除・移動"
                        path_clear = True
                # Recovery-unrelated healing, MP recovery, buffs, ordinary attacks,
                # passes and waits intentionally receive no main-action bonus.
            elif state == "self_possession":
                team_role = "carrier"
                if purpose == "score": main_adjustment, main_reason = 350.0, "即時得点"
                elif action == "move" and purpose == "advance": movement_adjustment, movement_reason = 130.0, "保持者のゴール前進"
                elif action == "pass" or purpose == "pass": main_adjustment, main_reason = 100.0, "得点へつなぐパス"
                elif action == "keep" or purpose in {"keep", "safe_move"}: main_adjustment, main_reason = 60.0, "安全なボール保持"
                elif action == "skill" and purpose in {"attack", "kill", "normal_attack", "kill_attack"}: main_adjustment, main_reason = -75.0, "保持中の無関係な攻撃"
            elif state == "ally_possession":
                team_role = "guard"
                if action == "move" and purpose == "receive": movement_adjustment, movement_reason, team_role = 95.0, "パス受け位置", "receiver"
                elif action == "move" and purpose == "guard_owner": movement_adjustment, movement_reason = 75.0, "味方保持者の護衛"
                elif action == "skill" and purpose in {"attack", "kill", "normal_attack", "kill_attack"}: main_adjustment, main_reason = -60.0, "保持者支援と無関係な攻撃"
                elif purpose in {"heal", "buff", "mp_support", "defense"}: main_adjustment, main_reason = 25.0, "味方保持者支援"
            else:
                team_role = "defender"
                holder_target = target == self.ball.holder_id
                if holder_target and action in {"skill", "steal", "cut"}: main_adjustment, main_reason, team_role = 140.0, "敵保持者への奪取・ドロップ対応", "interceptor"
                elif action in {"steal", "cut"}: main_adjustment, main_reason, team_role = 110.0, "ボール奪取", "interceptor"
                elif action == "move" and purpose in {"approach_holder", "block"}: movement_adjustment, movement_reason = 80.0, "保持者進路・パス経路の妨害"
                elif action == "skill" and purpose in {"attack", "kill", "normal_attack", "kill_attack"}: main_adjustment, main_reason = -85.0, "敵保持者と無関係な攻撃"

            return (movement_purpose, movement_adjustment, movement_reason, main_purpose,
                    main_adjustment, main_reason, path_clear, before, after, team_role)

        def add(destination: Position, action: str, purpose: str, score: float, target: str = "", skill: str = "", system: str = "") -> None:
            direction = self.movement_direction(actor.team, origin, destination)
            target_unit = self.characters.get(target)
            direct_holder_response = bool(
                target_unit and target_unit.char_id == self.ball.holder_id
                and action in {"skill", "cut", "steal"}
            )
            if destination != origin and destination in own_goals and not direct_holder_response:
                return
            if direction == "backward" and purpose in {"buff", "defense"}:
                return
            if state == "self_possession" and direction == "backward":
                safer = self._ai_nearby_enemies(actor.team, destination) < self._ai_nearby_enemies(actor.team, origin)
                pass_progress = bool(
                    action == "pass" and target_unit and target_unit.position
                    and self.goal_distance(actor.team, target_unit.position) < self.goal_distance(actor.team, origin)
                )
                pass_safer = bool(
                    action == "pass" and target_unit and target_unit.position
                    and self._ai_nearby_enemies(actor.team, target_unit.position) < self._ai_nearby_enemies(actor.team, origin)
                )
                allowed = pass_progress or pass_safer if action == "pass" else safer
                if action == "move" or purpose in {"buff", "defense", "keep"} or not allowed:
                    return
            if state == "ally_possession" and direction == "backward" and action == "move":
                return
            if state == "loose_ball" and purpose == "pickup" and recovery_id and actor.char_id != recovery_id:
                return
            if action == "wait":
                plans.append(AIActionPlan(destination, action, purpose, 999, score, target, skill, system,
                                          score, "", 0.0, 0.0, 0.0, state, 0.0, "待機専用評価", "none"))
                return
            key = (
                "score_priority" if purpose == "score" else "pass_priority" if action == "pass" or purpose == "pass" else
                "heal_priority" if purpose == "heal" else "steal_priority" if action in {"steal", "cut"} or purpose in {"steal", "holder_attack", "holder_kill"} else
                "attack_priority" if action == "skill" and purpose in {"attack", "kill", "normal_attack", "kill_attack", "threat_attack"} else "ball_keep_priority" if action == "keep" or purpose == "keep" else
                "loose_ball_priority" if state == "loose_ball" and action == "move" else "ally_guard_priority" if state == "ally_possession" and action == "move" else
                "own_goal_defense_priority" if purpose in {"defense", "reaction"} or (action == "move" and state == "enemy_possession") else
                "support_priority" if purpose in {"buff", "debuff", "defense", "reaction", "mp_support"} else
                "score_priority" if action == "move" and state == "self_possession" else "support_priority"
            )
            value = self.ai_value(actor, key)
            if action != "wait" and value == 0:
                return
            mp_cost = self.skills.get(skill).resource_cost if skill and self.skills.get(skill) else 0
            mp_value = self.ai_value(actor, "mp_usage")
            if mp_cost and mp_value == 0:
                return
            (movement_purpose, movement_adjustment, movement_reason, main_purpose,
             main_adjustment, main_reason, path_clear, ball_distance_before,
             ball_distance_after, team_ball_role) = ball_context(action, purpose, destination, target, skill)
            context_adjustment = movement_adjustment + main_adjustment
            context_reason = " / ".join(reason for reason in (movement_reason, main_reason) if reason)
            context_score = score + context_adjustment
            profile_adjustment = abs(context_score) * (value / 5.0 - 1.0)
            mp_adjustment = mp_cost * (mp_value - 5) * 2.0
            risk_adjustment = self._ai_nearby_enemies(actor.team, destination) * (self.ai_value(actor, "risk_tolerance") - 5) * 2.0
            final_score = context_score + profile_adjustment + mp_adjustment + risk_adjustment
            plans.append(AIActionPlan(destination, action, purpose, 0, final_score, target, skill, system,
                                      score, key, profile_adjustment, mp_adjustment, risk_adjustment,
                                      state, context_adjustment, context_reason, team_ball_role,
                                      movement_purpose, movement_adjustment, movement_reason,
                                      main_purpose, main_adjustment, main_reason, path_clear,
                                      ball_distance_before, ball_distance_after))

        for destination in sorted(destinations):
            moved = destination != origin
            actor.position = destination
            try:
                if self.is_ball_holder(actor) and self.is_opponent_goal(actor.team, destination):
                    add(destination, "score", "score", 10000 - manhattan(origin, destination))
                    continue

                for candidate in self.action_candidates(actor.char_id, "attack"):
                    if not candidate.usable or (moved and not candidate.usable_after_move) or not candidate.skill_id:
                        continue
                    skill = self.skills.get(candidate.skill_id)
                    if not skill:
                        continue
                    for target in self.skill_targets(actor.char_id, candidate.skill_id):
                        preview = self.common_skill_damage_preview(actor.char_id, target.char_id, candidate.skill_id)
                        damage = int(preview.get("predicted_damage", 0))
                        if not preview.get("valid") and skill.effect_mode != "common":
                            legacy = self.damage_preview(actor.char_id, target.char_id, skill.base_effect, skill.skill_system if skill.skill_system in {"physical", "magic"} else "physical")
                            damage = int(legacy.get("predicted_damage", legacy.get("damage", 0)))
                        effective = min(target.hp, max(0, damage))
                        purpose = "kill_attack" if damage >= target.hp else "normal_attack"
                        if target.char_id == self.ball.holder_id and state == "enemy_possession":
                            purpose = "holder_kill" if damage >= target.hp else "holder_attack"
                        holder_bonus = 35 if target.char_id == self.ball.holder_id else 0
                        ball_bonus = 0
                        if target.char_id == self.ball.holder_id:
                            ball_preview = self.ball_effect_preview(actor.char_id, target.char_id, candidate.skill_id)
                            rate = int(ball_preview.get("final_rate", 0))
                            ball_bonus = rate * (1.0 if ball_preview.get("effect_type") == "cut" else 0.65)
                        add(destination, "skill", purpose, effective * 5 + damage + holder_bonus + ball_bonus - candidate.resource_cost, target.char_id, candidate.skill_id)

                holder = self.characters.get(self.ball.holder_id or "")
                if holder and holder.team != actor.team:
                    if "steal" in actor.skills and self.can_use_skill(actor.char_id, "steal")[0] and holder in self.steal_targets(actor.char_id):
                        add(destination, "steal", "steal", 100 + actor.technique, holder.char_id, "steal")

                if self.is_ball_holder(actor):
                    for system in ("physical", "magic"):
                        for target in self.valid_pass_targets(actor.char_id, system):
                            if self._ai_pass_is_return(actor, target):
                                continue
                            preview = self.pass_preview(actor.char_id, target.char_id, system)
                            passer_distance = self.goal_distance(actor.team, destination)
                            target_distance = self.goal_distance(actor.team, target.position)
                            progress = (passer_distance or 0) - (target_distance or 0)
                            if progress < 0:
                                passer_danger = self._ai_nearby_enemies(actor.team, destination)
                                target_danger = self._ai_nearby_enemies(actor.team, target.position)
                                if passer_danger < 2 or target_danger >= passer_danger:
                                    continue
                            score, reasons = self._ai_pass_score(actor, target, preview, destination, best_move_progress)
                            LOGGER.debug("AIパス候補: %s→%s / 通常%s / 距離%s / 経路%s / カット%s / 評価%.1f / 判断Lv%s / %s",
                                         actor.name, target.name, system, preview.get("distance"), preview.get("line"),
                                         len(preview.get("candidates", [])), score, self.ai_value(actor, "pass_judgment_level"), ",".join(reasons))
                            purpose = "score" if target.position and self.is_opponent_goal(actor.team, target.position) else "pass"
                            add(destination, "pass", purpose, score, target.char_id, system=system)

                for command in ("skill", "wait", "ball"):
                    for candidate in self.action_candidates(actor.char_id, command):
                        if not candidate.usable or not candidate.skill_id or (moved and not candidate.usable_after_move):
                            continue
                        skill = self.skills.get(candidate.skill_id)
                        if not skill or skill.purpose_tag in {"attack", "acquisition"}:
                            continue
                        if skill.purpose_tag == "pass" and self._effect_of_type(skill, "pass"):
                            system = "magic" if skill.actor_secondary_stat == "magic" else "physical"
                            for target in self.skill_pass_targets(actor.char_id, skill.skill_id, system):
                                if self._ai_pass_is_return(actor, target):
                                    continue
                                preview = self.skill_pass_preview(actor.char_id, target.char_id, skill.skill_id, system)
                                passer_distance = self.goal_distance(actor.team, destination)
                                target_distance = self.goal_distance(actor.team, target.position)
                                progress = (passer_distance or 0) - (target_distance or 0)
                                if progress < 0 and self._ai_nearby_enemies(actor.team, destination) < 2:
                                    continue
                                score, reasons = self._ai_pass_score(actor, target, preview, destination, best_move_progress, skill.resource_cost)
                                score += self._ai_bias(actor, "ball_skill_priority", 0.2) + self._ai_bias(actor, "mp_usage", 0.1)
                                LOGGER.debug("AIパス候補: %s→%s / %s / 距離%s / 経路%s / カット%s / 評価%.1f / 判断Lv%s / %s",
                                             actor.name, target.name, skill.name, preview.get("distance"), preview.get("line"),
                                             len(preview.get("candidates", [])), score, self.ai_value(actor, "pass_judgment_level"), ",".join(reasons))
                                purpose = "score" if target.position and self.is_opponent_goal(actor.team, target.position) else "pass"
                                add(destination, "skill", purpose, score, target.char_id, skill.skill_id, system)
                            continue
                        purpose = skill.purpose_tag or "buff"
                        if purpose in {"buff", "defense"} and not self.config.ai_allow_active_buff_reuse:
                            if self._ai_active_modifier(actor, skill.skill_id) or (purpose == "defense" and (actor.reaction_skill or actor.defending)):
                                continue
                        if purpose == "pass_disrupt":
                            if state != "enemy_possession" or actor.reaction_skill or not holder:
                                continue
                            receivers = [unit for unit in self.active_characters(holder.team) if unit.char_id != holder.char_id]
                            if not receivers or not any(
                                any(manhattan(destination, cell) <= max(1, skill.effects[0].distance if skill.effects else 1) for cell in straight_line_cells(holder.position, unit.position))
                                for unit in receivers if holder.position and unit.position
                            ):
                                continue
                            purpose = "reaction"
                        targets = self.skill_targets(actor.char_id, skill.skill_id)
                        for target in targets:
                            score = self.ai_value(actor, "support_priority") - skill.resource_cost * 2
                            if purpose == "heal":
                                ratio = target.hp / target.max_hp
                                if ratio > self.ai_value(actor, "heal_start_hp_percent") / 100 or target.hp >= target.max_hp:
                                    continue
                                amount = max((self._effect_amount(actor, effect) for effect in skill.effects if effect.effect_type == "heal_hp"), default=target.max_hp - target.hp)
                                score += min(amount, target.max_hp - target.hp) * 5 + (1 - ratio) * 100
                            elif purpose == "mp_support":
                                ratio = target.mana / target.max_mana
                                amount = max((self._effect_amount(actor, effect) for effect in skill.effects if effect.effect_type == "recover_mp"), default=0)
                                effective = min(amount, target.max_mana - target.mana)
                                unlocks = any(
                                    self.skills.get(sid) and target.mana < self.skills[sid].resource_cost <= target.mana + effective
                                    for sid in target.skills
                                )
                                if ratio > self.config.ai_mp_recovery_threshold or (effective < target.max_mana * self.config.ai_min_mp_recovery_rate and not unlocks):
                                    continue
                                score += effective * 8
                            elif purpose in {"buff", "defense"} and self._ai_active_modifier(target, skill.skill_id):
                                continue
                            elif purpose == "debuff" and self._ai_active_modifier(target, skill.skill_id):
                                continue
                            add(destination, "skill", purpose, score, target.char_id, skill.skill_id)

                if destination != origin:
                    purpose = self._ai_position_purpose(actor, state, origin, destination)
                    add(destination, "move", purpose, self._ai_position_score(actor, state, origin, destination))
                elif self.is_ball_holder(actor):
                    add(destination, "keep", "keep", -20)
            finally:
                actor.position = origin
        add(origin, "wait", "wait", -10000)
        if state == "enemy_possession" and self.ball.holder_id:
            holder = self.characters.get(self.ball.holder_id)
            direct = [
                plan for plan in plans
                if plan.target_id == self.ball.holder_id and plan.action_type in {"skill", "cut", "steal"}
            ]
            if direct and holder and holder.position:
                origin_distance = manhattan(origin, holder.position)
                plans = [
                    plan for plan in plans
                    if plan.purpose not in {"buff", "defense"}
                    and not (
                        plan.action_type == "move"
                        and manhattan(plan.destination, holder.position) > origin_distance
                    )
                ]
        return plans

    def _execute_ai_plan(self, actor: Character, plan: AIActionPlan) -> ActionResult:
        if plan.action_type == "score":
            return self.score_holder_in_goal() or self._result(False, "得点条件が変化しました")
        if plan.action_type == "skill":
            return self.use_skill(actor.char_id, plan.skill_id, plan.target_id or actor.char_id, plan.system or None)
        if plan.action_type == "pass":
            return self.pass_ball(actor.char_id, plan.target_id, plan.system)
        if plan.action_type == "cut":
            return self.cut_ball(actor.char_id, plan.target_id, plan.system)
        if plan.action_type == "steal":
            return self.use_steal(actor.char_id, plan.target_id)
        if plan.action_type == "keep":
            return self.keep_ball(actor.char_id)
        if plan.action_type == "move":
            return self.wait(actor.char_id)
        return self.wait(actor.char_id)

    def ai_take_turn(self, advance: bool = True, defer_move: bool = False, allow_move: bool = True) -> ActionResult:
        actor = self.current_actor
        if actor is None or actor.position is None:
            return self._result(False, "行動可能なAIキャラクターがいません")
        actor_id = actor.char_id

        def complete(result: ActionResult) -> ActionResult:
            if not advance or result.details.get("move_prepared"):
                return result
            if result.scored and not result.match_ended:
                prepared = self.prepare_restart_after_goal()
                if prepared.success:
                    self.auto_substitute(PLAYER)
                    self.auto_substitute(ENEMY)
                    self.auto_select_restart_holder()
            elif not result.match_ended and self.current_actor is actor:
                self.advance_turn(actor_id)
            return result

        scored = self.score_holder_in_goal()
        if scored:
            return complete(scored)
        plan = self.ai_pending_plans.pop(actor_id, None) if not allow_move else None
        try:
            plans = [plan] if plan else self._build_ai_plans(actor, allow_move=allow_move)
        except Exception:
            LOGGER.exception("AI行動案の作成に失敗したため待機します: %s", actor.name)
            fallback = self.wait(actor_id)
            fallback.details["ai_wait_reason"] = "行動案作成エラー"
            return complete(fallback)
        plans = [item for item in plans if item is not None]
        plans.sort(key=lambda item: (-item.score, item.destination, item.action_type, item.target_id, item.skill_id))
        level = clamp_ai_level(getattr(actor, "ai_level", AI_LEVEL_DEFAULT))
        if level >= 10:
            selection_pool = plans[:1]
        elif level >= 8:
            selection_pool = plans[:2]
        elif level >= 6:
            selection_pool = plans[:3]
        elif level >= 4:
            selection_pool = plans[:min(5, max(1, (len(plans) + 1) // 2))]
        else:
            selection_pool = plans
        if selection_pool and level < 10:
            if level == 1:
                first = self.rng.choice(selection_pool)
            else:
                floor = min(item.score for item in selection_pool)
                first = self.rng.choices(selection_pool, weights=[max(1.0, item.score - floor + level) for item in selection_pool], k=1)[0]
            plans = [first, *(item for item in plans if item is not first)]
        origin = actor.position
        enemy_holder = self.characters.get(self.ball.holder_id or "")
        enemy_holder_id = enemy_holder.char_id if enemy_holder and enemy_holder.team != actor.team else ""
        can_attack_holder = any(
            item.target_id == enemy_holder_id and item.action_type == "skill"
            for item in plans
        ) if enemy_holder_id else False
        pass_plans = [item for item in plans if item.action_type == "pass" or (item.action_type == "skill" and item.purpose in {"pass", "score"})]
        recovery_id = self.loose_ball_recovery_actor_id.get(actor.team, "") if plans and plans[0].decision_ball_state == "loose_ball" else ""
        for selected in plans:
            decision_state = selected.decision_ball_state or self.ai_ball_state(actor)
            decision_role = self.ai_role(actor)
            before_distance = self.goal_distance(actor.team, origin)
            after_distance = self.goal_distance(actor.team, selected.destination)
            direction = self.movement_direction(actor.team, origin, selected.destination)
            own_goals = self.own_goal_cells(actor.team)
            target_goals = self.target_goal_cells(actor.team)
            ai_details = {
                "ai_team": actor.team,
                "ai_ball_state": decision_state,
                "ai_role": decision_role,
                "ai_own_goal": (min(own_goals), max(own_goals)) if own_goals else None,
                "ai_target_goal": (min(target_goals), max(target_goals)) if target_goals else None,
                "ai_attack_direction": self.attack_direction(actor.team),
                "ai_goal_distance_before": before_distance,
                "ai_goal_distance_after": after_distance,
                "ai_movement_direction": direction,
                "ai_purpose": selected.purpose,
                "ai_enemy_ball_holder_id": enemy_holder_id,
                "ai_can_attack_holder": can_attack_holder,
                "ai_buff_reason": "直接成果候補なし" if selected.purpose in {"buff", "defense"} else "",
                "ai_retreat_reason": "安全性または前方パス改善" if direction == "backward" else "",
                "ai_wait_reason": "実行可能な有効行動案なし" if selected.action_type == "wait" else "",
                "ai_pass_candidates": len(pass_plans),
                "ai_pass_judgment_level": self.ai_value(actor, "pass_judgment_level"),
                "ai_pass_priority": self.ai_value(actor, "pass_priority"),
                "ai_profile_id": actor.ai_profile_id,
                "ai_profile_name": actor.ai_profile_name,
                "ai_profile_source": actor.ai_profile_source,
                "ai_profile_settings": dict(actor.ai_settings),
                "ai_level": level,
                "ai_team_ball_role": selected.team_ball_role,
                "ai_loose_ball_recovery_actor_id": recovery_id,
                "ai_loose_ball_recovery_actor_name": self.characters[recovery_id].name if recovery_id in self.characters else "",
                "ai_loose_ball_recovery_reason": self.loose_ball_recovery_reason.get(actor.team, ""),
                "ai_selection_pool_size": len(selection_pool),
                "ai_candidates": [{"rank": rank, "action": item.action_type,
                    "action_name": (self.skills[item.skill_id].name if item.skill_id in self.skills else
                                    {"pass": "通常パス", "steal": "スティール", "cut": "ボールカット", "keep": "ボールキープ", "move": "移動", "score": "得点", "wait": "待機"}.get(item.action_type, item.action_type)),
                    "purpose": item.purpose, "target": item.target_id,
                    "target_name": self.characters[item.target_id].name if item.target_id in self.characters else "-",
                    "selected": item is selected,
                    "destination": item.destination, "base_score": round(item.base_score, 2), "main_ai_key": item.main_ai_key,
                    "decision_ball_state": item.decision_ball_state, "ball_context_adjustment": round(item.ball_context_adjustment, 2),
                    "ball_context_reason": item.ball_context_reason, "team_ball_role": item.team_ball_role,
                    "movement_purpose": item.movement_purpose,
                    "movement_context_adjustment": round(item.movement_context_adjustment, 2),
                    "movement_context_reason": item.movement_context_reason,
                    "main_action_purpose": item.main_action_purpose,
                    "main_action_context_adjustment": round(item.main_action_context_adjustment, 2),
                    "main_action_context_reason": item.main_action_context_reason,
                    "is_recovery_path_clear_action": item.is_recovery_path_clear_action,
                    "ball_distance_before": item.ball_distance_before,
                    "ball_distance_after": item.ball_distance_after,
                    "profile_adjustment": round(item.profile_adjustment, 2), "mp_adjustment": round(item.mp_adjustment, 2),
                    "risk_adjustment": round(item.risk_adjustment, 2), "final_score": round(item.score, 2)} for rank, item in enumerate(sorted(plans, key=lambda candidate: -candidate.score), 1)],
                "ai_selection_reason": f"AIレベル{level}の上位{len(selection_pool)}候補から選択",
                "ai_pass_decision": (
                    "パスを選択" if selected in pass_plans else
                    (f"他候補を優先: {selected.purpose} 評価{selected.score:.1f}" if pass_plans else "有効なパス候補なし")
                ),
            }
            if selected in pass_plans:
                self._log(f"AIパス選択: {actor.name} / 対象={selected.target_id} / 評価{selected.score:.1f} / 候補{len(pass_plans)}件")
            elif pass_plans:
                self._log(f"AIパス見送り: {actor.name} / {selected.purpose}を優先 / パス候補{len(pass_plans)}件")
            move_result: ActionResult | None = None
            if selected.destination != actor.position:
                if defer_move:
                    move_result = self.prepare_move(actor_id, selected.destination)
                    if move_result.success:
                        self.ai_pending_plans[actor_id] = selected
                        move_result.details.update(ai_details)
                        move_result.details["ai_main_action"] = selected.action_type
                        return move_result
                else:
                    move_result = self.move_character(actor_id, selected.destination)
                    if move_result.scored:
                        return complete(move_result)
                if not move_result or not move_result.success:
                    continue
            self.report.prepare_ai_annotation(ai_details)
            try:
                result = self._execute_ai_plan(actor, selected)
            except Exception:
                self.report.pending_ai_details = None
                LOGGER.exception("AI行動案の実行に失敗したため次候補を検討します: %s", actor.name)
                continue
            if not result.success:
                if actor.position != origin:
                    LOGGER.warning("AI計画の主行動を実行できません: %s / %s", actor.name, result.message)
                continue
            self._attach_path(result, move_result)
            result.details.update({
                **ai_details, "ai_role_name": AI_ROLE_NAMES[decision_role], "ai_destination": selected.destination,
                "ai_main_action": selected.action_type, "ai_priority": selected.priority,
                "ai_ball_state_after": self.ai_ball_state(actor),
            })
            self.report.annotate_last_action(result.details)
            target_name = self.characters[selected.target_id].name if selected.target_id in self.characters else "なし"
            ai_item_name = AI_SETTINGS_BY_KEY[selected.main_ai_key].label if selected.main_ai_key in AI_SETTINGS_BY_KEY else "補正なし"
            profile_name = actor.ai_profile_name or "標準型"
            profile_id = actor.ai_profile_id or "standard"
            self._log(f"AI判断 / 行動者: {actor.name} / プロフィール: {profile_name} ({profile_id}) / AIレベル: {level} / 判断開始時ボール状態: {BALL_STATE_NAMES[decision_state]} / チーム内役割: {selected.team_ball_role}")
            if recovery_id:
                self._log(f"{actor.team}ルーズボール担当: {self.characters[recovery_id].name} / {self.loose_ball_recovery_reason[actor.team]}")
            self._log(f"AI選択 / 行動: {selected.action_type} / 目的: {selected.purpose} / 対象: {target_name} / 移動先: {selected.destination} / 候補内順位: {next((item['rank'] for item in ai_details['ai_candidates'] if item['selected']), '-')}位")
            self._log(f"AI移動評価 / 目的: {selected.movement_purpose} / ボール距離: {selected.ball_distance_before}→{selected.ball_distance_after} / 移動状況補正: {selected.movement_context_adjustment:+.1f} ({selected.movement_context_reason or 'なし'})")
            self._log(f"AI主行動評価 / 目的: {selected.main_action_purpose} / 主行動状況補正: {selected.main_action_context_adjustment:+.1f} ({selected.main_action_context_reason or 'なし'}) / 回収経路確保: {'はい' if selected.is_recovery_path_clear_action else 'いいえ'}")
            self._log(f"AI評価 / 基本: {selected.base_score:.1f} / {ai_item_name}補正: {selected.profile_adjustment:+.1f} / MP補正: {selected.mp_adjustment:+.1f} / 危険補正: {selected.risk_adjustment:+.1f} / 最終: {selected.score:.1f}")
            self._log(f"AI選択理由 / {ai_details['ai_selection_reason']}")
            self.ai_action_history.setdefault(actor_id, []).append({"destination": selected.destination, "purpose": selected.purpose, "round": self.round})
            self.ai_action_history[actor_id] = self.ai_action_history[actor_id][-4:]
            if selected.action_type == "pass" and self.ball.holder_id == selected.target_id:
                target = self.characters.get(selected.target_id)
                self.ai_pass_history.append({
                    "passer_id": actor_id, "receiver_id": selected.target_id,
                    "passer_position": origin, "receiver_position": target.position if target else None,
                    "round": self.round,
                    "progressed": bool(
                        target and target.position
                        and self.goal_distance(actor.team, target.position) < self.goal_distance(actor.team, origin)
                    ),
                    "safer": bool(target and target.position and self._ai_nearby_enemies(actor.team, target.position) < self._ai_nearby_enemies(actor.team, origin)),
                })
                self.ai_pass_history = self.ai_pass_history[-self.config.ai_pass_history_limit:]
            return complete(result)
        fallback = self.wait(actor_id)
        fallback.details.update({"ai_wait_reason": "実行可能な行動案なし", "ai_ball_state": self.ai_ball_state(actor), "ai_role": self.ai_role(actor)})
        return complete(fallback)

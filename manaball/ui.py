"""Pygame user interface for Mana's Ball."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

import pygame

from .ai import AIProfile
from .audio import AudioManager
from .core import (
    COMMAND_GROUP_LABELS,
    ENEMY,
    PLAYER,
    RULE_RITUAL,
    ActionCandidate,
    ActionResult,
    Character,
    MAX_PARTY_SIZE as CORE_MAX_PARTY_SIZE,
    MatchGame,
    Skill,
    STAT_LABELS,
    manhattan,
)
from .data import (
    EnemyGroupMatchInputs,
    PROJECT_ROOT,
    RosterCharacter,
    create_match,
    load_ai_profiles,
    load_character_roster,
    load_classes,
    load_config,
    load_elements,
    load_valid_enemy_group_match_inputs,
    load_skills,
    build_enemy_group_match_inputs,
)
from .battle_setup import BattleSetup, load_battle_setup, load_quests
from .debug_save import load_debug_match_settings, save_debug_match_settings


LOGGER = logging.getLogger("manaball.ui")
WINDOW_SIZE = (1440, 900)
CELL_SIZE = 104
HEADER_RECT = pygame.Rect(16, 12, 1408, 104)
FIELD_RECT = pygame.Rect(44, 128, 1352, 520)
CHARACTER_INFO_RECT = pygame.Rect(44, 664, 1352, 100)
FIELD_MESSAGE_RECT = pygame.Rect(44, 776, 1060, 108)
# Compatibility aliases for callers that imported the former permanent side-panel geometry.
SIDE_PANEL_RECT = pygame.Rect(0, 0, 0, 0)
COMMAND_RECT = pygame.Rect(0, 0, 0, 0)
SKILL_DETAIL_RECT = pygame.Rect(0, 0, 0, 0)
TURN_ORDER_RECT = pygame.Rect(690, 20, 414, 82)
FULL_LOG_BUTTON_RECT = pygame.Rect(1252, 808, 144, 44)
ACTION_LIST_RECT = pygame.Rect(34, 92, 430, 704)
ACTION_DETAIL_RECT = pygame.Rect(482, 92, 924, 704)
ACTION_FOOTER_RECT = pygame.Rect(34, 812, 1372, 70)
TOUCH_MOUSE_SUPPRESSION_MS = 750
TOUCH_MOUSE_SUPPRESSION_DISTANCE = 32
GOLD = (181, 132, 62)
GOLD_LIGHT = (231, 190, 103)
PANEL_NAVY = (7, 20, 34, 232)
ACTION_COMMAND_KEYS = ("attack", "move", "ball", "skills", "wait")
PARTY_SIZE = 3
MAX_PARTY_SIZE = CORE_MAX_PARTY_SIZE
FREE_ENEMY_GROUP_MODE = "group"
FREE_ENEMY_MANUAL_MODE = "manual"
FREE_ENEMY_MODE_LABELS = {
    FREE_ENEMY_GROUP_MODE: "敵グループ",
    FREE_ENEMY_MANUAL_MODE: "敵手動編成",
}
QUEST_LIST_RECT = pygame.Rect(36, 142, 500, 620)
QUEST_DETAIL_RECT = pygame.Rect(560, 142, 844, 620)
PARTY_ROSTER_RECT = pygame.Rect(16, 142, 550, 570)
PARTY_DETAIL_RECT = pygame.Rect(580, 142, 410, 350)
PARTY_TOTAL_RECT = pygame.Rect(580, 506, 410, 206)
PARTY_TEAMS_RECT = pygame.Rect(1004, 142, 420, 570)
PARTY_SORT_OPTIONS = (
    ("csv", "CSV順"), ("name", "名前順"),
    ("power", "パワー順"), ("magic", "マジック順"), ("speed", "スピード順"),
    ("technique", "テクニック順"), ("stamina", "スタミナ順"),
)
DEBUG_PARAMETER_FIELDS = (
    ("power", "パワー", 1, 10),
    ("magic", "マジック", 1, 10),
    ("speed", "スピード", 1, 10),
    ("technique", "テクニック", 1, 10),
    ("stamina", "スタミナ", 1, 10),
    ("max_hp", "最大HP", 1, 999),
    ("max_mana", "最大MP", 1, 99),
)

# Existing callers and tests use these aliases for coordinate conversion.
FIELD_ORIGIN = FIELD_RECT.topleft
SIDE_X = SIDE_PANEL_RECT.x


@dataclass
class Button:
    rect: pygame.Rect
    text: str
    key: str
    enabled: bool = True
    reason: str = ""


@dataclass
class PointerInput:
    source: str
    position: tuple[int, int]
    raw_position: tuple[float, float]
    timestamp: int
    touch: bool
    normalized: bool
    consumed: bool = False


class GameApp:
    def __init__(self, start_match: bool = False, seed: int | None = None) -> None:
        pygame.init()
        pygame.display.set_caption("Mana's Ball - 人数可変プロトタイプ")
        self.screen = pygame.display.set_mode(WINDOW_SIZE)
        self.clock = pygame.time.Clock()
        self.running = True
        self.seed = seed
        self.audio = AudioManager(PROJECT_ROOT)
        self.state = "match" if start_match else "title"
        self.game: MatchGame | None = create_match(seed, report_policy="test") if start_match else None
        self.active_player_ids: tuple[str, ...] | None = None
        self.active_enemy_ids: tuple[str, ...] | None = None
        self.active_enemy_ai_profile_ids: tuple[str, ...] | None = None
        self.active_enemy_group_id: str | None = None
        self.active_quest_id: str | None = None
        self.active_skill_overrides: dict[str, tuple[str, ...]] | None = None
        self.active_config_overrides: dict[str, object] | None = None
        self.active_character_overrides: dict[str, dict[str, int]] | None = None
        self.active_debug_mode = False
        self.party_roster: list[RosterCharacter] = []
        self.party_skills: dict[str, Skill] = {}
        self.party_ai_profiles: dict[str, AIProfile] = {}
        self.party_classes: dict[str, object] = {}
        self.party_elements: dict[str, object] = {}
        self.party_enemy_groups: list[EnemyGroupMatchInputs] = []
        self.party_selected_enemy_group_id: str | None = None
        self.party_enemy_group_selector_open = False
        self.party_enemy_group_candidate_id: str | None = None
        self.party_enemy_group_list_scroll = 0
        self.party_enemy_group_detail_scroll = 0
        self.party_default_player_ids: tuple[str, ...] = ()
        self.party_default_enemy_ids: tuple[str, ...] = ()
        self.party_default_enemy_ai_profile_ids: tuple[str, ...] = ()
        self.party_default_enemy_group_id = ""
        self.party_player_ids: list[str | None] = [None] * MAX_PARTY_SIZE
        self.party_enemy_ids: list[str | None] = [None] * MAX_PARTY_SIZE
        self.party_enemy_ai_profile_ids: list[str] = [""] * MAX_PARTY_SIZE
        self.party_enemy_ai_levels: list[int] = [5] * MAX_PARTY_SIZE
        self.quest_select_setups: dict[str, BattleSetup] = {}
        self.quest_select_enemy_inputs: dict[str, EnemyGroupMatchInputs] = {}
        self.quest_select_ids: list[str] = []
        self.quest_selected_id: str | None = None
        self.quest_select_scroll = 0
        self.quest_select_message = ""
        self.party_quest_setups: dict[str, BattleSetup] = {}
        self.party_quest_enemy_inputs: dict[str, EnemyGroupMatchInputs] = {}
        self.party_selected_quest_id = "training_3v3"
        self.party_selected_character_id: str | None = None
        self.party_selected_slot: tuple[str, int] | None = None
        self.party_class_filter = ""
        self.party_element_filter = ""
        self.party_sort_key = "csv"
        self.party_scroll = 0
        self.party_message = ""
        self.party_skill_loadouts: dict[str, tuple[str, ...]] = {}
        self.party_skill_editor_open = False
        self.party_skill_slot_index = 0
        self.party_skill_candidate_id: str | None = None
        self.party_skill_scroll = 0
        self.party_debug_mode = False
        self.party_debug_settings_open = False
        self.party_debug_character_overrides: dict[str, dict[str, int]] = {}
        self.party_debug_slot_overrides: dict[str, dict[str, int]] = {}
        self.party_debug_slot_skill_loadouts: dict[str, tuple[str, ...]] = {}
        self.party_free_enemy_mode = FREE_ENEMY_GROUP_MODE
        self.party_debug_player_party_count = PARTY_SIZE
        self.party_debug_enemy_party_count = PARTY_SIZE
        self.party_debug_player_count = PARTY_SIZE
        self.party_debug_enemy_count = PARTY_SIZE
        self.party_debug_target_score = 2
        self.party_debug_turn_limit = 8
        self.party_debug_substitution_enabled = False
        self.party_debug_injury_enabled = False
        self.party_debug_player_positions: list[tuple[int, int]] = [(3, 1), (3, 2), (3, 3)]
        self.party_debug_enemy_positions: list[tuple[int, int]] = [(9, 1), (9, 2), (9, 3)]
        self.party_field_size = (13, 5)
        self.title_notice = ""
        self.font_path = PROJECT_ROOT / "assets" / "fonts" / "NotoSansJP-VariableFont_wght.ttf"
        self.fonts: dict[int, pygame.font.Font] = {}
        self.icons: dict[str, pygame.Surface | None] = {}
        self.round_icons: dict[tuple[str, int], pygame.Surface | None] = {}
        self.portraits: dict[str, pygame.Surface | None] = {}
        self.preview_portraits: dict[str, pygame.Surface | None] = {}
        self.battle_background_original: pygame.Surface | None = None
        self.battle_field_original: pygame.Surface | None = None
        self.battle_background: pygame.Surface | None = None
        self.battle_field_background: pygame.Surface | None = None
        self._load_battle_backgrounds()
        self.buttons: list[Button] = []
        self.mode: str | None = None
        self.skill_menu = False
        self.action_menu_command: str | None = None
        self.action_menu_selected = 0
        self.action_menu_scroll = 0
        self.action_detail_scroll = 0
        self.command_window_open = False
        self.command_window_actor_id: str | None = None
        self.command_window_rect: pygame.Rect | None = None
        self.command_window_opened_frame = -1
        self.input_frame = 0
        self.pointer_processed_frame = -1
        self.last_pointer_source: str | None = None
        self.last_pointer_at = -TOUCH_MOUSE_SUPPRESSION_MS
        self.last_pointer_position: tuple[int, int] | None = None
        self.viewport_size = self.screen.get_size()
        self.options_open = False
        self.turn_moved = False
        self.pending_skill_confirmation: tuple[str, str] | None = None
        self.pending_attack_target: str | None = None
        self.pending_pass_target: str | None = None
        self.pending_pass_system: str | None = None
        self.pending_pass_skill_id: str | None = None
        self.pending_cut_target: str | None = None
        self.pending_cut_system: str | None = None
        self.action_preview: dict[str, object] | None = None
        self.special_move_ignore_zoc = False
        self.selected_id: str | None = self.game.current_actor.char_id if self.game and self.game.current_actor else None
        self.last_actor_id = self.selected_id
        self.message = "行動を選択してください"
        self.ai_ready_at = 0
        self.auto_player = False
        self.round_control_mode = "manual"
        self.control_mode_selected = True
        self.show_control_mode_modal = False
        self.round_control_round = self.game.round if self.game else 0
        self.speed_levels = (1, 2, 5, 10, 20)
        self.speed_index = 0
        self.skip_mode = False
        self.skip_previous_auto = False
        self.skip_previous_speed_index = self.speed_index
        self.skip_score_confirmation = False
        self.presentation_steps: list[dict[str, object]] = []
        self.presentation_until = 0
        self.presentation_callback: Callable[[], None] | None = None
        self.presentation_details: dict[str, object] | None = None
        self.presentation_waiting_for_confirm = False
        self.visual_positions: dict[str, tuple[int, int]] = {}
        self.full_log_open = False
        self.log_scroll = 0
        self.log_notice = ""
        self.ability_modal_actor_id: str | None = None
        self.ability_status_scroll = 0
        self.pending_move_ignore_zoc = False
        self.focused_skill_id: str | None = None
        self.retire_confirmation = False
        self.pending_substitution_out_id: str | None = None
        self._ensure_manual_command_window()

    @property
    def input_locked(self) -> bool:
        return bool(
            self.presentation_steps
            or self.presentation_until
            or self.presentation_callback
            or self.presentation_waiting_for_confirm
        )

    def font(self, size: int) -> pygame.font.Font:
        if size not in self.fonts:
            try:
                self.fonts[size] = pygame.font.Font(str(self.font_path), size)
            except (FileNotFoundError, OSError):
                LOGGER.warning("日本語フォントを読み込めないためシステムフォントへ切り替えます")
                self.fonts[size] = pygame.font.SysFont("meiryo", size)
        return self.fonts[size]

    def icon(self, char_id: str) -> pygame.Surface | None:
        if char_id not in self.icons:
            asset_id = char_id.split(":", 2)[1] if char_id.startswith("enemy:") else char_id
            path = PROJECT_ROOT / "assets" / "images" / "characters" / "icons" / f"{asset_id}.png"
            try:
                image = pygame.image.load(str(path)).convert_alpha()
                self.icons[char_id] = pygame.transform.smoothscale(image, (68, 68))
            except (FileNotFoundError, pygame.error):
                self.icons[char_id] = None
        return self.icons[char_id]

    def portrait(self, char_id: str) -> pygame.Surface | None:
        if char_id not in self.portraits:
            asset_id = char_id.split(":", 2)[1] if char_id.startswith("enemy:") else char_id
            path = PROJECT_ROOT / "assets" / "images" / "characters" / "portraits" / f"{asset_id}.png"
            try:
                image = pygame.image.load(str(path)).convert_alpha()
                width, height = image.get_size()
                crop = pygame.Rect(
                    round(width * 0.22),
                    round(height * 0.04),
                    max(1, round(width * 0.56)),
                    max(1, round(height * 0.68)),
                ).clip(image.get_rect())
                self.portraits[char_id] = self._cover_scale(image.subsurface(crop), (120, 132))
            except (FileNotFoundError, OSError, pygame.error):
                self.portraits[char_id] = None
        return self.portraits[char_id]

    def preview_portrait(self, character: Character) -> pygame.Surface | None:
        """Load an uncropped portrait for a prediction-modal side margin."""

        if character.char_id not in self.preview_portraits:
            if character.team == ENEMY:
                asset_id = character.enemy_master_id
                folder = PROJECT_ROOT / "assets" / "images" / "enemies" / "portraits"
            else:
                asset_id = character.char_id
                folder = PROJECT_ROOT / "assets" / "images" / "characters" / "portraits"
            if not asset_id:
                self.preview_portraits[character.char_id] = None
            else:
                try:
                    self.preview_portraits[character.char_id] = pygame.image.load(
                        str(folder / f"{asset_id}.png")
                    ).convert_alpha()
                except (FileNotFoundError, OSError, pygame.error):
                    self.preview_portraits[character.char_id] = None
        return self.preview_portraits[character.char_id]

    def circular_icon(self, char_id: str, size: int) -> pygame.Surface | None:
        key = (char_id, size)
        if key not in self.round_icons:
            icon = self.icon(char_id)
            if icon is None:
                self.round_icons[key] = None
            else:
                result = pygame.transform.smoothscale(icon, (size, size))
                mask = pygame.Surface((size, size), pygame.SRCALPHA)
                pygame.draw.circle(mask, (255, 255, 255, 255), (size // 2, size // 2), size // 2)
                result.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                self.round_icons[key] = result
        return self.round_icons[key]

    @staticmethod
    def _cover_scale(image: pygame.Surface, size: tuple[int, int]) -> pygame.Surface:
        """Scale without distortion and crop the centered excess area."""
        source_width, source_height = image.get_size()
        target_width, target_height = size
        scale = max(target_width / source_width, target_height / source_height)
        scaled_size = (max(1, round(source_width * scale)), max(1, round(source_height * scale)))
        scaled = pygame.transform.smoothscale(image, scaled_size)
        crop = pygame.Rect(0, 0, target_width, target_height)
        crop.center = scaled.get_rect().center
        return scaled.subsurface(crop).copy()

    def _load_battle_backgrounds(self) -> None:
        background_dir = PROJECT_ROOT / "assets" / "images" / "backgrounds"
        definitions = (
            ("battle_background.png", "battle_background", WINDOW_SIZE),
            ("battle_field.png", "battle_field_background", FIELD_RECT.size),
        )
        for filename, scaled_attr, size in definitions:
            path = background_dir / filename
            try:
                original = pygame.image.load(str(path)).convert()
                setattr(self, scaled_attr, self._cover_scale(original, size))
            except (FileNotFoundError, OSError, pygame.error) as error:
                LOGGER.warning("戦闘背景画像を読み込めません: %s (%s)", path, error)

    def _load_configured_field_background(self) -> None:
        if not self.game or not self.game.config.background_path:
            return
        path = PROJECT_ROOT / self.game.config.background_path
        try:
            image = pygame.image.load(str(path)).convert()
            self.battle_field_background = self._cover_scale(image, FIELD_RECT.size)
        except (FileNotFoundError, OSError, pygame.error) as error:
            LOGGER.warning("フィールド背景を読み込めないため既定背景で続行します: %s (%s)", path, error)

    def _draw_panel(
        self,
        rect: pygame.Rect,
        fill: tuple[int, int, int, int] = PANEL_NAVY,
        border: tuple[int, int, int] = GOLD,
    ) -> None:
        surface = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(surface, fill, surface.get_rect(), border_radius=5)
        self.screen.blit(surface, rect)
        pygame.draw.rect(self.screen, border, rect, 2, border_radius=5)
        pygame.draw.rect(self.screen, (92, 69, 39), rect.inflate(-8, -8), 1, border_radius=3)
        corner = 14
        for x, y, sx, sy in (
            (rect.left, rect.top, 1, 1), (rect.right, rect.top, -1, 1),
            (rect.left, rect.bottom, 1, -1), (rect.right, rect.bottom, -1, -1),
        ):
            pygame.draw.line(self.screen, GOLD_LIGHT, (x, y + sy * corner), (x + sx * corner, y), 2)

    def _draw_hud_card(
        self,
        rect: pygame.Rect,
        fill: tuple[int, int, int, int],
        border: tuple[int, int, int] = GOLD,
    ) -> None:
        local_points = [
            (12, 0), (rect.width - 12, 0), (rect.width, rect.height // 2),
            (rect.width - 12, rect.height), (12, rect.height), (0, rect.height // 2),
        ]
        points = [(rect.x + x, rect.y + y) for x, y in local_points]
        surface = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.polygon(surface, fill, local_points)
        self.screen.blit(surface, rect)
        pygame.draw.polygon(self.screen, border, points, 2)
        inner = rect.inflate(-8, -8)
        pygame.draw.line(self.screen, (91, 67, 35), (inner.left + 8, inner.top), (inner.right - 8, inner.top), 1)
        pygame.draw.line(self.screen, (91, 67, 35), (inner.left + 8, inner.bottom), (inner.right - 8, inner.bottom), 1)

    def start_new_match(
        self,
        player_ids: tuple[str, ...] | None = None,
        enemy_ids: tuple[str, ...] | None = None,
        skill_overrides: dict[str, tuple[str, ...]] | None = None,
        enemy_ai_profile_ids: tuple[str, ...] | None = None,
        enemy_ai_levels: tuple[int, ...] | None = None,
        config_overrides: dict[str, object] | None = None,
        character_overrides: dict[str, dict[str, int]] | None = None,
        enemy_group_id: str | None = None,
        quest_id: str | None = None,
    ) -> bool:
        selected_players = player_ids if player_ids is not None else self.active_player_ids
        selected_enemies = enemy_ids if enemy_ids is not None else self.active_enemy_ids
        selected_skill_overrides = skill_overrides if skill_overrides is not None else self.active_skill_overrides
        selected_enemy_ai_profile_ids = (
            enemy_ai_profile_ids if enemy_ai_profile_ids is not None else self.active_enemy_ai_profile_ids
        )
        selected_config_overrides = config_overrides if config_overrides is not None else self.active_config_overrides
        selected_character_overrides = (
            character_overrides if character_overrides is not None else self.active_character_overrides
        )
        selected_enemy_group_id = (
            None
            if enemy_ids is not None
            else enemy_group_id if enemy_group_id is not None else self.active_enemy_group_id
        )
        selected_quest_id = quest_id if quest_id is not None else self.active_quest_id
        if selected_config_overrides and selected_config_overrides.get("debug_mode"):
            selected_quest_id = None
        if selected_enemy_group_id:
            selected_enemies = None
            selected_enemy_ai_profile_ids = None
        try:
            self.game = create_match(
                self.seed,
                selected_players,
                selected_enemies,
                skill_overrides=selected_skill_overrides,
                enemy_ai_profile_ids=selected_enemy_ai_profile_ids,
                enemy_ai_levels=enemy_ai_levels,
                config_overrides=selected_config_overrides,
                character_overrides=selected_character_overrides,
                enemy_group_id=selected_enemy_group_id,
                quest_id=selected_quest_id,
                report_policy="play",
            )
        except (OSError, ValueError) as error:
            LOGGER.error("選択した編成で試合を開始できません: %s", error)
            self.party_message = f"試合を開始できません：{error}"
            return False
        self.round_control_mode = "manual"
        self.control_mode_selected = True
        self.show_control_mode_modal = False
        self.auto_player = False
        self.speed_index = 0
        self._load_configured_field_background()
        if selected_players is not None and (selected_enemies is not None or selected_enemy_group_id):
            self.active_player_ids = tuple(selected_players)
            self.active_enemy_ids = tuple(selected_enemies) if selected_enemies is not None else None
            self.active_enemy_group_id = selected_enemy_group_id
            self.active_quest_id = selected_quest_id
            self.active_enemy_ai_profile_ids = tuple(selected_enemy_ai_profile_ids or ())
            self.active_skill_overrides = dict(selected_skill_overrides or {})
            self.active_config_overrides = dict(selected_config_overrides or {})
            self.active_character_overrides = {
                char_id: dict(values) for char_id, values in (selected_character_overrides or {}).items()
            }
            self.active_debug_mode = bool(self.active_config_overrides.get("debug_mode"))
        self.state = "match"
        self.mode = None
        self.skill_menu = False
        self.action_menu_command = None
        self.action_menu_selected = 0
        self.action_menu_scroll = 0
        self.action_detail_scroll = 0
        self.command_window_open = False
        self.command_window_actor_id = None
        self.command_window_rect = None
        self.command_window_opened_frame = -1
        self.options_open = False
        self.turn_moved = False
        self.pending_skill_confirmation = None
        self.pending_attack_target = None
        self.pending_pass_target = None
        self.pending_pass_system = None
        self.pending_pass_skill_id = None
        self.pending_cut_target = None
        self.pending_cut_system = None
        self.special_move_ignore_zoc = False
        self.selected_id = self.game.current_actor.char_id if self.game.current_actor else None
        self.last_actor_id = self.selected_id
        self.message = "試合開始。現在の行動者を操作してください"
        self.ai_ready_at = pygame.time.get_ticks() + 450
        self.presentation_steps.clear()
        self.presentation_until = 0
        self.presentation_callback = None
        self.presentation_details = None
        self.presentation_waiting_for_confirm = False
        self.visual_positions.clear()
        self.full_log_open = False
        self.log_scroll = 0
        self.log_notice = ""
        self.pending_move_ignore_zoc = False
        self.focused_skill_id = None
        self.retire_confirmation = False
        self.skip_mode = False
        self.skip_previous_auto = False
        self.skip_previous_speed_index = self.speed_index
        self.skip_score_confirmation = False
        self._ensure_manual_command_window()
        return True

    def open_quest_select(self, selected_quest_id: str | None = None) -> bool:
        """Load enabled quest setup data without creating a match."""

        try:
            config = load_config()
            classes = load_classes()
            elements = load_elements()
            skills = load_skills(config=config)
            ai_profiles = load_ai_profiles()
            quests = load_quests()
            setups: dict[str, BattleSetup] = {}
            enemy_inputs: dict[str, EnemyGroupMatchInputs] = {}
            for quest_id, quest in quests.items():
                if not quest.enabled:
                    continue
                setup = load_battle_setup(quest_id)
                setups[quest_id] = setup
                enemy_inputs[quest_id] = build_enemy_group_match_inputs(
                    setup.quest.enemy_group_id,
                    config=config,
                    classes=classes,
                    elements=elements,
                    skills=skills,
                    ai_profiles=ai_profiles,
                )
        except (OSError, ValueError) as error:
            LOGGER.error("クエスト選択用データを読み込めません: %s", error)
            self.quest_select_setups = {}
            self.quest_select_enemy_inputs = {}
            self.quest_select_ids = []
            self.quest_selected_id = None
            self.quest_select_scroll = 0
            self.quest_select_message = f"クエスト設定を読み込めません：{error}"
            self.state = "quest_select"
            self.game = None
            return False
        self.quest_select_setups = setups
        self.quest_select_enemy_inputs = enemy_inputs
        self.quest_select_ids = list(setups)
        requested = selected_quest_id or self.quest_selected_id or self.party_selected_quest_id
        self.quest_selected_id = requested if requested in setups else next(iter(setups), None)
        self.quest_select_scroll = min(self.quest_select_scroll, max(0, len(self.quest_select_ids) - 5))
        self.quest_select_message = "" if setups else "有効なクエストがありません。data/csv/quests.csv を確認してください"
        self.state = "quest_select"
        self.game = None
        self.title_notice = ""
        return True

    def open_party_setup(
        self,
        debug_mode: bool = False,
        quest_id: str | None = None,
        preserve_party: bool = False,
    ) -> bool:
        """Load master data and initialize the temporary pre-match party."""

        previous_player_ids = (
            list(self.party_player_ids)
            if preserve_party and not debug_mode and any(self.party_player_ids)
            else []
        )
        previous_skill_loadouts = dict(self.party_skill_loadouts) if preserve_party and not debug_mode else {}
        try:
            config = load_config()
            classes = load_classes()
            elements = load_elements()
            roster = load_character_roster(config, classes, elements)
            skills = load_skills(config=config)
            ai_profiles = load_ai_profiles()
            enemy_groups = load_valid_enemy_group_match_inputs(
                config=config,
                classes=classes,
                elements=elements,
                skills=skills,
                ai_profiles=ai_profiles,
            )
            quest_setups = {} if debug_mode else {
                quest_id: load_battle_setup(quest_id)
                for quest_id, quest in load_quests().items() if quest.enabled
            }
            quest_enemy_inputs = {} if debug_mode else {
                quest_id: build_enemy_group_match_inputs(
                    setup.quest.enemy_group_id,
                    config=config,
                    classes=classes,
                    elements=elements,
                    skills=skills,
                    ai_profiles=ai_profiles,
                )
                for quest_id, setup in quest_setups.items()
            }
        except (OSError, ValueError) as error:
            LOGGER.error("パーティー編成用データを読み込めません: %s", error)
            self.title_notice = f"編成データを読み込めません：{error}"
            return False
        self.title_notice = ""
        self.party_roster = roster
        self.party_skills = skills
        self.party_ai_profiles = ai_profiles
        self.party_classes = classes
        self.party_elements = elements
        self.party_enemy_groups = enemy_groups
        self.party_quest_setups = quest_setups
        self.party_quest_enemy_inputs = quest_enemy_inputs
        self.party_selected_quest_id = (
            "training_3v3" if "training_3v3" in quest_setups
            else next(iter(quest_setups), "")
        )
        self.party_debug_mode = debug_mode
        self.party_debug_settings_open = False
        self.party_debug_character_overrides = {}
        self.party_debug_slot_overrides = {}
        self.party_debug_slot_skill_loadouts = {}
        self.party_free_enemy_mode = FREE_ENEMY_GROUP_MODE
        self.party_debug_player_party_count = PARTY_SIZE
        self.party_debug_enemy_party_count = PARTY_SIZE
        self.party_debug_player_count = PARTY_SIZE
        self.party_debug_enemy_count = PARTY_SIZE
        self.party_debug_target_score = config.target_score
        self.party_debug_turn_limit = max(1, config.max_rounds)
        self.party_debug_substitution_enabled = config.substitution_enabled
        self.party_debug_injury_enabled = config.injury_enabled
        self.party_field_size = (config.field_width, config.field_height)
        self.party_debug_player_positions = self._extended_positions(config.player_positions, PLAYER)
        self.party_debug_enemy_positions = self._extended_positions(config.enemy_positions, ENEMY)
        self.party_skill_loadouts = {member.char_id: tuple(member.skills) for member in roster}
        self.party_skill_editor_open = False
        self.party_skill_slot_index = 0
        self.party_skill_candidate_id = None
        self.party_skill_scroll = 0
        self.party_default_player_ids = tuple(config.player_ids[:PARTY_SIZE])
        self.party_default_enemy_ids = tuple(config.enemy_ids[:PARTY_SIZE])
        self.party_default_enemy_ai_profile_ids = tuple(config.enemy_ai_profile_ids[:PARTY_SIZE])
        valid_group_ids = {item.group.enemy_group_id for item in enemy_groups}
        self.party_default_enemy_group_id = (
            config.default_enemy_group_id
            if config.default_enemy_group_id in valid_group_ids
            else enemy_groups[0].group.enemy_group_id if enemy_groups else ""
        )
        self.party_selected_enemy_group_id = self.party_default_enemy_group_id or None
        self.party_enemy_group_selector_open = False
        self.party_enemy_group_candidate_id = self.party_selected_enemy_group_id
        self.party_enemy_group_list_scroll = 0
        self.party_enemy_group_detail_scroll = 0
        self.party_class_filter = ""
        self.party_element_filter = ""
        self.party_sort_key = "csv"
        self.party_scroll = 0
        self.party_selected_slot = None
        requested_quest_id = quest_id or self.quest_selected_id
        if not debug_mode and requested_quest_id in quest_setups:
            self.party_selected_quest_id = requested_quest_id
        self._reset_party_to_default()
        if previous_player_ids and not debug_mode:
            valid_ids = {member.char_id for member in roster}
            setup = self._selected_quest_setup()
            limit = setup.match.ally_party_limit if setup else PARTY_SIZE
            restored: list[str | None] = []
            used: set[str] = set()
            for char_id in previous_player_ids:
                if char_id and char_id in valid_ids and char_id not in used and len(restored) < limit:
                    restored.append(char_id)
                    used.add(char_id)
            self.party_player_ids = restored + [None] * max(0, limit - len(restored))
            self._apply_selected_quest(fill_required=False)
            self.party_skill_loadouts.update({
                char_id: tuple(skills)
                for char_id, skills in previous_skill_loadouts.items()
                if char_id in valid_ids
            })
        self.party_selected_character_id = roster[0].char_id if roster else None
        self.party_message = (
            "自由試合の方式、人数、編成、条件を設定してください"
            if roster and debug_mode
            else "選択したクエストの出場枠と必要に応じてベンチ枠を編成してください"
            if roster and self._party_enemy_group() is not None
            else "利用可能な敵グループがありません"
            if roster and not debug_mode
            else "キャラクターデータがありません"
        )
        if debug_mode:
            try:
                saved = load_debug_match_settings()
                if saved is not None:
                    self._apply_debug_settings(saved)
                    self.party_message = "前回の自由試合設定を読み込みました"
            except ValueError as error:
                LOGGER.warning("前回の自由試合設定を適用できません: %s", error)
                self.party_message = f"保存設定を読み込めないため初期値を使用します：{error}"
        self.game = None
        self.state = "party"
        return True

    def _extended_positions(self, base: tuple[tuple[int, int], ...], team: str) -> list[tuple[int, int]]:
        positions = list(base[:MAX_PARTY_SIZE])
        if team == PLAYER:
            candidates = ((3, 1), (3, 2), (3, 3), (4, 0), (4, 4), (5, 2), (5, 1))
        else:
            width = self.party_field_size[0]
            candidates = (
                (max(0, width - 4), 1), (max(0, width - 4), 2), (max(0, width - 4), 3),
                (max(0, width - 5), 0), (max(0, width - 5), 4), (max(0, width - 6), 2),
                (max(0, width - 6), 1),
            )
        for cell in candidates:
            if len(positions) >= MAX_PARTY_SIZE:
                break
            if cell not in positions:
                positions.append(cell)
        while len(positions) < MAX_PARTY_SIZE:
            index = len(positions)
            positions.append((index % max(1, self.party_field_size[0]), index % max(1, self.party_field_size[1])))
        return positions

    def _reset_party_to_default(self) -> None:
        valid_ids = {member.char_id for member in self.party_roster}
        def valid_slots(ids: tuple[str, ...]) -> list[str | None]:
            slots: list[str | None] = []
            used: set[str] = set()
            slot_count = MAX_PARTY_SIZE
            for char_id in ids[:slot_count]:
                if char_id in valid_ids and char_id not in used:
                    slots.append(char_id)
                    used.add(char_id)
                else:
                    slots.append(None)
            return (slots + [None] * slot_count)[:slot_count]

        self.party_player_ids = valid_slots(self.party_default_player_ids)
        self.party_enemy_ids = valid_slots(self.party_default_enemy_ids)
        self.party_enemy_ai_profile_ids = [
            profile_id if profile_id in self.party_ai_profiles else ""
            for profile_id in self.party_default_enemy_ai_profile_ids[:MAX_PARTY_SIZE]
        ]
        slot_count = MAX_PARTY_SIZE
        self.party_enemy_ai_profile_ids = (self.party_enemy_ai_profile_ids + [""] * slot_count)[:slot_count]
        if not self.party_debug_mode:
            self.party_selected_enemy_group_id = self.party_default_enemy_group_id or None
        self.party_selected_slot = None
        if not self.party_debug_mode:
            self._apply_selected_quest(fill_required=True)

    def _selected_quest_setup(self) -> BattleSetup | None:
        return self.party_quest_setups.get(self.party_selected_quest_id)

    def _apply_selected_quest(self, *, fill_required: bool = False) -> None:
        setup = self._selected_quest_setup()
        if setup is None:
            return
        quest_input = self.party_quest_enemy_inputs.get(self.party_selected_quest_id)
        if quest_input is not None or any(
            item.group.enemy_group_id == setup.quest.enemy_group_id for item in self.party_enemy_groups
        ):
            self.party_selected_enemy_group_id = setup.quest.enemy_group_id
        required = setup.match.ally_field_count
        limit = setup.match.ally_party_limit
        self.party_player_ids = self.party_player_ids[:limit]
        if len(self.party_player_ids) < required:
            self.party_player_ids.extend([None] * (required - len(self.party_player_ids)))
        if fill_required:
            used = {char_id for char_id in self.party_player_ids if char_id}
            available = [member.char_id for member in self.party_roster if member.char_id not in used]
            for index in range(required):
                if self.party_player_ids[index] is None and available:
                    self.party_player_ids[index] = available.pop(0)
                    used.add(self.party_player_ids[index])
        self.party_selected_slot = None

    def _pointer_input(self, event: pygame.event.Event) -> PointerInput | None:
        now = int(getattr(event, "timestamp", pygame.time.get_ticks()))
        if event.type == pygame.FINGERDOWN:
            raw = (float(event.x), float(event.y))
            width, height = self.screen.get_size()
            return PointerInput(
                source="touch",
                position=(int(raw[0] * width), int(raw[1] * height)),
                raw_position=raw,
                timestamp=now,
                touch=True,
                normalized=True,
            )
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            raw = (float(event.pos[0]), float(event.pos[1]))
            return PointerInput(
                source="mouse",
                position=(int(raw[0]), int(raw[1])),
                raw_position=raw,
                timestamp=now,
                touch=False,
                normalized=False,
            )
        return None

    def _is_duplicate_pointer(self, pointer: PointerInput) -> bool:
        if self.pointer_processed_frame == self.input_frame:
            return True
        if self.last_pointer_source is None or self.last_pointer_source == pointer.source:
            return False
        elapsed = pointer.timestamp - self.last_pointer_at
        if not 0 <= elapsed <= TOUCH_MOUSE_SUPPRESSION_MS or self.last_pointer_position is None:
            return False
        if self.last_pointer_source == "touch" and pointer.source == "mouse":
            return True
        dx = pointer.position[0] - self.last_pointer_position[0]
        dy = pointer.position[1] - self.last_pointer_position[1]
        return dx * dx + dy * dy <= TOUCH_MOUSE_SUPPRESSION_DISTANCE ** 2

    def _dispatch_pointer(self, pointer: PointerInput) -> bool:
        if self._is_duplicate_pointer(pointer):
            LOGGER.debug(
                "重複ポインター入力を無視: source=%s time=%s raw=%s logical=%s frame=%s",
                pointer.source, pointer.timestamp, pointer.raw_position, pointer.position, self.input_frame,
            )
            return False
        self.pointer_processed_frame = self.input_frame
        self.last_pointer_source = pointer.source
        self.last_pointer_at = pointer.timestamp
        self.last_pointer_position = pointer.position
        before = self.command_window_open
        self.handle_pointer(pointer.position)
        pointer.consumed = True
        actor = self.game.current_actor if self.game else None
        LOGGER.debug(
            "ポインター入力: source=%s time=%s raw=%s normalized=%s logical=%s actor=%s "
            "selected=%s command=%s->%s rect=%s frame=%s",
            pointer.source, pointer.timestamp, pointer.raw_position, pointer.normalized, pointer.position,
            actor.char_id if actor else None, self.selected_id, before, self.command_window_open,
            self.command_window_rect, self.input_frame,
        )
        return True

    def process_events(self, events: list[pygame.event.Event]) -> None:
        """Route desktop and browser input through the same UI handlers."""

        self.input_frame += 1
        for event in events:
            if event.type in {pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN}:
                self.audio.notify_user_interaction()
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                if self.ability_modal_actor_id is not None:
                    self.ability_modal_actor_id = None
                    self.ability_status_scroll = 0
                    self.message = "行動を選択してください"
                    continue
                if self.state == "match" and self.show_control_mode_modal and not self.control_mode_selected:
                    continue
                if self.action_preview is not None:
                    self.handle_button("preview_back")
                    continue
                if self.retire_confirmation:
                    self.retire_confirmation = False
                    self.message = "リタイアをキャンセルしました"
                    continue
                if self.full_log_open:
                    self.full_log_open = False
                    self.log_notice = ""
                    continue
                if self.options_open:
                    self.options_open = False
                    continue
                if self.skill_menu:
                    self._close_action_menu(reopen_commands=True)
                    continue
                if self.input_locked:
                    continue
                if self.state == "quest_select":
                    self.state = "title"
                    self.quest_select_message = ""
                elif self.state == "party" and self.party_skill_editor_open:
                    self.party_skill_editor_open = False
                elif self.state == "party" and self.party_enemy_group_selector_open:
                    self.party_enemy_group_selector_open = False
                    self.party_enemy_group_candidate_id = self.party_selected_enemy_group_id
                elif self.state == "party" and self.party_debug_settings_open:
                    self.party_debug_settings_open = False
                elif self.state == "party":
                    self.state = "title" if self.party_debug_mode else "quest_select"
                    self.party_message = ""
                elif self.state == "match" and not (self.game and self.game.match_over):
                    self._cancel_current_selection()
                else:
                    self.running = False
            elif pointer := self._pointer_input(event):
                self._dispatch_pointer(pointer)
            elif event.type in {pygame.VIDEORESIZE, getattr(pygame, "WINDOWSIZECHANGED", -1)}:
                width = int(getattr(event, "w", getattr(event, "x", self.viewport_size[0])))
                height = int(getattr(event, "h", getattr(event, "y", self.viewport_size[1])))
                if width > 0 and height > 0:
                    self.viewport_size = (width, height)
                    self.command_window_rect = None
            elif event.type == pygame.MOUSEWHEEL:
                if self.ability_modal_actor_id is not None:
                    self._scroll_ability_status(-event.y)
                elif self.full_log_open:
                    self.log_scroll = max(0, self.log_scroll - event.y * 3)
                elif self.skill_menu:
                    mouse_x = pygame.mouse.get_pos()[0]
                    if mouse_x < ACTION_DETAIL_RECT.x:
                        self._scroll_action_menu(-event.y)
                    else:
                        self.action_detail_scroll = max(0, self.action_detail_scroll - event.y * 3)
                elif self.state == "party":
                    if self.party_debug_settings_open:
                        continue
                    if self.party_enemy_group_selector_open:
                        mouse_x = pygame.mouse.get_pos()[0]
                        if mouse_x < 440:
                            self._scroll_enemy_group_list(-event.y)
                    elif self.party_skill_editor_open:
                        self._scroll_party_skills(-event.y)
                    else:
                        self._scroll_party(-event.y)
                elif self.state == "quest_select":
                    self._scroll_quest_select(-event.y)

    def run_frame(self, events: list[pygame.event.Event] | None = None) -> bool:
        """Run the shared input, update, draw, and display work for one frame."""

        self.process_events(pygame.event.get() if events is None else events)
        if not self.running:
            return False
        self.update()
        self._sync_bgm()
        self.draw()
        pygame.display.flip()
        self.clock.tick(60)
        return True

    def run(self, max_frames: int | None = None) -> int:
        frames = 0
        while self.running and (max_frames is None or frames < max_frames):
            self.run_frame()
            frames += 1
        pygame.quit()
        return 0

    async def run_async(self, max_frames: int | None = None) -> int:
        """Run in a browser while yielding control to pygbag every frame."""

        frames = 0
        while self.running and (max_frames is None or frames < max_frames):
            self.run_frame()
            frames += 1
            await asyncio.sleep(0)
        pygame.quit()
        return 0

    def update(self) -> None:
        if self.state != "match" or not self.game:
            return
        if self.ability_modal_actor_id is not None or self.full_log_open or self.retire_confirmation or self.skill_menu or self.options_open or self.action_preview is not None:
            return
        if self.input_locked:
            self._update_presentation()
            return
        if self.game.match_over:
            return
        if self.game.round != self.round_control_round:
            self.round_control_round = self.game.round
        scored_at_turn_start = self.game.score_holder_in_goal()
        if scored_at_turn_start:
            scorer_id = str(scored_at_turn_start.details.get("actor_id", self.game.ball.holder_id or ""))
            self._present_result(
                scored_at_turn_start,
                scorer_id,
                advance_after=not scored_at_turn_start.scored and not scored_at_turn_start.match_ended,
            )
            return
        actor = self.game.current_actor
        actor_id = actor.char_id if actor else None
        if actor_id != self.last_actor_id:
            self.last_actor_id = actor_id
            self.selected_id = actor_id
            self.mode = None
            self.skill_menu = False
            self.action_menu_command = None
            self.action_menu_selected = 0
            self.action_menu_scroll = 0
            self.action_detail_scroll = 0
            self._close_command_window()
            self.options_open = False
            self.turn_moved = False
            self.pending_skill_confirmation = None
            self.pending_attack_target = None
            self.pending_pass_target = None
            self.pending_pass_system = None
            self.pending_pass_skill_id = None
            self.pending_cut_target = None
            self.pending_cut_system = None
            self.special_move_ignore_zoc = False
            self.pending_move_ignore_zoc = False
            self.focused_skill_id = None
            self.ai_ready_at = pygame.time.get_ticks() + self._auto_delay(450)
        if actor and (actor.team == ENEMY or self.auto_player) and pygame.time.get_ticks() >= self.ai_ready_at:
            try:
                result = self.game.ai_take_turn(advance=False, defer_move=True)
            except Exception:
                LOGGER.exception("AI行動で予期しないエラーが発生しました")
                self.message = "AI行動を継続できませんでした"
                return
            if result.details.get("move_prepared"):
                self._present_result(
                    result,
                    actor.char_id,
                    advance_after=False,
                    on_complete=lambda: self._finish_ai_move(actor.char_id),
                )
            else:
                self._present_result(result, actor.char_id, advance_after=not result.scored and not result.match_ended)
        else:
            self._ensure_manual_command_window()

    def _duration(self, milliseconds: int) -> int:
        try:
            multiplier = self.speed_levels[self.speed_index]
            if multiplier not in (1, 2, 5, 10, 20):
                raise ValueError(multiplier)
        except (IndexError, TypeError, ValueError):
            self.speed_index = 0
            multiplier = 1
        return max(1, int(milliseconds / multiplier))

    def _auto_delay(self, milliseconds: int) -> int:
        return self._duration(milliseconds)

    def _skip_can_start(self) -> tuple[bool, str]:
        if not self.game or self.game.match_over:
            return False, "試合終了後は開始できません"
        if self.input_locked or self.full_log_open or self.options_open or self.retire_confirmation:
            return False, "モーダルまたは行動演出の終了後に開始できます"
        if self.skill_menu or self.mode or self.game.pending_move:
            return False, "行動・対象・移動の選択を解除してから開始してください"
        if any((self.pending_skill_confirmation, self.pending_attack_target, self.pending_pass_target, self.pending_cut_target)):
            return False, "対象選択を解除してから開始してください"
        if self.game.restart_team is not None:
            return False, "得点後の再開操作を完了してから開始してください"
        return True, ""

    def _start_skip(self) -> None:
        allowed, reason = self._skip_can_start()
        if not allowed:
            self.message = reason
            return
        self.skip_previous_auto = self.auto_player
        self.skip_previous_speed_index = self.speed_index
        self.skip_mode = True
        self.auto_player = True
        self._close_command_window()
        self.selected_id = self.game.current_actor.char_id if self.game and self.game.current_actor else None
        self.ai_ready_at = pygame.time.get_ticks()
        self.message = f"スキップ中 ×{self.game.config.skip_speed_multiplier}"
        self.game._log(f"スキップ操作: ×{self.game.config.skip_speed_multiplier} 開始")
        self.game.report.set_execution_mode("auto")

    def _stop_skip(self, restore_previous_auto: bool = False) -> None:
        self.skip_mode = False
        self.skip_score_confirmation = False
        self.speed_index = self.skip_previous_speed_index
        self.auto_player = self.skip_previous_auto if restore_previous_auto else False

    def _resolve_skip_score(self, continue_skip: bool) -> None:
        self.skip_score_confirmation = False
        if continue_skip:
            self.auto_player = True
            self._begin_restart_after_score()
            self.ai_ready_at = pygame.time.get_ticks()
            self.message = f"スキップ中 ×{self.game.config.skip_speed_multiplier}"
        else:
            self._stop_skip()
            self._begin_restart_after_score()

    def _present_result(
        self,
        result: ActionResult,
        actor_id: str,
        advance_after: bool,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        assert self.game is not None
        self._close_command_window()
        details = result.details
        action_name = str(details.get("action_name", "行動"))
        se_name = ""
        if result.scored:
            se_name = "score"
        elif details.get("damage") or result.damage:
            se_name = "knockout" if details.get("knockout") else "damage"
        elif "パス" in action_name:
            se_name = "pass"
        elif "攻撃" in action_name or details.get("damage_calculation"):
            se_name = "attack"
        elif details.get("path"):
            se_name = "move"
        if se_name:
            self.audio.play_se(se_name)
        actor_name = str(details.get("actor_name", self.game.characters.get(actor_id).name))
        intro_ms = self.game.config.action_intro_ms
        result_ms = self.game.config.result_ms
        steps: list[dict[str, object]] = [
            {"text": f"{actor_name}：{action_name}", "duration": self._duration(intro_ms)},
        ]
        path = details.get("path")
        visual_actor_id = str(details.get("actor_id", actor_id))
        if isinstance(path, list) and len(path) > 1:
            self.visual_positions[visual_actor_id] = path[0]
            move_ms = self.game.config.move_step_ms
            for cell in path:
                steps.append(
                    {
                        "text": f"{actor_name}が移動中",
                        "duration": self._duration(move_ms),
                        "visual": (visual_actor_id, cell),
                    }
                )
        if (
            details.get("contest")
            or details.get("ability")
            or details.get("damage_calculation")
            or details.get("success_rate") is not None
            or details.get("pass_cut_results") is not None
            or details.get("effect_results") is not None
        ):
            steps.append(
                {
                    "text": "能力値とダイスの判定",
                    "duration": self._duration(self.game.config.judgement_ms),
                    "details": details,
                    "manual_confirm": True,
                }
            )
        steps.append({"text": result.message, "duration": self._duration(result_ms)})

        def finish() -> None:
            self.visual_positions.clear()
            self.presentation_details = None
            if on_complete:
                on_complete()
            if result.scored:
                self.last_actor_id = None
                if self.game and not self.game.match_over and self.game.restart_team and not self.game.restart_prepared:
                    if self.skip_mode:
                        self.skip_score_confirmation = True
                        self.message = result.message
                    else:
                        self._begin_restart_after_score()
            if advance_after and self.game and not self.game.match_over:
                self.game.advance_turn(actor_id)
                self.last_actor_id = None

        self.presentation_steps = steps
        self.presentation_callback = finish
        self.presentation_until = 0
        self.presentation_waiting_for_confirm = False
        self._update_presentation()

    def _update_presentation(self) -> None:
        if self.presentation_waiting_for_confirm:
            return
        now = pygame.time.get_ticks()
        if self.presentation_until and now < self.presentation_until:
            return
        self.presentation_until = 0
        self.presentation_details = None
        if self.presentation_steps:
            step = self.presentation_steps.pop(0)
            self.message = str(step.get("text", ""))
            visual = step.get("visual")
            if isinstance(visual, tuple) and len(visual) == 2:
                char_id, cell = visual
                if isinstance(char_id, str) and isinstance(cell, tuple):
                    self.visual_positions[char_id] = cell
            details = step.get("details")
            if isinstance(details, dict):
                self.presentation_details = details
            if step.get("manual_confirm") and not self.auto_player:
                self.presentation_waiting_for_confirm = True
                self.presentation_until = 0
            else:
                self.presentation_until = now + int(step.get("duration", 1))
            return
        callback = self.presentation_callback
        self.presentation_callback = None
        if callback:
            callback()

    def _finish_ai_move(self, actor_id: str) -> None:
        assert self.game is not None
        arrived = self.game.arrive_prepared_move(actor_id)
        if not arrived.success:
            self.message = arrived.message
            return
        committed = self.game.commit_pending_move(actor_id)
        if committed.scored:
            committed.details.pop("path", None)
            self._present_result(committed, actor_id, advance_after=False)
            return
        follow_up = self.game.ai_take_turn(advance=False, defer_move=True, allow_move=False)
        self._present_result(
            follow_up,
            actor_id,
            advance_after=not follow_up.scored and not follow_up.match_ended,
        )

    def _begin_restart_after_score(self) -> None:
        assert self.game is not None
        prepared = self.game.prepare_restart_after_goal()
        if not prepared.success:
            self.message = prepared.message
            return
        self.game.auto_substitute(ENEMY)
        if self.game.can_substitute(PLAYER) and not self.auto_player:
            self.mode = "substitution_out"
            self.pending_substitution_out_id = None
            self.selected_id = None
            self.message = "交代しない、またはフィールドから下げるキャラクターを選択してください。"
            return
        if self.auto_player:
            self.game.auto_substitute(PLAYER)
        self._continue_restart_after_substitution()

    def _continue_restart_after_substitution(self) -> None:
        assert self.game is not None
        self.pending_substitution_out_id = None
        if self.game.restart_team == ENEMY or self.auto_player:
            selected = self.game.auto_select_restart_holder()
            self.message = selected.message
            self.last_actor_id = None
            return
        self.mode = "restart_holder"
        self.selected_id = None
        self.message = "ボールを持たせるキャラクターを選択してください。"

    def _sync_bgm(self) -> None:
        if self.state == "title":
            desired = "main"
        elif self.state == "quest_select":
            desired = "main"
        elif self.state == "party":
            desired = "party"
        elif self.game and self.game.match_over:
            desired = "result"
        elif self.game:
            desired = self.game.config.bgm_path or "battle"
        else:
            desired = ""
        self.audio.play_bgm(desired)

    def _commit_move_before_action(self, actor_id: str) -> bool:
        assert self.game is not None
        if self.game.pending_move is None:
            return True
        result = self.game.commit_pending_move(actor_id)
        if not result.success:
            self.message = result.message
            return False
        self.turn_moved = True
        self.pending_move_ignore_zoc = False
        if result.scored:
            result.details.pop("path", None)
            self.pending_skill_confirmation = None
            self.pending_attack_target = None
            self.pending_pass_target = None
            self.pending_pass_system = None
            self.pending_pass_skill_id = None
            self.pending_cut_target = None
            self.pending_cut_system = None
            self.mode = None
            self.action_menu_command = None
            self._present_result(result, actor_id, advance_after=False)
            return False
        return True

    def _action_command_specs(self, actor: Character) -> list[tuple[str, str, bool, str]]:
        """Return the fixed player command list and its current availability."""

        assert self.game is not None
        specs: list[tuple[str, str, bool, str]] = []
        for key in ACTION_COMMAND_KEYS:
            command_group = self._command_group_for_button(key)
            candidates = self.game.action_candidates(actor.char_id, command_group)
            usable = [candidate for candidate in candidates if candidate.usable]
            reason = "" if usable else self._unavailable_command_reason(candidates)
            specs.append((COMMAND_GROUP_LABELS[command_group], key, bool(usable), reason))
        specs.append(("能力", "ability", True, ""))
        return specs

    @staticmethod
    def _command_group_for_button(key: str) -> str:
        return "skill" if key == "skills" else key

    @staticmethod
    def _unavailable_command_reason(candidates: list[ActionCandidate]) -> str:
        if not candidates:
            return "使用できる行動がありません"
        reasons = [candidate.reason for candidate in candidates if candidate.reason]
        return reasons[0] if reasons else "使用できる行動がありません"

    def _open_or_execute_command(self, actor: Character, key: str) -> None:
        assert self.game is not None
        command_group = self._command_group_for_button(key)
        candidates = self.game.action_candidates(actor.char_id, command_group)
        usable = [candidate for candidate in candidates if candidate.usable]
        if not usable:
            self.message = self._unavailable_command_reason(candidates)
            return
        if command_group == "wait" and len(candidates) == 1:
            self._select_action_candidate(actor, candidates[0])
            return
        self.skill_menu = True
        self.action_menu_command = command_group
        self.action_menu_selected = next(
            (index for index, candidate in enumerate(candidates) if candidate.usable),
            0,
        )
        self.action_menu_scroll = max(0, self.action_menu_selected - 6)
        self.action_detail_scroll = 0
        self._close_command_window()
        self.mode = None
        self.focused_skill_id = None
        self.message = f"{COMMAND_GROUP_LABELS[command_group]}の行動を選択してください"

    def _close_action_menu(self, reopen_commands: bool) -> None:
        self.skill_menu = False
        self.action_menu_command = None
        self.action_menu_selected = 0
        self.action_menu_scroll = 0
        self.action_detail_scroll = 0
        actor = self.game.current_actor if self.game else None
        if reopen_commands and actor is not None:
            self._open_command_window(actor)
        else:
            self._close_command_window()
        self.message = "行動を選択してください" if reopen_commands else self.message

    def _open_command_window(self, actor: Character) -> None:
        self.command_window_open = True
        self.command_window_actor_id = actor.char_id
        self.command_window_opened_frame = self.input_frame
        self.command_window_rect = None

    def _close_command_window(self) -> None:
        self.command_window_open = False
        self.command_window_actor_id = None
        self.command_window_rect = None
        self.command_window_opened_frame = -1

    def _ensure_manual_command_window(self) -> None:
        actor = self.game.current_actor if self.game else None
        if (
            self.state != "match" or actor is None or actor.team != PLAYER or self.auto_player
            or self.input_locked or self.full_log_open or self.retire_confirmation
            or self.skill_menu or self.options_open or self.game.match_over
            or self.game.restart_team is not None or actor.acted or actor.disabled
            or actor.off_field or actor.position is None or self.mode is not None
            or self.pending_skill_confirmation or self.pending_attack_target
            or self.pending_pass_target or self.pending_cut_target
        ):
            return
        self.selected_id = actor.char_id
        if not self.command_window_open or self.command_window_actor_id != actor.char_id:
            self._open_command_window(actor)
            self.message = "行動を選択してください"

    def _action_menu_candidates(self) -> list[ActionCandidate]:
        if not self.game or not self.game.current_actor or not self.action_menu_command:
            return []
        return self.game.action_candidates(self.game.current_actor.char_id, self.action_menu_command)

    def _scroll_action_menu(self, amount: int) -> None:
        candidates = self._action_menu_candidates()
        visible = 9
        self.action_menu_scroll = min(
            max(0, self.action_menu_scroll + amount),
            max(0, len(candidates) - visible),
        )

    def _focus_action_candidate(self, index: int) -> None:
        candidates = self._action_menu_candidates()
        if not 0 <= index < len(candidates):
            return
        self.action_menu_selected = index
        self.action_detail_scroll = 0
        candidate = candidates[index]
        self.message = candidate.reason if not candidate.usable else f"{candidate.name}を選択中"

    def _handle_action_selection(self, actor: Character, action_id: str) -> None:
        assert self.game is not None
        if not self.action_menu_command:
            self.message = "行動一覧が更新されています"
            return
        candidates = self.game.action_candidates(actor.char_id, self.action_menu_command)
        candidate = next((item for item in candidates if item.action_id == action_id), None)
        if candidate is None:
            self.message = "行動データが見つかりません"
            return
        if not candidate.usable:
            self.message = candidate.reason or "現在は使用できません"
            return
        self._select_action_candidate(actor, candidate)

    def _select_action_candidate(self, actor: Character, candidate: ActionCandidate) -> None:
        self._close_action_menu(reopen_commands=False)
        command_name = COMMAND_GROUP_LABELS[candidate.command_group]
        if candidate.source_type == "standard":
            self._start_standard_action(actor, candidate, command_name)
            return
        self._start_skill_action(actor, candidate.skill_id, command_name)

    def _pass_targets(self, actor_id: str, system: str) -> list[Character]:
        assert self.game is not None
        if self.pending_pass_skill_id:
            return self.game.skill_pass_targets(actor_id, self.pending_pass_skill_id, system)
        return self.game.valid_pass_targets(actor_id, system)

    def _pass_preview(self, actor_id: str, target_id: str, system: str) -> dict[str, object]:
        assert self.game is not None
        if self.pending_pass_skill_id:
            return self.game.skill_pass_preview(actor_id, target_id, self.pending_pass_skill_id, system)
        return self.game.pass_preview(actor_id, target_id, system)

    def _pass_range(self, actor: Character, system: str) -> int:
        assert self.game is not None
        bonus = self.game.skill_pass_range_bonus(self.pending_pass_skill_id or "")
        return self.game.pass_range_for(actor, system, bonus)

    def _start_standard_action(self, actor: Character, candidate: ActionCandidate, command_name: str) -> None:
        assert self.game is not None
        self.focused_skill_id = None
        if candidate.action_id == "standard:move":
            self.mode = "move"
            self.message = f"{command_name} ＞ {candidate.name} / 緑色の移動先マスを選択してください"
            return
        if candidate.action_id == "standard:attack":
            self.mode = "attack"
            self.message = f"{command_name} ＞ {candidate.name} / 赤枠の敵を選択してください"
            return
        if candidate.action_id.startswith("standard:pass:"):
            self.pending_pass_system = candidate.action_id.rsplit(":", 1)[-1]
            self.pending_pass_skill_id = None
            self.mode = "pass"
            system_name = "パワー" if self.pending_pass_system == "physical" else "マジック"
            self.message = f"{command_name} ＞ {candidate.name} / {system_name}パスの対象を選択してください"
            return
        if candidate.action_id == "standard:keep":
            if not self._commit_move_before_action(actor.char_id):
                return
            self._finish_if_consumed(actor.char_id, self.game.keep_ball(actor.char_id))
            return
        if candidate.action_id == "standard:cut":
            self.mode = "cut"
            self.message = f"{command_name} ＞ {candidate.name} / 赤枠の敵ボール保持者を選択してください"
            return
        if candidate.action_id == "standard:wait":
            if not self._commit_move_before_action(actor.char_id):
                return
            self.special_move_ignore_zoc = False
            result = self.game.wait(actor.char_id)
            self._finish_if_consumed(actor.char_id, result)
            return
        self.message = "未対応の通常行動です"

    def _start_skill_action(
        self,
        actor: Character,
        skill_id: str,
        command_name: str | None = None,
    ) -> None:
        assert self.game is not None
        usable, reason = self.game.can_use_skill(actor.char_id, skill_id)
        if not usable:
            self.message = reason
            return
        self.focused_skill_id = skill_id
        skill = self.game.skills[skill_id]
        prefix = f"{command_name or COMMAND_GROUP_LABELS.get(skill.command_group, 'スキル')} ＞ {skill.name}"
        if self.game._effect_of_type(skill, "pass"):
            self.pending_pass_system = "magic" if skill.actor_secondary_stat == "magic" else "physical"
            self.pending_pass_skill_id = skill_id
            self.skill_menu = False
            self.action_menu_command = None
            self.mode = "pass"
            system_name = "パワー" if self.pending_pass_system == "physical" else "マジック"
            self.message = f"{prefix} / {system_name}パスの対象を選択してください"
            return
        if skill.target_type == "self" or skill_id == "shadow_step":
            self.pending_skill_confirmation = (skill_id, actor.char_id)
            self.skill_menu = False
            self.action_menu_command = None
            self.mode = None
            if skill.effect_mode == "common" and skill_id != "shadow_step":
                self.action_preview = self.game.action_preview(actor.char_id, skill_id, actor.char_id)
            self.message = f"{prefix} / {self.game.skill_cost_text(skill)}。最終決定してください"
            return
        self.mode = f"skill_{skill_id}"
        self.skill_menu = False
        self.action_menu_command = None
        prompts = {
            "steal": "赤枠の敵ボール保持者を選択してください",
            "heal": "緑枠の回復対象を選択してください",
            "quick_pass": "青枠のパス対象を選択してください",
            "push_strike": "赤枠の隣接する敵を選択してください",
            "elemental_bolt": "赤枠の射程内の敵を選択してください",
            "breakthrough": "赤枠の隣接する敵を選択してください",
            "normal_physical_attack": "赤枠の隣接する敵を選択してください",
            "normal_magic_attack": "赤枠の射程内の敵を選択してください",
            "heal_hp": "緑枠の回復対象を選択してください",
            "recover_mp": "緑枠のMP回復対象を選択してください",
        }
        self.message = f"{prefix} / {prompts.get(skill_id, '対象を選択してください')}"

    def _cancel_pending_move(self, actor: Character) -> None:
        assert self.game is not None
        result = self.game.cancel_pending_move(actor.char_id)
        if not result.success:
            self.message = result.message
            return
        self.turn_moved = False
        self.pending_skill_confirmation = None
        self.pending_attack_target = None
        self.pending_pass_target = None
        self.pending_pass_system = None
        self.pending_pass_skill_id = None
        self.pending_cut_target = None
        self.pending_cut_system = None
        self.skill_menu = False
        self.action_menu_command = None
        self.mode = None
        self.special_move_ignore_zoc = False
        self.pending_move_ignore_zoc = False
        self.message = result.message + " 行動を選択してください"

    def _cancel_current_selection(self) -> None:
        if not self.game:
            return
        actor = self.game.current_actor
        if self.pending_attack_target:
            self.pending_attack_target = None
            self.mode = None
            self.action_menu_command = None
            self.message = "攻撃を取り消しました"
            return
        if self.pending_pass_target or self.pending_pass_system:
            self.pending_pass_target = None
            self.pending_pass_system = None
            self.pending_pass_skill_id = None
            self.mode = None
            self.action_menu_command = None
            self.message = "パスを取り消しました"
            return
        if self.pending_cut_target or self.pending_cut_system:
            self.pending_cut_target = None
            self.pending_cut_system = None
            self.mode = None
            self.action_menu_command = None
            self.message = "カットを取り消しました"
            return
        if self.pending_skill_confirmation:
            self.pending_skill_confirmation = None
            self.mode = None
            self.action_menu_command = None
            self.message = "スキル使用を取り消しました"
            return
        if self.mode is not None or self.skill_menu:
            self.mode = None
            self.skill_menu = False
            self.action_menu_command = None
            self.message = "選択を解除しました"
            return
        if (
            actor
            and self.game.pending_move
            and self.game.pending_move.plan.actor_id == actor.char_id
        ):
            self._cancel_pending_move(actor)
            return
        self.message = "選択を解除しました"

    def _copy_full_log(self) -> None:
        assert self.game is not None
        try:
            if not pygame.scrap.get_init():
                pygame.scrap.init()
            pygame.scrap.put(pygame.SCRAP_TEXT, self.game.full_log_text().encode("utf-8"))
            self.log_notice = "ログをコピーしました。"
        except Exception:
            LOGGER.exception("ログをクリップボードへコピーできませんでした")
            self.log_notice = "ログをコピーできませんでした。"

    def _party_member(self, char_id: str | None) -> RosterCharacter | None:
        if char_id is None:
            return None
        return next((member for member in self.party_roster if member.char_id == char_id), None)

    def _party_enemy_group(self, group_id: str | None = None) -> EnemyGroupMatchInputs | None:
        selected = group_id if group_id is not None else self.party_selected_enemy_group_id
        selectable = next(
            (item for item in self.party_enemy_groups if item.group.enemy_group_id == selected),
            None,
        )
        if selectable is not None:
            return selectable
        quest_input = self.party_quest_enemy_inputs.get(self.party_selected_quest_id)
        if quest_input is not None and quest_input.group.enemy_group_id == selected:
            return quest_input
        return None

    def _party_enemy_group_rows(
        self,
        group: EnemyGroupMatchInputs | None = None,
    ) -> list[dict[str, str]]:
        selected = group or self._party_enemy_group()
        return [selected.rows[char_id] for char_id in selected.enemy_ids] if selected else []

    def _party_enemy_group_name(self) -> str:
        group = self._party_enemy_group()
        return group.group.name if group else (self._selected_quest_setup().quest.enemy_group_id if self._selected_quest_setup() else "未設定")

    def _active_quest_name(self) -> str:
        quest_id = self.active_quest_id or (self.game.config.quest_id if self.game else "")
        if not quest_id:
            return ""
        setup = self.party_quest_setups.get(quest_id) or self.quest_select_setups.get(quest_id)
        if setup:
            return setup.quest.name
        try:
            return load_battle_setup(quest_id).quest.name
        except (OSError, ValueError):
            return quest_id

    def _party_enemy_group_totals(self) -> dict[str, int]:
        fields = ("max_hp", "max_mana", "power", "magic", "speed", "technique", "stamina")
        rows = self._party_enemy_group_rows()
        setup = self._selected_quest_setup()
        if setup is not None:
            rows = rows[:setup.match.enemy_field_count]
        return {field: sum(int(row[field]) for row in rows) for field in fields}

    def _scroll_enemy_group_list(self, delta: int) -> None:
        maximum = max(0, len(self.party_enemy_groups) - 6)
        self.party_enemy_group_list_scroll = min(
            maximum,
            max(0, self.party_enemy_group_list_scroll + delta),
        )

    def _scroll_quest_select(self, delta: int) -> None:
        maximum = max(0, len(self.quest_select_ids) - 5)
        self.quest_select_scroll = min(maximum, max(0, self.quest_select_scroll + delta))

    def _party_equipped_skills(self, char_id: str | None) -> tuple[str, ...]:
        member = self._party_member(char_id)
        if member is None:
            return ()
        if self.party_debug_mode and self.party_selected_slot:
            team, index = self.party_selected_slot
            ids = self.party_player_ids if team == PLAYER else self.party_enemy_ids
            if 0 <= index < len(ids) and ids[index] == char_id:
                return self.party_debug_slot_skill_loadouts.get(
                    f"{team}:{index + 1}", self.party_skill_loadouts.get(member.char_id, member.skills)
                )
        return self.party_skill_loadouts.get(member.char_id, member.skills)

    def _party_slot_skills(self, team: str, index: int, char_id: str) -> tuple[str, ...]:
        member = self._party_member(char_id)
        default = self.party_skill_loadouts.get(char_id, member.skills if member else ())
        return self.party_debug_slot_skill_loadouts.get(f"{team}:{index + 1}", default)

    def _party_profile(self, profile_id: str | None) -> AIProfile | None:
        if profile_id and profile_id in self.party_ai_profiles:
            return self.party_ai_profiles[profile_id]
        return self.party_ai_profiles.get("standard") or next(iter(self.party_ai_profiles.values()), None)

    def _party_member_value(self, member: RosterCharacter, field: str) -> int:
        if self.party_debug_mode:
            override = self.party_debug_character_overrides.get(member.char_id, {})
            if self.party_selected_slot:
                team, index = self.party_selected_slot
                ids = self.party_player_ids if team == PLAYER else self.party_enemy_ids
                if 0 <= index < len(ids) and ids[index] == member.char_id:
                    override = self.party_debug_slot_overrides.get(f"{team}:{index + 1}", override)
            if field in override:
                return int(override[field])
        return int(getattr(member, field))

    def _active_team_count(self, team: str) -> int:
        if not self.party_debug_mode:
            setup = self._selected_quest_setup()
            if setup is not None:
                return setup.match.ally_party_limit if team == PLAYER else setup.match.enemy_party_limit
            return PARTY_SIZE
        return self.party_debug_player_party_count if team == PLAYER else self.party_debug_enemy_party_count

    def _active_party_ids(self, team: str) -> list[str | None]:
        ids = self.party_player_ids if team == PLAYER else self.party_enemy_ids
        return ids[:self._active_team_count(team)]

    def _selected_is_player_member(self) -> bool:
        return bool(self.party_selected_character_id and self.party_selected_character_id in self.party_player_ids)

    def _enemy_slot_profile_id(self, index: int) -> str:
        if 0 <= index < len(self.party_enemy_ai_profile_ids):
            profile_id = self.party_enemy_ai_profile_ids[index]
            if profile_id in self.party_ai_profiles:
                return profile_id
        member = self._party_member(self.party_enemy_ids[index] if 0 <= index < len(self.party_enemy_ids) else None)
        return member.default_ai_profile_id if member else "standard"

    def _cycle_debug_enemy_ai(self, index: int) -> None:
        profile_ids = list(self.party_ai_profiles)
        if not profile_ids or not 0 <= index < MAX_PARTY_SIZE:
            return
        current = self._enemy_slot_profile_id(index)
        next_index = (profile_ids.index(current) + 1) % len(profile_ids) if current in profile_ids else 0
        selected = profile_ids[next_index]
        self.party_enemy_ai_profile_ids[index] = selected
        self.party_message = f"敵{index + 1}枠のAIを{self.party_ai_profiles[selected].name}へ変更しました"

    def _party_skill_candidates(self) -> list[Skill]:
        internal_commands = {
            "normal_attack", "normal_pass", "normal_physical_attack", "normal_magic_attack",
        }
        candidates = [
            skill for skill in self.party_skills.values()
            if skill.skill_id not in internal_commands and skill.enabled and not skill.validation_error
        ]
        equipped = self._party_equipped_skills(self.party_selected_character_id)
        if equipped and 0 <= self.party_skill_slot_index < len(equipped):
            selected = self.party_skills.get(equipped[self.party_skill_slot_index])
            if selected:
                candidates = [skill for skill in candidates if skill.equip_slot == selected.equip_slot]
        return candidates

    def _scroll_party_skills(self, delta: int) -> None:
        maximum = max(0, len(self._party_skill_candidates()) - 8)
        self.party_skill_scroll = min(maximum, max(0, self.party_skill_scroll + delta))

    def _party_filter_options(self, field: str) -> list[str]:
        values = {getattr(member, field) for member in self.party_roster if getattr(member, field)}
        return [""] + sorted(values, key=lambda value: int(value) if value.isdigit() else value)

    def _party_filtered_roster(self) -> list[RosterCharacter]:
        roster = list(self.party_roster)
        if self.party_sort_key == "csv":
            return sorted(roster, key=lambda member: member.csv_order)
        if self.party_sort_key == "name":
            return sorted(roster, key=lambda member: member.name)
        return sorted(
            roster,
            key=lambda member: (getattr(member, self.party_sort_key), -member.csv_order),
            reverse=True,
        )

    def _scroll_party(self, delta: int) -> None:
        maximum = max(0, len(self._party_filtered_roster()) - 8)
        self.party_scroll = min(maximum, max(0, self.party_scroll + delta))

    def _cycle_party_filter(self, field: str) -> None:
        options = self._party_filter_options(field)
        attribute = "party_class_filter" if field == "class_id" else "party_element_filter"
        current = getattr(self, attribute)
        index = options.index(current) if current in options else 0
        setattr(self, attribute, options[(index + 1) % len(options)])
        self.party_scroll = 0

    def _party_is_complete(self) -> bool:
        player_ids = self._active_party_ids(PLAYER)
        if not self.party_debug_mode:
            setup = self._selected_quest_setup()
            if setup is None:
                return False
            selected = [char_id for char_id in player_ids if char_id]
            return (
                len(selected) >= setup.match.ally_field_count
                and len(set(selected)) == len(selected)
                and self._party_enemy_group() is not None
            )
        selected_players = [char_id for char_id in player_ids if char_id]
        if len(selected_players) < self.party_debug_player_count:
            return False
        if len(set(selected_players)) != len(selected_players):
            return False
        if self.party_debug_player_count > self.party_debug_player_party_count:
            return False
        if self.party_debug_enemy_count > self.party_debug_enemy_party_count:
            return False
        if self.party_free_enemy_mode == FREE_ENEMY_GROUP_MODE:
            group = self._party_enemy_group()
            return group is not None and len(group.enemy_ids) >= self.party_debug_enemy_party_count
        enemy_ids = self._active_party_ids(ENEMY)
        selected_enemies = [char_id for char_id in enemy_ids if char_id]
        return (
            len(selected_enemies) >= self.party_debug_enemy_count
            and len(set(selected_enemies)) == len(selected_enemies)
        )

    def _debug_slot_payload(self, team: str, index: int) -> dict[str, Any]:
        ids = self.party_player_ids if team == PLAYER else self.party_enemy_ids
        positions = self.party_debug_player_positions if team == PLAYER else self.party_debug_enemy_positions
        char_id = ids[index] if 0 <= index < len(ids) else None
        member = self._party_member(char_id)
        slot_key = f"{team}:{index + 1}"
        enabled = index < self._active_team_count(team) and member is not None
        overrides = dict(self.party_debug_character_overrides.get(member.char_id, {})) if member else {}
        overrides.update(self.party_debug_slot_overrides.get(slot_key, {}))
        values = {
            field: int(overrides.get(field, getattr(member, field))) if member else None
            for field, _label, _minimum, _maximum in DEBUG_PARAMETER_FIELDS
        }
        return {
            "slot_number": index + 1,
            "enabled": enabled,
            "team": team,
            "base_character_id": member.char_id if member else None,
            "base_enemy_id": None,
            "character_name": member.name if member else "",
            **values,
            "move_range": member.move_range if member else None,
            "skill_ids": list(self._party_slot_skills(team, index, member.char_id)) if member else [],
            "ai_role_id": member.ai_role_id if member else "",
            "ai_profile_id": self._enemy_slot_profile_id(index) if team == ENEMY and member else "",
            "enemy_ai_level": self.party_enemy_ai_levels[index] if team == ENEMY and member else 5,
            "initial_position": list(positions[index]) if index < len(positions) else [0, 0],
            "other_overrides": overrides,
        }

    def _debug_settings_payload(self) -> dict[str, Any]:
        return {
            "mode_name": "free_match",
            "enemy_formation_mode": self.party_free_enemy_mode,
            "selected_enemy_group_id": self.party_selected_enemy_group_id or "",
            "player_party_size": self.party_debug_player_party_count,
            "enemy_party_size": self.party_debug_enemy_party_count,
            "player_team_size": self.party_debug_player_count,
            "enemy_team_size": self.party_debug_enemy_count,
            "player_field_count": self.party_debug_player_count,
            "enemy_field_count": self.party_debug_enemy_count,
            "player_slots": [self._debug_slot_payload(PLAYER, index) for index in range(MAX_PARTY_SIZE)],
            "enemy_slots": [self._debug_slot_payload(ENEMY, index) for index in range(MAX_PARTY_SIZE)],
            "match_settings": {
                "score_to_win": self.party_debug_target_score,
                "turn_limit": self.party_debug_turn_limit,
                "substitution_enabled": self.party_debug_substitution_enabled,
                "injury_enabled": self.party_debug_injury_enabled,
                "field_id": "",
                "layout_id": "",
            },
        }

    @staticmethod
    def _saved_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, parsed))

    def _apply_debug_settings(self, payload: dict[str, Any]) -> None:
        valid_members = {member.char_id: member for member in self.party_roster}
        saved_player_field = payload.get("player_field_count", payload.get("player_team_size"))
        saved_enemy_field = payload.get("enemy_field_count", payload.get("enemy_team_size"))
        self.party_debug_player_party_count = self._saved_int(
            payload.get("player_party_size", payload.get("player_team_size")),
            PARTY_SIZE,
            1,
            MAX_PARTY_SIZE,
        )
        self.party_debug_enemy_party_count = self._saved_int(
            payload.get("enemy_party_size", payload.get("enemy_team_size")),
            PARTY_SIZE,
            1,
            MAX_PARTY_SIZE,
        )
        self.party_debug_player_count = self._saved_int(
            saved_player_field, min(PARTY_SIZE, self.party_debug_player_party_count), 1, self.party_debug_player_party_count
        )
        self.party_debug_enemy_count = self._saved_int(
            saved_enemy_field, min(PARTY_SIZE, self.party_debug_enemy_party_count), 1, self.party_debug_enemy_party_count
        )
        saved_mode = str(payload.get("enemy_formation_mode") or "")
        if saved_mode in {FREE_ENEMY_GROUP_MODE, FREE_ENEMY_MANUAL_MODE}:
            self.party_free_enemy_mode = saved_mode
        else:
            enemy_slots = payload.get("enemy_slots", [])
            has_manual_enemy = any(
                isinstance(slot, dict) and slot.get("enabled") and str(slot.get("base_character_id") or "") in valid_members
                for slot in enemy_slots
            )
            self.party_free_enemy_mode = FREE_ENEMY_MANUAL_MODE if has_manual_enemy else FREE_ENEMY_GROUP_MODE
        group_id = str(payload.get("selected_enemy_group_id") or "")
        if group_id and self._party_enemy_group(group_id):
            self.party_selected_enemy_group_id = group_id
            self.party_enemy_group_candidate_id = group_id
        self.party_debug_slot_overrides = {}
        self.party_debug_slot_skill_loadouts = {}
        used_by_team: dict[str, set[str]] = {PLAYER: set(), ENEMY: set()}
        for team, key, ids, positions in (
            (PLAYER, "player_slots", self.party_player_ids, self.party_debug_player_positions),
            (ENEMY, "enemy_slots", self.party_enemy_ids, self.party_debug_enemy_positions),
        ):
            saved_slots = payload.get(key, [])
            for index in range(MAX_PARTY_SIZE):
                slot = saved_slots[index] if index < len(saved_slots) and isinstance(saved_slots[index], dict) else {}
                char_id = str(slot.get("base_character_id") or "")
                if not slot.get("enabled") or char_id not in valid_members or char_id in used_by_team[team]:
                    ids[index] = None
                    continue
                ids[index] = char_id
                used_by_team[team].add(char_id)
                slot_key = f"{team}:{index + 1}"
                member = valid_members[char_id]
                overrides: dict[str, int] = {}
                for field, _label, minimum, maximum in DEBUG_PARAMETER_FIELDS:
                    default = int(getattr(member, field))
                    value = self._saved_int(slot.get(field), default, minimum, maximum)
                    if value != default:
                        overrides[field] = value
                self.party_debug_slot_overrides[slot_key] = overrides
                saved_skills = slot.get("skill_ids", [])
                valid_skills = tuple(
                    skill_id for skill_id in saved_skills
                    if isinstance(skill_id, str) and skill_id in self.party_skills
                    and self.party_skills[skill_id].enabled and not self.party_skills[skill_id].validation_error
                )
                if valid_skills and len(valid_skills) == len(set(valid_skills)):
                    self.party_debug_slot_skill_loadouts[slot_key] = valid_skills
                raw_position = slot.get("initial_position")
                if isinstance(raw_position, list) and len(raw_position) == 2:
                    width, height = self.party_field_size
                    positions[index] = (
                        self._saved_int(raw_position[0], positions[index][0], 0, width - 1),
                        self._saved_int(raw_position[1], positions[index][1], 0, height - 1),
                    )
                if team == ENEMY:
                    profile_id = str(slot.get("ai_profile_id") or "")
                    self.party_enemy_ai_profile_ids[index] = profile_id if profile_id in self.party_ai_profiles else ""
                    self.party_enemy_ai_levels[index] = self._saved_int(slot.get("enemy_ai_level"), 5, 1, 10)
        settings = payload.get("match_settings", {})
        self.party_debug_target_score = self._saved_int(settings.get("score_to_win"), 2, 1, 9)
        self.party_debug_turn_limit = self._saved_int(settings.get("turn_limit"), 8, 1, 99)
        if "substitution_enabled" in settings:
            self.party_debug_substitution_enabled = bool(settings.get("substitution_enabled"))
        if "injury_enabled" in settings:
            self.party_debug_injury_enabled = bool(settings.get("injury_enabled"))
        self.party_selected_slot = None

    def _reset_debug_to_master(self) -> None:
        config = load_config()
        self.party_debug_character_overrides = {}
        self.party_debug_slot_overrides = {}
        self.party_debug_slot_skill_loadouts = {}
        self.party_skill_loadouts = {member.char_id: tuple(member.skills) for member in self.party_roster}
        self.party_free_enemy_mode = FREE_ENEMY_GROUP_MODE
        self.party_debug_player_party_count = PARTY_SIZE
        self.party_debug_enemy_party_count = PARTY_SIZE
        self.party_debug_player_count = PARTY_SIZE
        self.party_debug_enemy_count = PARTY_SIZE
        self.party_debug_target_score = config.target_score
        self.party_debug_turn_limit = max(1, config.max_rounds)
        self.party_debug_substitution_enabled = config.substitution_enabled
        self.party_debug_injury_enabled = config.injury_enabled
        self.party_field_size = (config.field_width, config.field_height)
        self.party_debug_player_positions = self._extended_positions(config.player_positions, PLAYER)
        self.party_debug_enemy_positions = self._extended_positions(config.enemy_positions, ENEMY)
        self._reset_party_to_default()

    def _save_debug_settings(self, automatic: bool = False) -> bool:
        try:
            save_debug_match_settings(self._debug_settings_payload())
        except ValueError as error:
            self.party_message = f"自由試合設定の保存に失敗しました：{error}"
            return False
        self.party_message = "試合開始設定を自動保存しました" if automatic else "現在の自由試合設定を保存しました"
        return True

    def _handle_party_button(self, key: str) -> None:
        if key == "party_quest_cycle" and not self.party_debug_mode:
            quest_ids = list(self.party_quest_setups)
            if not quest_ids:
                self.party_message = "利用可能なクエスト設定がありません"
                return
            current = quest_ids.index(self.party_selected_quest_id) if self.party_selected_quest_id in quest_ids else -1
            self.party_selected_quest_id = quest_ids[(current + 1) % len(quest_ids)]
            self._apply_selected_quest(fill_required=True)
            setup = self._selected_quest_setup()
            if setup:
                self.party_message = (
                    f"{setup.quest.name}：出場 {setup.match.ally_field_count}対{setup.match.enemy_field_count} / "
                    f"味方パーティー上限 {setup.match.ally_party_limit}"
                )
            return
        if key == "party_enemy_group_open":
            if not self.party_debug_mode:
                return
            if self.party_free_enemy_mode != FREE_ENEMY_GROUP_MODE:
                self.party_message = "敵手動編成方式では敵グループ選択を使用しません"
                return
            self.party_enemy_group_selector_open = True
            self.party_enemy_group_candidate_id = self.party_selected_enemy_group_id
            self.party_enemy_group_detail_scroll = 0
            self.party_skill_editor_open = False
            return
        if key == "party_free_enemy_mode":
            self.party_free_enemy_mode = (
                FREE_ENEMY_MANUAL_MODE
                if self.party_free_enemy_mode == FREE_ENEMY_GROUP_MODE
                else FREE_ENEMY_GROUP_MODE
            )
            label = FREE_ENEMY_MODE_LABELS[self.party_free_enemy_mode]
            self.party_message = f"敵編成方式を{label}へ変更しました"
            self.party_selected_slot = None
            return
        if key.startswith("party_enemy_group_item:"):
            group_id = key.split(":", 1)[1]
            if self._party_enemy_group(group_id):
                self.party_enemy_group_candidate_id = group_id
                self.party_enemy_group_detail_scroll = 0
            return
        if key == "party_enemy_group_up":
            self._scroll_enemy_group_list(-1)
            return
        if key == "party_enemy_group_down":
            self._scroll_enemy_group_list(1)
            return
        if key == "party_enemy_group_apply":
            selected = self._party_enemy_group(self.party_enemy_group_candidate_id)
            if selected is None:
                self.party_message = "決定できる敵グループがありません"
                return
            self.party_selected_enemy_group_id = selected.group.enemy_group_id
            self.party_enemy_group_selector_open = False
            self.party_message = f"対戦相手を「{selected.group.name}」へ変更しました"
            return
        if key == "party_enemy_group_back":
            self.party_enemy_group_selector_open = False
            self.party_enemy_group_candidate_id = self.party_selected_enemy_group_id
            self.party_message = "敵グループの変更をキャンセルしました"
            return
        if key == "party_debug_open":
            self.party_debug_settings_open = True
            self.party_skill_editor_open = False
            return
        if key == "party_debug_close":
            self.party_debug_settings_open = False
            return
        if key == "party_debug_save":
            self._save_debug_settings()
            return
        if key == "party_debug_reload":
            try:
                saved = load_debug_match_settings()
                if saved is None:
                    self.party_message = "保存データがありません"
                else:
                    self._apply_debug_settings(saved)
                    self.party_message = "最後に保存した自由試合設定を再読込しました"
            except ValueError as error:
                self.party_message = f"自由試合設定の読込に失敗しました：{error}"
            return
        if key == "party_debug_reset":
            self._reset_debug_to_master()
            self.party_message = "CSVなどの初期設定へ戻しました"
            return
        if key.startswith("party_debug_count:"):
            _, team, delta_text = key.split(":")
            delta = int(delta_text)
            field = "party_debug_player_count" if team == PLAYER else "party_debug_enemy_count"
            limit = self.party_debug_player_party_count if team == PLAYER else self.party_debug_enemy_party_count
            count = max(1, min(limit, int(getattr(self, field)) + delta))
            setattr(self, field, count)
            self.party_message = f"{'味方' if team == PLAYER else '敵'}の出場人数を{count}人へ変更しました"
            return
        if key.startswith("party_debug_party_count:"):
            _, team, delta_text = key.split(":")
            delta = int(delta_text)
            field = "party_debug_player_party_count" if team == PLAYER else "party_debug_enemy_party_count"
            count = max(1, min(MAX_PARTY_SIZE, int(getattr(self, field)) + delta))
            setattr(self, field, count)
            field_count_name = "party_debug_player_count" if team == PLAYER else "party_debug_enemy_count"
            if getattr(self, field_count_name) > count:
                setattr(self, field_count_name, count)
            ids = self.party_player_ids if team == PLAYER else self.party_enemy_ids
            for index in range(count, MAX_PARTY_SIZE):
                ids[index] = None
            if self.party_selected_slot and self.party_selected_slot[0] == team and self.party_selected_slot[1] >= count:
                self.party_selected_slot = None
            self.party_message = f"{'味方' if team == PLAYER else '敵'}の参加人数を{count}人へ変更しました"
            return
        if key.startswith("party_debug_toggle:"):
            field = key.rsplit(":", 1)[1]
            if field == "substitution":
                self.party_debug_substitution_enabled = not self.party_debug_substitution_enabled
                self.party_message = f"交代機能を{'有効' if self.party_debug_substitution_enabled else '無効'}にしました"
            elif field == "injury":
                self.party_debug_injury_enabled = not self.party_debug_injury_enabled
                self.party_message = f"負傷機能を{'有効' if self.party_debug_injury_enabled else '無効'}にしました"
            return
        if key.startswith("party_debug_match:"):
            _, field, delta_text = key.split(":")
            delta = int(delta_text)
            if field == "score":
                self.party_debug_target_score = max(1, min(9, self.party_debug_target_score + delta))
            elif field == "turn":
                self.party_debug_turn_limit = max(1, min(99, self.party_debug_turn_limit + delta))
            return
        if key.startswith("party_debug_param:"):
            _, field, delta_text = key.split(":")
            member = self._party_member(self.party_selected_character_id)
            definition = next((item for item in DEBUG_PARAMETER_FIELDS if item[0] == field), None)
            if member is None or definition is None:
                self.party_message = "変更するキャラクターを選択してください"
                return
            current = self._party_member_value(member, field)
            value = max(definition[2], min(definition[3], current + int(delta_text)))
            if self.party_selected_slot:
                team, index = self.party_selected_slot
                slot_key = f"{team}:{index + 1}"
                self.party_debug_slot_overrides.setdefault(slot_key, {})[field] = value
            else:
                self.party_debug_character_overrides.setdefault(member.char_id, {})[field] = value
            self.party_message = f"{member.name}の{definition[1]}を{value}へ変更しました"
            return
        if key.startswith("party_debug_position:"):
            _, axis, delta_text = key.split(":")
            if self.party_selected_slot is None:
                self.party_message = "配置を変更する編成枠を選択してください"
                return
            team, index = self.party_selected_slot
            if index >= self._active_team_count(team):
                self.party_message = "無効な編成枠です"
                return
            positions = self.party_debug_player_positions if team == PLAYER else self.party_debug_enemy_positions
            x, y = positions[index]
            if axis == "x":
                x += int(delta_text)
            else:
                y += int(delta_text)
            width, height = self.party_field_size
            positions[index] = (max(0, min(width - 1, x)), max(0, min(height - 1, y)))
            self.party_message = f"{team} {index + 1}枠の配置を{positions[index]}へ変更しました"
            return
        if key.startswith("party_debug_enemy_ai:") and not key.startswith("party_debug_enemy_ai_level:"):
            self._cycle_debug_enemy_ai(int(key.rsplit(":", 1)[1]))
            return
        if key.startswith("party_debug_enemy_ai_level:"):
            index = int(key.rsplit(":", 1)[1])
            if 0 <= index < MAX_PARTY_SIZE:
                self.party_enemy_ai_levels[index] = self.party_enemy_ai_levels[index] % 10 + 1
                self.party_message = f"敵{index + 1}枠のAIレベルを{self.party_enemy_ai_levels[index]}へ変更しました"
            return
        if key.startswith("party_enemy_ai:"):
            if not self.party_debug_mode:
                return
            index = int(key.rsplit(":", 1)[1])
            profile_ids = list(self.party_ai_profiles)
            if not profile_ids or not 0 <= index < MAX_PARTY_SIZE:
                return
            current = self._enemy_slot_profile_id(index)
            next_index = (profile_ids.index(current) + 1) % len(profile_ids) if current in profile_ids else 0
            self.party_enemy_ai_profile_ids[index] = profile_ids[next_index]
            profile = self.party_ai_profiles[profile_ids[next_index]]
            self.party_message = f"敵{index + 1}枠のAIを{profile.name}へ変更しました"
            return
        if key == "party_skill_open":
            equipped = self._party_equipped_skills(self.party_selected_character_id)
            if not equipped:
                self.party_message = "交代できるスキル枠がありません"
                return
            self.party_skill_editor_open = True
            self.party_skill_slot_index = 0
            self.party_skill_candidate_id = equipped[0]
            self.party_skill_scroll = 0
            return
        if key.startswith("party_skill_slot:"):
            index = int(key.rsplit(":", 1)[1])
            equipped = self._party_equipped_skills(self.party_selected_character_id)
            if 0 <= index < len(equipped):
                self.party_skill_slot_index = index
                self.party_skill_candidate_id = equipped[index]
            return
        if key.startswith("party_skill_candidate:"):
            self.party_skill_candidate_id = key.split(":", 1)[1]
            return
        if key == "party_skill_scroll_up":
            self._scroll_party_skills(-1)
            return
        if key == "party_skill_scroll_down":
            self._scroll_party_skills(1)
            return
        if key == "party_skill_apply":
            char_id = self.party_selected_character_id
            equipped = list(self._party_equipped_skills(char_id))
            candidate_id = self.party_skill_candidate_id
            if char_id is None or not equipped or candidate_id not in {skill.skill_id for skill in self._party_skill_candidates()}:
                self.party_message = "交代するスキル枠と候補を選択してください"
                return
            if candidate_id in equipped and equipped[self.party_skill_slot_index] != candidate_id:
                self.party_message = "同じスキルを複数の枠へ設定できません"
                return
            old_skill_id = equipped[self.party_skill_slot_index]
            old_skill = self.party_skills.get(old_skill_id)
            candidate = self.party_skills[candidate_id]
            if old_skill and old_skill.equip_slot != candidate.equip_slot:
                self.party_message = "選択した枠には異なる区分のスキルを設定できません"
                return
            equipped[self.party_skill_slot_index] = candidate_id
            if self.party_debug_mode and self.party_selected_slot:
                team, index = self.party_selected_slot
                self.party_debug_slot_skill_loadouts[f"{team}:{index + 1}"] = tuple(equipped)
            else:
                self.party_skill_loadouts[char_id] = tuple(equipped)
            old_name = self.party_skills.get(old_skill_id).name if old_skill_id in self.party_skills else old_skill_id
            new_name = self.party_skills[candidate_id].name
            self.party_message = f"{old_name}を{new_name}へ交代しました"
            return
        if key == "party_skill_reset":
            member = self._party_member(self.party_selected_character_id)
            if member:
                if self.party_debug_mode and self.party_selected_slot:
                    team, index = self.party_selected_slot
                    self.party_debug_slot_skill_loadouts.pop(f"{team}:{index + 1}", None)
                else:
                    self.party_skill_loadouts[member.char_id] = tuple(member.skills)
                self.party_skill_slot_index = 0
                self.party_skill_candidate_id = member.skills[0] if member.skills else None
                self.party_message = f"{member.name}のスキルを初期状態へ戻しました"
            return
        if key == "party_skill_close":
            self.party_skill_editor_open = False
            return
        if key.startswith("party_roster:"):
            self.party_selected_character_id = key.split(":", 1)[1]
            self.party_selected_slot = None
            self.party_message = "追加先のチームを選択してください"
            return
        if key.startswith("party_ally_slot:") or key.startswith("party_enemy_slot:"):
            team = PLAYER if key.startswith("party_ally") else ENEMY
            if team == ENEMY and not self.party_debug_mode:
                return
            if team == ENEMY and self.party_free_enemy_mode == FREE_ENEMY_GROUP_MODE:
                self.party_message = "敵グループ方式では敵の手動枠を選択できません"
                return
            index = int(key.rsplit(":", 1)[1])
            self.party_selected_slot = (team, index)
            ids = self.party_player_ids if team == PLAYER else self.party_enemy_ids
            if index >= len(ids) and index < self._active_team_count(team):
                ids.extend([None] * (index + 1 - len(ids)))
            if ids[index]:
                self.party_selected_character_id = ids[index]
            self.party_message = f"{'味方' if team == PLAYER else '敵'} {index + 1}枠目を選択中"
            return
        if key == "party_class":
            self._cycle_party_filter("class_id")
            return
        if key == "party_element":
            self._cycle_party_filter("element_id")
            return
        if key == "party_sort":
            keys = [option[0] for option in PARTY_SORT_OPTIONS]
            self.party_sort_key = keys[(keys.index(self.party_sort_key) + 1) % len(keys)]
            self.party_scroll = 0
            return
        if key == "party_scroll_up":
            self._scroll_party(-1)
            return
        if key == "party_scroll_down":
            self._scroll_party(1)
            return
        if key in {"party_add_ally", "party_add_enemy"}:
            if key == "party_add_enemy" and not self.party_debug_mode:
                self.party_message = "クエスト編成の敵はクエスト設定で固定されています"
                return
            if key == "party_add_enemy" and self.party_free_enemy_mode == FREE_ENEMY_GROUP_MODE:
                self.party_message = "敵グループ方式では敵手動編成を使用しません"
                return
            char_id = self.party_selected_character_id
            if not char_id or not self._party_member(char_id):
                self.party_message = "追加するキャラクターを一覧から選択してください"
                return
            ids = self.party_player_ids if key == "party_add_ally" else self.party_enemy_ids
            if char_id in ids:
                team_name = "味方" if key == "party_add_ally" else "敵"
                self.party_message = f"同じ{team_name}キャラクターは複数編成できません（重複）"
                return
            team = PLAYER if key == "party_add_ally" else ENEMY
            active_count = self._active_team_count(team)
            if None not in ids[:active_count] and len(ids) < active_count:
                ids.append(None)
            try:
                index = ids[:active_count].index(None)
            except ValueError:
                self.party_message = "空き枠がありません"
                return
            ids[index] = char_id
            self.party_selected_slot = (team, index)
            setup = self._selected_quest_setup()
            is_bench = bool(not self.party_debug_mode and team == PLAYER and setup and index >= setup.match.ally_field_count)
            role = "控え" if is_bench else ("味方" if team == PLAYER else "敵")
            guidance = "（全員出場させる場合は6対6クエストを選択）" if is_bench else ""
            self.party_message = f"{self._party_member(char_id).name}を{role}へ追加しました{guidance}"
            return
        if key == "party_remove":
            if self.party_selected_slot is None:
                self.party_message = "外す編成枠を選択してください"
                return
            team, index = self.party_selected_slot
            if team == ENEMY and not self.party_debug_mode:
                return
            if team == ENEMY and self.party_free_enemy_mode == FREE_ENEMY_GROUP_MODE:
                self.party_message = "敵グループ方式では敵手動編成を使用しません"
                return
            ids = self.party_player_ids if team == PLAYER else self.party_enemy_ids
            if ids[index] is None:
                self.party_message = "選択した枠は空です"
                return
            removed = self._party_member(ids[index])
            ids[index] = None
            self.party_selected_slot = None
            self.party_message = f"{removed.name if removed else 'キャラクター'}を編成から外しました"
            return
        if key == "party_default":
            self._reset_party_to_default()
            self.party_message = "初期編成へ戻しました"
            return
        if key == "party_clear":
            setup = self._selected_quest_setup()
            clear_count = self.party_debug_player_party_count if self.party_debug_mode else setup.match.ally_party_limit if setup else PARTY_SIZE
            self.party_player_ids = [None] * clear_count
            if self.party_debug_mode:
                self.party_player_ids.extend([None] * (MAX_PARTY_SIZE - len(self.party_player_ids)))
                if self.party_free_enemy_mode == FREE_ENEMY_MANUAL_MODE:
                    self.party_enemy_ids = [None] * MAX_PARTY_SIZE
            self.party_selected_slot = None
            self.party_message = "すべての編成枠を解除しました"
            return
        if key == "party_start":
            if not self._party_is_complete():
                setup = self._selected_quest_setup()
                selected_count = len([char_id for char_id in self._active_party_ids(PLAYER) if char_id])
                self.party_message = (
                    "有効な味方枠と敵枠を重複なしで編成してください"
                    if self.party_debug_mode
                    else (
                        f"味方が不足しています（必要 {setup.match.ally_field_count if setup else PARTY_SIZE}人 / 現在 {selected_count}人）"
                        if setup
                        else "有効なクエスト設定を選択してください"
                    )
                )
                return
            if not self.party_debug_mode:
                setup = self._selected_quest_setup()
                selected = [char_id for char_id in self._active_party_ids(PLAYER) if char_id]
                if setup and len(selected) > setup.match.ally_party_limit:
                    self.party_message = f"味方パーティー人数が上限を超えています（現在 {len(selected)}人 / 上限 {setup.match.ally_party_limit}人）"
                    return
                current_cost = sum(
                    self._party_member_value(member, "cost")
                    for char_id in selected
                    if (member := self._party_member(char_id))
                )
                if setup and setup.match.ally_cost_limit > 0 and current_cost > setup.match.ally_cost_limit:
                    self.party_message = f"味方総コストが上限を超えています（現在 {current_cost} / 上限 {setup.match.ally_cost_limit}）"
                    return
            player_ids = tuple(char_id for char_id in self._active_party_ids(PLAYER) if char_id)
            enemy_ids: tuple[str, ...] | None = None
            skill_overrides = {char_id: self.party_skill_loadouts.get(char_id, ()) for char_id in player_ids}
            config_overrides: dict[str, object] = {}
            character_overrides: dict[str, dict[str, int]] = {}
            enemy_ai_profile_ids: tuple[str, ...] | None = None
            enemy_ai_levels: tuple[int, ...] | None = None
            enemy_group_id: str | None = self.party_selected_enemy_group_id
            if self.party_debug_mode:
                manual_enemy_ids = tuple(char_id for char_id in self._active_party_ids(ENEMY) if char_id)
                use_group = self.party_free_enemy_mode == FREE_ENEMY_GROUP_MODE
                enemy_ids = None if use_group else manual_enemy_ids
                if use_group:
                    group = self._party_enemy_group()
                    if group is None:
                        self.party_message = "有効な敵グループを選択してください"
                        return
                    enemy_group_id = group.group.enemy_group_id
                    skill_overrides = {
                        f"{PLAYER}:{index + 1}": self._party_slot_skills(PLAYER, index, char_id)
                        for index, char_id in enumerate(player_ids)
                    }
                else:
                    skill_overrides = {
                        **{
                            f"{PLAYER}:{index + 1}": self._party_slot_skills(PLAYER, index, char_id)
                            for index, char_id in enumerate(player_ids)
                        },
                        **{
                            f"{ENEMY}:{index + 1}": self._party_slot_skills(ENEMY, index, char_id)
                            for index, char_id in enumerate(manual_enemy_ids)
                        },
                    }
                positions = (
                    self.party_debug_player_positions[:self.party_debug_player_count]
                    + self.party_debug_enemy_positions[:self.party_debug_enemy_count]
                )
                if len(set(positions)) != len(positions):
                    self.party_message = "味方と敵の初期配置が重複しています"
                    return
                config_overrides = {
                    "debug_mode": True,
                    "player_team_size": self.party_debug_player_count,
                    "enemy_team_size": self.party_debug_enemy_count,
                    "player_party_limit": self.party_debug_player_party_count,
                    "enemy_party_limit": self.party_debug_enemy_party_count,
                    "target_score": self.party_debug_target_score,
                    "turn_limit": self.party_debug_turn_limit,
                    "substitution_enabled": self.party_debug_substitution_enabled,
                    "injury_enabled": self.party_debug_injury_enabled,
                    "player_positions": tuple(self.party_debug_player_positions[:self.party_debug_player_count]),
                    "enemy_positions": tuple(self.party_debug_enemy_positions[:self.party_debug_enemy_count]),
                }
                character_overrides = {
                    slot_key: dict(values)
                    for slot_key, values in self.party_debug_slot_overrides.items()
                    if slot_key.startswith(f"{PLAYER}:") or not use_group
                }
                for char_id, values in self.party_debug_character_overrides.items():
                    active_ids = set(player_ids + (() if use_group else manual_enemy_ids))
                    if char_id in active_ids:
                        character_overrides.setdefault(char_id, dict(values))
                enemy_ai_profile_ids = None if use_group else tuple(self.party_enemy_ai_profile_ids[:len(manual_enemy_ids)])
                enemy_ai_levels = None if use_group else tuple(self.party_enemy_ai_levels[:len(manual_enemy_ids)])
                if not use_group:
                    enemy_group_id = None
                if not self._save_debug_settings(automatic=True):
                    LOGGER.warning("保存失敗後も現在の確定済み設定で自由試合を開始します")
            self.start_new_match(
                player_ids=player_ids,
                enemy_ids=enemy_ids,
                skill_overrides=skill_overrides,
                enemy_ai_profile_ids=enemy_ai_profile_ids,
                enemy_ai_levels=enemy_ai_levels,
                config_overrides=config_overrides,
                character_overrides=character_overrides,
                enemy_group_id=enemy_group_id,
                quest_id=None if self.party_debug_mode else self.party_selected_quest_id,
            )
            return
        if key == "party_back":
            if self.party_debug_mode:
                self.state = "title"
            else:
                if not self.quest_select_setups:
                    self.open_quest_select(self.party_selected_quest_id)
                else:
                    self.quest_selected_id = self.party_selected_quest_id
                    self.state = "quest_select"
            self.party_message = ""

    def _party_filter_label(self, field: str, selected: str, fallback: str) -> str:
        if not selected:
            return fallback
        member = next((item for item in self.party_roster if getattr(item, field) == selected), None)
        if member is None:
            return fallback
        return member.class_name if field == "class_id" else member.element_name

    def _party_totals(self, ids: list[str | None]) -> dict[str, int]:
        fields = ("max_hp", "max_mana", "power", "magic", "speed", "technique", "stamina")
        return {
            field: sum(self._party_member_value(member, field) for char_id in ids if (member := self._party_member(char_id)))
            for field in fields
        }

    def handle_pointer(self, position: tuple[int, int]) -> None:
        if self.state == "title":
            if pygame.Rect(500, 410, 440, 68).collidepoint(position):
                self.open_quest_select(self.quest_selected_id or self.party_selected_quest_id)
            elif pygame.Rect(500, 500, 440, 58).collidepoint(position):
                self.open_party_setup(True)
            elif pygame.Rect(590, 585, 260, 52).collidepoint(position):
                self.running = False
            return
        if self.state == "quest_select":
            for button in self.buttons:
                if not button.rect.collidepoint(position):
                    continue
                if not button.enabled:
                    self.quest_select_message = button.reason or "現在は選択できません"
                elif button.key.startswith("quest_item:"):
                    self.quest_selected_id = button.key.split(":", 1)[1]
                    self.quest_select_message = ""
                elif button.key == "quest_up":
                    self._scroll_quest_select(-1)
                elif button.key == "quest_down":
                    self._scroll_quest_select(1)
                elif button.key == "quest_confirm":
                    if self.quest_selected_id:
                        self.open_party_setup(False, quest_id=self.quest_selected_id, preserve_party=True)
                    else:
                        self.quest_select_message = "クエストを選択してください"
                elif button.key == "quest_back":
                    self.state = "title"
                    self.quest_select_message = ""
                return
            return
        if self.state == "party":
            party_buttons = self.buttons
            if self.party_enemy_group_selector_open:
                party_buttons = [
                    button for button in self.buttons
                    if button.key.startswith("party_enemy_group_")
                ]
            elif self.party_debug_settings_open:
                party_buttons = [button for button in self.buttons if button.key.startswith("party_debug_")]
            if self.party_skill_editor_open:
                party_buttons = [
                    button for button in self.buttons
                    if button.key.startswith("party_skill_") and button.key != "party_skill_open"
                ]
            ai_buttons = [
                button for button in party_buttons
                if button.key.startswith("party_debug_enemy_ai:")
                or button.key.startswith("party_debug_enemy_ai_level:")
            ]
            other_buttons = [button for button in party_buttons if button not in ai_buttons]
            party_buttons = [*ai_buttons, *other_buttons]
            for button in party_buttons:
                if not button.rect.collidepoint(position):
                    continue
                if not button.enabled:
                    self.party_message = button.reason or "現在は選択できません"
                else:
                    self._handle_party_button(button.key)
                return
            return
        if not self.game:
            return
        if self.ability_modal_actor_id is not None:
            for button in self.buttons:
                if button.key in {"ability_close", "ability_up", "ability_down"} and button.rect.collidepoint(position):
                    if button.enabled:
                        self.handle_button(button.key)
                    return
            return
        if self.action_preview is not None:
            for button in self.buttons:
                if button.key in {"preview_confirm", "preview_back"} and button.rect.collidepoint(position):
                    if button.enabled:
                        self.handle_button(button.key)
                    else:
                        self.message = button.reason or "現在は実行できません"
                    return
            return
        # The control-mode chooser is the highest-priority match modal.  Every
        # pointer event is consumed here, including clicks outside its buttons.
        if self.show_control_mode_modal and not self.control_mode_selected:
            button = next(
                (item for item in self.buttons if item.key in {"round_manual", "round_ai"} and item.rect.collidepoint(position)),
                None,
            )
            if button is not None and button.enabled:
                self.handle_button(button.key)
            return
        if self.skip_score_confirmation:
            for button in self.buttons:
                if button.rect.collidepoint(position):
                    if button.key == "skip_resume_manual":
                        self._resolve_skip_score(False)
                    elif button.key == "skip_continue":
                        self._resolve_skip_score(True)
                    return
            return
        if self.presentation_waiting_for_confirm:
            self.presentation_waiting_for_confirm = False
            self.presentation_until = 0
            self._update_presentation()
            return
        if self.retire_confirmation:
            for button in self.buttons:
                if not button.rect.collidepoint(position):
                    continue
                if button.key == "retire_confirm":
                    result = self.game.retire(PLAYER)
                    self.retire_confirmation = False
                    self.message = result.message
                elif button.key == "retire_cancel":
                    self.retire_confirmation = False
                    self.message = "リタイアをキャンセルしました"
                return
            return
        if self.full_log_open:
            for button in self.buttons:
                if not button.rect.collidepoint(position):
                    continue
                if button.key == "log_back":
                    self.full_log_open = False
                    self.log_notice = ""
                elif button.key == "log_copy":
                    self._copy_full_log()
                elif button.key == "log_up":
                    self.log_scroll = max(0, self.log_scroll - 12)
                elif button.key == "log_down":
                    self.log_scroll += 12
                return
            return
        if self.options_open:
            for button in self.buttons:
                if button.key.startswith("options_") and button.rect.collidepoint(position):
                    self.handle_button(button.key)
                    return
            return
        if self.game.match_over:
            if self.input_locked:
                return
            button = next((item for item in self.buttons if item.rect.collidepoint(position)), None)
            if button is None:
                return
            if button.key == "full_log":
                self.full_log_open = True
                self.log_scroll = 0
            elif button.key == "restart":
                self.start_new_match()
            elif button.key == "result_party":
                self.state = "party"
                self.game = None
                self.party_enemy_group_selector_open = False
                self.party_enemy_group_candidate_id = self.party_selected_enemy_group_id
                self.party_message = (
                    "同じクエストと直前の編成を維持しています"
                    if self.active_quest_id
                    else "直前の自由試合設定を維持しています"
                )
            elif button.key == "result_quest_select":
                selected = self.active_quest_id or self.party_selected_quest_id
                self.game = None
                self.open_quest_select(selected)
            elif button.key == "title":
                self.state = "title"
                self.game = None
                self.active_player_ids = None
                self.active_enemy_ids = None
                self.active_enemy_group_id = None
                self.active_quest_id = None
                self.active_enemy_ai_profile_ids = None
                self.active_skill_overrides = None
                self.active_config_overrides = None
                self.active_character_overrides = None
                self.active_debug_mode = False
            return
        actor = self.game.current_actor
        for button in self.buttons:
            if button.rect.collidepoint(position):
                if button.key in {"auto", "speed", "skip", "options"}:
                    self.handle_button(button.key)
                    return
                if self.input_locked:
                    self.message = "行動演出中です"
                    return
                if not button.enabled:
                    self.message = button.reason or "現在は選択できません"
                else:
                    self.handle_button(button.key)
                return
        if self.skill_menu or self.options_open or self.round_control_mode == "unselected":
            return
        if self.input_locked or actor is None or actor.team != PLAYER or self.auto_player:
            if self.game.restart_team == PLAYER and self.game.restart_prepared and not self.auto_player:
                cell = self.screen_to_cell(position)
                target = self.game.character_at(cell) if cell is not None else None
                if target in self.game.restart_candidates():
                    result = self.game.select_restart_holder(target.char_id)
                    self.message = result.message
                    self.mode = None
                    self.selected_id = target.char_id
                    self.last_actor_id = None
            return
        if (
            self.pending_skill_confirmation
            or self.pending_attack_target
            or self.pending_pass_target
            or self.pending_cut_target
        ):
            return
        cell = self.screen_to_cell(position)
        if cell is not None:
            self.handle_cell(cell)

    def handle_button(self, key: str) -> None:
        assert self.game is not None
        self.audio.play_se("confirm")
        if key == "ability_close":
            self.ability_modal_actor_id = None
            self.ability_status_scroll = 0
            self.message = "行動を選択してください"
            return
        if key == "ability_up":
            self._scroll_ability_status(-1)
            return
        if key == "ability_down":
            self._scroll_ability_status(1)
            return
        if key == "preview_back":
            preview = self.action_preview or {}
            self.action_preview = None
            self.pending_skill_confirmation = None
            self.pending_attack_target = None
            if preview.get("self_target"):
                self.skill_menu = True
                self.action_menu_command = "skill"
                self.action_menu_selected = 0
            else:
                skill_id = str(preview.get("action_id", ""))
                self.mode = f"skill_{skill_id}" if skill_id else None
            self.message = "対象選択へ戻りました"
            return
        if key == "preview_confirm":
            preview = self.action_preview or {}
            actor = self.game.current_actor
            if actor is None:
                return
            skill_id = str(preview.get("action_id", ""))
            target_id = str(preview.get("target_id", ""))
            usable, reason = self.game.can_use_skill(actor.char_id, skill_id)
            if not usable or self.game.characters.get(target_id) not in self.game.skill_targets(actor.char_id, skill_id):
                preview["executable"] = False
                preview["reason"] = reason if not usable else "対象が不正または射程外です"
                self.message = str(preview["reason"])
                return
            self.action_preview = None
            self.pending_skill_confirmation = None
            self.pending_attack_target = None
            if not self._commit_move_before_action(actor.char_id):
                return
            self._execute_skill(actor, skill_id, target_id)
            return
        if key in {"round_manual", "round_ai"}:
            self.round_control_mode = "manual" if key == "round_manual" else "round_ai"
            self.control_mode_selected = True
            self.show_control_mode_modal = False
            self.auto_player = key == "round_ai"
            self.speed_index = 1 if self.auto_player else 0
            self.ai_ready_at = pygame.time.get_ticks()
            self.game.report.set_execution_mode("auto" if self.auto_player else "manual")
            self.message = "このラウンドをAIに任せます" if self.auto_player else "このラウンドを手動操作します"
            return
        if key == "round_remaining_ai":
            if self.round_control_mode != "manual" or self.input_locked or self.mode or self.skill_menu:
                self.message = "現在の操作を完了してからAIへ切り替えてください"
                return
            self.round_control_mode = "remaining_ai"
            self.auto_player = True
            self.speed_index = 1
            self._close_command_window()
            self.ai_ready_at = pygame.time.get_ticks()
            self.game.report.set_execution_mode("auto")
            self.message = "このラウンドの残りをAIに任せます"
            return
        if key == "substitution_skip":
            self.mode = None
            self._continue_restart_after_substitution()
            return
        if key.startswith("substitution_out:"):
            char_id = key.split(":", 1)[1]
            if any(unit.char_id == char_id for unit in self.game.field_characters(PLAYER)):
                self.pending_substitution_out_id = char_id
                self.mode = "substitution_in"
                self.message = "ベンチから出場させるキャラクターを選択してください。"
            return
        if key.startswith("substitution_in:"):
            char_id = key.split(":", 1)[1]
            result = self.game.substitute(PLAYER, self.pending_substitution_out_id or "", char_id)
            self.message = result.message
            if result.success:
                self.mode = None
                self._continue_restart_after_substitution()
            return
        if key == "substitution_back":
            self.pending_substitution_out_id = None
            self.mode = "substitution_out"
            self.message = "交代しない、またはフィールドから下げるキャラクターを選択してください。"
            return
        if key == "options":
            self.options_open = not self.options_open
            return
        if key == "options_close":
            self.options_open = False
            return
        if key == "options_full_log":
            self.options_open = False
            key = "full_log"
        elif key == "options_retire":
            self.options_open = False
            key = "retire"
        if key == "action_back":
            self._close_action_menu(reopen_commands=True)
            return
        if key == "action_list_up":
            self._scroll_action_menu(-1)
            return
        if key == "action_list_down":
            self._scroll_action_menu(1)
            return
        if key == "action_detail_up":
            self.action_detail_scroll = max(0, self.action_detail_scroll - 3)
            return
        if key == "action_detail_down":
            self.action_detail_scroll += 3
            return
        if key.startswith("action_focus:"):
            self._focus_action_candidate(int(key.split(":", 1)[1]))
            return
        if key == "action_confirm":
            actor = self.game.current_actor
            candidates = self._action_menu_candidates()
            if actor is None or not 0 <= self.action_menu_selected < len(candidates):
                self.message = "行動データが見つかりません"
                return
            candidate = candidates[self.action_menu_selected]
            if not candidate.usable:
                self.message = candidate.reason or "現在は使用できません"
                return
            self._select_action_candidate(actor, candidate)
            return
        if key == "retire":
            if self.game.match_over:
                self.message = "試合はすでに終了しています"
                return
            if self.input_locked:
                self.message = "行動演出の完了後にリタイアできます"
                return
            self.retire_confirmation = True
            self._close_command_window()
            self.message = "現在の試合をリタイアしますか"
            return
        if key == "full_log":
            if self.input_locked and not self.skip_mode:
                self.message = "行動演出の完了後に全ログを開けます"
                return
            self.full_log_open = True
            self.log_scroll = 0
            self.log_notice = ""
            return
        if key == "auto":
            enabling = not self.auto_player
            if enabling:
                try:
                    self._cancel_current_selection()
                except Exception:
                    LOGGER.exception("オート切り替え前の手動操作取消に失敗しました")
                    self.message = "手動操作を取り消せないため、オート切り替えを中止しました"
                    return
            self.auto_player = enabling
            self._close_command_window()
            state = "有効" if self.auto_player else "無効"
            self.message = f"オートを{state}にしました"
            self.game._log(f"オート操作: {state}")
            if self.auto_player:
                self.game.report.set_execution_mode("auto")
            else:
                self.game.report.set_execution_mode("manual")
            if self.auto_player:
                self.ai_ready_at = pygame.time.get_ticks() + self._auto_delay(250)
                if self.game.restart_team == PLAYER and self.game.restart_prepared:
                    result = self.game.auto_select_restart_holder()
                    self.message = result.message
                    self.mode = None
                    self.last_actor_id = None
            return
        if key == "speed":
            self.speed_index = (self.speed_index + 1) % len(self.speed_levels)
            self.message = f"表示速度: {self.speed_levels[self.speed_index]:g}倍"
            return
        actor = self.game.current_actor
        if actor is None:
            if key == "ability":
                self.message = "確認対象が存在しません"
            return
        if self.input_locked:
            return
        if self.special_move_ignore_zoc and key == "move":
            self.mode = "shadow_move"
            self.skill_menu = False
            self.action_menu_command = None
            self.message = "紫色のZOC無視移動先を選択してください"
            return
        if self.special_move_ignore_zoc and key == "wait":
            if not self._commit_move_before_action(actor.char_id):
                return
            self.special_move_ignore_zoc = False
            result = self.game.wait(actor.char_id)
            self._finish_if_consumed(actor.char_id, result)
            return
        if key == "ability":
            self.ability_modal_actor_id = actor.char_id
            self.ability_status_scroll = 0
            self.message = f"{actor.name}の能力を確認中"
            return
        if key in ACTION_COMMAND_KEYS:
            spec = next(item for item in self._action_command_specs(actor) if item[1] == key)
            if not spec[2]:
                self.message = spec[3] or "現在は選択できません"
                return
            self._open_or_execute_command(actor, key)
            return
        if key == "cut":
            self.mode = "cut"
            self.skill_menu = False
            self.action_menu_command = None
            self._close_command_window()
            self.message = "赤枠の隣接する敵ボール保持者を選択してください"
            return
        if key.startswith("action:"):
            self._handle_action_selection(actor, key.removeprefix("action:"))
            return
        if key.startswith("skill:"):
            skill_id = key.split(":", 1)[1]
            self._start_skill_action(actor, skill_id)
            return
        if key == "confirm_skill":
            if not self.pending_skill_confirmation:
                return
            skill_id, target_id = self.pending_skill_confirmation
            usable, reason = self.game.can_use_skill(actor.char_id, skill_id)
            if not usable:
                self.message = reason
                return
            self.pending_skill_confirmation = None
            if not self._commit_move_before_action(actor.char_id):
                return
            self._execute_skill(actor, skill_id, target_id)
            return
        if key in {"cut_physical", "cut_magic"}:
            if not self.pending_cut_target:
                return
            self.pending_cut_system = key.removeprefix("cut_")
            self.mode = None
            preview = self.game.cut_preview(actor.char_id, self.pending_cut_target, self.pending_cut_system)
            self.message = f"{preview['system_name']}カット / 成功率{preview['success_rate']}%。最終決定してください"
            return
        if key == "confirm_attack":
            if not self.pending_attack_target:
                return
            target_id = self.pending_attack_target
            self.pending_attack_target = None
            if not self._commit_move_before_action(actor.char_id):
                return
            result = self.game.normal_attack(actor.char_id, target_id)
            self._after_attack(actor, result)
            return
        if key == "cancel_attack":
            self.pending_attack_target = None
            self.mode = None
            self.action_menu_command = None
            self._open_command_window(actor)
            self.message = "攻撃を取り消しました"
            return
        if key == "cancel_skill":
            self.pending_skill_confirmation = None
            self.mode = None
            self.action_menu_command = None
            self._open_command_window(actor)
            self.message = "スキル使用を取り消しました"
            return
        if key == "confirm_pass":
            if not self.pending_pass_target or not self.pending_pass_system:
                return
            target_id = self.pending_pass_target
            system = self.pending_pass_system
            skill_id = self.pending_pass_skill_id
            self.pending_pass_target = None
            self.pending_pass_system = None
            self.pending_pass_skill_id = None
            if not self._commit_move_before_action(actor.char_id):
                return
            result = (
                self.game.use_skill(actor.char_id, skill_id, target_id, system)
                if skill_id
                else self.game.pass_ball(actor.char_id, target_id, system)
            )
            self._finish_if_consumed(actor.char_id, result)
            return
        if key == "cancel_pass":
            self.pending_pass_target = None
            self.pending_pass_system = None
            self.pending_pass_skill_id = None
            self.mode = None
            self.action_menu_command = None
            self._open_command_window(actor)
            self.message = "パスを取り消しました"
            return
        if key == "confirm_cut":
            if not self.pending_cut_target or not self.pending_cut_system:
                return
            target_id = self.pending_cut_target
            system = self.pending_cut_system
            self.pending_cut_target = None
            self.pending_cut_system = None
            if not self._commit_move_before_action(actor.char_id):
                return
            result = self.game.cut_ball(actor.char_id, target_id, system)
            self._finish_if_consumed(actor.char_id, result)
            return
        if key == "cancel_cut":
            self.pending_cut_target = None
            self.pending_cut_system = None
            self.mode = None
            self.action_menu_command = None
            self._open_command_window(actor)
            self.message = "カットを取り消しました"
            return
        if key == "cancel_move":
            self._cancel_pending_move(actor)
            return
        if key == "defend":
            if not self._commit_move_before_action(actor.char_id):
                return
            result = self.game.defend(actor.char_id)
            self._finish_if_consumed(actor.char_id, result)
        elif key == "keep":
            if not self._commit_move_before_action(actor.char_id):
                return
            result = self.game.keep_ball(actor.char_id)
            self._finish_if_consumed(actor.char_id, result)
        elif key == "wait":
            if not self._commit_move_before_action(actor.char_id):
                return
            self.special_move_ignore_zoc = False
            result = self.game.wait(actor.char_id)
            self._finish_if_consumed(actor.char_id, result)
        else:
            LOGGER.warning("不明な行動コマンドを無視しました: %s", key)

    def handle_cell(self, cell: tuple[int, int]) -> None:
        assert self.game is not None
        actor = self.game.current_actor
        if actor is None:
            return
        target = self.game.character_at(cell)
        if self.mode in {"move", "shadow_move"}:
            ignore_zoc = self.mode == "shadow_move"
            result = self.game.prepare_move(actor.char_id, cell, ignore_zoc=ignore_zoc)
            if result.success:
                self.mode = None
                self.pending_move_ignore_zoc = ignore_zoc

                def mark_arrived() -> None:
                    assert self.game is not None
                    arrived = self.game.arrive_prepared_move(actor.char_id)
                    self.message = arrived.message
                    if arrived.success:
                        if self.game.is_ball_holder(actor) and actor.position is not None and self.game.is_opponent_goal(actor.team, actor.position):
                            scored = self.game.commit_pending_move(actor.char_id)
                            scored.details.pop("path", None)
                            self._present_result(scored, actor.char_id, advance_after=False)
                            return
                        self.turn_moved = True
                        if ignore_zoc:
                            self.special_move_ignore_zoc = False

                self._present_result(result, actor.char_id, advance_after=False, on_complete=mark_arrived)
            else:
                self.message = result.message
            return
        if self.mode == "attack" and target:
            if target not in self.game.attack_targets(actor.char_id):
                self.message = "その敵は攻撃対象にできません"
                return
            self.pending_attack_target = target.char_id
            self.mode = None
            skill_id = f"normal_{actor.primary_system}_attack"
            self.pending_skill_confirmation = (skill_id, target.char_id)
            self.action_preview = self.game.action_preview(actor.char_id, skill_id, target.char_id)
            self.message = "攻撃予測を確認してください"
            return
        if self.mode == "cut" and target:
            if target not in self.game.cut_targets(actor.char_id):
                self.message = "その敵はカット対象にできません"
                return
            self.pending_cut_target = target.char_id
            self.mode = None
            self.message = "物理カットまたは魔法カットを選択してください"
            return
        if self.mode == "pass" and target:
            if not self.pending_pass_system:
                self.message = "先にパス系統を選択してください"
                return
            if target not in self._pass_targets(actor.char_id, self.pending_pass_system):
                self.message = "その味方へはパスできません"
                return
            self.pending_pass_target = target.char_id
            self.mode = None
            preview = self._pass_preview(actor.char_id, target.char_id, self.pending_pass_system)
            names = [str(item["character_name"]) for item in preview["candidates"]]
            cut_text = f" / カット候補:{','.join(names)}" if names else " / カット候補なし"
            self.message = f"パス先:{target.name}{cut_text}。最終決定してください"
            return
        if self.mode and self.mode.startswith("skill_") and target:
            skill_id = self.mode.removeprefix("skill_")
            valid_targets = self.game.skill_targets(actor.char_id, skill_id)
            if target not in valid_targets:
                self.message = "そのキャラクターは対象にできません"
                return
            skill = self.game.skills[skill_id]
            self.pending_skill_confirmation = (skill_id, target.char_id)
            self.mode = None
            if not self.game._effect_of_type(skill, "pass"):
                self.action_preview = self.game.action_preview(actor.char_id, skill_id, target.char_id)
            self.message = (
                f"{skill.name} / 対象:{target.name} / {self.game.skill_cost_text(skill)}。最終決定してください"
            )
            return
        if target:
            self.selected_id = target.char_id
            if target is actor and actor.team == PLAYER and not actor.acted and not self.auto_player:
                self._open_command_window(actor)
                self.message = "行動を選択してください"
            else:
                self._close_command_window()
                self.message = f"{target.name} の情報を表示しています"
            return
        self._close_command_window()

    def _execute_skill(self, actor: Character, skill_id: str, target_id: str) -> None:
        assert self.game is not None
        if skill_id == "shadow_step":
            result = self.game.use_skill(actor.char_id, skill_id, target_id)
            if not result.success:
                self.message = result.message
                return

            def select_move() -> None:
                self.special_move_ignore_zoc = True
                self.mode = "shadow_move"
                self.message = "紫色のZOC無視移動先を選択してください"

            self._present_result(result, actor.char_id, advance_after=False, on_complete=select_move)
            return
        result = self.game.use_skill(actor.char_id, skill_id, target_id)
        if skill_id == "quick_pass":
            if result.consumed:
                self._present_result(result, actor.char_id, advance_after=True)
            else:
                self.message = result.message
            return
        self._finish_if_consumed(actor.char_id, result)

    def _after_attack(self, actor: Character, result: ActionResult) -> None:
        self.mode = None
        if not result.consumed:
            self.message = result.message
            return
        self._present_result(result, actor.char_id, advance_after=not result.scored)

    def _finish_if_consumed(self, actor_id: str, result: ActionResult) -> None:
        if result.consumed:
            self._present_result(result, actor_id, advance_after=not result.scored)
        else:
            self.message = result.message
        if result.success:
            self.mode = None
            self.skill_menu = False
            self.action_menu_command = None

    def screen_to_cell(self, position: tuple[int, int]) -> tuple[int, int] | None:
        if not self.game:
            return None
        board_rect, cell_size = self._board_geometry()
        if not board_rect.collidepoint(position):
            return None
        local_x = position[0] - board_rect.x
        local_y = position[1] - board_rect.y
        cell = (local_x // cell_size, local_y // cell_size)
        return cell if self.game.in_bounds(cell) else None

    def _board_geometry(self) -> tuple[pygame.Rect, int]:
        if not self.game:
            return pygame.Rect(FIELD_RECT.x, FIELD_RECT.y, CELL_SIZE * 13, CELL_SIZE * 5), CELL_SIZE
        width = max(1, self.game.config.field_width)
        height = max(1, self.game.config.field_height)
        cell_size = max(1, min(FIELD_RECT.width // width, FIELD_RECT.height // height))
        board_width = width * cell_size
        board_height = height * cell_size
        return pygame.Rect(
            FIELD_RECT.x + (FIELD_RECT.width - board_width) // 2,
            FIELD_RECT.y + (FIELD_RECT.height - board_height) // 2,
            board_width,
            board_height,
        ), cell_size

    def _cell_size(self) -> tuple[int, int]:
        _, cell_size = self._board_geometry()
        return cell_size, cell_size

    def cell_rect(self, cell: tuple[int, int]) -> pygame.Rect:
        board_rect, cell_size = self._board_geometry()
        return pygame.Rect(
            board_rect.x + cell[0] * cell_size,
            board_rect.y + cell[1] * cell_size,
            cell_size,
            cell_size,
        )

    def draw(self) -> None:
        self.screen.fill((18, 24, 38))
        if self.state == "title":
            self.draw_title()
        elif self.state == "quest_select":
            self.draw_quest_select()
        elif self.state == "party":
            self.draw_party_setup()
        elif self.game:
            self.draw_match()

    def draw_title(self) -> None:
        self.buttons = []
        self._center_text("Mana's Ball", 72, (224, 238, 255), 190)
        self._center_text("1対1～6対6 ターン制ファンタジー球技", 30, (142, 196, 225), 285)
        title_buttons = (
            Button(pygame.Rect(500, 410, 440, 68), "クエスト", "quest"),
            Button(pygame.Rect(500, 500, 440, 58), "自由試合", "debug_start"),
            Button(pygame.Rect(590, 585, 260, 52), "終了", "quit"),
        )
        self.buttons.extend(title_buttons)
        self._draw_button(title_buttons[0], 28)
        self._draw_button(title_buttons[1], 23)
        self._draw_button(title_buttons[2], 22)
        if self.title_notice:
            self._center_text(self.title_notice, 17, (255, 151, 151), 615)
        self._center_text("クリックとタップは同じ操作として処理されます", 20, (150, 160, 178), 690)

    def draw_quest_select(self) -> None:
        self.buttons = []
        self.screen.fill((11, 20, 32))
        pygame.draw.rect(self.screen, (19, 35, 51), (0, 0, WINDOW_SIZE[0], 126))
        self._center_text("クエスト選択", 42, (245, 222, 158), 38)
        self._center_text("挑戦するクエストを選び、条件を確認して編成へ進みます", 18, (158, 191, 219), 82)
        self._draw_panel(QUEST_LIST_RECT)
        self._draw_panel(QUEST_DETAIL_RECT)
        self._text("一覧", 22, GOLD_LIGHT, (QUEST_LIST_RECT.x + 18, QUEST_LIST_RECT.y + 14))
        maximum_scroll = max(0, len(self.quest_select_ids) - 5)
        self.quest_select_scroll = min(self.quest_select_scroll, maximum_scroll)
        scroll_buttons = (
            Button(pygame.Rect(QUEST_LIST_RECT.right - 96, QUEST_LIST_RECT.y + 12, 36, 34), "▲", "quest_up", self.quest_select_scroll > 0),
            Button(pygame.Rect(QUEST_LIST_RECT.right - 52, QUEST_LIST_RECT.y + 12, 36, 34), "▼", "quest_down", self.quest_select_scroll < maximum_scroll),
        )
        self.buttons.extend(scroll_buttons)
        for button in scroll_buttons:
            self._draw_button(button, 14)
        if not self.quest_select_ids:
            self._draw_wrapped(
                self.quest_select_message or "有効なクエストがありません。data/csv/quests.csv を確認してください",
                18,
                (255, 151, 151),
                pygame.Rect(QUEST_LIST_RECT.x + 24, QUEST_LIST_RECT.y + 92, QUEST_LIST_RECT.width - 48, 120),
            )
        for row_index, quest_id in enumerate(self.quest_select_ids[self.quest_select_scroll:self.quest_select_scroll + 5]):
            setup = self.quest_select_setups[quest_id]
            rect = pygame.Rect(QUEST_LIST_RECT.x + 16, QUEST_LIST_RECT.y + 62 + row_index * 104, QUEST_LIST_RECT.width - 32, 92)
            selected = quest_id == self.quest_selected_id
            pygame.draw.rect(self.screen, (34, 65, 83) if selected else (15, 38, 55), rect, border_radius=6)
            pygame.draw.rect(self.screen, GOLD_LIGHT if selected else (69, 92, 111), rect, 3 if selected else 1, border_radius=6)
            self._text(setup.quest.name, 19, (244, 234, 212), (rect.x + 14, rect.y + 10))
            self._text(
                f"出場 {setup.match.ally_field_count}対{setup.match.enemy_field_count} / "
                f"上限 {setup.match.ally_party_limit}対{setup.match.enemy_party_limit} / "
                f"{setup.layout.width}x{setup.layout.height}",
                13,
                (255, 201, 143),
                (rect.x + 14, rect.y + 40),
            )
            self._text((setup.quest.description or "説明なし")[:32], 13, (177, 193, 207), (rect.x + 14, rect.y + 63))
            self.buttons.append(Button(rect, setup.quest.name, f"quest_item:{quest_id}"))

        setup = self.quest_select_setups.get(self.quest_selected_id or "")
        self._text("クエスト詳細", 22, GOLD_LIGHT, (QUEST_DETAIL_RECT.x + 22, QUEST_DETAIL_RECT.y + 14))
        if setup is None:
            self._center_text_at("左の一覧からクエストを選択してください", 18, (166, 185, 202), QUEST_DETAIL_RECT.center)
        else:
            group = self.quest_select_enemy_inputs.get(setup.quest.quest_id)
            enemy_group_name = group.group.name if group else setup.quest.enemy_group_id
            self._text(setup.quest.name, 30, (247, 226, 184), (QUEST_DETAIL_RECT.x + 26, QUEST_DETAIL_RECT.y + 56))
            self._text(f"ID: {setup.quest.quest_id}", 13, (156, 183, 205), (QUEST_DETAIL_RECT.x + 28, QUEST_DETAIL_RECT.y + 94))
            self._draw_wrapped(
                setup.quest.description or "説明なし",
                15,
                (204, 216, 226),
                pygame.Rect(QUEST_DETAIL_RECT.x + 28, QUEST_DETAIL_RECT.y + 122, QUEST_DETAIL_RECT.width - 56, 48),
            )
            rows = (
                ("出場", f"{setup.match.ally_field_count}対{setup.match.enemy_field_count}"),
                ("ベンチ最大", f"味方{setup.match.ally_party_limit - setup.match.ally_field_count} / 敵{setup.match.enemy_party_limit - setup.match.enemy_field_count}"),
                ("フィールド", setup.field.name or setup.field.field_id),
                ("フィールドサイズ", f"{setup.layout.width}×{setup.layout.height}"),
                ("敵グループ", enemy_group_name),
                ("味方出場人数", str(setup.match.ally_field_count)),
                ("敵出場人数", str(setup.match.enemy_field_count)),
                ("味方パーティー上限", str(setup.match.ally_party_limit)),
                ("敵パーティー上限", str(setup.match.enemy_party_limit)),
                ("勝利必要得点", str(setup.match.score_to_win)),
                ("最大ターン数", str(setup.match.max_turns or "制限なし")),
                ("交代機能", "あり" if setup.match.substitution_enabled else "なし"),
                ("負傷機能", "あり" if setup.match.injury_enabled else "なし"),
                ("推奨レベル", str(setup.quest.recommended_level or "-")),
                ("推奨コスト", str(setup.quest.recommended_cost or "-")),
            )
            for index, (label, value) in enumerate(rows):
                column = index % 2
                row = index // 2
                x = QUEST_DETAIL_RECT.x + 32 + column * 390
                y = QUEST_DETAIL_RECT.y + 196 + row * 52
                self._text(label, 13, (147, 172, 194), (x, y))
                self._text(value, 18, (232, 234, 222), (x, y + 20))
        footer_buttons = (
            Button(pygame.Rect(484, 798, 220, 50), "決定", "quest_confirm", setup is not None, "クエストを選択してください"),
            Button(pygame.Rect(736, 798, 220, 50), "戻る", "quest_back"),
        )
        self.buttons.extend(footer_buttons)
        for button in footer_buttons:
            self._draw_button(button, 20)
        if self.quest_select_message:
            self._center_text(self.quest_select_message, 17, (255, 218, 125), 774)

    def draw_party_setup(self) -> None:
        self.buttons = []
        self.screen.fill((11, 20, 32))
        pygame.draw.rect(self.screen, (19, 35, 51), (0, 0, WINDOW_SIZE[0], 126))
        title = "自由試合設定" if self.party_debug_mode else "クエスト編成"
        setup = self._selected_quest_setup()
        mode_label = FREE_ENEMY_MODE_LABELS.get(self.party_free_enemy_mode, "敵グループ")
        subtitle = (
            f"敵方式:{mode_label} / 出場 {self.party_debug_player_count}対{self.party_debug_enemy_count} / "
            f"参加 {self.party_debug_player_party_count}対{self.party_debug_enemy_party_count} / "
            f"交代{'あり' if self.party_debug_substitution_enabled else 'なし'} / "
            f"負傷{'あり' if self.party_debug_injury_enabled else 'なし'}"
        ) if self.party_debug_mode else (
            f"{setup.quest.name} / {setup.field.name or setup.field.field_id} / "
            f"敵:{self._party_enemy_group_name()} / 出場 {setup.match.ally_field_count}対{setup.match.enemy_field_count} / "
            f"交代{'あり' if setup.match.substitution_enabled else 'なし'} / "
            f"負傷{'あり' if setup.match.injury_enabled else 'なし'}" if setup else "クエスト設定なし"
        )
        self._center_text(title, 38, (255, 187, 105) if self.party_debug_mode else (245, 222, 158), 30)
        self._center_text(subtitle, 18, (255, 158, 124) if self.party_debug_mode else (158, 191, 219), 66)

        sort_label = dict(PARTY_SORT_OPTIONS)[self.party_sort_key]
        filters = (Button(pygame.Rect(16, 84, 550, 44), f"並び替え：{sort_label}", "party_sort"),)
        self.buttons.extend(filters)
        for button in filters:
            self._draw_button(button, 17)
        if self.party_debug_mode:
            mode_button = Button(pygame.Rect(1004, 84, 200, 44), f"敵方式：{mode_label}", "party_free_enemy_mode")
            debug_button = Button(pygame.Rect(1214, 84, 210, 44), "自由試合設定", "party_debug_open")
            self.buttons.extend((mode_button, debug_button))
            self._draw_button(mode_button, 14)
            self._draw_button(debug_button, 16)

        self._draw_panel(PARTY_ROSTER_RECT)
        self._text("キャラクター一覧", 20, GOLD_LIGHT, (PARTY_ROSTER_RECT.x + 16, PARTY_ROSTER_RECT.y + 12))
        filtered = self._party_filtered_roster()
        maximum_scroll = max(0, len(filtered) - 8)
        self.party_scroll = min(self.party_scroll, maximum_scroll)
        scroll_buttons = (
            Button(pygame.Rect(468, 151, 38, 34), "▲", "party_scroll_up", self.party_scroll > 0),
            Button(pygame.Rect(512, 151, 38, 34), "▼", "party_scroll_down", self.party_scroll < maximum_scroll),
        )
        self.buttons.extend(scroll_buttons)
        for button in scroll_buttons:
            self._draw_button(button, 15)
        self._text(f"{len(filtered)}人", 14, (158, 179, 201), (420, 160))
        assigned_ids = {
            char_id
            for char_id in (
                (*self.party_player_ids, *self.party_enemy_ids)
                if self.party_debug_mode and self.party_free_enemy_mode == FREE_ENEMY_MANUAL_MODE
                else self.party_player_ids
                if self.party_debug_mode
                else self.party_player_ids
            )
            if char_id
        }
        if not filtered:
            empty_text = "キャラクターデータがありません" if not self.party_roster else "該当するキャラクターがいません"
            self._center_text_at(empty_text, 18, (177, 187, 201), PARTY_ROSTER_RECT.center)
        for row_index, member in enumerate(filtered[self.party_scroll:self.party_scroll + 8]):
            rect = pygame.Rect(PARTY_ROSTER_RECT.x + 12, 194 + row_index * 63, PARTY_ROSTER_RECT.width - 24, 57)
            selected = member.char_id == self.party_selected_character_id
            fill = (30, 69, 91) if selected else (12, 31, 47)
            pygame.draw.rect(self.screen, fill, rect, border_radius=5)
            pygame.draw.rect(self.screen, GOLD_LIGHT if selected else (69, 92, 111), rect, 2, border_radius=5)
            icon = self.circular_icon(member.char_id, 44)
            if icon:
                self.screen.blit(icon, icon.get_rect(center=(rect.x + 30, rect.centery)))
            else:
                pygame.draw.circle(self.screen, (65, 88, 106), (rect.x + 30, rect.centery), 21)
                self._center_text_at(member.name[:1], 17, (226, 235, 244), (rect.x + 30, rect.centery))
            self._text(member.name, 17, (239, 239, 230), (rect.x + 60, rect.y + 6))
            self._text(
                f"力{self._party_member_value(member, 'power')} 魔{self._party_member_value(member, 'magic')} 速{self._party_member_value(member, 'speed')} "
                f"技{self._party_member_value(member, 'technique')} 体{self._party_member_value(member, 'stamina')}",
                13, (195, 208, 220), (rect.x + 60, rect.y + 31),
            )
            if member.char_id in assigned_ids:
                self._text("編成済", 13, (255, 206, 103), (rect.right - 58, rect.y + 4))
            self.buttons.append(Button(rect, member.name, f"party_roster:{member.char_id}"))

        self._draw_party_detail()
        self._draw_party_totals_panel()
        self._draw_party_teams()

        complete = self._party_is_complete()
        action_buttons = [
            Button(pygame.Rect(16, 730, 170, 48), "味方に追加", "party_add_ally", bool(self.party_selected_character_id)),
            Button(pygame.Rect(196, 730, 130, 48), "外す", "party_remove", self.party_selected_slot is not None),
            Button(pygame.Rect(336, 730, 150, 48), "初期編成", "party_default"),
            Button(pygame.Rect(496, 730, 130, 48), "全解除", "party_clear"),
        ]
        if self.party_debug_mode:
            action_buttons.insert(1, Button(
                pygame.Rect(196, 730, 170, 48),
                "敵に追加",
                "party_add_enemy",
                bool(self.party_selected_character_id) and self.party_free_enemy_mode == FREE_ENEMY_MANUAL_MODE,
                "敵グループ方式では使用しません",
            ))
            action_buttons[2].rect.x = 376
            action_buttons[3].rect.x = 516
            action_buttons[4].rect.x = 676
        action_buttons.extend((
            Button(
                pygame.Rect(960, 730, 230, 48),
                "自由試合開始" if self.party_debug_mode else "試合開始",
                "party_start",
                complete,
                "有効な味方枠と敵枠を編成してください",
            ),
            Button(
                pygame.Rect(1200, 730, 224, 48),
                "ホームへ戻る" if self.party_debug_mode else "クエスト選択へ戻る",
                "party_back",
            ),
        ))
        self.buttons.extend(action_buttons)
        for button in action_buttons:
            self._draw_button(button, 17)
        message_color = (255, 218, 125) if self.party_message else (173, 190, 207)
        self._center_text(self.party_message or "編成枠の順番が試合開始時の初期配置順になります", 18, message_color, 812)
        footer = (
            "一覧選択 → 味方へ追加／敵方式に応じて敵グループ選択または敵へ追加　｜　ホイールまたは▲▼で一覧移動"
            if self.party_debug_mode
            else "一覧から味方を編成します。敵グループは選択したクエストで固定されます"
        )
        self._center_text(footer, 15, (139, 162, 183), 856)
        if self.party_skill_editor_open:
            self._draw_party_skill_editor()
        if self.party_debug_settings_open:
            self._draw_party_debug_settings()
        if self.party_enemy_group_selector_open:
            self._draw_enemy_group_selector()

    def _draw_party_detail(self) -> None:
        self._draw_panel(PARTY_DETAIL_RECT)
        self._text("選択キャラクター詳細", 20, GOLD_LIGHT, (PARTY_DETAIL_RECT.x + 16, PARTY_DETAIL_RECT.y + 12))
        member = self._party_member(self.party_selected_character_id)
        if member is None:
            self._center_text_at("一覧からキャラクターを選択してください", 17, (161, 177, 194), PARTY_DETAIL_RECT.center)
            return
        portrait = self.portrait(member.char_id)
        portrait_rect = pygame.Rect(PARTY_DETAIL_RECT.x + 18, PARTY_DETAIL_RECT.y + 54, 110, 122)
        if portrait:
            self.screen.blit(pygame.transform.smoothscale(portrait, portrait_rect.size), portrait_rect)
        else:
            pygame.draw.rect(self.screen, (31, 53, 68), portrait_rect)
            self._center_text_at(member.name[:1], 34, (217, 227, 235), portrait_rect.center)
        pygame.draw.rect(self.screen, GOLD, portrait_rect, 2)
        x = PARTY_DETAIL_RECT.x + 144
        self._text(member.name, 23, (241, 237, 215), (x, PARTY_DETAIL_RECT.y + 50))
        self._text(
            f"HP {self._party_member_value(member, 'max_hp')}   MP {self._party_member_value(member, 'max_mana')}",
            15, (209, 220, 231), (x, PARTY_DETAIL_RECT.y + 90),
        )
        stats = (
            ("パワー", self._party_member_value(member, "power")),
            ("マジック", self._party_member_value(member, "magic")),
            ("スピード", self._party_member_value(member, "speed")),
            ("テクニック", self._party_member_value(member, "technique")),
            ("スタミナ", self._party_member_value(member, "stamina")),
        )
        for index, (label, value) in enumerate(stats):
            column = index % 2
            row = index // 2
            self._text(f"{label}：{value}", 15, (221, 224, 221), (PARTY_DETAIL_RECT.x + 24 + column * 185, PARTY_DETAIL_RECT.y + 196 + row * 27))
        equipped = self._party_equipped_skills(member.char_id)
        skill_names = [self.party_skills[skill_id].name for skill_id in equipped if skill_id in self.party_skills]
        skill_text = "、".join(skill_names) if skill_names else "なし"
        self._draw_wrapped(
            f"設定スキル：{skill_text}", 12, (194, 208, 220),
            pygame.Rect(PARTY_DETAIL_RECT.x + 24, PARTY_DETAIL_RECT.y + 286, 220, 54),
        )
        skill_button = Button(
            pygame.Rect(PARTY_DETAIL_RECT.right - 154, PARTY_DETAIL_RECT.bottom - 45, 138, 32),
            "スキル交代",
            "party_skill_open",
            bool(equipped),
            "交代できるスキル枠がありません",
        )
        self.buttons.append(skill_button)
        self._draw_button(skill_button, 14)

    def _draw_enemy_group_selector(self) -> None:
        overlay = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        overlay.fill((2, 6, 12, 232))
        self.screen.blit(overlay, (0, 0))
        panel = pygame.Rect(24, 28, 1392, 844)
        list_panel = pygame.Rect(48, 96, 374, 682)
        detail_panel = pygame.Rect(442, 96, 950, 682)
        self._draw_panel(panel, (8, 20, 33, 252), GOLD_LIGHT)
        self._draw_panel(list_panel, (10, 30, 47, 248), (93, 143, 178))
        self._draw_panel(detail_panel, (10, 30, 47, 248), (93, 143, 178))
        self._text("敵グループ選択", 30, (248, 226, 165), (panel.x + 26, panel.y + 18))
        self._text("一覧", 19, GOLD_LIGHT, (list_panel.x + 16, list_panel.y + 12))

        maximum_scroll = max(0, len(self.party_enemy_groups) - 6)
        self.party_enemy_group_list_scroll = min(self.party_enemy_group_list_scroll, maximum_scroll)
        scroll_buttons = (
            Button(pygame.Rect(list_panel.right - 92, list_panel.y + 8, 34, 32), "▲", "party_enemy_group_up", self.party_enemy_group_list_scroll > 0),
            Button(pygame.Rect(list_panel.right - 50, list_panel.y + 8, 34, 32), "▼", "party_enemy_group_down", self.party_enemy_group_list_scroll < maximum_scroll),
        )
        self.buttons.extend(scroll_buttons)
        for button in scroll_buttons:
            self._draw_button(button, 14)
        if not self.party_enemy_groups:
            self._center_text_at("利用可能な敵グループがありません", 17, (255, 151, 151), list_panel.center)
        for index, group in enumerate(
            self.party_enemy_groups[
                self.party_enemy_group_list_scroll:self.party_enemy_group_list_scroll + 6
            ]
        ):
            rect = pygame.Rect(list_panel.x + 12, list_panel.y + 52 + index * 100, list_panel.width - 24, 90)
            selected = group.group.enemy_group_id == self.party_enemy_group_candidate_id
            fill = (45, 68, 79) if selected else (18, 42, 58)
            pygame.draw.rect(self.screen, fill, rect, border_radius=6)
            pygame.draw.rect(self.screen, GOLD_LIGHT if selected else (69, 92, 111), rect, 2, border_radius=6)
            names = " / ".join(row["name"] for row in self._party_enemy_group_rows(group))
            self._text(group.group.name, 17, (244, 234, 212), (rect.x + 12, rect.y + 8))
            self._text(f"倍率 x{group.group.enemy_scale:g}", 13, (255, 188, 137), (rect.right - 92, rect.y + 10))
            self._text(names[:27], 12, (183, 204, 219), (rect.x + 12, rect.y + 36))
            self._text((group.group.description or "説明なし")[:30], 12, (157, 177, 194), (rect.x + 12, rect.y + 61))
            self.buttons.append(Button(rect, group.group.name, f"party_enemy_group_item:{group.group.enemy_group_id}"))

        group = self._party_enemy_group(self.party_enemy_group_candidate_id)
        self._text("グループ詳細", 19, GOLD_LIGHT, (detail_panel.x + 18, detail_panel.y + 12))
        if group is None:
            self._center_text_at("左の一覧から敵グループを選択してください", 18, (166, 185, 202), detail_panel.center)
        else:
            self._text(group.group.name, 24, (247, 226, 184), (detail_panel.x + 20, detail_panel.y + 46))
            self._text(f"ID: {group.group.enemy_group_id}", 13, (156, 183, 205), (detail_panel.x + 22, detail_panel.y + 80))
            self._text(f"能力倍率 x{group.group.enemy_scale:g}", 15, (255, 188, 137), (detail_panel.x + 340, detail_panel.y + 51))
            common_profile = self._party_profile(group.group.group_ai_profile_id) if group.group.group_ai_profile_id else None
            self._text(
                f"共通AI: {common_profile.name if common_profile else '指定なし'}",
                15,
                (177, 205, 226),
                (detail_panel.x + 570, detail_panel.y + 51),
            )
            self._text((group.group.description or "説明なし")[:62], 13, (184, 198, 210), (detail_panel.x + 22, detail_panel.y + 104))
            for index, (char_id, row, profile_id) in enumerate(
                zip(group.enemy_ids, self._party_enemy_group_rows(group), group.ai_profile_ids)
            ):
                card = pygame.Rect(detail_panel.x + 16, detail_panel.y + 134 + index * 172, detail_panel.width - 32, 160)
                pygame.draw.rect(self.screen, (35, 31, 43), card, border_radius=7)
                pygame.draw.rect(self.screen, (119, 78, 91), card, 2, border_radius=7)
                pygame.draw.circle(self.screen, (82, 58, 70), (card.x + 40, card.y + 42), 28)
                self._center_text_at(row["name"][:1], 20, (242, 224, 218), (card.x + 40, card.y + 42))
                metadata = group.metadata[char_id]
                profile = self._party_profile(profile_id)
                self._text(f"{index + 1}. {row['name']}", 19, (247, 234, 215), (card.x + 78, card.y + 12))
                self._text(
                    f"タイプ {metadata['enemy_type_id'] or '未設定'} / 5能力体系",
                    13,
                    (174, 202, 222),
                    (card.x + 78, card.y + 42),
                )
                self._text(
                    f"HP {row['max_hp']}  MP {row['max_mana']}  パワー {row['power']}  マジック {row['magic']}  スピード {row['speed']}",
                    13,
                    (220, 222, 216),
                    (card.x + 20, card.y + 76),
                )
                self._text(
                    f"テクニック {row['technique']}  スタミナ {row['stamina']}  移動 {row['move_range']}",
                    13,
                    (220, 222, 216),
                    (card.x + 20, card.y + 102),
                )
                skill_names = [self.party_skills[skill_id].name for skill_id in group.skill_overrides[char_id] if skill_id in self.party_skills]
                self._text(
                    f"スキル: {' / '.join(skill_names) if skill_names else 'なし'}",
                    12,
                    (190, 208, 220),
                    (card.x + 20, card.y + 130),
                )
                self._text(
                    f"最終AI: {profile.name if profile else '標準型'}",
                    12,
                    (255, 201, 143),
                    (card.right - 180, card.y + 130),
                )

        footer_buttons = (
            Button(
                pygame.Rect(panel.centerx - 246, panel.bottom - 70, 220, 46),
                "決定",
                "party_enemy_group_apply",
                group is not None,
                "敵グループを選択してください",
            ),
            Button(pygame.Rect(panel.centerx + 26, panel.bottom - 70, 220, 46), "戻る", "party_enemy_group_back"),
        )
        self.buttons.extend(footer_buttons)
        for button in footer_buttons:
            self._draw_button(button, 18)

    def _draw_party_debug_settings(self) -> None:
        overlay = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        overlay.fill((2, 6, 12, 224))
        self.screen.blit(overlay, (0, 0))
        panel = pygame.Rect(120, 55, 1200, 790)
        self._draw_panel(panel, (13, 24, 36, 252), (238, 145, 86))
        self._text("自由試合設定", 30, (255, 202, 135), (panel.x + 28, panel.y + 18))
        self._text("前回設定JSONだけを更新します。CSVや正式セーブデータは変更しません", 15, (255, 161, 135), (panel.x + 30, panel.y + 60))

        def adjust_row(label: str, value: int, key_prefix: str, x: int, y: int, width: int = 330) -> None:
            self._text(label, 17, (217, 227, 237), (x, y + 8))
            minus = Button(pygame.Rect(x + width - 124, y, 36, 36), "-", f"{key_prefix}:-1")
            plus = Button(pygame.Rect(x + width - 40, y, 36, 36), "+", f"{key_prefix}:1")
            self.buttons.extend((minus, plus))
            self._draw_button(minus, 20)
            self._draw_button(plus, 20)
            self._center_text_at(str(value), 18, (255, 231, 171), (x + width - 64, y + 18))

        left = panel.x + 30
        mode_label = FREE_ENEMY_MODE_LABELS.get(self.party_free_enemy_mode, "敵グループ")
        mode_button = Button(pygame.Rect(left + 780, panel.y + 100, 330, 38), f"敵方式：{mode_label}", "party_free_enemy_mode")
        self.buttons.append(mode_button)
        self._draw_button(mode_button, 15)
        adjust_row("味方参加人数", self.party_debug_player_party_count, f"party_debug_party_count:{PLAYER}", left, panel.y + 105)
        adjust_row("味方出場人数", self.party_debug_player_count, f"party_debug_count:{PLAYER}", left + 370, panel.y + 105)
        adjust_row("敵参加人数", self.party_debug_enemy_party_count, f"party_debug_party_count:{ENEMY}", left, panel.y + 153)
        adjust_row("敵出場人数", self.party_debug_enemy_count, f"party_debug_count:{ENEMY}", left + 370, panel.y + 153)
        adjust_row("先取点", self.party_debug_target_score, "party_debug_match:score", left, panel.y + 201)
        adjust_row("ターン制限", self.party_debug_turn_limit, "party_debug_match:turn", left + 370, panel.y + 201)
        toggle_buttons = (
            Button(
                pygame.Rect(left + 780, panel.y + 150, 150, 36),
                f"交代:{'有' if self.party_debug_substitution_enabled else '無'}",
                "party_debug_toggle:substitution",
            ),
            Button(
                pygame.Rect(left + 960, panel.y + 150, 150, 36),
                f"負傷:{'有' if self.party_debug_injury_enabled else '無'}",
                "party_debug_toggle:injury",
            ),
        )
        self.buttons.extend(toggle_buttons)
        for button in toggle_buttons:
            self._draw_button(button, 14)
        self._text("敵手動編成時のAIは背面の各敵枠にあるAIボタンで変更できます", 14, (161, 190, 212), (left + 760, panel.y + 202))

        member = self._party_member(self.party_selected_character_id)
        parameter_panel = pygame.Rect(left, panel.y + 258, 740, 402)
        placement_panel = pygame.Rect(left + 760, panel.y + 258, 380, 402)
        self._draw_panel(parameter_panel, (10, 31, 47, 248), (98, 135, 162))
        self._draw_panel(placement_panel, (10, 31, 47, 248), (98, 135, 162))
        self._text(
            f"一時パラメータ：{member.name if member else 'キャラクター未選択'}",
            20, GOLD_LIGHT, (parameter_panel.x + 18, parameter_panel.y + 14),
        )
        if member:
            for index, (field, label, _minimum, _maximum) in enumerate(DEBUG_PARAMETER_FIELDS):
                column = index % 2
                row = index // 2
                x = parameter_panel.x + 18 + column * 350
                y = parameter_panel.y + 62 + row * 78
                adjust_row(label, self._party_member_value(member, field), f"party_debug_param:{field}", x, y, 320)
        else:
            self._text("背面の一覧から変更対象を選択してください", 16, (169, 188, 204), (parameter_panel.x + 22, parameter_panel.y + 74))

        self._text("選択枠の初期配置", 20, GOLD_LIGHT, (placement_panel.x + 18, placement_panel.y + 14))
        if self.party_selected_slot:
            team, index = self.party_selected_slot
            positions = self.party_debug_player_positions if team == PLAYER else self.party_debug_enemy_positions
            position = positions[index]
            self._text(f"{'味方' if team == PLAYER else '敵'} {index + 1}枠：{position}", 18, (229, 228, 208), (placement_panel.x + 20, placement_panel.y + 58))
            adjust_row("X座標", position[0], "party_debug_position:x", placement_panel.x + 20, placement_panel.y + 105, 330)
            adjust_row("Y座標", position[1], "party_debug_position:y", placement_panel.x + 20, placement_panel.y + 160, 330)
        else:
            self._draw_wrapped(
                "背面の味方・敵編成枠を選択してから配置を変更してください。配置重複時は試合を開始できません。",
                16, (169, 188, 204), placement_panel.inflate(-36, -100),
            )
        self._text("設定対象", 16, (174, 198, 218), (placement_panel.x + 20, placement_panel.y + 250))
        self._draw_wrapped(
            f"・敵方式：{mode_label}\n・参加人数と出場人数（各1～{MAX_PARTY_SIZE}人）\n・出場超過分はベンチ\n・交代／負傷の有無\n・系統値と4基礎能力\n・最大HP／最大MP\n・先取点とターン制限",
            15, (199, 214, 225), pygame.Rect(placement_panel.x + 20, placement_panel.y + 280, 335, 140),
        )

        footer = (
            Button(pygame.Rect(panel.x + 85, panel.bottom - 68, 230, 44), "現在設定を保存", "party_debug_save"),
            Button(pygame.Rect(panel.x + 345, panel.bottom - 68, 230, 44), "最後の設定を再読込", "party_debug_reload"),
            Button(pygame.Rect(panel.x + 605, panel.bottom - 68, 230, 44), "CSV初期設定へ戻す", "party_debug_reset"),
            Button(pygame.Rect(panel.x + 865, panel.bottom - 68, 230, 44), "自由試合へ戻る", "party_debug_close"),
        )
        self.buttons.extend(footer)
        for button in footer:
            self._draw_button(button, 16)

    def _draw_party_skill_editor(self) -> None:
        overlay = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        overlay.fill((2, 6, 12, 218))
        self.screen.blit(overlay, (0, 0))
        panel = pygame.Rect(170, 60, 1100, 780)
        self._draw_panel(panel, (8, 22, 36, 248), GOLD_LIGHT)
        member = self._party_member(self.party_selected_character_id)
        if member is None:
            self.party_skill_editor_open = False
            return
        equipped = self._party_equipped_skills(member.char_id)
        candidates = self._party_skill_candidates()
        self._text(f"スキル交代：{member.name}", 28, (248, 226, 165), (panel.x + 28, panel.y + 20))
        self._text("交換する現在枠と、設定する候補を選択してください", 15, (166, 192, 213), (panel.x + 30, panel.y + 62))

        current_panel = pygame.Rect(panel.x + 24, panel.y + 96, 400, 560)
        candidate_panel = pygame.Rect(panel.x + 440, panel.y + 96, 636, 560)
        self._draw_panel(current_panel, (10, 30, 47, 245), (93, 143, 178))
        self._draw_panel(candidate_panel, (10, 30, 47, 245), (93, 143, 178))
        normal_count = sum(self.party_skills.get(sid) and self.party_skills[sid].equip_slot == "normal" for sid in equipped)
        hold_count = sum(self.party_skills.get(sid) and self.party_skills[sid].equip_slot == "ball_hold" for sid in equipped)
        self._text(f"通常スキル {normal_count} / 6　ボール保持 {hold_count} / 1", 17, (157, 209, 239), (current_panel.x + 18, current_panel.y + 14))
        self._text(f"交換候補：有効な実装済みスキル {len(candidates)}件", 19, (157, 209, 239), (candidate_panel.x + 18, candidate_panel.y + 14))

        for index, skill_id in enumerate(equipped):
            skill = self.party_skills.get(skill_id)
            rect = pygame.Rect(current_panel.x + 16, current_panel.y + 54 + index * 82, current_panel.width - 32, 70)
            selected = index == self.party_skill_slot_index
            pygame.draw.rect(self.screen, (31, 73, 96) if selected else (17, 43, 62), rect, border_radius=5)
            pygame.draw.rect(self.screen, GOLD_LIGHT if selected else (75, 104, 126), rect, 3 if selected else 1, border_radius=5)
            slot_label = "保持" if skill and skill.equip_slot == "ball_hold" else "通常"
            self._text(slot_label, 14, GOLD_LIGHT, (rect.x + 12, rect.y + 9))
            self._text(skill.name if skill else skill_id, 18, (237, 237, 224), (rect.x + 64, rect.y + 7))
            if skill:
                self._text(skill.description[:20], 12, (169, 188, 204), (rect.x + 64, rect.y + 40))
            self.buttons.append(Button(rect, skill.name if skill else skill_id, f"party_skill_slot:{index}"))

        maximum_scroll = max(0, len(candidates) - 8)
        self.party_skill_scroll = min(self.party_skill_scroll, maximum_scroll)
        scroll_buttons = (
            Button(pygame.Rect(candidate_panel.right - 92, candidate_panel.y + 10, 32, 32), "▲", "party_skill_scroll_up", self.party_skill_scroll > 0),
            Button(pygame.Rect(candidate_panel.right - 50, candidate_panel.y + 10, 32, 32), "▼", "party_skill_scroll_down", self.party_skill_scroll < maximum_scroll),
        )
        self.buttons.extend(scroll_buttons)
        for button in scroll_buttons:
            self._draw_button(button, 13)
        equipped_other = {
            skill_id for index, skill_id in enumerate(equipped)
            if index != self.party_skill_slot_index
        }
        for row_index, skill in enumerate(candidates[self.party_skill_scroll:self.party_skill_scroll + 8]):
            rect = pygame.Rect(candidate_panel.x + 16, candidate_panel.y + 54 + row_index * 61, candidate_panel.width - 32, 53)
            selected = skill.skill_id == self.party_skill_candidate_id
            unavailable = skill.skill_id in equipped_other
            fill = (39, 75, 91) if selected else (17, 43, 62)
            if unavailable:
                fill = (42, 45, 51)
            pygame.draw.rect(self.screen, fill, rect, border_radius=5)
            pygame.draw.rect(self.screen, GOLD_LIGHT if selected else (75, 104, 126), rect, 2 if selected else 1, border_radius=5)
            self._text(skill.name, 16, (231, 232, 219) if not unavailable else (132, 136, 142), (rect.x + 12, rect.y + 5))
            system_label = {"physical": "物理", "magic": "魔法", "common": "能力"}.get(skill.skill_system, skill.skill_system)
            cost = f"MP{skill.resource_cost}" if skill.resource_type == "mana" else "消費なし"
            command = "ボール保持・自動" if skill.activation == "automatic" and skill.purpose_tag == "ball_hold" else COMMAND_GROUP_LABELS.get(skill.command_group, skill.command_group)
            move = "移動後可" if skill.usable_after_move else "移動後不可"
            pass_effect = next((effect for effect in skill.effects if effect.effect_type == "pass"), None)
            range_text = (
                f"射程通常+{pass_effect.distance}"
                if pass_effect
                else f"射程{skill.min_range}-{skill.range}"
                if skill.min_range != skill.range
                else f"射程{skill.range}"
            )
            if skill.activation == "automatic" and skill.purpose_tag == "ball_hold":
                range_text = "本人" if skill.ball_hold_scope == "self" else f"周囲{skill.ball_hold_range}マス（本人{' 含む' if skill.ball_hold_include_self else ' 除外'}）"
                cost = "無消費"
                move = "保持中のみ"
            self._text(
                f"{system_label}/{command} {cost} {range_text} {move} / {skill.description[:18]}",
                11,
                (157, 181, 199),
                (rect.x + 12, rect.y + 30),
            )
            if unavailable:
                self._text("設定済", 12, (192, 166, 111), (rect.right - 56, rect.y + 5))
            self.buttons.append(
                Button(
                    rect,
                    skill.name,
                    f"party_skill_candidate:{skill.skill_id}",
                    not unavailable,
                    "同じスキルは複数枠へ設定できません",
                )
            )

        candidate_valid = bool(self.party_skill_candidate_id) and self.party_skill_candidate_id not in equipped_other
        footer_buttons = (
            Button(pygame.Rect(panel.x + 248, panel.bottom - 88, 210, 48), "選択枠と交代", "party_skill_apply", candidate_valid),
            Button(pygame.Rect(panel.x + 474, panel.bottom - 88, 210, 48), "このキャラを初期化", "party_skill_reset"),
            Button(pygame.Rect(panel.x + 700, panel.bottom - 88, 180, 48), "閉じる", "party_skill_close"),
        )
        self.buttons.extend(footer_buttons)
        for button in footer_buttons:
            self._draw_button(button, 16)

    def _draw_party_totals_panel(self) -> None:
        self._draw_panel(PARTY_TOTAL_RECT)
        self._text("チーム合計 / 条件", 20, GOLD_LIGHT, (PARTY_TOTAL_RECT.x + 16, PARTY_TOTAL_RECT.y + 12))
        player = self._party_totals(self._active_party_ids(PLAYER))
        enemy = (
            self._party_totals(self._active_party_ids(ENEMY))
            if self.party_debug_mode and self.party_free_enemy_mode == FREE_ENEMY_MANUAL_MODE
            else self._party_enemy_group_totals()
        )
        setup = self._selected_quest_setup()
        selected_players = [char_id for char_id in self._active_party_ids(PLAYER) if char_id]
        current_cost = sum(self._party_member_value(member, "cost") for char_id in selected_players if (member := self._party_member(char_id)))
        if not self.party_debug_mode and setup is not None:
            self._text(
                f"人数 {len(selected_players)}/{setup.match.ally_party_limit}  コスト {current_cost}/{setup.match.ally_cost_limit or '制限なし'}",
                13,
                (151, 184, 207),
                (PARTY_TOTAL_RECT.x + 18, PARTY_TOTAL_RECT.y + 42),
            )
            self._text(
                f"先取{setup.match.score_to_win}点 / 最大ターン {setup.match.max_turns or '制限なし'}",
                13,
                (151, 184, 207),
                (PARTY_TOTAL_RECT.x + 18, PARTY_TOTAL_RECT.y + 64),
            )
            self._text(
                f"フィールド {setup.field.name or setup.field.field_id}",
                12,
                (177, 193, 207),
                (PARTY_TOTAL_RECT.x + 18, PARTY_TOTAL_RECT.y + 86),
            )
            stat_start_y = PARTY_TOTAL_RECT.y + 116
        else:
            stat_start_y = PARTY_TOTAL_RECT.y + 54
        rows = (
            (("HP", "max_hp"), ("MP", "max_mana")),
            (("パワー", "power"), ("マジック", "magic")),
            (("スピード", "speed"), ("テクニック", "technique")),
            (("スタミナ", "stamina"), ("", "stamina")),
        )
        self._text("味方 － 敵", 14, (151, 184, 207), (PARTY_TOTAL_RECT.right - 110, PARTY_TOTAL_RECT.y + 17))
        for row_index, pair in enumerate(rows):
            for column, (label, field) in enumerate(pair):
                if not label:
                    continue
                x = PARTY_TOTAL_RECT.x + 22 + column * 198
                y = stat_start_y + row_index * 21
                self._text(f"{label} {player[field]}－{enemy[field]}", 12 if not self.party_debug_mode else 15, (220, 224, 220), (x, y))

    def _draw_party_teams(self) -> None:
        self._draw_panel(PARTY_TEAMS_RECT)
        self._text("味方チーム", 20, (130, 211, 255), (PARTY_TEAMS_RECT.x + 18, PARTY_TEAMS_RECT.y + 12))
        self._text("敵チーム", 20, (255, 155, 155), (PARTY_TEAMS_RECT.x + 18, PARTY_TEAMS_RECT.y + 292))

        def draw_slots(ids: list[str | None], team: str, top: int) -> None:
            compact = self.party_debug_mode and self._active_team_count(team) > 4
            row_height = 34 if compact else 74 if self.party_debug_mode else 40
            rect_height = 30 if compact else 64 if self.party_debug_mode else 35
            icon_size = 24 if compact else 44 if self.party_debug_mode else 28
            name_size = 13 if compact else 17 if self.party_debug_mode else 15
            detail_size = 9 if compact else 12 if self.party_debug_mode else 10
            for index, char_id in enumerate(ids):
                rect = pygame.Rect(PARTY_TEAMS_RECT.x + 14, top + index * row_height, PARTY_TEAMS_RECT.width - 28, rect_height)
                active = index < self._active_team_count(team)
                selected = self.party_selected_slot == (team, index)
                fill = (29, 56, 72) if team == PLAYER else (67, 35, 43)
                pygame.draw.rect(self.screen, fill if active else (39, 42, 47), rect, border_radius=5)
                pygame.draw.rect(self.screen, GOLD_LIGHT if selected else (78, 91, 105), rect, 3 if selected else 1, border_radius=5)
                member = self._party_member(char_id)
                setup = self._selected_quest_setup()
                field_count = (
                    setup.match.ally_field_count if setup and team == PLAYER
                    else setup.match.enemy_field_count if setup else self._active_team_count(team)
                )
                if self.party_debug_mode:
                    field_count = self.party_debug_player_count if team == PLAYER else self.party_debug_enemy_count
                slot_label = "出" if index < field_count else "控"
                label_y = rect.y + (6 if compact else 8 if not self.party_debug_mode else 21)
                self._text(f"{index + 1}{slot_label}", 13 if not compact else 12, GOLD_LIGHT, (rect.x + 8, label_y))
                if member and active:
                    icon = self.circular_icon(member.char_id, icon_size)
                    if icon:
                        self.screen.blit(icon, icon.get_rect(center=(rect.x + (46 if compact else 56), rect.centery)))
                    self._text(member.name, name_size, (239, 235, 218), (rect.x + 78, rect.y + (3 if compact else 7 if self.party_debug_mode else 2)))
                    position = (
                        self.party_debug_player_positions[index]
                        if self.party_debug_mode and team == PLAYER
                        else self.party_debug_enemy_positions[index]
                        if self.party_debug_mode
                        else None
                    )
                    suffix = f" 配置{position}" if position else ""
                    detail = (
                        f"力{self._party_member_value(member, 'power')} 魔{self._party_member_value(member, 'magic')} 速{self._party_member_value(member, 'speed')} "
                        f"技{self._party_member_value(member, 'technique')} 体{self._party_member_value(member, 'stamina')}{suffix}"
                    )
                    detail_y = rect.y + (18 if compact else 20 if not self.party_debug_mode else 36)
                    self._text(detail, detail_size, (187, 202, 214), (rect.x + 78, detail_y))
                else:
                    empty_y = rect.y + (6 if compact else 8 if not self.party_debug_mode else 21)
                    self._text("無効枠" if not active else "空き枠", 14 if not compact else 12, (139, 151, 163), (rect.x + 82, empty_y))
                if active and team == ENEMY and self.party_debug_mode and self.party_free_enemy_mode == FREE_ENEMY_MANUAL_MODE:
                    profile_id = self._enemy_slot_profile_id(index)
                    profile = self._party_profile(profile_id)
                    label = f"AI:{profile.name if profile else '標準型'}"
                    button_width = 88 if compact else 104
                    button_height = 24 if compact else 30
                    level_width = 54 if compact else 64
                    ai_rect = pygame.Rect(rect.right - button_width - level_width - 10, rect.centery - button_height // 2, button_width, button_height)
                    key = f"party_debug_enemy_ai:{index}"
                    ai_button = Button(ai_rect, label, key, member is not None)
                    self.buttons.append(ai_button)
                    self._draw_button(ai_button, 10 if compact else 12)
                    level_rect = pygame.Rect(ai_rect.right + 4, ai_rect.y, level_width, button_height)
                    level_button = Button(level_rect, f"Lv:{self.party_enemy_ai_levels[index]}", f"party_debug_enemy_ai_level:{index}", member is not None)
                    self.buttons.append(level_button)
                    self._draw_button(level_button, 10)
                prefix = "party_ally_slot" if team == PLAYER else "party_enemy_slot"
                self.buttons.append(Button(rect, "編成枠", f"{prefix}:{index}", active, "参加人数外の枠です"))

        player_display_ids = self.party_player_ids
        if not self.party_debug_mode:
            player_display_ids = (
                self.party_player_ids + [None] * self._active_team_count(PLAYER)
            )[:self._active_team_count(PLAYER)]
        draw_slots(player_display_ids, PLAYER, PARTY_TEAMS_RECT.y + 48)
        if self.party_debug_mode and self.party_free_enemy_mode == FREE_ENEMY_MANUAL_MODE:
            draw_slots(self.party_enemy_ids, ENEMY, PARTY_TEAMS_RECT.y + 328)
            self._text("有効枠の順番と配置を試合へ反映します", 14, (161, 180, 197), (PARTY_TEAMS_RECT.x + 50, PARTY_TEAMS_RECT.bottom - 26))
            return
        if self.party_debug_mode:
            group_button = Button(
                pygame.Rect(PARTY_TEAMS_RECT.x + 18, PARTY_TEAMS_RECT.y + 326, PARTY_TEAMS_RECT.width - 36, 38),
                "敵グループ選択",
                "party_enemy_group_open",
                bool(self.party_enemy_groups),
                "利用可能な敵グループがありません",
            )
            self.buttons.append(group_button)
            self._draw_button(group_button, 15)
        group = self._party_enemy_group()
        if group is None:
            self._center_text_at(
                "利用可能な敵グループがありません",
                16,
                (255, 151, 151),
                (PARTY_TEAMS_RECT.centerx, PARTY_TEAMS_RECT.y + 430),
            )
        else:
            title_y = PARTY_TEAMS_RECT.y + 374 if self.party_debug_mode else PARTY_TEAMS_RECT.y + 324
            self._text(group.group.name, 18, (246, 214, 184), (PARTY_TEAMS_RECT.x + 18, title_y))
            self._text(f"倍率 x{group.group.enemy_scale:g}", 14, (255, 188, 137), (PARTY_TEAMS_RECT.right - 112, title_y + 3))
            description = group.group.description or "説明なし"
            self._text(description[:25], 12, (177, 193, 207), (PARTY_TEAMS_RECT.x + 18, title_y + 26))
            enemy_count = self.party_debug_enemy_party_count if self.party_debug_mode else self._selected_quest_setup().match.enemy_field_count if self._selected_quest_setup() else 3
            row_top = title_y + 50
            row_height = 33 if enemy_count > 5 else 48
            rect_height = 30 if enemy_count > 5 else 42
            for index, (char_id, row, profile_id) in enumerate(zip(group.enemy_ids, self._party_enemy_group_rows(group), group.ai_profile_ids)):
                if index >= enemy_count:
                    break
                rect = pygame.Rect(PARTY_TEAMS_RECT.x + 14, row_top + index * row_height, PARTY_TEAMS_RECT.width - 28, rect_height)
                pygame.draw.rect(self.screen, (67, 35, 43), rect, border_radius=5)
                pygame.draw.rect(self.screen, (101, 75, 82), rect, 1, border_radius=5)
                self._center_text_at(row["name"][:1], 14 if enemy_count > 5 else 16, (244, 226, 218), (rect.x + 24, rect.centery))
                profile = self._party_profile(profile_id)
                slot_label = "出" if index < self.party_debug_enemy_count else "控"
                self._text(f"{index + 1}{slot_label}. {row['name']}", 13 if enemy_count > 5 else 15, (239, 235, 218), (rect.x + 46, rect.y + 2))
                self._text(
                    f"AI:{profile.name if profile else '標準型'}",
                    10 if enemy_count > 5 else 12,
                    (187, 202, 214),
                    (rect.x + 46, rect.y + (18 if enemy_count > 5 else 23)),
                )

    def draw_match(self) -> None:
        assert self.game is not None
        self.buttons = []
        if self.battle_background:
            self.screen.blit(self.battle_background, (0, 0))
        else:
            self.screen.fill((13, 22, 36))
        shade = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        shade.fill((4, 9, 18, 142))
        self.screen.blit(shade, (0, 0))
        if self.skill_menu:
            self._draw_action_selection_screen()
            return
        self._draw_header()
        self._draw_field()
        self._draw_field_message()
        selected = self.game.characters.get(self.selected_id or "") or self.game.current_actor
        if selected:
            self._draw_character_info(selected)
        if self.command_window_open:
            self._draw_command_window()
        self._draw_match_controls()
        self._draw_context_controls()
        if self.mode in {"substitution_out", "substitution_in"}:
            self._draw_substitution_overlay()
        if self.presentation_details:
            self._draw_judgement_panel(self.presentation_details)
        if self.game.match_over and not self.input_locked:
            self._draw_result_overlay()
        if self.full_log_open:
            self._draw_full_log_overlay()
        if self.retire_confirmation:
            self._draw_retire_confirmation()
        elif self.options_open:
            self._draw_options_overlay()
        if self.action_preview is not None:
            self._draw_action_preview_modal()
        if self.ability_modal_actor_id is not None:
            self._draw_ability_modal()

    def _draw_action_preview_modal(self) -> None:
        assert self.game is not None and self.action_preview is not None
        data = self.action_preview
        overlay = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        overlay.fill((1, 5, 12, 190))
        self.screen.blit(overlay, (0, 0))
        panel = pygame.Rect(350, 150, 740, 600)
        actor = self.game.characters.get(str(data.get("actor_id", "")))
        target = self.game.characters.get(str(data.get("target_id", "")))
        self._draw_preview_portraits(panel, actor, None if data.get("self_target") else target)
        self._draw_panel(panel, (5, 20, 34, 252), GOLD_LIGHT)
        self._center_text_at(str(data.get("title", "行動予測")), 34, GOLD_LIGHT, (panel.centerx, panel.y + 45))
        if actor:
            self._text("行動者（味方）", 17, (133, 211, 255), (panel.x + 45, panel.y + 92))
            self._text(actor.name, 25, (232, 242, 252), (panel.x + 45, panel.y + 122))
        if data.get("self_target"):
            self._text("対象（自身）", 19, GOLD_LIGHT, (panel.right - 210, panel.y + 100))
        elif target:
            self._text("対象", 17, (255, 143, 143), (panel.right - 210, panel.y + 92))
            self._text(target.name, 25, (248, 226, 226), (panel.right - 210, panel.y + 122))
        self._center_text_at(str(data.get("action_name", "")), 22, (222, 230, 240), (panel.centerx, panel.y + 115))
        primary = str(data.get("primary_value", "-"))
        if data.get("preview_type") in {"buff", "debuff"}:
            primary = str(data.get("primary_label", primary))
        color = (100, 195, 255) if data.get("preview_type") == "buff" else (255, 210, 125)
        self._center_text_at(str(data.get("primary_label", "主要効果")), 20, (218, 225, 235), (panel.centerx, panel.y + 175))
        self._center_text_at(primary, 58, color, (panel.centerx, panel.y + 230))
        left = panel.x + 55
        y = panel.y + 295
        if data.get("preview_type") == "damage":
            damage = data.get("damage", {})
            assert isinstance(damage, dict)
            rows = [
                ("対象HP", f"{data.get('before_value')} → {data.get('after_value')}"),
                ("戦闘不能", "戦闘不能" if data.get("knockout") else "ならない"),
                ("消費MP / 残りMP", f"{data.get('mana_cost')} / {data.get('mana_after')}"),
                ("攻撃値 / 防御値", f"{damage.get('attack_value', '-'):g} / {damage.get('defense_value', '-'):g}"),
                ("能力差", f"{damage.get('ability_difference', 0):+g}"),
                ("軽減効果", " / ".join(str(item.get('name')) for item in damage.get('reductions', [])) or "なし"),
            ]
            ball = data.get("ball_effect")
            if isinstance(ball, dict):
                label = "ボールカット率" if ball.get("effect_type") == "cut" else "ボールドロップ率"
                rows.append((label, f"{ball.get('final_rate', '-')}%"))
        else:
            target_hp = f"{target.hp} / {target.max_hp}" if target else "-"
            rows = [
                ("能力変化", f"{data.get('stat_name', '-')} {data.get('before_value', '-')} → {data.get('after_value', '-')}"),
                ("効果時間", f"{data.get('duration', '-')} 行動"),
                ("消費MP / 残りMP", f"{data.get('mana_cost')} / {data.get('mana_after')}"),
                ("重複ルール", str(data.get("stack_rule", "-"))),
                ("対象HP", target_hp),
                ("スキル説明", str(data.get("description", "-"))),
            ]
        for label, value in rows:
            self._text(label, 16, GOLD_LIGHT, (left, y))
            self._text(value, 17, (225, 234, 243), (left + 185, y))
            y += 34
        reason = str(data.get("reason", ""))
        if reason:
            self._center_text_at(reason, 16, (255, 128, 128), (panel.centerx, panel.bottom - 105))
        confirm = Button(pygame.Rect(panel.x + 55, panel.bottom - 75, 300, 50), str(data.get("confirm_label", "行動を決定")), "preview_confirm", bool(data.get("executable")), reason)
        back = Button(pygame.Rect(panel.right - 355, panel.bottom - 75, 300, 50), "戻る", "preview_back")
        self.buttons.extend((confirm, back))
        self._draw_button(confirm, 21)
        self._draw_button(back, 21)

    def _draw_preview_portraits(
        self,
        panel: pygame.Rect,
        actor: Character | None,
        target: Character | None,
    ) -> None:
        occupants = self._preview_portrait_slots(actor, target)
        margin_rects = {
            "left": pygame.Rect(10, panel.y + 5, panel.x - 20, panel.height - 10),
            "right": pygame.Rect(panel.right + 10, panel.y + 5, WINDOW_SIZE[0] - panel.right - 20, panel.height - 10),
        }
        for side, characters in occupants.items():
            for index, character in enumerate(characters):
                portrait = self.preview_portrait(character)
                if portrait is None:
                    continue
                margin = margin_rects[side]
                slot_width = margin.width // max(1, len(characters))
                slot = pygame.Rect(margin.x + index * slot_width, margin.y, slot_width, margin.height)
                width, height = portrait.get_size()
                scale = min(slot.width / max(1, width), slot.height / max(1, height))
                size = (max(1, round(width * scale)), max(1, round(height * scale)))
                rendered = pygame.transform.smoothscale(portrait, size)
                destination = rendered.get_rect(midbottom=(slot.centerx, slot.bottom))
                self.screen.blit(rendered, destination)

    @staticmethod
    def _preview_portrait_slots(
        actor: Character | None,
        target: Character | None,
    ) -> dict[str, list[Character]]:
        return {
            "left": [actor] if actor is not None else [],
            "right": [target] if target is not None else [],
        }

    def _draw_round_control_overlay(self) -> None:
        overlay = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        overlay.fill((3, 8, 16, 210))
        self.screen.blit(overlay, (0, 0))
        rect = pygame.Rect(390, 245, 660, 330)
        self._draw_panel(rect, (8, 21, 36, 252), GOLD_LIGHT)
        self._center_text_at(f"ラウンド {self.game.round}", 30, GOLD_LIGHT, (rect.centerx, rect.y + 60))
        self._center_text_at("操作方法を選択してください", 21, (220, 232, 244), (rect.centerx, rect.y + 115))
        manual = Button(pygame.Rect(rect.x + 55, rect.bottom - 105, 255, 58), "このラウンドを手動操作", "round_manual")
        auto = Button(pygame.Rect(rect.right - 310, rect.bottom - 105, 255, 58), "このラウンドをAIに任せる", "round_ai")
        self.buttons.extend((manual, auto))
        self._draw_button(manual, 17)
        self._draw_button(auto, 17)

    def _draw_substitution_overlay(self) -> None:
        assert self.game is not None
        rect = pygame.Rect(260, 170, 920, 560)
        self._draw_panel(rect, (5, 16, 29, 248), GOLD_LIGHT)
        title = "交代：フィールドから下げるキャラクター" if self.mode == "substitution_out" else "交代：ベンチから出場させるキャラクター"
        self._center_text_at(title, 25, GOLD_LIGHT, (rect.centerx, rect.y + 42))
        candidates = self.game.field_characters(PLAYER) if self.mode == "substitution_out" else self.game.bench_characters(PLAYER)
        row_height = 58 if len(candidates) > 5 else 68
        button_height = 46 if len(candidates) > 5 else 52
        font_size = 15 if len(candidates) > 5 else 17
        for index, character in enumerate(candidates):
            y = rect.y + 88 + index * row_height
            multiplier = round(self.game.injury_multiplier(character) * 100)
            text = (
                f"{character.name}  HP {character.hp}/{character.max_hp}  MP {character.mana}/{character.max_mana}  "
                f"負傷{character.injury_rate}%  補正 {multiplier}%"
            )
            prefix = "substitution_out" if self.mode == "substitution_out" else "substitution_in"
            button = Button(pygame.Rect(rect.x + 70, y, rect.width - 140, button_height), text, f"{prefix}:{character.char_id}")
            self.buttons.append(button)
            self._draw_button(button, font_size)
        if self.mode == "substitution_out":
            button = Button(pygame.Rect(rect.centerx - 160, rect.bottom - 68, 320, 46), "交代しない", "substitution_skip")
        else:
            button = Button(pygame.Rect(rect.centerx - 160, rect.bottom - 68, 320, 46), "戻る", "substitution_back")
        self.buttons.append(button)
        self._draw_button(button, 18)

    def _draw_header(self) -> None:
        assert self.game is not None
        player_card = pygame.Rect(16, 14, 300, 66)
        round_card = pygame.Rect(326, 14, 260, 66)
        enemy_card = pygame.Rect(596, 14, 300, 66)
        order_card = pygame.Rect(906, 14, 502, 104)
        self._draw_hud_card(player_card, (5, 43, 72, 238), (73, 135, 181))
        self._draw_hud_card(round_card, (14, 31, 45, 238))
        self._draw_hud_card(enemy_card, (70, 11, 20, 238), (164, 70, 58))
        self._draw_panel(order_card, (6, 20, 33, 238), GOLD)
        self._text("味方", 20, (196, 225, 246), (player_card.x + 34, player_card.y + 20))
        self._text(str(self.game.scores[PLAYER]), 34, (246, 239, 211), (player_card.right - 54, player_card.y + 11))
        self._center_text_at(
            f"ターン  {self.game.round:02d}", 22, GOLD_LIGHT, round_card.center,
        )
        round_subtitle = (
            f"儀式維持：{self.game.ritual_hold_turns}／{self.game.config.required_hold_turns}ターン"
            if self.game.config.rule_type == RULE_RITUAL else self.game.rule_name
        )
        self._center_text_at(round_subtitle, 11, (157, 178, 197), (round_card.centerx, round_card.bottom - 12))
        self._text("敵", 20, (244, 205, 206), (enemy_card.x + 40, enemy_card.y + 20))
        self._text(str(self.game.scores[ENEMY]), 34, (246, 239, 211), (enemy_card.right - 54, enemy_card.y + 11))
        holder = self.game.characters.get(self.game.ball.holder_id or "")
        holder_rect = pygame.Rect(16, 86, 390, 32)
        self._draw_panel(holder_rect, (6, 21, 35, 230), (135, 98, 49))
        ball_text = "ルーズボール" if holder is None else f"{holder.name}（{'味方' if holder.team == PLAYER else '敵'}）"
        self._text("ボール保持者", 13, GOLD_LIGHT, (holder_rect.x + 14, holder_rect.y + 7))
        if holder:
            holder_icon = self.circular_icon(holder.char_id, 26)
            if holder_icon:
                self.screen.blit(holder_icon, (holder_rect.x + 132, holder_rect.y + 3))
            self._text(ball_text, 14, (226, 234, 242), (holder_rect.x + 166, holder_rect.y + 5))
        else:
            self._text(ball_text, 14, (226, 234, 242), (holder_rect.x + 144, holder_rect.y + 5))
        self._draw_turn_order()
        condition_rect = pygame.Rect(906, 86, 502, 32)
        self._draw_panel(condition_rect, (6, 21, 35, 230), (135, 98, 49))
        self._center_text_at(self.game.victory_condition_text, 13, GOLD_LIGHT, condition_rect.center)
        auto_button = Button(
            pygame.Rect(416, 86, 150, 32),
            f"オート：{'ON' if self.auto_player else 'OFF'}",
            "auto",
            True,
        )
        speed_button = Button(
            pygame.Rect(576, 86, 140, 32),
            f"速度：{self.speed_levels[self.speed_index]:g}倍",
            "speed",
            True,
        )
        options_button = Button(pygame.Rect(726, 86, 170, 32), "オプション", "options", True)
        self.buttons.extend((auto_button, speed_button, options_button))
        self._draw_button(auto_button, 13)
        self._draw_button(speed_button, 13)
        self._draw_button(options_button, 13)
        mode_label = f"{'オート' if self.auto_player else '手動'}・{self.speed_levels[self.speed_index]:g}倍"
        self._text(mode_label, 11, (173, 194, 214), (905, 82))
        self._text(
            f"味方:{self.game.team_state_name(PLAYER).replace('状態', '')} / 敵:{self.game.team_state_name(ENEMY).replace('状態', '')}",
            10, (173, 194, 214), (976, 96),
        )

    def _draw_skip_score_confirmation(self) -> None:
        assert self.game is not None
        overlay = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        overlay.fill((3, 8, 16, 195))
        self.screen.blit(overlay, (0, 0))
        rect = pygame.Rect(400, 245, 640, 390)
        self._draw_panel(rect, (8, 21, 36, 252), GOLD_LIGHT)
        scorer_team, scorer_name = self.game.scorers[-1] if self.game.scorers else ("", "不明")
        team_name = "味方" if scorer_team == PLAYER else "敵"
        self._center_text_at("ゴール", 38, GOLD_LIGHT, (rect.centerx, rect.y + 58))
        self._center_text_at(
            f"現在スコア  {self.game.scores[PLAYER]} - {self.game.scores[ENEMY]}",
            27, (238, 242, 247), (rect.centerx, rect.y + 120),
        )
        self._center_text_at(f"得点チーム：{team_name}　得点者：{scorer_name}", 21, (205, 226, 255), (rect.centerx, rect.y + 168))
        self._center_text_at("次の進行方法を選択してください", 18, (190, 201, 216), (rect.centerx, rect.y + 215))
        manual = Button(pygame.Rect(rect.x + 55, rect.bottom - 100, 245, 54), "手動で再開", "skip_resume_manual")
        continued = Button(pygame.Rect(rect.right - 300, rect.bottom - 100, 245, 54), "スキップを続ける", "skip_continue")
        self.buttons.extend((manual, continued))
        self._draw_button(manual, 20)
        self._draw_button(continued, 20)

    def _draw_turn_order(self) -> None:
        assert self.game is not None
        self._center_text_at("行動順", 15, GOLD_LIGHT, (1157, 29))
        ordered = [self.game.characters[char_id] for char_id in self.game.turn_order]
        ordered.extend(character for character in self.game.characters.values() if character.off_field)
        visible = ordered[:12]
        icon_step = 39 if len(visible) > 10 else 43 if len(visible) > 8 else 54 if len(visible) > 6 else 75
        icon_radius = 17 if len(visible) > 8 else 20 if len(visible) > 6 else 23
        icon_size = max(28, icon_radius * 2 - 6)
        start_x = 938
        for index, character in enumerate(visible):
            rect = pygame.Rect(start_x + index * icon_step, 44, max(34, icon_step - 4), 66)
            frame = (78, 166, 255) if character.team == PLAYER else (241, 100, 111)
            if index == self.game.turn_index and not character.off_field:
                frame = GOLD_LIGHT
            pygame.draw.circle(self.screen, (12, 27, 42), (rect.centerx, rect.y + 24), icon_radius)
            pygame.draw.circle(self.screen, frame, (rect.centerx, rect.y + 24), icon_radius, 3 if index == self.game.turn_index else 2)
            icon = self.circular_icon(character.char_id, icon_size)
            if icon:
                self.screen.blit(icon, icon.get_rect(center=(rect.centerx, rect.y + 24)))
            else:
                self._center_text_at(character.name[:1], 14 if len(visible) > 8 else 18, (245, 247, 250), (rect.centerx, rect.y + 24))
            if self.game.is_ball_holder(character):
                pygame.draw.circle(self.screen, (255, 224, 74), (rect.right - 5, rect.y + 8), 6)
            status = "×" if character.off_field else "済" if character.acted or character.waiting else ""
            if status:
                self._center_text_at(status, 10 if len(visible) > 8 else 12, (255, 170, 170) if character.off_field else (172, 183, 201), (rect.centerx, rect.bottom - 7))
            elif index == self.game.turn_index:
                pygame.draw.rect(self.screen, GOLD_LIGHT, (rect.x + 7, rect.bottom - 15, rect.width - 14, 16), border_radius=3)
                self._center_text_at("現在", 9 if len(visible) > 8 else 11, (13, 28, 40), (rect.centerx, rect.bottom - 7))

    def _draw_field(self) -> None:
        assert self.game is not None
        if self.battle_field_background:
            self.screen.blit(self.battle_field_background, FIELD_RECT)
        else:
            pygame.draw.rect(self.screen, (25, 52, 61), FIELD_RECT)
        field_tint = pygame.Surface(FIELD_RECT.size, pygame.SRCALPHA)
        field_tint.fill((10, 27, 42, 86))
        self.screen.blit(field_tint, FIELD_RECT)

        highlights: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        actor = self.game.current_actor
        if actor:
            if self.mode in {"move", "shadow_move"}:
                ignore_zoc = self.mode == "shadow_move"
                reachable = self.game.reachable_positions(actor.char_id, ignore_zoc=ignore_zoc)
                zoc = self.game.zoc_cells(actor.team)
                for cell in reachable:
                    highlights[cell] = (146, 80, 196, 132) if ignore_zoc else (224, 139, 54, 142) if cell in zoc else (56, 183, 104, 132)
        if self.game.restart_team == PLAYER and self.game.restart_prepared and not self.auto_player:
            for character in self.game.restart_candidates():
                if character.position is not None:
                    highlights[character.position] = (59, 170, 221, 132)
        board_rect, cell_size = self._board_geometry()
        grid = pygame.Surface(board_rect.size, pygame.SRCALPHA)
        for x in range(self.game.config.field_width):
            for y in range(self.game.config.field_height):
                rect = pygame.Rect(x * cell_size, y * cell_size, cell_size, cell_size)
                base = (35, 76, 91, 38) if (x + y) % 2 == 0 else (22, 54, 77, 48)
                if self.game.config.left_goal_x_start <= x <= self.game.config.left_goal_x_end and self.game.config.goal_y_start <= y <= self.game.config.goal_y_end:
                    base = (38, 116, 214, 105)
                elif self.game.config.right_goal_x_start <= x <= self.game.config.right_goal_x_end and self.game.config.goal_y_start <= y <= self.game.config.goal_y_end:
                    base = (205, 54, 72, 105)
                pygame.draw.rect(grid, highlights.get((x, y), base), rect)
                pygame.draw.rect(grid, (190, 151, 82, 112), rect, 2)
        self.screen.blit(grid, board_rect)
        pygame.draw.rect(self.screen, GOLD, board_rect, 3)
        if actor and self.mode in {"move", "shadow_move"}:
            for teammate in self.game.active_characters(actor.team):
                if teammate.char_id != actor.char_id and teammate.position:
                    pygame.draw.rect(self.screen, (83, 218, 224), self.cell_rect(teammate.position), 3)
        self._text("味方ゴール", 15, (185, 220, 255), (FIELD_RECT.x + 8, FIELD_RECT.y + 7))
        self._text("敵ゴール", 15, (255, 205, 210), (FIELD_RECT.right - 90, FIELD_RECT.y + 7))
        self._draw_target_lines()
        for character in self.game.active_characters():
            self._draw_character(character)
        self._draw_pass_candidate_markers()
        ball_position = self.game.ball_position
        if self.game.ball.holder_id is None and ball_position:
            center = self.cell_rect(ball_position).center
            radius = max(8, min(15, self._cell_size()[0] // 6))
            pygame.draw.circle(self.screen, (255, 225, 82), center, radius)
            pygame.draw.circle(self.screen, (84, 62, 16), center, radius, 2)
            if radius >= 11:
                self._text("BALL", 11, (40, 31, 10), (center[0] - 16, center[1] - 8))

    def _draw_target_lines(self) -> None:
        assert self.game is not None
        actor = self.game.current_actor
        if actor is None or actor.position is None:
            return
        target_ids: list[str] = []
        if self.mode == "pass":
            system = self.pending_pass_system or "physical"
            target_ids = [target.char_id for target in self._pass_targets(actor.char_id, system)]
        elif self.mode == "skill_quick_pass":
            system = "magic"
            target_ids = [target.char_id for target in self.game.valid_pass_targets(actor.char_id, system)]
        elif self.pending_pass_target:
            target_ids = [self.pending_pass_target]
        for target_id in target_ids:
            target = self.game.characters[target_id]
            if target.position is None:
                continue
            system = "magic" if self.mode == "skill_quick_pass" else (self.pending_pass_system or "physical")
            preview = (
                self._pass_preview(actor.char_id, target_id, system)
                if self.mode != "skill_quick_pass"
                else self.game.pass_preview(actor.char_id, target_id, system, quick=True)
            )
            line = preview["line"]
            start = self.cell_rect(actor.position).center
            end = self.cell_rect(target.position).center
            pygame.draw.line(self.screen, (108, 195, 255), start, end, 4)
            for candidate in preview["candidates"]:
                interceptor = self.game.characters.get(str(candidate["character_id"]))
                if interceptor and interceptor.position:
                    pygame.draw.rect(self.screen, (255, 179, 51), self.cell_rect(interceptor.position), 5)
            for cell in line[1:-1]:
                pygame.draw.circle(self.screen, (194, 227, 255), self.cell_rect(cell).center, 4)
        if self.mode and self.mode.startswith("skill_"):
            skill_id = self.mode.removeprefix("skill_")
            skill = self.game.skills.get(skill_id)
            if skill and self.game._effect_of_type(skill, "force_move"):
                for target in self.game.skill_targets(actor.char_id, skill_id):
                    destination = self.game._push_destination(actor, target)
                    if destination:
                        pygame.draw.rect(self.screen, (255, 190, 74), self.cell_rect(destination).inflate(-18, -18), 4)

    def _draw_pass_candidate_markers(self) -> None:
        actor = self.game.current_actor
        if actor is None or not self.pending_pass_target or not self.pending_pass_system:
            return
        preview = self._pass_preview(actor.char_id, self.pending_pass_target, self.pending_pass_system)
        for index, candidate in enumerate(preview.get("candidates", []), start=1):
            interceptor = self.game.characters.get(str(candidate.get("character_id", "")))
            if interceptor is None or interceptor.position is None:
                continue
            marker = self.cell_rect(interceptor.position)
            pygame.draw.rect(self.screen, (255, 179, 51), marker, 6, border_radius=7)
            label_rect = pygame.Rect(marker.x + 2, marker.y + 2, marker.width - 4, 20)
            pygame.draw.rect(self.screen, (58, 37, 8), label_rect, border_radius=4)
            self._center_text_at(
                f"{index} カット{candidate.get('success_rate')}%", 10, (255, 235, 154), label_rect.center,
            )

    def _draw_character(self, character: Character) -> None:
        assert self.game is not None
        assert character.position is not None
        display_position = self.visual_positions.get(character.char_id, character.position)
        rect = self.cell_rect(display_position)
        center = rect.center
        cell_size = rect.width
        color = (73, 157, 244) if character.team == PLAYER else (224, 85, 94)
        outline = (255, 219, 80) if self.game.current_actor is character else (225, 232, 242)
        if character.char_id == self.selected_id:
            pygame.draw.rect(self.screen, (255, 243, 126), rect.inflate(-max(4, cell_size // 12), -max(4, cell_size // 12)), 4, border_radius=8)
        if self._is_target(character):
            target_color = (
                (84, 255, 142) if self.mode in {"skill_heal", "skill_heal_hp", "skill_recover_mp"}
                else (102, 201, 255) if "pass" in (self.mode or "") or self.mode == "restart_holder"
                else (255, 93, 82)
            )
            pygame.draw.rect(self.screen, target_color, rect.inflate(-max(8, cell_size // 6), -max(8, cell_size // 6)), 5, border_radius=7)
        token_radius = max(24, min(40, cell_size // 2 - 12))
        pygame.draw.circle(self.screen, color, center, token_radius)
        pygame.draw.circle(self.screen, outline, center, token_radius, max(2, token_radius // 10))
        if self.game.current_actor is character:
            badge_width = max(52, min(68, cell_size - 18))
            badge = pygame.Rect(center[0] - badge_width // 2, rect.y - 2, badge_width, 22)
            pygame.draw.rect(self.screen, (87, 61, 8), badge, border_radius=5)
            pygame.draw.rect(self.screen, GOLD_LIGHT, badge, 2, border_radius=5)
            self._center_text_at("行動中", 12 if cell_size < 88 else 13, (255, 239, 166), badge.center)
        icon_size = max(42, min(68, token_radius * 2 - 12))
        icon = self.circular_icon(character.char_id, icon_size)
        if icon:
            self.screen.blit(icon, icon.get_rect(center=center))
        else:
            self._center_text_at(character.name[:1], max(18, min(24, cell_size // 4)), (255, 255, 255), center)
        if self.game.is_ball_holder(character):
            ball_offset = max(22, min(34, token_radius - 6))
            ball_radius = max(8, min(12, cell_size // 8))
            ball_center = (center[0] + ball_offset, center[1] - ball_offset)
            pygame.draw.circle(self.screen, (255, 224, 74), ball_center, ball_radius)
            pygame.draw.circle(self.screen, (74, 54, 12), ball_center, ball_radius, 2)
        if character.defending:
            self._text("盾", 16, (173, 230, 255), (rect.x + 5, rect.y + 3))
        if character.keeping:
            self._text("K", 16, (255, 225, 80), (rect.right - 18, rect.bottom - 23))
        name_width = max(70, min(96, cell_size - 8))
        name_back = pygame.Surface((name_width, 20), pygame.SRCALPHA)
        name_back.fill((8, 13, 24, 180))
        self.screen.blit(name_back, (center[0] - name_width // 2, rect.bottom - 24))
        self._center_text_at(character.name, 12 if cell_size < 88 else 14, (245, 247, 250), (center[0], rect.bottom - 14))
        bar_width = max(50, min(76, cell_size - 28))
        hp_width = int(bar_width * character.hp / character.max_hp)
        pygame.draw.rect(self.screen, (47, 34, 43), (center[0] - bar_width // 2, rect.y + 10, bar_width, 7))
        pygame.draw.rect(self.screen, (80, 224, 117), (center[0] - bar_width // 2, rect.y + 10, hp_width, 7))

    def _is_target(self, character: Character) -> bool:
        assert self.game is not None
        if self.mode == "restart_holder":
            return character in self.game.restart_candidates()
        actor = self.game.current_actor
        if actor is None:
            return False
        if self.mode == "attack":
            return character in self.game.attack_targets(actor.char_id)
        if self.mode == "skill_steal":
            return character in self.game.steal_targets(actor.char_id)
        if self.mode == "cut":
            return character in self.game.cut_targets(actor.char_id)
        if self.mode == "pass":
            return character in self._pass_targets(actor.char_id, self.pending_pass_system or "physical")
        if self.mode == "skill_quick_pass":
            return character in self.game.valid_pass_targets(actor.char_id, "magic")
        if self.mode and self.mode.startswith("skill_"):
            return character in self.game.skill_targets(actor.char_id, self.mode.removeprefix("skill_"))
        return False

    def _draw_field_message(self) -> None:
        assert self.game is not None
        self._draw_panel(FIELD_MESSAGE_RECT, (7, 20, 34, 224), GOLD)
        actor = self.game.current_actor
        mode_names = {
            "move": "移動", "shadow_move": "影渡り移動", "attack": "攻撃", "steal": "スティール",
            "pass": "パス", "pass_system": "パス系統選択", "cut": "カット", "restart_holder": "再開保持者選択",
        }
        action = mode_names.get(self.mode or "", "行動選択")
        actor_text = "行動者なし" if actor is None else f"現在行動：{actor.name}：{action}"
        self._text(actor_text, 21, (255, 224, 126), (FIELD_MESSAGE_RECT.x + 16, FIELD_MESSAGE_RECT.y + 12))
        self._draw_wrapped(
            self.message,
            17,
            (226, 235, 246),
            pygame.Rect(FIELD_MESSAGE_RECT.x + 16, FIELD_MESSAGE_RECT.y + 48, 820, 54),
        )
        guide = "盤面またはコマンドを選択 / Esc: 選択解除"
        if self.input_locked:
            guide = "行動演出中：盤面と通常コマンドはロック中"
        elif self.pending_attack_target:
            guide = "予想ダメージを確認して攻撃を決定してください"
        elif self.pending_cut_target:
            guide = "カット補正と成功率を確認して決定してください"
        elif self.pending_pass_target:
            guide = "パス経路とパスカット候補を確認して決定してください"
        elif self.pending_skill_confirmation:
            guide = "対象と消費リソースを確認して決定してください"
        elif self.mode:
            guide = "Esc: 対象または移動先の選択を解除"
        elif self.game.pending_move:
            guide = "Escまたは移動キャンセル: 移動前の位置へ戻る"
        self._text(guide, 13, (145, 181, 211), (FIELD_MESSAGE_RECT.x + 16, FIELD_MESSAGE_RECT.bottom - 25))

    def _draw_side_panel(self) -> None:
        assert self.game is not None
        self._draw_panel(SIDE_PANEL_RECT, (5, 15, 27, 210), GOLD)
        self._draw_panel(CHARACTER_INFO_RECT)
        self._draw_panel(COMMAND_RECT)
        self._draw_panel(SKILL_DETAIL_RECT)
        actor = self.game.current_actor
        selected = self.game.characters.get(self.selected_id or "") or actor
        if selected:
            self._draw_character_info(selected)
        else:
            self._text("キャラクター情報", 18, (180, 208, 232), (CHARACTER_INFO_RECT.x + 12, CHARACTER_INFO_RECT.y + 10))
        self._draw_actions(actor)
        if actor:
            self._draw_selected_skill_details(actor)

    def _draw_resource_bar(
        self,
        label: str,
        value: int,
        maximum: int,
        y: int,
        color: tuple[int, int, int],
        x: int | None = None,
        bar_width: int = 226,
    ) -> None:
        x = CHARACTER_INFO_RECT.x + 16 if x is None else x
        self._text(f"{label} {value}/{maximum}", 13, (219, 229, 241), (x, y))
        bar = pygame.Rect(x, y + 19, bar_width, 10)
        pygame.draw.rect(self.screen, (35, 42, 55), bar, border_radius=4)
        width = 0 if maximum <= 0 else round(bar.width * max(0, value) / maximum)
        if width:
            pygame.draw.rect(self.screen, color, (bar.x, bar.y, width, bar.height), border_radius=4)
        pygame.draw.rect(self.screen, (100, 119, 143), bar, 1, border_radius=4)

    def _draw_character_info(self, character: Character) -> None:
        assert self.game is not None
        self._draw_panel(CHARACTER_INFO_RECT, (5, 18, 31, 230), GOLD)
        states: list[str] = []
        if self.game.is_ball_holder(character):
            states.append("ボール保持")
        hold_modifiers = self.game.ball_hold_status(character)["modifiers"]
        modifier_text = ",".join(
            f"{STAT_LABELS.get(stat, '移動力' if stat == 'move_range' else stat)}{value:+d}"
            for stat, value in hold_modifiers.items() if value
        )
        if modifier_text:
            states.append(f"保持効果[{modifier_text}]")
        if character.defending:
            states.append("防御")
        if character.keeping:
            states.append(f"キープ+{self.game.config.keep_bonus}")
        if character.reaction_skill:
            states.append(f"{character.reaction_skill.get('skill_name', '反応')}待機")
        if character.acted:
            states.append("行動済み")
        if character.waiting:
            states.append("待機中")
        if character.off_field:
            states.append("戦闘不能")
        if character.on_bench:
            states.append("ベンチ")
        if character.injury_rate:
            states.append(f"負傷{character.injury_rate}%（能力{round(self.game.injury_multiplier(character) * 100)}%）")
        if character.off_field:
            states.append("得点後に復帰")
        state_text = " / ".join(states) or "通常"
        icon = self.circular_icon(character.char_id, 70)
        icon_center = (CHARACTER_INFO_RECT.x + 52, CHARACTER_INFO_RECT.centery)
        if icon:
            self.screen.blit(icon, icon.get_rect(center=icon_center))
        else:
            pygame.draw.circle(self.screen, (25, 48, 68), icon_center, 35)
            self._center_text_at(character.name[:1], 24, (245, 247, 250), icon_center)
        frame = (87, 175, 242) if character.team == PLAYER else (234, 98, 110)
        pygame.draw.circle(self.screen, frame, icon_center, 36, 3)
        self._text(character.name, 25, (255, 229, 142), (CHARACTER_INFO_RECT.x + 104, CHARACTER_INFO_RECT.y + 17))
        self._text(f"状態：{state_text}", 17, (210, 224, 240), (CHARACTER_INFO_RECT.x + 104, CHARACTER_INFO_RECT.y + 57))
        self._draw_resource_bar("HP", character.hp, character.max_hp, CHARACTER_INFO_RECT.y + 17, (72, 210, 111), CHARACTER_INFO_RECT.x + 410, 300)
        self._draw_resource_bar("MP", character.mana, character.max_mana, CHARACTER_INFO_RECT.y + 17, (72, 145, 233), CHARACTER_INFO_RECT.x + 760, 300)
        active_hold_skills = self.game.ball_hold_skills(character) if self.game.is_ball_holder(character) else []
        ball_text = (
            f"保持スキル:{'/'.join(skill.name for skill in active_hold_skills)}"
            if active_hold_skills else "ボール保持中"
        ) if self.game.is_ball_holder(character) else (
            f"負傷補正 {round(self.game.injury_multiplier(character) * 100)}%"
        )
        self._text(ball_text, 18, GOLD_LIGHT, (CHARACTER_INFO_RECT.right - 230, CHARACTER_INFO_RECT.y + 39))

    def _ability_status_lines(self, character: Character) -> list[str]:
        assert self.game is not None
        lines: list[str] = []
        if character.injury_rate > 0:
            lines.append(f"負傷{character.injury_rate}%：5能力へ負傷補正（解除：ベンチ回復）")
        modifiers = character.temporary_effects.get("stat_modifiers", [])
        if isinstance(modifiers, list):
            for item in modifiers:
                if not isinstance(item, dict):
                    continue
                try:
                    stat = STAT_LABELS.get(str(item.get("stat", "")), str(item.get("stat", "能力")))
                    value = int(item.get("value", 0))
                    amount = f"{value:+d}%" if item.get("mode") == "percent" else f"{value:+d}"
                    name = str(item.get("source_skill_name") or item.get("source_skill_id") or "一時効果")
                    remaining = int(item.get("remaining", 0))
                    suffix = f"／残り{remaining}行動" if remaining > 0 else "／解除条件不明"
                    lines.append(f"{name}：{stat}{amount}{suffix}")
                except (TypeError, ValueError):
                    LOGGER.warning("能力確認で不正な一時効果を無視しました: %r", item)
        if self.game.is_ball_holder(character):
            skills = self.game.ball_hold_skills(character)
            details = self.game.ball_hold_status(character)
            modifiers = details.get("modifiers", {})
            text = "、".join(f"{STAT_LABELS.get(k, '移動力' if k == 'move_range' else k)}{int(v):+d}" for k, v in modifiers.items() if v)
            for skill in skills:
                lines.append(f"{skill.name}：{text or 'ボール保持効果'}／ボール保持中")
            if not skills:
                lines.append("ボール保持：解除条件 ボールを失うまで")
        if character.defending:
            lines.append("防御状態：被ダメージ軽減／次の行動開始まで")
        movement_down = int(character.temporary_effects.get("movement_down", 0) or 0)
        if movement_down:
            lines.append(f"移動力低下：移動力{-movement_down:+d}／次の行動開始まで")
        if character.reaction_skill:
            lines.append(f"{character.reaction_skill.get('skill_name', '反応スキル')}：発動条件成立まで")
        return lines or ["能力変化なし"]

    def _scroll_ability_status(self, amount: int) -> None:
        if not self.game or self.ability_modal_actor_id is None:
            return
        character = self.game.characters.get(self.ability_modal_actor_id)
        maximum = max(0, len(self._ability_status_lines(character)) - 4) if character else 0
        self.ability_status_scroll = min(maximum, max(0, self.ability_status_scroll + amount))

    def _draw_ability_modal(self) -> None:
        assert self.game is not None
        character = self.game.characters.get(self.ability_modal_actor_id or "")
        if character is None:
            LOGGER.warning("能力確認対象を取得できません: %s", self.ability_modal_actor_id)
            self.ability_modal_actor_id = None
            self.message = "確認対象が存在しません"
            return
        overlay = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        overlay.fill((1, 5, 12, 205))
        self.screen.blit(overlay, (0, 0))
        panel = pygame.Rect(190, 70, 1060, 760)
        self._draw_panel(panel, (5, 20, 34, 252), GOLD_LIGHT)
        self._center_text_at("能力確認", 30, GOLD_LIGHT, (panel.centerx, panel.y + 34))
        left = pygame.Rect(panel.x + 28, panel.y + 76, 320, 594)
        right = pygame.Rect(left.right + 24, left.y, panel.right - left.right - 52, left.height)
        self._draw_panel(left, (9, 29, 47, 245), (75, 111, 139))
        self._draw_panel(right, (9, 29, 47, 245), (75, 111, 139))
        portrait = self.preview_portrait(character)
        portrait_rect = pygame.Rect(left.x + 22, left.y + 72, left.width - 44, 350)
        if portrait:
            image = self._cover_scale(portrait, portrait_rect.size)
            self.screen.blit(image, portrait_rect)
        else:
            pygame.draw.rect(self.screen, (20, 47, 67), portrait_rect, border_radius=8)
            icon = self.circular_icon(character.char_id, 120)
            if icon:
                self.screen.blit(icon, icon.get_rect(center=portrait_rect.center))
            else:
                self._center_text_at(character.name[:1], 48, (235, 240, 246), portrait_rect.center)
        self._center_text_at(character.name, 26, GOLD_LIGHT, (left.centerx, left.y + 34))
        team = "味方" if character.team == PLAYER else "敵"
        states = [team]
        if self.game.is_ball_holder(character): states.append("ボール保持中")
        if character.defending: states.append("防御中")
        if character.off_field: states.append("戦闘不能")
        self._center_text_at("／".join(states), 18, (204, 220, 236), (left.centerx, portrait_rect.bottom + 28))
        self._draw_resource_bar("HP", character.hp, character.max_hp, right.y + 18, (72, 210, 111), right.x + 20, 290)
        self._draw_resource_bar("MP", character.mana, character.max_mana, right.y + 18, (72, 145, 233), right.x + 340, 290)
        self._text(f"負傷率：{max(0, character.injury_rate)}%", 18, (230, 218, 194), (right.x + 20, right.y + 68))
        self._text(f"防御：{'あり' if character.defending else 'なし'}　戦闘不能：{'はい' if character.off_field else 'いいえ'}", 16, (195, 211, 228), (right.x + 240, right.y + 70))
        self._text("能力値", 22, GOLD_LIGHT, (right.x + 20, right.y + 108))
        for index, (stat, label) in enumerate((("power", "パワー"), ("magic", "マジック"), ("speed", "スピード"), ("technique", "テクニック"), ("stamina", "スタミナ"))):
            base = int(getattr(character, stat, 0) or 0)
            try:
                current = int(self.game.effective_stat(character, stat))
                difference = current - base
            except (AttributeError, TypeError, ValueError):
                LOGGER.warning("実効能力を取得できないため基礎値を表示します: %s / %s", character.char_id, stat)
                current, difference = base, 0
            color = (99, 224, 132) if difference > 0 else (245, 102, 102) if difference < 0 else (230, 235, 241)
            value = f"{current}（{difference:+d}）" if difference else str(current)
            y = right.y + 145 + index * 38
            self._text(label, 19, (202, 216, 231), (right.x + 28, y))
            self._text(value, 21, color, (right.x + 210, y - 2))
        self._text("状態変化", 22, GOLD_LIGHT, (right.x + 20, right.y + 345))
        lines = self._ability_status_lines(character)
        visible = lines[self.ability_status_scroll:self.ability_status_scroll + 4]
        for index, line in enumerate(visible):
            self._draw_wrapped(line, 16, (211, 224, 238), pygame.Rect(right.x + 24, right.y + 382 + index * 43, right.width - 100, 40))
        maximum = max(0, len(lines) - 4)
        up = Button(pygame.Rect(right.right - 64, right.y + 382, 40, 36), "▲", "ability_up", self.ability_status_scroll > 0)
        down = Button(pygame.Rect(right.right - 64, right.y + 510, 40, 36), "▼", "ability_down", self.ability_status_scroll < maximum)
        close = Button(pygame.Rect(panel.centerx - 150, panel.bottom - 66, 300, 44), "閉じる", "ability_close")
        self.buttons.extend((up, down, close))
        for button in (up, down, close): self._draw_button(button, 17)

    def _draw_command_window(self) -> None:
        assert self.game is not None
        actor = self.game.current_actor
        if (
            actor is None or actor.position is None or actor.team != PLAYER or self.auto_player
            or self.command_window_actor_id != actor.char_id
        ):
            self._close_command_window()
            return
        specs = self._action_command_specs(actor)
        if self.game.pending_move and self.game.pending_move.plan.actor_id == actor.char_id:
            specs.append(("移動キャンセル", "cancel_move", True, ""))
        width = 224
        row_height = 44
        height = 18 + len(specs) * row_height
        safe_rect = FIELD_RECT.inflate(-12, -12)
        try:
            actor_rect = self.cell_rect(actor.position)
            x = actor_rect.right + 10
            if x + width > safe_rect.right:
                x = actor_rect.left - width - 10
            y = actor_rect.top - 8
            panel = pygame.Rect(x, y, width, height)
            panel.clamp_ip(safe_rect)
        except (AttributeError, TypeError, ValueError) as error:
            LOGGER.error("コマンドウィンドウ位置を計算できません: %s", error)
            panel = pygame.Rect(0, 0, width, height)
            panel.center = FIELD_RECT.center
            panel.clamp_ip(safe_rect)
        self.command_window_rect = panel.copy()
        x, y = panel.topleft
        self._draw_panel(panel, (4, 16, 29, 246), GOLD_LIGHT)
        for index, (text, key, enabled, reason) in enumerate(specs):
            button = Button(pygame.Rect(x + 10, y + 9 + index * row_height, width - 20, 38), text, key, enabled, reason)
            self.buttons.append(button)
            self._draw_button(button, 17)

    def _draw_context_controls(self) -> None:
        """Draw confirmation/cancel controls after an action has left the command window."""

        assert self.game is not None
        actor = self.game.current_actor
        if actor is None:
            return
        specs: list[tuple[str, str, bool, str]] = []
        if self.pending_skill_confirmation:
            skill = self.game.skills[self.pending_skill_confirmation[0]]
            specs = [(f"{skill.name}を決定", "confirm_skill", True, ""), ("戻る", "cancel_skill", True, "")]
        elif self.pending_attack_target:
            specs = [("攻撃を決定", "confirm_attack", True, ""), ("戻る", "cancel_attack", True, "")]
        elif self.pending_pass_target:
            specs = [("パスを決定", "confirm_pass", True, ""), ("戻る", "cancel_pass", True, "")]
        elif self.pending_cut_target and not self.pending_cut_system:
            specs = [("物理カット", "cut_physical", True, ""), ("魔法カット", "cut_magic", True, ""), ("戻る", "cancel_cut", True, "")]
        elif self.pending_cut_target:
            specs = [("カットを決定", "confirm_cut", True, ""), ("戻る", "cancel_cut", True, "")]
        elif self.special_move_ignore_zoc:
            specs = [
                ("影渡り移動", "move", bool(self.game.reachable_positions(actor.char_id, ignore_zoc=True)), "移動可能なマスがありません"),
                ("移動せず終了", "wait", True, ""),
            ]
        if not specs:
            return
        width = 270
        gap = 6
        height = (FIELD_MESSAGE_RECT.height - gap * (len(specs) - 1)) // len(specs)
        x = FIELD_MESSAGE_RECT.right + 18
        for index, (text, key, enabled, reason) in enumerate(specs):
            button = Button(pygame.Rect(x, FIELD_MESSAGE_RECT.y + index * (height + gap), width, height), text, key, enabled, reason)
            self.buttons.append(button)
            self._draw_button(button, 16)

    @staticmethod
    def _ui_label(value: str) -> str:
        labels = {
            "attack": "攻撃", "move": "移動", "ball": "ボール", "skill": "スキル", "wait": "待機",
            "physical": "物理", "magic": "魔法", "active": "アクティブ", "automatic": "自動発動", "reaction": "リアクション",
            "reaction_wait": "リアクション待機", "advanced": "上級", "normal": "通常", "single": "単体",
            "self": "自分", "ally": "味方", "enemy": "敵", "cell": "マス", "none": "なし",
            "adjacent_enemy": "隣接する敵", "adjacent_enemy_holder": "隣接する敵ボール保持者",
            "self_or_adjacent_ally": "自分または隣接する味方", "self_or_ally": "自分または味方", "single_enemy": "敵単体",
            "single_ally": "味方単体", "selected": "選択対象", "always": "常時",
            "damage": "ダメージ", "modify_stat": "能力変化", "heal_hp": "HP回復",
            "recover_mp": "MP回復", "prepare_reaction": "反応待機", "drop_ball": "ボール落下",
            "steal_ball": "ボール奪取", "force_move": "強制移動", "guard": "防御・軽減",
            "mana": "MP", "basic": "基本", "technique": "テクニック", "power": "パワー",
            "stamina": "スタミナ", "defender": "防御側", "common": "共通効果", "legacy": "既存互換",
            "best_physical_magic": "物理・魔法の高い方", "acquisition": "ボール非保持", "contest": "争奪中",
            "possession": "ボール保持", "ball_transfer": "ボール奪取", "ball_retained": "ボール維持",
            "damage_and_push": "ダメージ後に押し出し", "damage_and_reposition": "ダメージ後に位置変更",
            "damage_reduction": "ダメージ軽減", "heal": "回復", "ignore_zoc": "ZOC無視",
            "magic_damage": "魔法ダメージ", "blocked": "阻止", "intercepted": "迎撃",
            "miss": "失敗", "no_effect": "効果なし", "pass_continues": "パス継続",
            "movement_down": "移動力低下", "special_move": "特殊移動", "breakthrough": "突破",
            "buff": "強化", "debuff": "弱体化", "movement": "移動", "position": "位置変更",
            "mp_support": "MP支援", "pass_disrupt": "パス妨害", "ball_hold": "ボール保持スキル",
            "passive": "パッシブ", "aura": "範囲型", "modify_move_range": "移動力変化", "away": "対象から離れる方向",
            "enemy_pass": "敵のパス", "add": "加算", "subtract": "減算",
        }
        return labels.get(value, value.replace("_", " ") if value else "なし")

    def _action_detail_lines(self, candidate: ActionCandidate) -> list[str]:
        lines = [candidate.description or "説明はありません。", ""]
        lines.extend((
            f"カテゴリ：{'通常行動' if candidate.source_type == 'standard' else 'スキル'}",
            f"コマンド種別：{self._ui_label(candidate.command_group)}",
            f"対象タイプ：{self._ui_label(candidate.target_type)}",
            f"射程：{candidate.min_range}～{candidate.max_range}",
            f"消費リソース：{self._ui_label(candidate.resource_type)}",
            f"消費量：{candidate.resource_cost}",
            f"クールタイム：{candidate.cooldown if candidate.cooldown else 'なし'}",
            f"移動後使用：{'可能' if candidate.usable_after_move else '不可'}",
            f"現在使用可能：{'はい' if candidate.usable else 'いいえ'}",
        ))
        if not candidate.usable:
            lines.append(f"使用不可理由：{candidate.reason or '現在は使用できません'}")
        if not self.game or not candidate.skill_id:
            return lines
        skill = self.game.skills.get(candidate.skill_id)
        if skill is None:
            lines.append("スキル詳細：不明")
            return lines
        lines.extend((
            "",
            f"スキルカテゴリ：{self._ui_label(skill.category)}",
            f"系統：{self._ui_label(skill.skill_system)}",
            f"スキルタイプ：{self._ui_label(skill.skill_type)}",
            f"発動形式：{self._ui_label(skill.activation)}",
            f"必要技能レベル：{skill.required_level}",
            f"使用回数上限：{skill.max_uses if skill.max_uses else 'なし'}",
            f"ボール状態：{'保持時可' if skill.usable_with_ball else '保持時不可'} / {'非保持時可' if skill.usable_without_ball else '非保持時不可'}",
            f"使用可能状態：{' / '.join(self._ui_label(value) for state in skill.allowed_states for value in state.split('|'))}",
            f"実行後：{'行動終了' if skill.ends_action else '行動継続'}",
        ))
        if skill.activation == "automatic" and skill.purpose_tag == "ball_hold":
            target = "保持者本人" if skill.ball_hold_scope == "self" else f"保持者から{skill.ball_hold_range}マス以内の味方"
            lines.extend((
                "分類：ボール保持スキル",
                "発動条件：所有者本人がボール保持中",
                f"効果対象：{target}",
                f"保持者本人：{'対象に含む' if skill.ball_hold_include_self else '対象外'}",
                "消費：MP・行動枠・移動枠・CT・使用回数なし",
            ))
        actor_stats = [self._ui_label(value) for value in (skill.actor_primary_stat, skill.actor_secondary_stat) if value]
        defender_stats = [self._ui_label(value) for value in (skill.defender_primary_stat, skill.defender_secondary_stat) if value]
        if actor_stats:
            lines.append(f"行動側参照能力：{'＋'.join(actor_stats)}")
        if defender_stats:
            lines.append(f"防御側参照能力：{'＋'.join(defender_stats)}")
        if skill.use_dice:
            lines.append(f"成功判定：対抗判定（同値は{'防御側' if skill.tie_rule == 'defender' else self._ui_label(skill.tie_rule)}）")
        elif any(effect.effect_type == "damage" for effect in skill.effects):
            lines.append("成功判定：必中")
        if skill.effects:
            lines.extend(("", "効果一覧："))
            for effect in sorted(skill.effects, key=lambda item: item.effect_order):
                values: list[str] = [f"{effect.effect_order}. {self._ui_label(effect.effect_type)}"]
                if effect.base_value:
                    values.append(f"基礎値{effect.base_value}")
                refs = []
                if effect.stat1 and effect.rate1:
                    refs.append(f"{self._ui_label(effect.stat1)}×{effect.rate1:g}")
                if effect.stat2 and effect.rate2:
                    refs.append(f"{self._ui_label(effect.stat2)}×{effect.rate2:g}")
                if refs:
                    values.append("＋".join(refs))
                if effect.duration:
                    values.append(f"継続{effect.duration}回")
                if effect.distance:
                    values.append(f"距離{effect.distance}")
                if effect.aux1:
                    values.append(self._ui_label(effect.aux1))
                lines.append(" / ".join(values))
        for label, value in (("成功時効果", skill.success_effect), ("失敗時効果", skill.failure_effect), ("追加効果", skill.additional_effect)):
            if value:
                lines.append(f"{label}：{self._ui_label(value)}")
        return lines

    def _draw_action_selection_screen(self) -> None:
        assert self.game is not None
        self.buttons = []
        self.screen.fill((3, 12, 23))
        candidates = self._action_menu_candidates()
        title = f"{COMMAND_GROUP_LABELS.get(self.action_menu_command or '', '行動')}を選択してください"
        self._center_text(title, 31, GOLD_LIGHT, 45)
        self._draw_panel(ACTION_LIST_RECT, (5, 19, 33, 250), GOLD)
        self._draw_panel(ACTION_DETAIL_RECT, (5, 19, 33, 250), GOLD)
        self._draw_panel(ACTION_FOOTER_RECT, (5, 19, 33, 250), GOLD)
        self._text("行動一覧", 22, GOLD_LIGHT, (ACTION_LIST_RECT.x + 18, ACTION_LIST_RECT.y + 14))
        list_up = Button(pygame.Rect(ACTION_LIST_RECT.right - 128, ACTION_LIST_RECT.y + 10, 50, 34), "▲", "action_list_up", self.action_menu_scroll > 0)
        list_down = Button(pygame.Rect(ACTION_LIST_RECT.right - 68, ACTION_LIST_RECT.y + 10, 50, 34), "▼", "action_list_down", self.action_menu_scroll < max(0, len(candidates) - 9))
        detail_up = Button(pygame.Rect(ACTION_DETAIL_RECT.right - 128, ACTION_DETAIL_RECT.bottom - 46, 50, 34), "▲", "action_detail_up", self.action_detail_scroll > 0)
        detail_down = Button(pygame.Rect(ACTION_DETAIL_RECT.right - 68, ACTION_DETAIL_RECT.bottom - 46, 50, 34), "▼", "action_detail_down", True)
        self.buttons.extend((list_up, list_down, detail_up, detail_down))
        for scroll_button in (list_up, list_down, detail_up, detail_down):
            self._draw_button(scroll_button, 16)
        visible = 9
        self.action_menu_scroll = min(self.action_menu_scroll, max(0, len(candidates) - visible))
        for row, index in enumerate(range(self.action_menu_scroll, min(len(candidates), self.action_menu_scroll + visible))):
            candidate = candidates[index]
            rect = pygame.Rect(ACTION_LIST_RECT.x + 14, ACTION_LIST_RECT.y + 55 + row * 68, ACTION_LIST_RECT.width - 28, 58)
            button = Button(rect, f"{self._ui_label(candidate.command_group)[:1]}　{candidate.name}", f"action_focus:{index}", True)
            self.buttons.append(button)
            self._draw_button(button, 19)
            if not candidate.usable:
                disabled = pygame.Surface(rect.size, pygame.SRCALPHA)
                disabled.fill((35, 35, 39, 125))
                self.screen.blit(disabled, rect)
            if index == self.action_menu_selected:
                pygame.draw.rect(self.screen, (74, 184, 255), rect, 4, border_radius=7)
        if not candidates:
            self._text("候補がありません", 20, (180, 186, 196), (ACTION_LIST_RECT.x + 30, ACTION_LIST_RECT.y + 80))
            return
        self.action_menu_selected = min(self.action_menu_selected, len(candidates) - 1)
        candidate = candidates[self.action_menu_selected]
        self._text(candidate.name, 32, (255, 232, 165), (ACTION_DETAIL_RECT.x + 30, ACTION_DETAIL_RECT.y + 24))
        status = "使用可能" if candidate.usable else "使用不可"
        self._text(status, 18, (103, 232, 145) if candidate.usable else (255, 127, 127), (ACTION_DETAIL_RECT.right - 140, ACTION_DETAIL_RECT.y + 34))
        detail_lines: list[str] = []
        for raw_line in self._action_detail_lines(candidate):
            detail_lines.extend(self._wrap_lines(raw_line, self.font(17), ACTION_DETAIL_RECT.width - 64))
        visible_detail = 24
        self.action_detail_scroll = min(self.action_detail_scroll, max(0, len(detail_lines) - visible_detail))
        y = ACTION_DETAIL_RECT.y + 82
        for line in detail_lines[self.action_detail_scroll:self.action_detail_scroll + visible_detail]:
            self._text(line, 17, (213, 225, 239), (ACTION_DETAIL_RECT.x + 30, y))
            y += 23
        confirm = Button(pygame.Rect(ACTION_FOOTER_RECT.right - 430, ACTION_FOOTER_RECT.y + 10, 190, 50), "決定", "action_confirm", candidate.usable, candidate.reason)
        back = Button(pygame.Rect(ACTION_FOOTER_RECT.right - 220, ACTION_FOOTER_RECT.y + 10, 190, 50), "戻る", "action_back")
        self.buttons.extend((confirm, back))
        self._draw_button(confirm, 22)
        self._draw_button(back, 22)
        guide = candidate.reason if not candidate.usable else "候補を選択して決定してください。マウスホイールで一覧・詳細をスクロールできます。"
        self._draw_wrapped(guide, 15, (187, 207, 228), pygame.Rect(ACTION_FOOTER_RECT.x + 20, ACTION_FOOTER_RECT.y + 15, 870, 44))

    def _draw_actions(self, actor: Character | None) -> None:
        assert self.game is not None
        self._text("行動コマンド", 18, (185, 218, 246), (COMMAND_RECT.x + 12, COMMAND_RECT.y + 8))
        if self.input_locked:
            self._draw_wrapped("行動演出中です", 16, (255, 183, 126), COMMAND_RECT.inflate(-24, -76))
            return
        if self.game.restart_team == PLAYER and self.game.restart_prepared and not self.auto_player:
            self._draw_wrapped("青い対象枠の味方から保持者を選択", 15, (147, 220, 255), COMMAND_RECT.inflate(-24, -76))
            return
        if actor is None or actor.team != PLAYER or self.auto_player:
            label = "オート操作中" if self.auto_player and actor and actor.team == PLAYER else "敵AIが行動中"
            self._text(label, 16, (182, 192, 211), (COMMAND_RECT.x + 14, COMMAND_RECT.y + 48))
            return
        specs: list[tuple[str, str, bool, str]] = []
        if self.pending_skill_confirmation:
            skill_id, _ = self.pending_skill_confirmation
            skill = self.game.skills[skill_id]
            specs = [(f"{skill.name}を決定", "confirm_skill", True, ""), ("取り消し", "cancel_skill", True, "")]
        elif self.pending_attack_target:
            specs = [("攻撃を決定", "confirm_attack", True, ""), ("取り消し", "cancel_attack", True, "")]
        elif self.pending_pass_target:
            specs = [("パスを決定", "confirm_pass", True, ""), ("取り消し", "cancel_pass", True, "")]
        elif self.pending_cut_target and not self.pending_cut_system:
            specs = [
                ("物理カット", "cut_physical", True, ""),
                ("魔法カット", "cut_magic", True, ""),
                ("取り消し", "cancel_cut", True, ""),
            ]
        elif self.pending_cut_target and self.pending_cut_system:
            specs = [("カットを決定", "confirm_cut", True, ""), ("取り消し", "cancel_cut", True, "")]
        elif self.special_move_ignore_zoc:
            can_move = bool(self.game.reachable_positions(actor.char_id, ignore_zoc=True))
            specs = [("影渡り移動", "move", can_move, "移動可能なマスがありません"), ("移動せず終了", "wait", True, "")]
        else:
            specs = self._action_command_specs(actor)
        if self.game.pending_move and self.game.pending_move.plan.actor_id == actor.char_id:
            specs.append(("移動キャンセル", "cancel_move", True, ""))
        start_y = COMMAND_RECT.y + 39
        rows = max(1, (len(specs) + 1) // 2)
        gap = 6
        button_height = min(38, (COMMAND_RECT.bottom - 8 - start_y - gap * (rows - 1)) // rows)
        for index, (text, key, enabled, reason) in enumerate(specs):
            rect = pygame.Rect(COMMAND_RECT.x + 12 + (index % 2) * 176, start_y + (index // 2) * (button_height + gap), 168, button_height)
            button = Button(rect, text, key, enabled, reason)
            self.buttons.append(button)
            self._draw_button(button, 15)
            selected_group = self._command_group_for_button(key)
            selected = key == self.mode or (
                self.skill_menu and selected_group == (self.action_menu_command or "skill")
            )
            if selected:
                glow = pygame.Surface(rect.size, pygame.SRCALPHA)
                glow.fill((27, 160, 218, 70))
                self.screen.blit(glow, rect)
                pygame.draw.rect(self.screen, (89, 206, 249), rect, 3, border_radius=4)

    def _draw_skill_menu(self, actor: Character) -> None:
        assert self.game is not None
        command_group = self.action_menu_command or "skill"
        title = f"{COMMAND_GROUP_LABELS.get(command_group, 'スキル')}行動一覧"
        self._text(title, 17, (177, 218, 255), (SKILL_DETAIL_RECT.x + 12, SKILL_DETAIL_RECT.y + 8))
        y = SKILL_DETAIL_RECT.y + 34
        for candidate in self.game.action_candidates(actor.char_id, command_group):
            if y + 34 > SKILL_DETAIL_RECT.bottom - 5:
                continue
            source = "通常" if candidate.source_type == "standard" else "派生"
            move_text = "移動後可" if candidate.usable_after_move else "移動後不可"
            if candidate.resource_type == "mana":
                cost = f"MP{candidate.resource_cost}"
            else:
                cost = "MP0"
            range_text = (
                f"射程{candidate.max_range}"
                if candidate.min_range == candidate.max_range
                else f"射程{candidate.min_range}-{candidate.max_range}"
            )
            label = f"{candidate.name} [{source}] {cost} {range_text} CT{candidate.cooldown} {move_text}"
            if not candidate.usable:
                label += f" ×{candidate.reason}"
            button = Button(
                pygame.Rect(SKILL_DETAIL_RECT.x + 10, y, SKILL_DETAIL_RECT.width - 20, 31),
                label,
                f"action:{candidate.action_id}",
                candidate.usable,
                candidate.reason,
            )
            self.buttons.append(button)
            self._draw_button(button, 9)
            y += 34

    def _draw_selected_skill_details(self, actor: Character) -> None:
        assert self.game is not None
        if self.skill_menu:
            self._draw_skill_menu(actor)
            return
        body = SKILL_DETAIL_RECT.inflate(-24, -50)
        body.y += 18
        if self.pending_attack_target:
            target = self.game.characters[self.pending_attack_target]
            preview = self.game.damage_preview(actor.char_id, target.char_id)
            reductions = " / ".join(
                f"{item['name']}×{item['multiplier']:g}" for item in preview["reductions"]
            ) or "なし"
            self._text("攻撃予測", 17, (255, 226, 128), (SKILL_DETAIL_RECT.x + 12, SKILL_DETAIL_RECT.y + 8))
            text = (
                f"攻撃者:{actor.name} / 攻撃:通常{preview['system_name']}攻撃 / 対象:{target.name}\n"
                f"予想ダメージ:{preview['predicted_damage']}  対象HP:{target.hp} → {max(0, target.hp - preview['predicted_damage'])}"
                f"{'（戦闘不能）' if target.hp <= preview['predicted_damage'] else ''}\n"
                f"技威力:{preview['base_power']}  攻撃:{preview['attack_system_value']}+パワー{preview['power']}={preview['attack_value']}\n"
                f"防御:{preview['defense_system_value']}+スタミナ{preview['stamina']}×0.5={preview['defense_value']:g}\n"
                f"能力差:{preview['ability_difference']:+g}×{preview['difference_multiplier']:g}="
                f"{preview['ability_correction']:+g}  基本ダメージ:{preview['basic_damage']}\n"
                f"軽減:{reductions}  予想ダメージ:{preview['predicted_damage']}"
            )
            self._draw_wrapped(text, 12, (207, 222, 238), body)
            return
        if self.pending_cut_target:
            target = self.game.characters[self.pending_cut_target]
            self._text("カット詳細", 17, (177, 218, 255), (SKILL_DETAIL_RECT.x + 12, SKILL_DETAIL_RECT.y + 8))
            if not self.pending_cut_system:
                self._draw_wrapped(
                    f"対象:{target.name}\n物理または魔法を選択すると成功率と補正を表示します",
                    13, (207, 222, 238), body,
                )
                return
            preview = self.game.cut_preview(actor.char_id, target.char_id, self.pending_cut_system)
            text = (
                f"{preview['system_name']}カット / 成功率:{preview['success_rate']}%\n"
                f"カット側 技術:{preview['cut_technique']} 系統補正:{preview['cut_system_bonus']} "
                f"カット補正:{preview['cut_bonus']:+d} 値:{preview['cut_value']}\n"
                f"保持側 技術:{preview['keep_technique']} 系統補正:{preview['keep_system_bonus']} "
                f"キープ補正:{preview['ball_keep_bonus']:+d} 状態:{preview['keep_state_bonus']:+d} 値:{preview['keep_value']}\n"
                f"能力差:{preview['ability_difference']:+d} 基礎:{preview['base_rate']}% "
                f"差補正:{preview['ability_rate_bonus']:+d}% 状況:{preview['situation_bonus']:+d}%"
            )
            self._draw_wrapped(text, 11, (207, 222, 238), body)
            return
        if self.pending_pass_target and self.pending_pass_system:
            target = self.game.characters[self.pending_pass_target]
            preview = self._pass_preview(actor.char_id, target.char_id, self.pending_pass_system)
            self._text("パス予測", 17, (255, 226, 128), (SKILL_DETAIL_RECT.x + 12, SKILL_DETAIL_RECT.y + 8))
            candidate_text = " / ".join(
                f"{index}.{item['character_name']} カット{item['success_rate']}% "
                f"({'受動' if item.get('cut_type') == 'passive' else '反応'}) 判定位置{item['reaction_cell']}"
                for index, item in enumerate(preview["candidates"], start=1)
            ) or "パスカット候補なし"
            text = (
                f"使用者:{actor.name} / {preview['system_name']}パス / 対象:{target.name}\n"
                f"最終パス成功率:{preview['pass_through_rate']}%  射程:{preview['pass_range']} 距離:{preview['distance']}\n"
                f"パス経路:{preview['line']}\n"
                f"技術:{preview['technique']} 系統値:{preview['system_value']} 補正:{preview['system_bonus']} "
                f"パス補正:{preview['pass_bonus']:+d} 距離補正:-{preview['distance_penalty']} 値:{preview['pass_value']}\n"
                f"候補: {candidate_text}\n通過率:{preview['pass_through_rate']}%"
            )
            self._draw_wrapped(text, 10, (207, 222, 238), body)
            return
        skill_id = self.pending_skill_confirmation[0] if self.pending_skill_confirmation else self.focused_skill_id
        skill = self.game.skills.get(skill_id or "")
        if skill:
            if self.pending_skill_confirmation and self.game._effect_of_type(skill, "damage"):
                _, target_id = self.pending_skill_confirmation
                preview = self.game.common_skill_damage_preview(actor.char_id, target_id, skill.skill_id)
                target = self.game.characters.get(target_id)
                if preview.get("valid") and target is not None:
                    reductions = " / ".join(
                        f"{item['name']}×{item['multiplier']:g}" for item in preview["reductions"]
                    ) or "なし"
                    self._text(f"攻撃予測：{skill.name}", 17, (255, 226, 128), (SKILL_DETAIL_RECT.x + 12, SKILL_DETAIL_RECT.y + 8))
                    text = (
                        f"攻撃者:{actor.name} / 攻撃:{skill.name} / 対象:{target.name}\n"
                        f"予想ダメージ:{preview['predicted_damage']}  対象HP:{target.hp} → {max(0, target.hp - preview['predicted_damage'])}"
                        f"{'（戦闘不能）' if target.hp <= preview['predicted_damage'] else ''}\n"
                        f"技威力:{preview['base_power']}  攻撃:{preview['attack_system_value']}+パワー{preview['power']}={preview['attack_value']}\n"
                        f"防御:{preview['defense_system_value']}+スタミナ{preview['stamina']}×0.5={preview['defense_value']:g}\n"
                        f"能力差:{preview['ability_difference']:+g}×{preview['difference_multiplier']:g}="
                        f"{preview['ability_correction']:+g}  基本ダメージ:{preview['basic_damage']}\n"
                        f"軽減:{reductions}  予想ダメージ:{preview['predicted_damage']}"
                    )
                    ball = self.game.ball_effect_preview(actor.char_id, target.char_id, skill.skill_id)
                    if ball.get("effect_type") != "none":
                        label = "ボールカット" if ball["effect_type"] == "cut" else "ボールドロップ"
                        if ball.get("active"):
                            text += (
                                f"\n{label}: {ball['actor_stat']} {ball['actor_value']} vs "
                                f"{ball['defender_stat']} {ball['defender_value']} / "
                                f"基礎{ball['base_rate']}% 差補正{ball['ability_rate_bonus']:+d}% "
                                f"→ {ball['final_rate']}%"
                            )
                        else:
                            text += f"\n{label}: 対象が保持者の場合のみ"
                    self._draw_wrapped(text, 11, (207, 222, 238), body)
                    return
            enabled, reason = self.game.can_use_skill(actor.char_id, skill.skill_id)
            effects = " / ".join(
                {
                    "damage": "ダメージ", "modify_stat": "能力上昇", "heal_hp": "HP回復",
                    "recover_mp": "MP回復", "prepare_reaction": "反応待機",
                    "drop_ball": "ボール落下", "steal_ball": "ボール奪取",
                    "force_move": "強制移動", "pass": "射程強化パス", "guard": "防御状態",
                }.get(effect.effect_type, effect.effect_type)
                for effect in skill.effects
            ) or skill.description
            self._text(skill.name, 17, (177, 218, 255), (SKILL_DETAIL_RECT.x + 12, SKILL_DETAIL_RECT.y + 8))
            self._draw_wrapped(
                f"{skill.description}\n効果:{effects}\n{self.game.skill_cost_text(skill)} / 射程:{skill.min_range}-{skill.range} "
                f"/ CT:{skill.cooldown} / {'使用可能' if enabled else reason}",
                11, (207, 222, 238), body,
            )
            return
        self._text("スキル", 17, (177, 218, 255), (SKILL_DETAIL_RECT.x + 12, SKILL_DETAIL_RECT.y + 8))
        self._draw_wrapped(
            "スキルを選択すると詳細を表示します",
            14,
            (158, 176, 197),
            SKILL_DETAIL_RECT.inflate(-24, -58),
        )

    def _draw_judgement_panel(self, details: dict[str, object]) -> None:
        contest = details.get("contest")
        ability = details.get("ability")
        damage = details.get("damage_calculation")
        cut_rate = details.get("success_rate")
        pass_results = details.get("pass_cut_results")
        effect_results = details.get("effect_results")
        ball_effect = details.get("ball_effect")
        if (
            not isinstance(contest, dict)
            and not isinstance(ability, dict)
            and not isinstance(damage, dict)
            and cut_rate is None
            and not isinstance(pass_results, list)
            and not isinstance(effect_results, list)
            and not isinstance(ball_effect, dict)
        ):
            return
        rect = pygame.Rect(FIELD_RECT.x + 176, FIELD_RECT.y + 354, 656, 166)
        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        overlay.fill((13, 19, 34, 238))
        self.screen.blit(overlay, rect)
        pygame.draw.rect(self.screen, (255, 212, 89), rect, 3, border_radius=8)
        title = f"判定：{details.get('action_name', '行動')}"
        self._text(title, 21, (255, 226, 128), (rect.x + 16, rect.y + 10))
        if self.presentation_waiting_for_confirm:
            self._text("クリックまたはタップで次へ", 14, (255, 226, 128), (rect.right - 224, rect.y + 14))
        if isinstance(damage, dict):
            self._text(
                f"{damage.get('system_name')} / 技威力{damage.get('base_power')} / "
                f"攻撃{damage.get('attack_value')} - 防御{damage.get('defense_value')} / "
                f"差{damage.get('ability_difference'):+g}×{damage.get('difference_multiplier'):g}",
                16, (205, 226, 255), (rect.x + 16, rect.y + 48),
            )
            reductions = damage.get("reductions")
            reduction_text = ""
            if isinstance(reductions, list) and reductions:
                reduction_text = " / " + " / ".join(str(item.get("name")) for item in reductions if isinstance(item, dict))
            self._text(
                f"必中 / 基本ダメージ{damage.get('basic_damage')} → 最終{damage.get('damage')}{reduction_text}",
                20, (118, 241, 151), (rect.x + 16, rect.y + 92),
            )
            if isinstance(ball_effect, dict):
                if ball_effect.get("forced_knockout_drop"):
                    ball_text = f"戦闘不能による強制ドロップ / 落下 {ball_effect.get('drop_position')}"
                else:
                    label = "カット" if ball_effect.get("effect_type") == "cut" else "ドロップ"
                    ball_text = (
                        f"{label}: {ball_effect.get('actor_stat')} {ball_effect.get('actor_value')} - "
                        f"{ball_effect.get('defender_stat')} {ball_effect.get('defender_value')} / "
                        f"基礎{ball_effect.get('base_rate')}% 差{ball_effect.get('ability_difference', 0):+d} "
                        f"補正{ball_effect.get('ability_rate_bonus', 0):+d}% 最終{ball_effect.get('final_rate')}% / "
                        f"乱数{ball_effect.get('roll')} / {'成功' if ball_effect.get('success') else '失敗'}"
                    )
                self._draw_wrapped(ball_text, 14, (255, 190, 112), pygame.Rect(rect.x + 16, rect.y + 126, rect.width - 32, 36))
            if damage.get("ball_drop_position") is not None:
                self._text(
                    f"遠距離撃破：ボール落下 {damage.get('ball_drop_position')}",
                    16, (255, 190, 112), (rect.x + 16, rect.y + 132),
                )
            return
        if cut_rate is not None and details.get("cut_value") is not None:
            success = bool(details.get("success"))
            self._text(
                f"カット値{details.get('cut_value')} - 保持値{details.get('keep_value')} = 差{details.get('ability_difference'):+d}",
                17, (205, 226, 255), (rect.x + 16, rect.y + 48),
            )
            self._text(
                f"成功率{cut_rate}% / 乱数{details.get('random_roll')} / {'成功' if success else '失敗'}",
                20, (118, 241, 151) if success else (255, 124, 124), (rect.x + 16, rect.y + 92),
            )
            return
        if isinstance(pass_results, list):
            result_text = " / ".join(
                f"{item.get('character_name')} {item.get('success_rate')}%→{item.get('roll')} {'成功' if item.get('success') else '失敗'}"
                for item in pass_results if isinstance(item, dict)
            ) or "候補なし：確定成功"
            self._draw_wrapped(result_text, 15, (205, 226, 255), pygame.Rect(rect.x + 16, rect.y + 48, rect.width - 32, 72))
            return
        if details.get("skill_id") == "steal":
            success = bool(details.get("skill_success"))
            before = self.game.characters.get(str(details.get("holder_before", "")))
            after = self.game.characters.get(str(details.get("holder_after", "")))
            lines = [
                f"使用者:{details.get('actor_name', '')} / 対象:{details.get('target_name', '')}",
                f"{details.get('actor_stat_name', 'テクニック')} {details.get('actor_value')} - "
                f"{details.get('target_stat_name', 'テクニック')} {details.get('target_value')} / 能力差{details.get('ability_difference', 0):+d}",
                f"基礎{details.get('base_rate')}% / 差補正{details.get('ability_rate_bonus', 0):+d}% / "
                f"最終{details.get('success_rate')}% / 乱数{details.get('random_roll')}",
                f"スティール{'成功' if success else '失敗'} / 保持者:"
                f"{before.name if before else 'なし'} → {after.name if after else 'なし'}",
            ]
            self._draw_wrapped("\n".join(lines), 15, (118, 241, 151) if success else (255, 150, 135), pygame.Rect(rect.x + 16, rect.y + 43, rect.width - 32, 116))
            return
        if isinstance(effect_results, list) and not isinstance(contest, dict):
            lines: list[str] = []
            for item in effect_results:
                if not isinstance(item, dict):
                    continue
                result = str(item.get("target_name", ""))
                if item.get("healing") is not None:
                    result += f" HP+{item.get('healing')}"
                if item.get("mana_recovery") is not None:
                    result += f" MP+{item.get('mana_recovery')}"
                if item.get("stat"):
                    value = int(item.get("value", 0))
                    stat = str(item.get("stat", ""))
                    result += f" {STAT_LABELS.get(stat, stat)}{value:+d} ({item.get('duration')}回)"
                if item.get("reaction"):
                    result += " 反応待機"
                if item.get("holder_id"):
                    result += " ボール奪取"
                if item.get("ball_drop_position"):
                    result += f" ボール落下{item.get('ball_drop_position')}"
                if item.get("target_move"):
                    result += f" 強制移動{item.get('target_move')}"
                if item.get("range_bonus"):
                    result += f" パス射程+{item.get('range_bonus')}"
                if item.get("defending"):
                    result += " 防御状態"
                references = item.get("references")
                if isinstance(references, list) and references:
                    result += " [" + ", ".join(
                        f"{ref.get('stat')} {ref.get('value')}×{ref.get('rate'):g}"
                        for ref in references if isinstance(ref, dict)
                    ) + "]"
                lines.append(result)
            self._draw_wrapped("\n".join(lines) or "効果なし", 16, (205, 226, 255), pygame.Rect(rect.x + 16, rect.y + 48, rect.width - 32, 82))
            return
        if not isinstance(contest, dict) and isinstance(ability, dict):
            pair = (
                f"{ability.get('primary_name')} {ability.get('primary_value')} + "
                f"{ability.get('secondary_name')} {ability.get('secondary_value')}"
            )
            self._text(
                f"{details.get('actor_name', '')}  {pair} = 合計{ability.get('total')}",
                17,
                (205, 226, 255),
                (rect.x + 16, rect.y + 50),
            )
            effect_die = details.get("effect_die")
            result = f"効果ダイス 1D6={effect_die}" if effect_die is not None else "対抗なし"
            if details.get("healing"):
                result += f" / 回復 {details.get('healing')}"
            self._text(result, 20, (118, 241, 151), (rect.x + 16, rect.y + 94))
            return
        offense_stats = contest.get("offense_stats")
        if isinstance(offense_stats, dict):
            offense_pair = (
                f"{offense_stats.get('primary_name')} {offense_stats.get('primary_value')} + "
                f"{offense_stats.get('secondary_name')} {offense_stats.get('secondary_value')}"
            )
        else:
            offense_pair = str(contest.get("offense_name"))
        offense = (
            f"{details.get('actor_name', '')}  {offense_pair} = {contest.get('offense_value')}面"
            f" →出目{contest.get('offense_roll')} +補正{contest.get('offense_bonus')}"
        )
        attribute = contest.get("attribute")
        if isinstance(attribute, dict) and attribute.get("used"):
            relation_names = {"advantage": "有利", "disadvantage": "不利", "neutral": "等倍"}
            offense += (
                f" +属性{attribute.get('rolls')}({relation_names.get(attribute.get('relation'), '等倍')})"
            )
        offense += f" = {contest.get('offense_total')}"
        defense_stats = contest.get("defense_stats")
        if isinstance(defense_stats, dict):
            defense_pair = (
                f"{defense_stats.get('primary_name')} {defense_stats.get('primary_value')} + "
                f"{defense_stats.get('secondary_name')} {defense_stats.get('secondary_value')}"
            )
        else:
            defense_pair = str(contest.get("defense_name"))
        defense = (
            f"{details.get('target_name', '')}  {defense_pair} = {contest.get('defense_value')}面"
            f" →出目{contest.get('defense_roll')} +補正{contest.get('defense_bonus')} = {contest.get('defense_total')}"
        )
        self._text(offense, 16, (205, 226, 255), (rect.x + 16, rect.y + 48))
        self._text(defense, 16, (255, 202, 207), (rect.x + 16, rect.y + 76))
        result = "成功" if contest.get("success") else "失敗（同値は防御側）"
        if details.get("damage"):
            result += f" / ダメージ {details.get('damage')}"
        elif details.get("healing"):
            result += f" / 回復 {details.get('healing')}"
        self._text(result, 20, (118, 241, 151) if contest.get("success") else (255, 124, 124), (rect.x + 16, rect.y + 112))

    def _draw_match_controls(self) -> None:
        assert self.game is not None
        button = Button(
            FULL_LOG_BUTTON_RECT.copy(),
            "全ログ",
            "full_log",
            not self.input_locked,
            "行動演出の完了後に開けます",
        )
        self.buttons.append(button)
        self._draw_button(button, 19)
        retire = Button(
            pygame.Rect(FULL_LOG_BUTTON_RECT.x - 154, FULL_LOG_BUTTON_RECT.y, 144, 44),
            "リタイア",
            "retire",
            not self.input_locked and not self.game.match_over,
            "行動演出の完了後に操作できます",
        )
        self.buttons.append(retire)
        self._draw_button(retire, 18)

    def _draw_options_overlay(self) -> None:
        overlay = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        overlay.fill((3, 8, 16, 190))
        self.screen.blit(overlay, (0, 0))
        panel = pygame.Rect(500, 245, 440, 410)
        self._draw_panel(panel, (8, 21, 36, 252), GOLD_LIGHT)
        self._center_text_at("オプション", 31, GOLD_LIGHT, (panel.centerx, panel.y + 52))
        specs = (
            ("全ログ", "options_full_log"),
            ("リタイア", "options_retire"),
            ("閉じる", "options_close"),
        )
        for index, (text, key) in enumerate(specs):
            button = Button(pygame.Rect(panel.x + 70, panel.y + 105 + index * 88, panel.width - 140, 58), text, key)
            self.buttons.append(button)
            self._draw_button(button, 22)

    def _draw_retire_confirmation(self) -> None:
        overlay = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        overlay.fill((4, 7, 15, 218))
        self.screen.blit(overlay, (0, 0))
        panel = pygame.Rect(360, 270, 720, 310)
        self._draw_panel(panel, (24, 28, 39, 252), (224, 112, 91))
        self._center_text("リタイア確認", 36, (255, 199, 160), panel.y + 38)
        self._center_text("現在の試合をリタイアしますか。", 25, (235, 239, 246), panel.y + 105)
        result_text = "勝敗なしのデバッグ中断になります" if self.game and self.game.config.debug_mode else "味方の敗北として試合を終了します"
        self._center_text(result_text, 18, (190, 207, 226), panel.y + 150)
        confirm = Button(pygame.Rect(panel.x + 80, panel.bottom - 82, 250, 50), "リタイアを確定", "retire_confirm")
        cancel = Button(pygame.Rect(panel.right - 330, panel.bottom - 82, 250, 50), "試合へ戻る", "retire_cancel")
        self.buttons.extend((confirm, cancel))
        self._draw_button(confirm, 19)
        self._draw_button(cancel, 19)

    def _draw_full_log_overlay(self) -> None:
        assert self.game is not None
        overlay = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        overlay.fill((4, 7, 15, 225))
        self.screen.blit(overlay, (0, 0))
        panel = pygame.Rect(170, 70, 1100, 760)
        pygame.draw.rect(self.screen, (25, 33, 50), panel, border_radius=12)
        pygame.draw.rect(self.screen, (112, 151, 196), panel, 3, border_radius=12)
        self._text("全ログ", 34, (238, 244, 255), (panel.x + 28, panel.y + 20))
        lines: list[str] = []
        for event in self.game.events:
            wrapped = self._wrap_lines(event, self.font(16), 940)
            lines.extend(wrapped or [""])
        if not lines:
            lines = ["ログはありません。"]
        visible_count = 29
        max_scroll = max(0, len(lines) - visible_count)
        self.log_scroll = min(self.log_scroll, max_scroll)
        y = panel.y + 78
        for line in lines[self.log_scroll:self.log_scroll + visible_count]:
            self._text(line, 16, (205, 216, 233), (panel.x + 34, y))
            y += 20
        position_text = f"{self.log_scroll + 1}-{min(len(lines), self.log_scroll + visible_count)} / {len(lines)}行"
        self._text(position_text, 14, (151, 171, 198), (panel.right - 195, panel.y + 28))
        buttons = (
            Button(pygame.Rect(panel.x + 30, panel.bottom - 68, 180, 44), "戻る", "log_back"),
            Button(pygame.Rect(panel.x + 230, panel.bottom - 68, 220, 44), "ログをコピー", "log_copy"),
            Button(pygame.Rect(panel.right - 250, panel.bottom - 68, 96, 44), "上へ", "log_up", self.log_scroll > 0),
            Button(pygame.Rect(panel.right - 136, panel.bottom - 68, 96, 44), "下へ", "log_down", self.log_scroll < max_scroll),
        )
        self.buttons.extend(buttons)
        for button in buttons:
            self._draw_button(button, 17)
        if self.log_notice:
            color = (125, 234, 158) if "しました" in self.log_notice else (255, 151, 151)
            self._text(self.log_notice, 17, color, (panel.x + 475, panel.bottom - 57))

    def _draw_result_overlay(self) -> None:
        assert self.game is not None
        overlay = pygame.Surface(WINDOW_SIZE, pygame.SRCALPHA)
        overlay.fill((4, 7, 15, 205))
        self.screen.blit(overlay, (0, 0))
        summary = self.game.result_summary()
        if summary["end_reason"] == "debug_interrupt":
            result = "デバッグ中断"
        elif summary["end_reason"] == "retire":
            result = "リタイア敗北"
        else:
            result = "引き分け" if summary["draw"] else "味方の勝利" if summary["winner"] == PLAYER else "敵の勝利"
        self._center_text("試合終了", 54, (255, 230, 133), 230)
        self._center_text(result, 42, (236, 242, 255), 320)
        self._center_text(f"終了理由：{summary['end_reason_name']}", 18, (184, 202, 224), 360)
        self._center_text(
            f"味方 {summary['player_score']} - {summary['enemy_score']} 敵    経過 {summary['rounds']}ラウンド",
            27,
            (196, 214, 238),
            400,
        )
        self._center_text(
            f"パス {summary['passes']} / 奪取 {summary['steals']} / パスカット {summary['interceptions']} / スキル {summary['skill_uses']}",
            20,
            (161, 182, 211),
            445,
        )
        group = self._party_enemy_group(self.active_enemy_group_id)
        quest_name = self._active_quest_name()
        if quest_name:
            self._center_text(
                f"クエスト：{quest_name}",
                20,
                (255, 201, 143),
                482,
            )
        elif self.active_enemy_group_id:
            self._center_text(
                f"対戦相手：{group.group.name if group else self.active_enemy_group_id}",
                20,
                (255, 201, 143),
                482,
            )
        result_buttons = [
            Button(pygame.Rect(520, 515, 400, 48), "全ログ", "full_log"),
            Button(pygame.Rect(520, 574, 400, 48), "もう一度試合", "restart"),
            Button(
                pygame.Rect(520, 633, 400, 48),
                "自由試合設定へ戻る" if self.active_debug_mode else "編成へ戻る",
                "result_party",
            ),
        ]
        if self.active_quest_id:
            result_buttons.extend((
                Button(pygame.Rect(520, 692, 400, 48), "クエスト選択へ戻る", "result_quest_select"),
                Button(pygame.Rect(520, 751, 400, 48), "ホームへ戻る", "title"),
            ))
        else:
            result_buttons.append(Button(pygame.Rect(520, 692, 400, 48), "ホームへ戻る", "title"))
        self.buttons.extend(result_buttons)
        for button in result_buttons:
            self._draw_button(button, 20)

    def _draw_button(self, button: Button, size: int) -> None:
        fill = (10, 35, 52) if button.enabled else (42, 45, 50)
        border = GOLD if button.enabled else (81, 82, 84)
        pygame.draw.rect(self.screen, fill, button.rect, border_radius=7)
        pygame.draw.rect(self.screen, border, button.rect, 2, border_radius=7)
        color = (239, 224, 190) if button.enabled else (123, 126, 130)
        self._center_text_at(button.text, size, color, button.rect.center)

    def _text(self, text: str, size: int, color: tuple[int, int, int], position: tuple[int, int]) -> None:
        self.screen.blit(self.font(size).render(text, True, color), position)

    def _center_text(self, text: str, size: int, color: tuple[int, int, int], y: int) -> None:
        surface = self.font(size).render(text, True, color)
        self.screen.blit(surface, surface.get_rect(center=(WINDOW_SIZE[0] // 2, y)))

    def _center_text_at(
        self, text: str, size: int, color: tuple[int, int, int], center: tuple[int, int]
    ) -> None:
        surface = self.font(size).render(text, True, color)
        self.screen.blit(surface, surface.get_rect(center=center))

    def _draw_wrapped(
        self, text: str, size: int, color: tuple[int, int, int], rect: pygame.Rect
    ) -> None:
        y = rect.y
        font = self.font(size)
        for line in self._wrap_lines(text, font, rect.width):
            if y + font.get_height() > rect.bottom:
                break
            self.screen.blit(font.render(line, True, color), (rect.x, y))
            y += font.get_linesize()

    @staticmethod
    def _wrap_lines(text: str, font: pygame.font.Font, width: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for character in text:
            if character == "\n":
                lines.append(current)
                current = ""
                continue
            candidate = current + character
            if current and font.size(candidate)[0] > width:
                lines.append(current)
                current = character
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines or [""]

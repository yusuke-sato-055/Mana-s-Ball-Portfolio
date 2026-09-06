"""Enemy AI profile definitions and validation."""
from __future__ import annotations
import logging
from dataclasses import dataclass

LOGGER = logging.getLogger("manaball.ai")
AI_VALUE_MIN, AI_VALUE_MAX, AI_VALUE_DEFAULT = 0, 10, 5
AI_LEVEL_MIN, AI_LEVEL_MAX, AI_LEVEL_DEFAULT = 1, 10, 5
DEFAULT_AI_PROFILE_ID = "standard"

@dataclass(frozen=True)
class AISettingDefinition:
    key: str
    label: str
    category: str
    description: str
    low_label: str = "低い"
    high_label: str = "高い"

@dataclass(frozen=True)
class AIProfile:
    ai_profile_id: str
    name: str
    description: str
    values: dict[str, int]

AI_CATEGORIES = (("action", "行動傾向"), ("position", "位置取り"), ("judgment", "判断補正"))
AI_SETTING_DEFINITIONS = (
    AISettingDefinition("attack_priority", "攻撃", "action", "通常攻撃とダメージスキル"),
    AISettingDefinition("pass_priority", "パス", "action", "通常パスとパススキル"),
    AISettingDefinition("score_priority", "得点", "action", "得点へ直接つながる行動"),
    AISettingDefinition("heal_priority", "回復", "action", "HP回復"),
    AISettingDefinition("support_priority", "支援", "action", "強化、MP回復、防御、反応準備"),
    AISettingDefinition("steal_priority", "奪取", "action", "スティールとボールカット"),
    AISettingDefinition("ball_keep_priority", "ボール保持", "action", "保持継続"),
    AISettingDefinition("loose_ball_priority", "ルーズボール取得", "position", "ルーズボールへの接近と取得"),
    AISettingDefinition("ally_guard_priority", "味方護衛", "position", "重要な味方の護衛"),
    AISettingDefinition("own_goal_defense_priority", "自軍ゴール守備", "position", "自軍ゴール防衛"),
    AISettingDefinition("mp_usage", "MP使用", "judgment", "MP消費行動の積極性"),
    AISettingDefinition("risk_tolerance", "危険許容", "judgment", "低成功率や危険位置の許容度"),
)
AI_SETTING_KEYS = tuple(item.key for item in AI_SETTING_DEFINITIONS)
AI_SETTINGS_BY_KEY = {item.key: item for item in AI_SETTING_DEFINITIONS}

def default_ai_values() -> dict[str, int]:
    return {key: AI_VALUE_DEFAULT for key in AI_SETTING_KEYS}

def clamp_ai_value(value: int) -> int:
    return min(AI_VALUE_MAX, max(AI_VALUE_MIN, int(value)))

def clamp_ai_level(value: int) -> int:
    return min(AI_LEVEL_MAX, max(AI_LEVEL_MIN, int(value)))

def trend_label(value: int) -> str:
    return ("禁止" if value == 0 else "非常に低い" if value <= 2 else "低い" if value <= 4 else
            "標準" if value == 5 else "高い" if value <= 8 else "非常に高い")

def safe_ai_profile(profiles: dict[str, AIProfile], ai_profile_id: str, context: str) -> AIProfile:
    profile_id = (ai_profile_id or DEFAULT_AI_PROFILE_ID).strip()
    if profile_id in profiles:
        return profiles[profile_id]
    LOGGER.warning("%s のAIプロフィールIDが存在しないか無効なため標準AIを使用します: %s", context, profile_id)
    return profiles.get(DEFAULT_AI_PROFILE_ID) or AIProfile(DEFAULT_AI_PROFILE_ID, "標準型", "システム共通の標準AI", default_ai_values())

def effective_ai_values(profile: AIProfile) -> dict[str, int]:
    return {key: clamp_ai_value(profile.values.get(key, AI_VALUE_DEFAULT)) for key in AI_SETTING_KEYS}

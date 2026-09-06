"""Match balance report generation for Mana's Ball."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import tempfile
from pathlib import Path
from typing import Any
import os


LOGGER = logging.getLogger("manaball.reporting")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_PREFIX = ""
REPORT_TITLE = "# Mana's Ball 試合バランスレポート"
AI_REPORT_CANDIDATE_LIMIT = 5
AI_SETTING_NAMES = {
    "attack_priority": "攻撃優先度", "pass_priority": "パス優先度", "score_priority": "得点優先度",
    "heal_priority": "回復優先度", "support_priority": "支援優先度", "steal_priority": "奪取優先度",
    "ball_keep_priority": "ボール保持優先度", "loose_ball_priority": "ルーズボール優先度",
    "ally_guard_priority": "味方護衛優先度", "own_goal_defense_priority": "自軍ゴール守備優先度",
    "mp_usage": "MP使用", "risk_tolerance": "危険許容",
}


@dataclass
class _PendingAction:
    action_name: str
    actor_id: str
    target_id: str
    command_group: str
    before: dict[str, Any]


class MatchReportCollector:
    """Collect one match's state transitions and write a Markdown report."""

    def __init__(self, output_policy: str = "test", output_root: Path | None = None) -> None:
        if output_policy not in {"play", "ci", "test"}:
            raise ValueError(f"ログ出力方針が不正です: {output_policy}")
        self.output_policy = output_policy
        self.output_root = output_root
        self.execution_mode = "manual"
        self.start_snapshot: dict[str, Any] | None = None
        self.pending_action: _PendingAction | None = None
        self.pending_ai_details: dict[str, Any] | None = None
        self.action_records: list[dict[str, Any]] = []
        self.pending_system_records: list[dict[str, Any]] = []
        self.match_finished = False
        self.finish_summary: dict[str, Any] | None = None
        self.report_written = False
        self.output_path: Path | None = None
        self.markdown: str = ""
        self.last_character_action_id = 0
        self.last_score_event_id = 0
        self._started = False

    def set_execution_mode(self, mode: str) -> None:
        normalized = (mode or "manual").strip().lower()
        self.execution_mode = normalized if normalized in {"manual", "auto", "headless"} else "manual"
        if self.start_snapshot is not None:
            self.start_snapshot["execution_mode"] = self.execution_mode

    def start(self, game: Any) -> None:
        if self._started:
            return
        self.start_snapshot = self._capture_start_snapshot(game)
        self._started = True

    def begin_action(
        self,
        game: Any,
        action_name: str,
        actor_id: str | None = None,
        target_id: str | None = None,
        command_group: str = "",
    ) -> None:
        if not self._started or self.report_written or self.pending_action is not None:
            return
        self.pending_action = _PendingAction(
            action_name=action_name,
            actor_id=actor_id or "",
            target_id=target_id or "",
            command_group=command_group or "",
            before=self._capture_state(game),
        )

    def record_action(self, game: Any, result: Any) -> None:
        pending = self.pending_action
        if pending is None or not self._started or self.report_written:
            return
        after = self._capture_state(game)
        normalized = self._normalize_result(result)
        if self.pending_ai_details:
            normalized["details"].update(self.pending_ai_details)
            self.pending_ai_details = None
        before_participants = self._participant_lookup(pending.before.get("participants", []))
        after_participants = self._participant_lookup(after.get("participants", []))
        actor_id = self._character_id(pending.actor_id or normalized["details"].get("actor_id"))
        target_id = self._character_id(pending.target_id or normalized["details"].get("target_id"))
        movement_path = self._movement_path(normalized["details"])
        movement_distance = self._movement_distance(normalized["details"], movement_path)
        calculated_damage = self._extract_calculated_damage(normalized["details"])
        effective_damage = self._extract_effective_damage(normalized, pending.before, after, actor_id, target_id)
        calculated_hp_recovery = self._extract_calculated_hp_recovery(normalized)
        effective_hp_recovery = self._extract_effective_hp_recovery(normalized, pending.before, after, actor_id, target_id)
        calculated_mp_recovery = self._extract_calculated_mp_recovery(normalized)
        effective_mp_recovery = self._extract_effective_mp_recovery(normalized, pending.before, after, actor_id)
        action_sequence = len(self.action_records) + 1
        record = {
            "sequence": action_sequence,
            "action_name": pending.action_name,
            "actor_id": actor_id,
            "target_id": target_id,
            "command_group": pending.command_group,
            "before": pending.before,
            "after": after,
            "result": normalized,
            "record_type": "character_action",
            "event_type": self._event_type_from_record(pending.action_name, normalized["details"], normalized),
            "movement_type": self._movement_type_from_record(pending.command_group, normalized["details"]),
            "score_method": self._score_method_from_record(pending.action_name, normalized["details"]),
            "source_action_id": action_sequence,
            "position_before": before_participants.get(actor_id, {}).get("position"),
            "position_after": after_participants.get(actor_id, {}).get("position"),
            "movement_path": movement_path,
            "movement_distance": movement_distance,
            "execution_success": bool(normalized.get("success")),
            "result_success": self._result_success(normalized),
            "calculated_damage": calculated_damage,
            "effective_damage": effective_damage,
            "overkill_damage": max(0, calculated_damage - effective_damage),
            "calculated_hp_recovery": calculated_hp_recovery,
            "effective_hp_recovery": effective_hp_recovery,
            "overheal_hp": max(0, calculated_hp_recovery - effective_hp_recovery),
            "calculated_mp_recovery": calculated_mp_recovery,
            "effective_mp_recovery": effective_mp_recovery,
            "overheal_mp": max(0, calculated_mp_recovery - effective_mp_recovery),
            "recovery_source": self._recovery_source(normalized),
            "scorer_id": "",
            "scorer_name": "",
            "scoring_team": "",
            "score_before": pending.before.get("scores"),
            "score_after": after.get("scores"),
            "is_winning_score": False,
            "score_reset_performed": False,
            "related_score_event_id": None,
        }
        self.action_records.append(record)
        self.last_character_action_id = action_sequence
        self._append_movement_result_records(
            normalized["details"],
            pending.before,
            after,
            action_sequence,
            pending.action_name,
        )
        if self.pending_system_records:
            for pending_record in self.pending_system_records:
                pending_record["source_action_id"] = action_sequence
                if pending_record.get("event_type") == "score":
                    record["related_score_event_id"] = pending_record.get("score_event_id")
                pending_record["sequence"] = len(self.action_records) + 1
                self.action_records.append(pending_record)
            self.pending_system_records.clear()
        self.pending_action = None
        if self.match_finished:
            self._write_report(game)

    def discard_pending(self) -> None:
        self.pending_action = None

    def annotate_last_action(self, details: dict[str, Any]) -> None:
        """Attach post-selection AI context after the shared action has resolved."""

        if self.report_written:
            return
        for record in reversed(self.action_records):
            if record.get("record_type") != "character_action":
                continue
            result = record.get("result")
            if not isinstance(result, dict):
                return
            stored_details = result.setdefault("details", {})
            if isinstance(stored_details, dict):
                stored_details.update(details)
            return

    def prepare_ai_annotation(self, details: dict[str, Any]) -> None:
        """Attach AI context to the shared action while it is being recorded."""
        self.pending_ai_details = dict(details)

    def finish(self, game: Any) -> None:
        self.match_finished = True
        self.finish_summary = self._normalize_summary(game)
        self.finish_summary["ended_at"] = datetime.now().isoformat(timespec="seconds")
        self.record_match_end(game)
        if self.pending_action is None:
            self._write_report(game)

    def capture_state(self, game: Any) -> dict[str, Any]:
        return self._capture_state(game)

    def record_score_reset(self, game: Any, before: dict[str, Any], scorer_id: str, scoring_team: str) -> None:
        if not self._started or self.report_written:
            return
        after = self._capture_state(game)
        before_participants = self._participant_lookup(before.get("participants", []))
        after_participants = self._participant_lookup(after.get("participants", []))
        for char_id, after_participant in after_participants.items():
            before_participant = before_participants.get(char_id, {})
            hp_before = self._int(before_participant.get("hp"))
            hp_after = self._int(after_participant.get("hp"))
            mp_before = self._int(before_participant.get("mp"))
            mp_after = self._int(after_participant.get("mp"))
            changed = (
                before_participant.get("position") != after_participant.get("position")
                or hp_before != hp_after
                or mp_before != mp_after
                or self._off_field(before_participant) != self._off_field(after_participant)
                or before.get("ball_holder_id") != after.get("ball_holder_id")
            )
            if not changed:
                continue
            self.action_records.append({
                "sequence": len(self.action_records) + 1,
                "action_name": "得点後再配置",
                "actor_id": char_id,
                "target_id": "",
                "command_group": "score",
                "before": before,
                "after": after,
                "result": {
                    "success": True,
                    "message": "得点後再配置",
                    "consumed": False,
                    "damage": 0,
                    "healing": max(0, hp_after - hp_before),
                    "ball_changed": before.get("ball_holder_id") != after.get("ball_holder_id"),
                    "extra_action": False,
                    "scored": False,
                    "match_ended": False,
                    "details": {
                        "action_name": "得点後再配置",
                        "score_reset": True,
                        "scorer_id": scorer_id,
                        "scoring_team": scoring_team,
                        "returned_from_knockout": self._off_field(before_participant) and not self._off_field(after_participant),
                        "return_hp": hp_after,
                        "return_mp": mp_after,
                        "return_injury_rate": self._int(after_participant.get("injury_rate")),
                    },
                },
                "record_type": "system_event",
                "event_type": "score_reset",
                "movement_type": "score_reset" if before_participant.get("position") != after_participant.get("position") else None,
                "score_method": "other",
                "source_action_id": self.last_character_action_id,
                "position_before": before_participant.get("position"),
                "position_after": after_participant.get("position"),
                "movement_path": [],
                "movement_distance": None,
                "execution_success": True,
                "result_success": None,
                "calculated_damage": 0,
                "effective_damage": 0,
                "overkill_damage": 0,
                "calculated_hp_recovery": max(0, hp_after - hp_before),
                "effective_hp_recovery": max(0, hp_after - hp_before),
                "overheal_hp": 0,
                "calculated_mp_recovery": max(0, mp_after - mp_before),
                "effective_mp_recovery": max(0, mp_after - mp_before),
                "overheal_mp": 0,
                "recovery_source": "knockout_revival" if self._off_field(before_participant) and not self._off_field(after_participant) else "score_reset",
                "scorer_id": scorer_id,
                "scorer_name": "",
                "scoring_team": scoring_team,
                "score_before": before.get("scores"),
                "score_after": after.get("scores"),
                "is_winning_score": False,
                "score_reset_performed": True,
            })

    def record_score_event(
        self,
        game: Any,
        *,
        scorer_id: str,
        scorer_name: str,
        scoring_team: str,
        score_before: dict[str, Any],
        score_after: dict[str, Any],
        score_position: Any,
        is_winning_score: bool,
        score_reset_performed: bool,
        state_before: dict[str, Any],
        state_after: dict[str, Any],
    ) -> None:
        if not self._started or self.report_written:
            return
        source_action = self.pending_action.action_name if self.pending_action is not None else ""
        self.last_score_event_id += 1
        score_event_id = f"score-{self.last_score_event_id}"
        record = {
            "sequence": 0,
            "action_name": "得点イベント",
            "actor_id": scorer_id,
            "target_id": "",
            "command_group": "score",
            "before": state_before,
            "after": state_after,
            "result": {
                "success": True,
                "message": f"{scorer_name}が得点",
                "consumed": False,
                "damage": 0,
                "healing": 0,
                "ball_changed": False,
                "extra_action": False,
                "scored": True,
                "match_ended": is_winning_score,
                "details": {
                    "action_name": source_action or "得点",
                    "scorer_id": scorer_id,
                    "scorer_name": scorer_name,
                    "scoring_team": scoring_team,
                },
            },
            "record_type": "system_event",
            "event_type": "score",
            "movement_type": None,
            "score_method": self._score_method_from_pending(),
            "source_action_id": None,
            "score_event_id": score_event_id,
            "score_change_authoritative": True,
            "position_before": score_position,
            "position_after": score_position,
            "movement_path": [],
            "movement_distance": 0,
            "execution_success": True,
            "result_success": True,
            "calculated_damage": 0,
            "effective_damage": 0,
            "overkill_damage": 0,
            "calculated_hp_recovery": 0,
            "effective_hp_recovery": 0,
            "overheal_hp": 0,
            "calculated_mp_recovery": 0,
            "effective_mp_recovery": 0,
            "overheal_mp": 0,
            "recovery_source": "other",
            "scorer_id": scorer_id,
            "scorer_name": scorer_name,
            "scoring_team": scoring_team,
            "score_before": dict(score_before),
            "score_after": dict(score_after),
            "is_winning_score": is_winning_score,
            "score_reset_performed": score_reset_performed,
        }
        if self.pending_action is not None:
            self.pending_system_records.append(record)
        else:
            record["source_action_id"] = self.last_character_action_id or None
            record["sequence"] = len(self.action_records) + 1
            self.action_records.append(record)

    def record_turn_regeneration(
        self,
        game: Any,
        actor_id: str,
        actor_name: str,
        mp_before: int,
        mp_after: int,
        calculated_gain: int,
        recovery_source: str,
        *,
        before_state: dict[str, Any],
    ) -> None:
        if not self._started or self.report_written:
            return
        after_state = self._capture_state(game)
        before_participant = self._participant_lookup(before_state.get("participants", [])).get(actor_id, {})
        after_participant = self._participant_lookup(after_state.get("participants", [])).get(actor_id, {})
        self.action_records.append({
            "sequence": len(self.action_records) + 1,
            "action_name": "ターン開始MP回復",
            "actor_id": actor_id,
            "target_id": "",
            "command_group": "system",
            "before": before_state,
            "after": after_state,
            "result": {
                "success": True,
                "message": "ターン開始MP回復",
                "consumed": False,
                "damage": 0,
                "healing": 0,
                "ball_changed": False,
                "extra_action": False,
                "scored": False,
                "match_ended": False,
                "details": {"action_name": "ターン開始MP回復", "actor_name": actor_name},
            },
            "record_type": "system_event",
            "event_type": "turn_regeneration",
            "movement_type": None,
            "score_method": "other",
            "source_action_id": self.last_character_action_id,
            "position_before": before_participant.get("position"),
            "position_after": after_participant.get("position"),
            "movement_path": [],
            "movement_distance": None,
            "execution_success": True,
            "result_success": None,
            "calculated_damage": 0,
            "effective_damage": 0,
            "overkill_damage": 0,
            "calculated_hp_recovery": 0,
            "effective_hp_recovery": 0,
            "overheal_hp": 0,
            "calculated_mp_recovery": max(0, calculated_gain),
            "effective_mp_recovery": max(0, mp_after - mp_before),
            "overheal_mp": max(0, calculated_gain - (mp_after - mp_before)),
            "recovery_source": recovery_source,
            "scorer_id": "",
            "scorer_name": "",
            "scoring_team": "",
            "score_before": before_state.get("scores"),
            "score_after": after_state.get("scores"),
            "is_winning_score": False,
            "score_reset_performed": False,
        })

    def _append_movement_result_records(
        self,
        details: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        source_action_id: int,
        source_action_name: str,
    ) -> None:
        movement_results = details.get("movement_results")
        if not isinstance(movement_results, list):
            return
        for movement in movement_results:
            if not isinstance(movement, dict):
                continue
            moved_id = self._character_id(movement.get("moved_character_id"))
            if not moved_id:
                continue
            path = list(movement.get("path")) if isinstance(movement.get("path"), list) else []
            distance = movement.get("distance")
            if not isinstance(distance, int):
                distance = max(0, len(path) - 1) if path else None
            movement_type = str(movement.get("movement_type") or "") or None
            movement_role = str(movement.get("movement_role") or "")
            self.action_records.append({
                "sequence": len(self.action_records) + 1,
                "action_name": f"{source_action_name}移動",
                "actor_id": moved_id,
                "target_id": "",
                "command_group": "movement",
                "before": before,
                "after": after,
                "result": {
                    "success": True,
                    "message": f"{source_action_name}による位置変更",
                    "consumed": False,
                    "damage": 0,
                    "healing": 0,
                    "ball_changed": before.get("ball_holder_id") != after.get("ball_holder_id"),
                    "extra_action": False,
                    "scored": False,
                    "match_ended": False,
                    "details": {
                        "action_name": source_action_name,
                        "movement_role": movement_role,
                        "source_action_id": source_action_id,
                    },
                },
                "record_type": "system_event",
                "event_type": "movement",
                "movement_type": movement_type,
                "score_method": "other",
                "source_action_id": source_action_id,
                "position_before": movement.get("position_before"),
                "position_after": movement.get("position_after"),
                "movement_path": path,
                "movement_distance": distance,
                "execution_success": True,
                "result_success": True,
                "calculated_damage": 0,
                "effective_damage": 0,
                "overkill_damage": 0,
                "calculated_hp_recovery": 0,
                "effective_hp_recovery": 0,
                "overheal_hp": 0,
                "calculated_mp_recovery": 0,
                "effective_mp_recovery": 0,
                "overheal_mp": 0,
                "recovery_source": "other",
                "scorer_id": "",
                "scorer_name": "",
                "scoring_team": "",
                "score_before": before.get("scores"),
                "score_after": after.get("scores"),
                "is_winning_score": False,
                "score_reset_performed": False,
            })

    def record_match_end(self, game: Any) -> None:
        if not self._started or self.report_written:
            return
        summary = self.finish_summary or self._normalize_summary(game)
        record = {
            "sequence": 0,
            "action_name": "試合終了",
            "actor_id": "",
            "target_id": "",
            "command_group": "system",
            "before": self._capture_state(game),
            "after": self._capture_state(game),
            "result": {
                "success": True,
                "message": "試合終了",
                "consumed": False,
                "damage": 0,
                "healing": 0,
                "ball_changed": False,
                "extra_action": False,
                "scored": False,
                "match_ended": True,
                "details": {"end_reason": summary.get("end_reason"), "winner": summary.get("winner")},
            },
            "record_type": "system_event",
            "event_type": "match_end",
            "movement_type": None,
            "score_method": "other",
            "source_action_id": None,
            "position_before": None,
            "position_after": None,
            "movement_path": [],
            "movement_distance": None,
            "execution_success": True,
            "result_success": None,
            "calculated_damage": 0,
            "effective_damage": 0,
            "overkill_damage": 0,
            "calculated_hp_recovery": 0,
            "effective_hp_recovery": 0,
            "overheal_hp": 0,
            "calculated_mp_recovery": 0,
            "effective_mp_recovery": 0,
            "overheal_mp": 0,
            "recovery_source": "other",
            "scorer_id": "",
            "scorer_name": "",
            "scoring_team": "",
            "score_before": self._capture_state(game).get("scores"),
            "score_after": self._capture_state(game).get("scores"),
            "is_winning_score": False,
            "score_reset_performed": False,
        }
        if self.pending_action is not None:
            self.pending_system_records.append(record)
            return
        record["sequence"] = len(self.action_records) + 1
        record["source_action_id"] = self.last_character_action_id
        self.action_records.append(record)

    def _capture_start_snapshot(self, game: Any) -> dict[str, Any]:
        enemy_characters = [
            character for character in game.characters.values()
            if character.team == "enemy" and getattr(character, "enemy_group_id", "")
        ]
        enemy_group = enemy_characters[0] if enemy_characters else None
        return {
            "match_id": self._match_id(game),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "match_mode": self._match_mode(game),
            "execution_mode": self.execution_mode,
            "random_seed": self._seed_value(game),
            "field_width": game.config.field_width,
            "field_height": game.config.field_height,
            "target_score": game.config.target_score,
            "turn_limit": game.config.turn_limit,
            "player_count": len([character for character in game.characters.values() if character.team == "player"]),
            "enemy_count": len([character for character in game.characters.values() if character.team == "enemy"]),
            "player_field_count": len([character for character in game.characters.values() if character.team == "player" and not character.on_bench]),
            "enemy_field_count": len([character for character in game.characters.values() if character.team == "enemy" and not character.on_bench]),
            "enemy_group_id": getattr(enemy_group, "enemy_group_id", ""),
            "enemy_scale": getattr(enemy_group, "enemy_scale", "") if enemy_group else "",
            "enemy_ai_settings": self._team_ai_settings(game, "enemy"),
            "player_ai_settings": self._team_ai_settings(game, "player"),
            "initial_positions": self._initial_positions(game),
            "characters": [self._character_snapshot(game, character) for character in self._ordered_characters(game)],
        }

    def _capture_state(self, game: Any) -> dict[str, Any]:
        return {
            "round": game.round,
            "turn_index": game.turn_index,
            "turn_order": list(game.turn_order),
            "actor_id": getattr(game.current_actor, "char_id", ""),
            "target_id": self._derive_target_id(game),
            "scores": dict(game.scores),
            "ball_holder_id": game.ball.holder_id or "",
            "participants": [self._participant_state(character) for character in self._ordered_characters(game)],
        }

    def _normalize_summary(self, game: Any) -> dict[str, Any]:
        summary = dict(game.result_summary())
        summary["report_written"] = self.report_written
        summary["execution_mode"] = self.execution_mode
        return summary

    def _normalize_result(self, result: Any) -> dict[str, Any]:
        details = dict(getattr(result, "details", {}) or {})
        return {
            "success": bool(getattr(result, "success", False)),
            "message": getattr(result, "message", ""),
            "consumed": bool(getattr(result, "consumed", False)),
            "damage": int(getattr(result, "damage", 0) or 0),
            "healing": int(getattr(result, "healing", 0) or 0),
            "ball_changed": bool(getattr(result, "ball_changed", False)),
            "extra_action": bool(getattr(result, "extra_action", False)),
            "scored": bool(getattr(result, "scored", False)),
            "match_ended": bool(getattr(result, "match_ended", False)),
            "details": details,
        }

    def _event_type_from_record(self, action_name: str, details: dict[str, Any], normalized: dict[str, Any]) -> str:
        if details.get("score_reset"):
            return "score_reset"
        if normalized.get("scored"):
            return "score"
        if details.get("special_move"):
            return "skill"
        if details.get("skill_id"):
            return "skill"
        if details.get("path") or details.get("target_move") or details.get("actor_move"):
            return "movement"
        return action_name or "action"

    def _movement_type_from_record(self, command_group: str, details: dict[str, Any]) -> str | None:
        if details.get("score_reset"):
            return "score_reset"
        if details.get("movement_results"):
            return None
        if details.get("target_move"):
            return "forced"
        if details.get("actor_move"):
            return "swap"
        if details.get("path"):
            return "skill" if command_group == "skill" else "normal"
        return None

    def _score_method_from_record(self, action_name: str, details: dict[str, Any]) -> str:
        if details.get("score_reset"):
            return "score_reset"
        if details.get("path") and action_name in {"移動", "移動確定", "通常移動"}:
            return "move"
        if action_name in {"パス", "クイックパス"}:
            return "pass_receive"
        if "カット" in action_name:
            return "cut"
        if "スティール" in action_name:
            return "steal"
        if details.get("skill_id"):
            return "skill"
        if action_name == "得点":
            return "goal"
        return "other"

    def _extract_effective_damage(
        self,
        normalized: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        actor_id: str,
        target_id: str,
    ) -> int:
        details = normalized["details"]
        calculation = details.get("damage_calculation")
        if isinstance(calculation, dict) and isinstance(calculation.get("effective_damage"), int):
            return int(calculation["effective_damage"])
        effect_results = details.get("effect_results")
        if isinstance(effect_results, list):
            total = 0
            for item in effect_results:
                if isinstance(item, dict):
                    damage_calculation = item.get("damage_calculation")
                    if isinstance(damage_calculation, dict) and isinstance(damage_calculation.get("effective_damage"), int):
                        total += int(damage_calculation["effective_damage"])
                    elif isinstance(item.get("damage"), int):
                        total += int(item.get("damage") or 0)
            if total > 0:
                return total
        if isinstance(normalized.get("damage"), int) and normalized["damage"] > 0:
            return int(normalized["damage"])
        if isinstance(details.get("damage"), int) and details["damage"] > 0:
            return int(details["damage"])
        before_map = self._participant_lookup(before.get("participants", []))
        after_map = self._participant_lookup(after.get("participants", []))
        candidate = self._character_id(target_id or details.get("target_id") or actor_id)
        if candidate and candidate in before_map and candidate in after_map:
            return max(0, self._int(before_map[candidate].get("hp")) - self._int(after_map[candidate].get("hp")))
        return 0

    def _extract_calculated_hp_recovery(self, normalized: dict[str, Any]) -> int:
        healing = normalized.get("healing", 0)
        details = normalized["details"]
        if isinstance(details.get("calculated_hp_recovery"), int) and details["calculated_hp_recovery"] > 0:
            return int(details["calculated_hp_recovery"])
        effect_results = details.get("effect_results")
        if isinstance(effect_results, list):
            total = sum(int(item.get("calculated_hp_recovery", 0) or 0) for item in effect_results if isinstance(item, dict))
            if total > 0:
                return total
        if isinstance(healing, int) and healing > 0:
            return int(healing)
        if isinstance(details.get("healing"), int) and details["healing"] > 0:
            return int(details["healing"])
        return 0

    def _extract_effective_hp_recovery(
        self,
        normalized: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        actor_id: str,
        target_id: str,
    ) -> int:
        details = normalized["details"]
        if isinstance(details.get("effective_hp_recovery"), int) and details["effective_hp_recovery"] > 0:
            return int(details["effective_hp_recovery"])
        effect_results = details.get("effect_results")
        if isinstance(effect_results, list):
            total = sum(int(item.get("effective_hp_recovery", item.get("healing", 0)) or 0) for item in effect_results if isinstance(item, dict))
            if total > 0:
                return total
        before_map = self._participant_lookup(before.get("participants", []))
        after_map = self._participant_lookup(after.get("participants", []))
        candidate = self._character_id(target_id or details.get("target_id") or actor_id)
        if candidate and candidate in before_map and candidate in after_map:
            return max(0, self._int(after_map[candidate].get("hp")) - self._int(before_map[candidate].get("hp")))
        return 0

    def _extract_calculated_mp_recovery(self, normalized: dict[str, Any]) -> int:
        details = normalized["details"]
        if isinstance(details.get("calculated_mp_recovery"), int) and details["calculated_mp_recovery"] > 0:
            return int(details["calculated_mp_recovery"])
        effect_results = details.get("effect_results")
        if isinstance(effect_results, list):
            total = sum(int(item.get("calculated_mp_recovery", 0) or 0) for item in effect_results if isinstance(item, dict))
            if total > 0:
                return total
        value = details.get("mana_recovery")
        return int(value) if isinstance(value, int) and value > 0 else 0

    def _extract_effective_mp_recovery(
        self,
        normalized: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        actor_id: str,
    ) -> int:
        details = normalized["details"]
        if isinstance(details.get("effective_mp_recovery"), int) and details["effective_mp_recovery"] > 0:
            return int(details["effective_mp_recovery"])
        effect_results = details.get("effect_results")
        if isinstance(effect_results, list):
            total = sum(int(item.get("effective_mp_recovery", item.get("mana_recovery", 0)) or 0) for item in effect_results if isinstance(item, dict))
            if total > 0:
                return total
        before_map = self._participant_lookup(before.get("participants", []))
        after_map = self._participant_lookup(after.get("participants", []))
        candidate = self._character_id(details.get("target_id") or actor_id)
        if candidate and candidate in before_map and candidate in after_map:
            return max(0, self._int(after_map[candidate].get("mp")) - self._int(before_map[candidate].get("mp")))
        return 0

    def _movement_path(self, details: dict[str, Any]) -> list[Any]:
        path = details.get("path")
        return list(path) if isinstance(path, list) else []

    def _movement_distance(self, details: dict[str, Any], movement_path: list[Any]) -> int | None:
        if movement_path:
            return max(0, len(movement_path) - 1)
        if isinstance(details.get("distance"), int):
            return int(details["distance"])
        return None

    def _result_success(self, normalized: dict[str, Any]) -> bool | None:
        details = normalized["details"]
        if isinstance(details.get("skill_success"), bool):
            return bool(details.get("skill_success"))
        if isinstance(details.get("pass_success"), bool):
            return bool(details.get("pass_success"))
        if isinstance(details.get("success"), bool):
            return bool(details.get("success"))
        contest = details.get("contest")
        if isinstance(contest, dict) and isinstance(contest.get("success"), bool):
            return bool(contest.get("success"))
        return None

    def _recovery_source(self, normalized: dict[str, Any]) -> str:
        details = normalized["details"]
        if isinstance(details.get("recovery_source"), str) and details.get("recovery_source"):
            return str(details.get("recovery_source"))
        effect_results = details.get("effect_results")
        if isinstance(effect_results, list):
            for item in effect_results:
                if isinstance(item, dict) and item.get("recovery_source"):
                    return str(item.get("recovery_source"))
        return "other"

    def _score_method_from_pending(self) -> str:
        if self.pending_action is None:
            return "turn_start"
        action_name = self.pending_action.action_name
        command_group = self.pending_action.command_group
        if action_name in {"移動", "移動確定"} or command_group == "move":
            return "move"
        if action_name in {"パス", "クイックパス"}:
            return "pass_receive"
        if "カット" in action_name:
            return "cut"
        if "スティール" in action_name:
            return "steal"
        if command_group == "skill":
            return "skill"
        return "other"

    def _write_report(self, game: Any) -> None:
        if self.report_written or not self.start_snapshot:
            return
        if self.pending_action is not None:
            return
        self._warn_on_score_mismatch(game)
        self.markdown = self._build_markdown(game, "memory_report")
        if self.finish_summary is not None:
            self.finish_summary["report_written"] = False
            self.finish_summary["output_policy"] = self.output_policy
        if self.output_policy == "test":
            self.report_written = True
            return
        try:
            report_root = self.output_root or PROJECT_ROOT / "log" / self.output_policy
            report_root.mkdir(parents=True, exist_ok=True)
        except Exception:
            LOGGER.exception("レポート保存先の作成に失敗しました")
            return

        started_at = self.start_snapshot["started_at"]
        timestamp = started_at.replace("-", "_").replace(":", "").replace("T", "_")
        base_name = f"{REPORT_PREFIX}{timestamp}"
        final_path = self._next_available_path(report_root, base_name)
        temp_file = None
        try:
            if self.finish_summary is not None:
                self.finish_summary["report_written"] = True
            markdown = self._build_markdown(game, final_path.stem)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=report_root,
                prefix=f".{final_path.stem}_",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_file = Path(handle.name)
                handle.write(markdown)
            self.markdown = markdown
            temp_file.replace(final_path)
            self.output_path = final_path
            self.report_written = True
            if self.finish_summary is not None:
                self.finish_summary["report_written"] = True
            LOGGER.info("試合バランスレポートを出力しました: %s", final_path)
        except Exception:
            LOGGER.exception("試合バランスレポートの保存に失敗しました")
            if temp_file is not None:
                try:
                    temp_file.unlink(missing_ok=True)
                except Exception:
                    LOGGER.exception("一時レポートファイルの削除に失敗しました")

    def _warn_on_score_mismatch(self, game: Any) -> None:
        recorded = {"player": 0, "enemy": 0}
        for record in self.action_records:
            if record.get("record_type") != "system_event" or not record.get("score_change_authoritative"):
                continue
            team = str(record.get("scoring_team") or "")
            if team in recorded:
                recorded[team] += 1
        expected = dict(getattr(game, "scores", {}) or {})
        for team, score in expected.items():
            if team in recorded and recorded[team] != self._int(score):
                LOGGER.warning(
                    "得点イベント数と試合スコアが一致しません: team=%s events=%s score=%s",
                    team,
                    recorded[team],
                    score,
                )

    def _next_available_path(self, report_root: Path, base_name: str) -> Path:
        for index in range(1, 1000):
            candidate = report_root / f"{base_name}_{index:03d}.md"
            if not candidate.exists():
                return candidate
        raise RuntimeError("レポートファイル名の採番に失敗しました")

    def _build_markdown(self, game: Any, match_id: str) -> str:
        start = self.start_snapshot or {}
        summary = self.finish_summary or self._normalize_summary(game)
        aggregates = self._build_aggregates(game)
        lines: list[str] = [REPORT_TITLE, ""]
        lines.extend(self._section_overview(start, summary, match_id))
        lines.append("")
        lines.extend(self._section_conditions(start))
        lines.append("")
        lines.extend(self._section_enemy_ai_settings(start))
        lines.append("")
        lines.extend(self._section_characters(start))
        lines.append("")
        lines.extend(self._section_result(summary))
        lines.append("")
        lines.extend(self._section_character_aggregates(aggregates["characters"]))
        lines.append("")
        lines.extend(self._section_skill_aggregates(aggregates["skills"]))
        lines.append("")
        lines.extend(self._section_action_log())
        lines.append("")
        lines.extend(self._section_overall_aggregate(summary, aggregates))
        lines.append("")
        return "\n".join(lines)

    def _section_overview(self, start: dict[str, Any], summary: dict[str, Any], match_id: str) -> list[str]:
        return [
            "## 1．試合概要",
            f"- 試合ID: {self._value(start.get('match_id'), match_id)}",
            f"- 開始日時: {self._value(start.get('started_at'))}",
            f"- 終了日時: {self._value(summary.get('ended_at'))}",
            f"- 試合モード: {self._value(start.get('match_mode'))}",
            f"- 実行形式: {self._value(start.get('execution_mode'))}",
            f"- 乱数シード: {self._value(start.get('random_seed'))}",
            f"- 終了理由: {self._value(summary.get('end_reason_name') or summary.get('end_reason'))}",
            f"- 勝敗: {self._winner_label(summary)}",
        ]

    def _section_conditions(self, start: dict[str, Any]) -> list[str]:
        lines = [
            "## 2．試合条件",
            f"- フィールド: {self._value(start.get('field_width'))} x {self._value(start.get('field_height'))}",
            f"- 先取点: {self._value(start.get('target_score'))}",
            f"- ターン制限: {self._turn_limit_value(start.get('turn_limit'))}",
            f"- 味方出場人数: {self._value(start.get('player_field_count'))}",
            f"- 味方パーティー人数: {self._value(start.get('player_count'))}",
            f"- 味方ベンチ人数: {self._int(start.get('player_count')) - self._int(start.get('player_field_count'))}",
            f"- 敵出場人数: {self._value(start.get('enemy_field_count'))}",
            f"- 敵パーティー人数: {self._value(start.get('enemy_count'))}",
            f"- 敵ベンチ人数: {self._int(start.get('enemy_count')) - self._int(start.get('enemy_field_count'))}",
            f"- 敵グループID: {self._value(start.get('enemy_group_id'))}",
            f"- 敵能力倍率: {self._value(start.get('enemy_scale'))}",
            f"- 初期配置: {self._format_mapping(start.get('initial_positions'))}",
        ]
        return lines

    def _section_enemy_ai_settings(self, start: dict[str, Any]) -> list[str]:
        lines = ["### 敵AI設定"]
        characters = {item.get("char_id"): item for item in start.get("characters", []) if item.get("team") == "enemy"}
        settings = start.get("enemy_ai_settings", {}) or {}
        if not settings:
            return [*lines, "- 敵AI設定はありません。"]
        for character_id, entry in settings.items():
            character = characters.get(character_id, {})
            lines.extend([
                "",
                f"#### {self._sanitize(character.get('name') or character_id)}",
                f"- プロフィール: {self._sanitize(entry.get('profile_name'))} (`{self._sanitize(entry.get('profile_id'))}`)",
                f"- AIレベル: {self._value(entry.get('ai_level'), '5')}",
            ])
            for key, value in (entry.get("settings", {}) or {}).items():
                lines.append(f"- {AI_SETTING_NAMES.get(key, key)}: {self._value(value)}")
        return lines

    def _section_characters(self, start: dict[str, Any]) -> list[str]:
        lines = ["## 3．試合開始時のキャラクター情報"]
        characters = start.get("characters", []) or []
        for character in characters:
            ai_text = (
                f"AI役割 {self._value(character.get('ai_role_name'))}"
                if character.get("team") == "player"
                else f"AI {self._value(character.get('ai_profile_id'))} ({self._value(character.get('ai_profile_name'))}) / Lv{self._value(character.get('ai_level'))}"
            )
            lines.append(
                f"- {self._value(character.get('team'))} / 枠{self._value(character.get('slot'))} / "
                f"ID {self._value(character.get('char_id'))} / {self._value(character.get('name'))} / "
                f"{ai_text} / "
                f"{'ベンチ枠' if character.get('on_bench') else '出場枠'} / "
                f"初期座標 {'ベンチ' if character.get('on_bench') else self._format_position(character.get('initial_position'))} / "
                f"HP {self._value(character.get('start_hp'))} / {self._value(character.get('max_hp'))} / "
                f"MP {self._value(character.get('start_mp'))} / {self._value(character.get('max_mana'))} / "
                f"パワー {self._value(character.get('power'))} / マジック {self._value(character.get('magic'))} / スピード {self._value(character.get('speed'))} / "
                f"テクニック {self._value(character.get('technique'))} / スタミナ {self._value(character.get('stamina'))} / "
                f"移動力 {self._value(character.get('move_range'))} / スキル {self._format_skill_list(character.get('skills'))}"
            )
        return lines

    def _section_result(self, summary: dict[str, Any]) -> list[str]:
        return [
            "## 4．試合結果",
            f"- 結果: {self._winner_label(summary)}",
            f"- スコア: {self._value(summary.get('player_score'))} - {self._value(summary.get('enemy_score'))}",
            f"- 終了ラウンド: {self._value(summary.get('rounds'))}",
            f"- 終了理由: {self._value(summary.get('end_reason_name') or summary.get('end_reason'))}",
        ]

    def _section_character_aggregates(self, aggregates: list[dict[str, Any]]) -> list[str]:
        lines = ["## 5．キャラクター別集計"]
        for item in aggregates:
            lines.append(
                f"- {self._value(item.get('team'))} / ID {self._value(item.get('char_id'))} / {self._value(item.get('name'))}: "
                f"行動{self._value(item.get('character_action_count'))}, イベント{self._value(item.get('system_event_count'))}, ログ{self._value(item.get('log_record_count'))}, "
                f"通常移動{self._value(item.get('normal_moves'))}回/{self._value(item.get('normal_move_distance'))}マス, "
                f"スキル移動{self._value(item.get('skill_moves'))}回/{self._value(item.get('skill_move_distance'))}マス, "
                f"強制移動{self._value(item.get('forced_moves'))}回/{self._value(item.get('forced_move_distance'))}マス, 待機{self._value(item.get('waits'))}, "
                f"通常攻撃{self._value(item.get('normal_attacks'))}, スキル{self._value(item.get('skill_uses'))}, "
                f"ダメージ{self._value(item.get('damage_dealt'))}/{self._value(item.get('damage_taken'))}, 過剰{self._value(item.get('overkill_dealt'))}, "
                f"HP回復(スキル/得点後/総計){self._value(item.get('skill_hp_recovery'))}/{self._value(item.get('score_reset_hp_recovery'))}/{self._value(item.get('healing_done'))}, "
                f"MP回復(スキル/ターン/得点後/総計){self._value(item.get('skill_mp_recovery'))}/{self._value(item.get('turn_mp_recovery'))}/{self._value(item.get('score_reset_mp_recovery'))}/{self._value(item.get('mp_recovery_total'))}, "
                f"MP消費{self._value(item.get('mp_spent'))}, パス{self._value(item.get('passes'))}, "
                f"攻撃由来カット{self._value(item.get('cuts'))}, パスカット成功{self._value(item.get('pass_cuts'))}, スティール成功{self._value(item.get('steals'))}, "
                f"得点{self._value(item.get('scores'))}, 戦闘不能付与{self._value(item.get('ko_inflicted'))}, "
                f"被戦闘不能{self._value(item.get('ko_taken'))}, 終了時HP/MP {self._value(item.get('end_hp'))}/{self._value(item.get('end_mp'))}"
            )
        return lines

    def _section_skill_aggregates(self, aggregates: list[dict[str, Any]]) -> list[str]:
        lines = ["## 6．スキル別集計"]
        for item in aggregates:
            lines.append(
                f"- 所有者ID {self._value(item.get('owner_id'))} / ID {self._value(item.get('skill_id'))} / {self._value(item.get('name'))}: "
                f"{self._value(item.get('command_group'))} / {self._value(item.get('purpose_tag'))} / "
                f"使用{self._value(item.get('uses'))}, 成功{self._value(item.get('successes'))}, 失敗{self._value(item.get('failures'))}, "
                f"自動発動{self._value(item.get('auto_activations'))}, 適用{self._value(item.get('effect_applications'))}, 解除{self._value(item.get('target_removals'))}, 計算参照{self._value(item.get('calculation_references'))}, "
                f"計算上ダメージ{self._value(item.get('calculated_damage'))}, 実ダメージ{self._value(item.get('actual_damage'))}, 過剰{self._value(item.get('overkill_damage'))}, "
                f"HP回復{self._value(item.get('healing'))}, MP回復{self._value(item.get('mp_recovery'))}, MP消費{self._value(item.get('mp_spent'))}, "
                f"平均実ダメージ{self._value(item.get('avg_actual_damage'))}, 平均回復量{self._value(item.get('avg_healing'))}"
            )
        return lines

    def _section_action_log(self) -> list[str]:
        lines = ["## 7．行動ログ"]
        if not self.action_records:
            lines.append("- 行動記録はありません。")
            return lines
        for record in self.action_records:
            lines.append(self._format_action_record(record))
            lines.extend(self._format_ai_decision(record))
        return lines

    def _format_ai_decision(self, record: dict[str, Any]) -> list[str]:
        details = (record.get("result") or {}).get("details", {}) or {}
        candidates = details.get("ai_candidates")
        if not isinstance(candidates, list):
            return []
        actor_id = self._character_id(record.get("actor_id") or details.get("actor_id"))
        character = next((item for item in (self.start_snapshot or {}).get("characters", []) if item.get("char_id") == actor_id), {})
        actor_name = character.get("name") or actor_id
        selected = next((item for item in candidates if item.get("selected")), None)
        shown = list(candidates[:AI_REPORT_CANDIDATE_LIMIT])
        if selected is not None and selected not in shown:
            shown.append(selected)
        lines = [
            "",
            f"  **AI判断 — {self._sanitize(actor_name)}**",
            f"  - プロフィール: {self._sanitize(details.get('ai_profile_name'))} (`{self._sanitize(details.get('ai_profile_id'))}`)",
            f"  - AIレベル: {self._value(details.get('ai_level'))}",
            f"  - 判断開始時ボール状態: {self._sanitize(details.get('ai_ball_state'))}",
            f"  - 行動後ボール状態: {self._sanitize(details.get('ai_ball_state_after'))}",
            f"  - チーム内役割: {self._sanitize(details.get('ai_team_ball_role'))}",
            f"  - ルーズボール担当: {self._sanitize(details.get('ai_loose_ball_recovery_actor_name'))}",
        ]
        if selected:
            main_name = AI_SETTING_NAMES.get(selected.get("main_ai_key"), selected.get("main_ai_key") or "なし")
            lines.extend([
                f"  - 移動目的: {self._sanitize(selected.get('movement_purpose'))} / 移動先 {self._format_position(selected.get('destination'))} / ボール距離 {self._value(selected.get('ball_distance_before'))}→{self._value(selected.get('ball_distance_after'))}",
                f"  - 移動状況補正: {self._signed(selected.get('movement_context_adjustment'))} / {self._sanitize(selected.get('movement_context_reason'))}",
                f"  - 主行動: {self._sanitize(selected.get('action_name'))} / 目的 {self._sanitize(selected.get('main_action_purpose'))} / 対象 {self._sanitize(selected.get('target_name'))}",
                f"  - 主行動状況補正: {self._signed(selected.get('main_action_context_adjustment'))} / {self._sanitize(selected.get('main_action_context_reason'))} / 回収経路確保 {'はい' if selected.get('is_recovery_path_clear_action') else 'いいえ'}",
                f"  - 評価内訳: 基本 {self._value(selected.get('base_score'))} / {main_name}補正 {self._signed(selected.get('profile_adjustment'))} / MP補正 {self._signed(selected.get('mp_adjustment'))} / 危険補正 {self._signed(selected.get('risk_adjustment'))} / 最終 {self._value(selected.get('final_score'))}",
            ])
        lines.extend([
            f"  - 選択理由: {self._sanitize(details.get('ai_selection_reason') or details.get('ai_wait_reason'))}",
            "",
            "  | 順位 | 移動目的 | 移動先 | 移動補正 | 主行動 | 主目的 | 対象 | 主行動補正 | 経路確保 | 基本評価 | AI項目 | AI補正 | MP補正 | 危険補正 | 最終評価 | 選択 |",
            "  | ---: | --- | --- | ---: | --- | --- | --- | ---: | :---: | ---: | --- | ---: | ---: | ---: | ---: | :---: |",
        ])
        for item in shown:
            lines.append(
                f"  | {self._value(item.get('rank'))} | {self._md_cell(item.get('movement_purpose'))} | {self._md_cell(self._format_position(item.get('destination')))} | {self._signed(item.get('movement_context_adjustment'))} | "
                f"{self._md_cell(item.get('action_name'))} | {self._md_cell(item.get('main_action_purpose'))} | {self._md_cell(item.get('target_name'))} | {self._signed(item.get('main_action_context_adjustment'))} | {'○' if item.get('is_recovery_path_clear_action') else ''} | "
                f"{self._value(item.get('base_score'))} | {self._md_cell(AI_SETTING_NAMES.get(item.get('main_ai_key'), item.get('main_ai_key') or 'なし'))} | "
                f"{self._signed(item.get('profile_adjustment'))} | {self._signed(item.get('mp_adjustment'))} | {self._signed(item.get('risk_adjustment'))} | "
                f"{self._value(item.get('final_score'))} | {'○' if item.get('selected') else ''} |"
            )
        return lines

    @staticmethod
    def _signed(value: Any) -> str:
        try:
            return f"{float(value):+.1f}"
        except (TypeError, ValueError):
            return "-"

    def _md_cell(self, value: Any) -> str:
        return self._sanitize(value).replace("|", "\\|").replace("\n", " ")

    def _section_overall_aggregate(self, summary: dict[str, Any], aggregates: dict[str, Any]) -> list[str]:
        balance = self.balance_metrics()
        character_action_count = sum(1 for record in self.action_records if record.get("record_type") == "character_action")
        system_event_count = sum(1 for record in self.action_records if record.get("record_type") == "system_event")
        log_record_count = len(self.action_records)
        total_damage = sum(int(item.get("damage_dealt", 0) or 0) for item in aggregates["characters"]) if aggregates["characters"] else 0
        total_healing = sum(int(item.get("healing_done", 0) or 0) for item in aggregates["characters"]) if aggregates["characters"] else 0
        total_mp_recovery = sum(int(item.get("mp_recovery_total", 0) or 0) for item in aggregates["characters"]) if aggregates["characters"] else 0
        total_overkill = sum(int(item.get("overkill_dealt", 0) or 0) for item in aggregates["characters"]) if aggregates["characters"] else 0
        return [
            "## 8．試合全体集計",
            f"- キャラクター行動数: {character_action_count}",
            f"- システムイベント数: {system_event_count}",
            f"- ログ総件数: {log_record_count}",
            f"- 総得点: {self._value(summary.get('player_score'))} - {self._value(summary.get('enemy_score'))}",
            f"- 総ダメージ: {total_damage}",
            f"- 総過剰ダメージ: {total_overkill}",
            f"- 総回復量: {total_healing}",
            f"- 総MP回復量: {total_mp_recovery}",
            f"- 戦闘不能発生: {balance['total_knockouts']}（味方 {balance['player_knockouts']} / 敵 {balance['enemy_knockouts']}）",
            f"- キャラクター別戦闘不能: {self._format_mapping(balance['knockouts_by_character'])}",
            f"- 得点後復帰: {balance['score_reset_returns']}",
            f"- 自然復帰: {balance['natural_returns']}",
            f"- スティール成功数: {sum(self._int(item.get('steals')) for item in aggregates['characters'])}",
            f"- パスカット成功数: {sum(self._int(item.get('pass_cuts')) for item in aggregates['characters'])}",
            f"- 攻撃ボールドロップ判定: {sum(1 for record in self.action_records if record['result']['details'].get('ball_effect', {}).get('effect_type') == 'drop')}",
            f"- 攻撃ボールドロップ成功: {sum(1 for record in self.action_records if record['result']['details'].get('ball_effect', {}).get('effect_type') == 'drop' and record['result']['details'].get('ball_effect', {}).get('success') and not record['result']['details'].get('ball_effect', {}).get('forced_knockout_drop'))}",
            f"- ボールカット使用/判定/成功: {balance['ball_cut_uses']}/{balance['ball_cut_checks']}/{balance['ball_cut_successes']}",
            f"- 戦闘不能強制ドロップ: {balance['forced_knockout_drops']}",
            f"- パス成功数: {self._count_pass_successes()}",
        ]

    def _build_aggregates(self, game: Any) -> dict[str, Any]:
        characters = self._character_aggregates(game)
        skills = self._skill_aggregates(game)
        return {"characters": characters, "skills": skills}

    def _character_aggregates(self, game: Any) -> list[dict[str, Any]]:
        start_characters = {item["char_id"]: item for item in (self.start_snapshot or {}).get("characters", [])}
        aggregates: dict[str, dict[str, Any]] = {}
        for character in self._ordered_characters(game):
            start = start_characters.get(character.char_id, {})
            aggregates[character.char_id] = {
                "team": character.team,
                "char_id": character.char_id,
                "name": character.name,
                "character_action_count": 0,
                "system_event_count": 0,
                "log_record_count": 0,
                "moves": 0,
                "normal_moves": 0,
                "normal_move_distance": 0,
                "skill_moves": 0,
                "skill_move_distance": 0,
                "forced_moves": 0,
                "forced_move_distance": 0,
                "waits": 0,
                "normal_attacks": 0,
                "skill_uses": 0,
                "damage_dealt": 0,
                "overkill_dealt": 0,
                "damage_taken": 0,
                "skill_hp_recovery": 0,
                "score_reset_hp_recovery": 0,
                "healing_done": 0,
                "healing_taken": 0,
                "skill_mp_recovery": 0,
                "turn_mp_recovery": 0,
                "score_reset_mp_recovery": 0,
                "mp_recovery_total": 0,
                "mp_spent": 0,
                "passes": 0,
                "cuts": 0,
                "steals": 0,
                "pass_cuts": 0,
                "scores": 0,
                "ko_inflicted": 0,
                "ko_taken": 0,
                "end_hp": self._value(character.hp),
                "end_mp": self._value(character.mana),
                "start_skills": list(start.get("skills", [])),
            }
        for record in self.action_records:
            details = record["result"]["details"]
            actor_id = self._character_id(record.get("actor_id") or details.get("actor_id"))
            target_id = self._character_id(record.get("target_id") or details.get("target_id"))
            action_name = str(record.get("action_name") or details.get("action_name") or "")
            before = self._participant_lookup(record["before"]["participants"])
            after = self._participant_lookup(record["after"]["participants"])
            effective_damage = int(record.get("effective_damage", 0) or 0)
            effective_hp_recovery = int(record.get("effective_hp_recovery", 0) or 0)
            effective_mp_recovery = int(record.get("effective_mp_recovery", 0) or 0)
            if actor_id in aggregates:
                aggregates[actor_id]["log_record_count"] += 1
                movement_type = record.get("movement_type")
                movement_distance = int(record.get("movement_distance", 0) or 0)
                if movement_type in {"normal", "skill", "forced"} and movement_distance > 0:
                    if movement_type == "normal":
                        aggregates[actor_id]["moves"] += 1
                        aggregates[actor_id]["normal_moves"] += 1
                        aggregates[actor_id]["normal_move_distance"] += movement_distance
                    elif movement_type == "skill":
                        aggregates[actor_id]["skill_moves"] += 1
                        aggregates[actor_id]["skill_move_distance"] += movement_distance
                    elif movement_type == "forced":
                        aggregates[actor_id]["forced_moves"] += 1
                        aggregates[actor_id]["forced_move_distance"] += movement_distance
                if record.get("record_type") == "character_action":
                    aggregates[actor_id]["character_action_count"] += 1
                    aggregates[actor_id]["mp_spent"] += max(0, self._int(before.get(actor_id, {}).get("mp")) - self._int(after.get(actor_id, {}).get("mp")))
                    if action_name == "待機":
                        aggregates[actor_id]["waits"] += 1
                    if action_name == "通常攻撃":
                        aggregates[actor_id]["normal_attacks"] += 1
                    if record.get("score_method") == "pass" or action_name in {"パス", "クイックパス"}:
                        aggregates[actor_id]["passes"] += 1
                    if record.get("score_method") == "skill" or details.get("skill_id"):
                        aggregates[actor_id]["skill_uses"] += 1
                    if (details.get("skill_id") == "steal" or "スティール" in action_name) and record.get("result_success") is True:
                        aggregates[actor_id]["steals"] += 1
                    if details.get("skill_id") == "cut_ball" or "カット" in action_name:
                        aggregates[actor_id]["cuts"] += 1
                    if details.get("pass_success") is False:
                        interceptor_id = self._character_id(details.get("final_holder_id"))
                        if interceptor_id in aggregates:
                            aggregates[interceptor_id]["pass_cuts"] += 1
                else:
                    aggregates[actor_id]["system_event_count"] += 1
                if record.get("event_type") == "score" and record.get("scorer_id") == actor_id:
                    aggregates[actor_id]["scores"] += 1
                if effective_damage > 0:
                    aggregates[actor_id]["damage_dealt"] += effective_damage
                    aggregates[actor_id]["overkill_dealt"] += int(record.get("overkill_damage", 0) or 0)
                if effective_hp_recovery > 0:
                    aggregates[actor_id]["healing_done"] += effective_hp_recovery
                    if record.get("recovery_source") == "skill":
                        aggregates[actor_id]["skill_hp_recovery"] += effective_hp_recovery
                    if record.get("recovery_source") in {"score_reset", "knockout_revival"}:
                        aggregates[actor_id]["score_reset_hp_recovery"] += effective_hp_recovery
                if effective_mp_recovery > 0:
                    aggregates[actor_id]["mp_recovery_total"] += effective_mp_recovery
                    if record.get("recovery_source") == "skill":
                        aggregates[actor_id]["skill_mp_recovery"] += effective_mp_recovery
                    if record.get("recovery_source") == "turn_regeneration":
                        aggregates[actor_id]["turn_mp_recovery"] += effective_mp_recovery
                    if record.get("recovery_source") in {"score_reset", "knockout_revival"}:
                        aggregates[actor_id]["score_reset_mp_recovery"] += effective_mp_recovery
                if self._off_field(before.get(actor_id)) is False and self._off_field(after.get(actor_id)) is True:
                    aggregates[actor_id]["ko_taken"] += 1
            if target_id in aggregates:
                target_before = before.get(target_id, {})
                target_after = after.get(target_id, {})
                aggregates[target_id]["log_record_count"] += 0
                if effective_damage > 0:
                    aggregates[target_id]["damage_taken"] += effective_damage
                if effective_hp_recovery > 0:
                    aggregates[target_id]["healing_taken"] += effective_hp_recovery
                if self._off_field(target_before) is False and self._off_field(target_after) is True:
                    aggregates[target_id]["ko_taken"] += 1
                    if actor_id in aggregates and actor_id != target_id:
                        aggregates[actor_id]["ko_inflicted"] += 1
        for character_id, aggregate in aggregates.items():
            aggregate["end_hp"] = self._character_end_value(game, character_id, "hp")
            aggregate["end_mp"] = self._character_end_value(game, character_id, "mp")
            start_skills = aggregate.pop("start_skills", [])
            aggregate["start_skills"] = start_skills
        return list(aggregates.values())

    def _skill_aggregates(self, game: Any) -> list[dict[str, Any]]:
        skill_lookup = getattr(game, "skills", {}) or {}
        start_characters = (self.start_snapshot or {}).get("characters", []) or []
        aggregates: dict[str, dict[str, Any]] = {}
        for character in start_characters:
            for skill_item in character.get("skills", []):
                skill_id = skill_item.get("skill_id") if isinstance(skill_item, dict) else str(skill_item)
                if not skill_id or skill_id not in skill_lookup:
                    continue
                key = f"{character.get('char_id')}::{skill_id}"
                if key in aggregates:
                    continue
                definition = skill_lookup[skill_id]
                aggregates[key] = {
                        "owner_id": character.get("char_id", ""),
                        "skill_id": skill_id,
                        "name": getattr(definition, "name", skill_id),
                        "command_group": getattr(definition, "command_group", "skill"),
                        "purpose_tag": getattr(definition, "purpose_tag", ""),
                        "uses": 0,
                        "successes": 0,
                        "failures": 0,
                        "auto_activations": 0,
                        "effect_applications": 0,
                        "target_removals": 0,
                        "calculation_references": 0,
                        "calculated_damage": 0,
                        "actual_damage": 0,
                        "overkill_damage": 0,
                        "healing": 0,
                        "mp_recovery": 0,
                        "mp_spent": 0,
                        "avg_actual_damage": 0,
                        "avg_healing": 0,
                }
        for record in self.action_records:
            if record.get("record_type") != "character_action":
                continue
            details = record["result"]["details"]
            skill_id = details.get("skill_id")
            actor_id = self._character_id(record.get("actor_id") or details.get("actor_id"))
            key = f"{actor_id}::{skill_id}" if actor_id and skill_id else ""
            if not skill_id or key not in aggregates:
                continue
            aggregate = aggregates[key]
            aggregate["uses"] += 1
            if record.get("result_success") is True:
                aggregate["successes"] += 1
            elif record.get("result_success") is False:
                aggregate["failures"] += 1
            aggregate["calculated_damage"] += int(record.get("calculated_damage", 0) or 0)
            aggregate["actual_damage"] += int(record.get("effective_damage", 0) or 0)
            aggregate["overkill_damage"] += int(record.get("overkill_damage", 0) or 0)
            aggregate["healing"] += int(record.get("effective_hp_recovery", 0) or 0)
            aggregate["mp_recovery"] += int(record.get("effective_mp_recovery", 0) or 0)
            aggregate["mp_spent"] += self._extract_mp_spent(record)
        for aggregate in aggregates.values():
            uses = aggregate["uses"] or 0
            aggregate["avg_actual_damage"] = aggregate["actual_damage"] // uses if uses else 0
            aggregate["avg_healing"] = aggregate["healing"] // uses if uses else 0
        for event in getattr(game, "ball_hold_events", []):
            for source in event.get("candidates", []):
                key = f"{event.get('holder_id')}::{source.get('skill_id')}"
                if key not in aggregates:
                    continue
                if event.get("event") == "適用":
                    aggregates[key]["auto_activations"] += 1
                    aggregates[key]["effect_applications"] += 1
                elif event.get("event") == "解除":
                    aggregates[key]["target_removals"] += 1
        for record in self.action_records:
            for reference in record.get("result", {}).get("details", {}).get("ball_hold_modifiers", []):
                for source in reference.get("candidates", []):
                    key = f"{reference.get('holder_id')}::{source.get('skill_id')}"
                    if key in aggregates:
                        aggregates[key]["calculation_references"] += 1
        return list(aggregates.values())

    def _format_action_record(self, record: dict[str, Any]) -> str:
        before = record["before"]
        after = record["after"]
        result = record["result"]
        details = result["details"]
        actor_id = self._character_id(record.get("actor_id") or details.get("actor_id"))
        target_id = self._character_id(record.get("target_id") or details.get("target_id"))
        actor_before = self._participant_lookup(before["participants"]).get(actor_id, {}) if actor_id else {}
        actor_after = self._participant_lookup(after["participants"]).get(actor_id, {}) if actor_id else {}
        target_before = self._participant_lookup(before["participants"]).get(target_id, {}) if target_id else {}
        target_after = self._participant_lookup(after["participants"]).get(target_id, {}) if target_id else {}
        calculated_damage = int(record.get("calculated_damage", 0) or 0)
        actual_damage = int(record.get("effective_damage", 0) or 0)
        overkill_damage = int(record.get("overkill_damage", 0) or 0)
        healing = int(record.get("effective_hp_recovery", 0) or 0)
        mp_recovery = int(record.get("effective_mp_recovery", 0) or 0)
        calculated_mp_recovery = int(record.get("calculated_mp_recovery", 0) or 0)
        overheal_mp = int(record.get("overheal_mp", 0) or 0)
        movement_path = record.get("movement_path") or []
        movement_distance = record.get("movement_distance")
        position_before = record.get("position_before", actor_before.get("position"))
        position_after = record.get("position_after", actor_after.get("position"))
        score_before = self._score_text(record.get("score_before"))
        score_after = self._score_text(record.get("score_after"))
        return (
            f"- #{record['sequence']} / ターン{self._value(before.get('round'))} / 順{self._format_turn_index(before.get('turn_index'))}: "
            f"{self._value(actor_id)}->{self._value(target_id)} / {self._value(record.get('action_name'))} / {self._value(record.get('command_group'))} / "
            f"種別 {self._value(record.get('record_type'))}/{self._value(record.get('event_type'))} / "
            f"位置 {self._format_position_pair(position_before)}→{self._format_position_pair(position_after)} / 対象位置 {self._format_position_label(target_before)}→{self._format_position_label(target_after)} / "
            f"前HPMP {self._hp_mp_text(actor_before)}→{self._hp_mp_text(actor_after)} / "
            f"対象HPMP {self._hp_mp_text(target_before)}→{self._hp_mp_text(target_after)} / "
            f"移動 {self._value(record.get('movement_type'))} / 経路 {self._format_record_list(movement_path)} / 距離 {self._value(movement_distance)} / "
            f"計算上ダメージ {calculated_damage} / 実ダメージ {actual_damage} / 過剰 {overkill_damage} / HP回復 {healing} / "
            f"MP回復 計算上{calculated_mp_recovery}/実{mp_recovery}/過剰{overheal_mp} / 回復原因 {self._value(record.get('recovery_source'))} / "
            f"実行成功 {self._value(record.get('execution_success'))} / 判定成功 {self._value(record.get('result_success'))} / "
            f"前後得点 {score_before}→{score_after} / 得点者 {self._value(record.get('scorer_id'))} / 得点方法 {self._value(record.get('score_method'))} / 勝利得点 {self._value(record.get('is_winning_score'))} / "
            f"行動ID {self._value(record.get('source_action_id'))} / 得点イベントID {self._value(record.get('score_event_id') or record.get('related_score_event_id'))} / 得点正式情報 {self._value(record.get('score_change_authoritative'))} / "
            f"戦闘不能 {self._ko_text(before, after, actor_id, target_id)} / 結果 {self._sanitize(result.get('message'))} / 判定詳細 {self._sanitize(self._detail_summary(details))}"
        )

    def _detail_summary(self, details: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in (
            "action_name",
            "skill_name",
            "pass_success",
            "final_holder_id",
            "interceptor",
            "effect_results",
            "contest",
            "damage_calculation",
            "ball_effect",
            "ball_hold_modifiers",
        ):
            if key in details:
                parts.append(f"{key}={self._simplify_value(details.get(key))}")
        return "; ".join(parts) if parts else "-"

    def _simplify_value(self, value: Any) -> str:
        if isinstance(value, dict):
            items = []
            for key, item in value.items():
                if key in {"path", "participants", "turn_order"}:
                    continue
                items.append(f"{key}:{self._simplify_value(item)}")
            return "{" + ", ".join(items) + "}"
        if isinstance(value, list):
            return "[" + ", ".join(self._simplify_value(item) for item in value) + "]"
        if value in {None, ""}:
            return "-"
        return str(value)

    def _team_ai_settings(self, game: Any, team: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for character in self._ordered_characters(game):
            if character.team != team:
                continue
            result[character.char_id] = {
                "profile_id": character.ai_profile_id,
                "profile_name": character.ai_profile_name,
                "ai_level": getattr(character, "ai_level", 5),
                "profile_source": getattr(character, "ai_profile_source", "fallback"),
                "settings": dict(character.ai_settings),
            }
        return result

    def _initial_positions(self, game: Any) -> dict[str, tuple[int, int] | str]:
        return {character.char_id: ("ベンチ" if character.on_bench else character.initial_position) for character in self._ordered_characters(game)}

    def _character_snapshot(self, game: Any, character: Any) -> dict[str, Any]:
        return {
            "team": character.team,
            "slot": self._slot_for_character(game, character),
            "char_id": character.char_id,
            "name": character.name,
            "class_id": character.class_id,
            "class_name": character.class_name,
            "element_id": character.element_id,
            "element_name": character.element_name,
            "ai_profile_id": character.ai_profile_id,
            "ai_profile_name": character.ai_profile_name,
            "ai_level": getattr(character, "ai_level", 5),
            "ai_role_id": getattr(character, "ai_role_id", ""),
            "ai_role_name": getattr(character, "ai_role_name", ""),
            "enemy_master_id": getattr(character, "enemy_master_id", ""),
            "enemy_type_id": getattr(character, "enemy_type_id", ""),
            "enemy_group_id": getattr(character, "enemy_group_id", ""),
            "enemy_group_slot": getattr(character, "enemy_group_slot", 0),
            "enemy_scale": getattr(character, "enemy_scale", 1.0),
            "initial_position": character.initial_position,
            "on_bench": bool(getattr(character, "on_bench", False)),
            "starting_role": "bench" if getattr(character, "on_bench", False) else "field",
            "max_hp": character.max_hp,
            "max_mana": character.max_mana,
            "start_hp": character.hp,
            "start_mp": character.mana,
            "magic": character.magic,
            "power": character.power,
            "speed": character.speed,
            "technique": character.technique,
            "stamina": character.stamina,
            "move_range": character.move_range,
            "ai_settings": dict(character.ai_settings),
            "skills": [
                {
                    "skill_id": skill_id,
                    "name": self._skill_name(game, skill_id),
                    "command_group": self._skill_command_group(game, skill_id),
                    "purpose_tag": self._skill_purpose_tag(game, skill_id),
                }
                for skill_id in character.skills
            ],
        }

    def _participant_state(self, character: Any) -> dict[str, Any]:
        return {
            "char_id": character.char_id,
            "team": character.team,
            "hp": character.hp,
            "mp": character.mana,
            "position": character.position,
            "on_bench": bool(getattr(character, "on_bench", False)),
            "off_field": character.off_field,
            "injury_rate": int(getattr(character, "injury_rate", 0) or 0),
            "ball_holder": self._ball_holder_label(character),
        }

    def balance_metrics(self) -> dict[str, Any]:
        """Aggregate batch and Markdown figures from the same recorded state transitions."""
        metrics: dict[str, Any] = {
            "total_knockouts": 0, "player_knockouts": 0, "enemy_knockouts": 0,
            "knockouts_by_character": {}, "score_reset_returns": 0, "natural_returns": 0,
            "forced_knockout_drops": 0, "pass_attempts": 0, "pass_successes": 0,
            "steal_attempts": 0, "steal_successes": 0, "pass_cut_checks": 0,
            "pass_cut_successes": 0, "normal_drop_checks": 0, "normal_drop_successes": 0,
            "ball_cut_uses": 0, "ball_cut_checks": 0, "ball_cut_successes": 0,
            "heal_uses": 0, "effective_healing": 0,
        }
        for record in self.action_records:
            before = self._participant_lookup(record.get("before", {}).get("participants", []))
            after = self._participant_lookup(record.get("after", {}).get("participants", []))
            participant_items = before.items()
            if record.get("event_type") == "score_reset" and record.get("actor_id") in before:
                actor_id = str(record.get("actor_id"))
                participant_items = ((actor_id, before[actor_id]),)
            for char_id, state in participant_items:
                if not self._off_field(state) and self._off_field(after.get(char_id, {})):
                    metrics["total_knockouts"] += 1
                    team_key = "player_knockouts" if state.get("team") == "player" else "enemy_knockouts"
                    metrics[team_key] += 1
                    counts = metrics["knockouts_by_character"]
                    counts[char_id] = counts.get(char_id, 0) + 1
                if self._off_field(state) and not self._off_field(after.get(char_id, {})):
                    if record.get("event_type") == "score_reset":
                        metrics["score_reset_returns"] += 1
                    else:
                        metrics["natural_returns"] += 1
            details = record.get("result", {}).get("details", {})
            action_name = str(record.get("action_name", ""))
            skill_id = str(details.get("skill_id", ""))
            if "パス" in action_name and record.get("record_type") == "character_action":
                metrics["pass_attempts"] += 1
                if details.get("pass_success") is True:
                    metrics["pass_successes"] += 1
            if skill_id == "steal" or "スティール" in action_name:
                metrics["steal_attempts"] += 1
                if details.get("skill_success") is True or details.get("success") is True:
                    metrics["steal_successes"] += 1
            pass_cuts = details.get("pass_cut_results")
            if isinstance(pass_cuts, list):
                metrics["pass_cut_checks"] += len(pass_cuts)
                metrics["pass_cut_successes"] += sum(1 for item in pass_cuts if isinstance(item, dict) and item.get("success"))
            ball_effect = details.get("ball_effect")
            if isinstance(ball_effect, dict):
                effect_type = ball_effect.get("effect_type")
                if effect_type == "drop" and not ball_effect.get("forced_knockout_drop"):
                    metrics["normal_drop_checks"] += 1
                    metrics["normal_drop_successes"] += int(bool(ball_effect.get("success")))
                if effect_type == "cut":
                    metrics["ball_cut_checks"] += 1
                    metrics["ball_cut_successes"] += int(bool(ball_effect.get("success")))
            if skill_id == "single_technique_ball_cut":
                metrics["ball_cut_uses"] += 1
            effect_results = details.get("effect_results")
            forced_in_effects = isinstance(effect_results, list) and any(
                isinstance(item, dict)
                and isinstance(item.get("damage_calculation"), dict)
                and item["damage_calculation"].get("forced_knockout_drop")
                for item in effect_results
            )
            if details.get("forced_knockout_drop") or (isinstance(details.get("damage_calculation"), dict) and details["damage_calculation"].get("forced_knockout_drop")) or forced_in_effects:
                metrics["forced_knockout_drops"] += 1
            if record.get("effective_hp_recovery", 0):
                metrics["effective_healing"] += int(record.get("effective_hp_recovery", 0) or 0)
            if "回復" in action_name or "ヒール" in action_name or skill_id in {"heal", "heal_hp", "single_stamina_recover"}:
                metrics["heal_uses"] += 1
        return metrics

    def _ordered_characters(self, game: Any) -> list[Any]:
        return sorted(game.characters.values(), key=lambda character: (character.team, character.char_id))

    def _skill_name(self, game: Any, skill_id: str) -> str:
        skill = getattr(game, "skills", {}).get(skill_id)
        return getattr(skill, "name", skill_id)

    def _skill_command_group(self, game: Any, skill_id: str) -> str:
        skill = getattr(game, "skills", {}).get(skill_id)
        return getattr(skill, "command_group", "skill")

    def _skill_purpose_tag(self, game: Any, skill_id: str) -> str:
        skill = getattr(game, "skills", {}).get(skill_id)
        return getattr(skill, "purpose_tag", "")

    def _slot_for_character(self, game: Any, character: Any) -> str | int | str:
        positions = game.config.player_positions if character.team == "player" else game.config.enemy_positions
        for index, position in enumerate(positions, start=1):
            if position == character.initial_position:
                return index
        return "取得不可"

    def _character_end_value(self, game: Any, character_id: str, field: str) -> Any:
        character = getattr(game, "characters", {}).get(character_id)
        if character is None:
            return "取得不可"
        return character.hp if field == "hp" else character.mana

    def _participant_lookup(self, participants: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if isinstance(participants, dict):
            return {str(key): value for key, value in participants.items() if isinstance(value, dict)}
        return {item.get("char_id", ""): item for item in participants if isinstance(item, dict)}

    def _extract_calculated_damage(self, details: dict[str, Any]) -> int:
        calculation = details.get("damage_calculation")
        if isinstance(calculation, dict):
            if isinstance(calculation.get("predicted_damage"), int):
                return calculation["predicted_damage"]
            if isinstance(calculation.get("basic_damage"), int):
                return calculation["basic_damage"]
        damage = details.get("damage")
        return int(damage) if isinstance(damage, int) else 0

    def _extract_damage_calculation(self, details: dict[str, Any]) -> int:
        return self._extract_calculated_damage(details)

    def _extract_result_damage(self, record: dict[str, Any]) -> int:
        result = record["result"]
        details = result["details"]
        damage = result.get("damage", 0)
        if isinstance(damage, int) and damage > 0:
            return damage
        if isinstance(details.get("damage"), int) and details["damage"] > 0:
            return int(details["damage"])
        before = self._participant_lookup(record["before"]["participants"])
        after = self._participant_lookup(record["after"]["participants"])
        target_id = self._character_id(record.get("target_id") or details.get("target_id"))
        if target_id and target_id in before and target_id in after:
            return max(0, self._int(before[target_id].get("hp")) - self._int(after[target_id].get("hp")))
        return 0

    def _extract_actual_damage(self, record: dict[str, Any], before: dict[str, Any], after: dict[str, Any], actor_id: str, target_id: str) -> int:
        result_damage = self._extract_result_damage(record)
        if result_damage > 0:
            return result_damage
        if target_id:
            before_map = before.get("participants") if isinstance(before, dict) and "participants" in before else before
            after_map = after.get("participants") if isinstance(after, dict) and "participants" in after else after
            before_target = self._participant_lookup(before_map).get(target_id, {})
            after_target = self._participant_lookup(after_map).get(target_id, {})
            return max(0, self._int(before_target.get("hp")) - self._int(after_target.get("hp")))
        return 0

    def _extract_healing_amount(self, record: dict[str, Any], character_id: str) -> int:
        result = record["result"]
        details = result["details"]
        if character_id == self._character_id(details.get("actor_id")) and isinstance(details.get("healing"), int):
            return int(details["healing"])
        if isinstance(result.get("healing"), int):
            return int(result["healing"])
        return 0

    def _extract_result_healing(self, record: dict[str, Any]) -> int:
        result = record["result"]
        healing = result.get("healing", 0)
        if isinstance(healing, int) and healing > 0:
            return healing
        details = result["details"]
        if isinstance(details.get("healing"), int) and details["healing"] > 0:
            return int(details["healing"])
        before = self._participant_lookup(record["before"]["participants"])
        after = self._participant_lookup(record["after"]["participants"])
        actor_id = self._character_id(record.get("actor_id") or details.get("actor_id"))
        if actor_id and actor_id in before and actor_id in after:
            return max(0, self._int(after[actor_id].get("hp")) - self._int(before[actor_id].get("hp")))
        return 0

    def _extract_mp_spent(self, record: dict[str, Any]) -> int:
        details = record["result"]["details"]
        resource_type = details.get("resource_type")
        resource_cost = details.get("resource_cost")
        if resource_type == "mana" and isinstance(resource_cost, int) and resource_cost > 0:
            return int(resource_cost)
        before = self._participant_lookup(record["before"]["participants"])
        after = self._participant_lookup(record["after"]["participants"])
        actor_id = self._character_id(record.get("actor_id") or details.get("actor_id"))
        if actor_id and actor_id in before and actor_id in after:
            return max(0, self._int(before[actor_id].get("mp")) - self._int(after[actor_id].get("mp")))
        return 0

    def _count_ko_events(self) -> int:
        count = 0
        for record in self.action_records:
            before = self._participant_lookup(record["before"]["participants"])
            after = self._participant_lookup(record["after"]["participants"])
            for char_id, snapshot in before.items():
                if not self._off_field(snapshot) and self._off_field(after.get(char_id, {})):
                    count += 1
        return count

    def _count_actions_containing(self, keyword: str) -> int:
        return sum(1 for record in self.action_records if keyword in self._sanitize(record.get("action_name")) or keyword in self._sanitize(record["result"]["message"]))

    def _ko_text(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        actor_id: str,
        target_id: str,
    ) -> str:
        if target_id:
            before_target = self._participant_lookup(before["participants"]).get(target_id, {})
            after_target = self._participant_lookup(after["participants"]).get(target_id, {})
            if not self._off_field(before_target) and self._off_field(after_target):
                return target_id
        if actor_id:
            before_actor = self._participant_lookup(before["participants"]).get(actor_id, {})
            after_actor = self._participant_lookup(after["participants"]).get(actor_id, {})
            if not self._off_field(before_actor) and self._off_field(after_actor):
                return actor_id
        return "-"

    def _ball_holder_label(self, character: Any) -> str:
        return character.char_id if character.char_id == getattr(character, "char_id", None) else "-"

    def _score_text(self, scores: Any) -> str:
        if isinstance(scores, dict):
            return f"{self._value(scores.get('player'))} - {self._value(scores.get('enemy'))}"
        return "-"

    def _format_mapping(self, mapping: Any) -> str:
        if not isinstance(mapping, dict):
            return "-"
        parts = []
        for key, value in mapping.items():
            parts.append(f"{self._sanitize(key)}={self._sanitize(value)}")
        return ", ".join(parts) if parts else "-"

    def _format_position(self, value: Any) -> str:
        if isinstance(value, tuple) and len(value) == 2:
            return f"({value[0]}, {value[1]})"
        return self._value(value)

    def _format_skill_list(self, skills: Any) -> str:
        if not isinstance(skills, list):
            return "-"
        parts = []
        for skill in skills:
            if isinstance(skill, dict):
                parts.append(f"{self._sanitize(skill.get('skill_id'))}:{self._sanitize(skill.get('name'))}:{self._sanitize(skill.get('command_group'))}")
        return "; ".join(parts) if parts else "-"

    def _hp_mp_text(self, participant: dict[str, Any]) -> str:
        if not participant:
            return "取得不可"
        return f"HP{self._value(participant.get('hp'))}/MP{self._value(participant.get('mp'))}"

    def _format_position_label(self, participant: dict[str, Any]) -> str:
        if not participant:
            return "取得不可"
        position = participant.get("position")
        if isinstance(position, tuple) and len(position) == 2:
            return f"({position[0]}, {position[1]})"
        return "-"

    def _winner_label(self, summary: dict[str, Any]) -> str:
        if summary.get("draw"):
            return "引き分け"
        winner = summary.get("winner")
        if winner == "player":
            return "味方の勝利"
        if winner == "enemy":
            return "敵の勝利"
        return "-"

    def _match_mode(self, game: Any) -> str:
        return "debug" if getattr(game.config, "debug_mode", False) else "normal"

    def _seed_value(self, game: Any) -> Any:
        return getattr(game, "seed", "取得不可") if getattr(game, "seed", None) is not None else "取得不可"

    def _derive_target_id(self, game: Any) -> str:
        actor = getattr(game, "current_actor", None)
        return getattr(actor, "char_id", "") if actor is not None else ""

    def _match_id(self, game: Any) -> str:
        started_at = datetime.now().strftime("%Y_%m_%d_%H%M%S")
        return f"{REPORT_PREFIX}{started_at}"

    def _turn_limit_value(self, value: Any) -> str:
        if value in {None, 0, ""}:
            return "なし"
        return self._value(value)

    def _sanitize(self, value: Any) -> str:
        if value is None or value == "":
            return "-"
        text = str(value)
        return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " / ")

    def _off_field(self, participant: dict[str, Any] | None) -> bool:
        return bool(participant and participant.get("off_field"))

    def _character_id(self, value: Any) -> str:
        if value in {None, "", "-"}:
            return ""
        return str(value)

    def _int(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _value(self, value: Any, fallback: str = "-") -> str:
        if value in {None, ""}:
            return fallback
        return str(value)

    def _format_position_pair(self, value: Any) -> str:
        if isinstance(value, tuple) and len(value) == 2:
            return f"({value[0]}, {value[1]})"
        return self._value(value)

    def _format_skill_name(self, skill: Any) -> str:
        if isinstance(skill, dict):
            return self._sanitize(skill.get("name"))
        return self._value(skill)

    def _format_action_result(self, result: Any) -> str:
        return self._sanitize(result)

    def _format_record_value(self, value: Any) -> str:
        if value in {None, ""}:
            return "-"
        return self._sanitize(value)

    def _format_record_dict(self, value: Any) -> str:
        if not isinstance(value, dict):
            return self._format_record_value(value)
        parts = []
        for key, item in value.items():
            if key == "participants":
                continue
            parts.append(f"{self._sanitize(key)}:{self._format_record_value(item)}")
        return "{" + ", ".join(parts) + "}" if parts else "{}"

    def _format_record_list(self, value: Any) -> str:
        if not isinstance(value, list):
            return self._format_record_value(value)
        return "[" + ", ".join(self._format_record_value(item) for item in value) + "]"

    def _format_record_tuple(self, value: Any) -> str:
        if not isinstance(value, tuple):
            return self._format_record_value(value)
        return "(" + ", ".join(self._format_record_value(item) for item in value) + ")"

    def _format_record(self, value: Any) -> str:
        if isinstance(value, dict):
            return self._format_record_dict(value)
        if isinstance(value, list):
            return self._format_record_list(value)
        if isinstance(value, tuple):
            return self._format_record_tuple(value)
        return self._format_record_value(value)

    def _format_target_label(self, actor_id: str, target_id: str) -> str:
        if actor_id and target_id:
            return f"{actor_id}->{target_id}"
        if actor_id:
            return actor_id
        return "-"

    def _format_turn_index(self, turn_index: Any) -> str:
        if isinstance(turn_index, int):
            return str(turn_index + 1)
        return self._value(turn_index)

    def _format_action_summary(self, record: dict[str, Any]) -> str:
        before = record["before"]
        after = record["after"]
        result = record["result"]
        details = result["details"]
        actor_id = self._character_id(record.get("actor_id") or details.get("actor_id"))
        target_id = self._character_id(record.get("target_id") or details.get("target_id"))
        actor_before = self._participant_lookup(before["participants"]).get(actor_id, {}) if actor_id else {}
        actor_after = self._participant_lookup(after["participants"]).get(actor_id, {}) if actor_id else {}
        target_before = self._participant_lookup(before["participants"]).get(target_id, {}) if target_id else {}
        target_after = self._participant_lookup(after["participants"]).get(target_id, {}) if target_id else {}
        return (
            f"- #{record['sequence']} / ターン{self._value(before.get('round'))} / 順{self._format_turn_index(before.get('turn_index'))}: "
            f"{self._format_target_label(actor_id, target_id)} / {self._value(record.get('action_name'))} / {self._value(record.get('command_group'))} / "
            f"前位置 {self._format_position_label(actor_before)}→{self._format_position_label(actor_after)} / "
            f"対象位置 {self._format_position_label(target_before)}→{self._format_position_label(target_after)} / "
            f"前HPMP {self._hp_mp_text(actor_before)}→{self._hp_mp_text(actor_after)} / "
            f"対象HPMP {self._hp_mp_text(target_before)}→{self._hp_mp_text(target_after)} / "
            f"計算上ダメージ {self._extract_calculated_damage(details)} / 実ダメージ {self._extract_actual_damage(record, before, after, actor_id, target_id)} / 回復量 {self._extract_result_healing(record)} / "
            f"前後得点 {self._score_text(before.get('scores'))}→{self._score_text(after.get('scores'))} / "
            f"戦闘不能 {self._ko_text(before, after, actor_id, target_id)} / 結果 {self._sanitize(result.get('message'))} / 判定詳細 {self._sanitize(self._detail_summary(details))}"
        )

    def _character_aggregates_final(self, aggregates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for aggregate in aggregates:
            aggregate.pop("start_skills", None)
        return aggregates

    def _count_pass_successes(self) -> int:
        count = 0
        for record in self.action_records:
            details = record["result"]["details"]
            if details.get("pass_success") is True:
                count += 1
        return count

    def _team_label(self, team: str) -> str:
        return "味方" if team == "player" else "敵" if team == "enemy" else team

    def _format_state_score(self, scores: Any) -> str:
        if isinstance(scores, dict):
            return f"{self._value(scores.get('player'))} - {self._value(scores.get('enemy'))}"
        return "-"

    def _build_markdown_placeholder(self) -> str:
        return REPORT_TITLE

    def _count_actions(self, action_names: set[str]) -> int:
        return sum(1 for record in self.action_records if record.get("action_name") in action_names)

    def _count_skill_uses(self, skill_id: str) -> int:
        return sum(1 for record in self.action_records if record["result"]["details"].get("skill_id") == skill_id)

    def _score_from_record(self, record: dict[str, Any]) -> str:
        return self._score_text(record["after"].get("scores"))

    def _ball_holder_from_record(self, record: dict[str, Any]) -> str:
        return self._value(record["after"].get("ball_holder_id"))

    def _safe_float_division(self, numerator: int, denominator: int) -> int:
        return numerator // denominator if denominator else 0

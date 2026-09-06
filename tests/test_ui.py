from __future__ import annotations

import asyncio
import gc
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from manaball.core import ENEMY, PLAYER
from manaball.data import create_match, load_character_roster
from manaball.ui import (
    ACTION_LIST_RECT,
    CELL_SIZE,
    FIELD_RECT,
    SIDE_PANEL_RECT,
    TOUCH_MOUSE_SUPPRESSION_MS,
    GameApp,
)


class MatchUiTests(unittest.TestCase):
    def test_attack_preview_modal_is_side_effect_free_and_confirms_shared_action(self) -> None:
        actor = self.app.game.characters["10"]
        self.app.game.turn_index = self.app.game.turn_order.index(actor.char_id)
        target = self.app.game.characters["20"]
        actor.position, target.position = (5, 2), (6, 2)
        skill_id = f"normal_{actor.primary_system}_attack"
        before = (target.hp, actor.mana, actor.position, self.app.game.ball.holder_id, actor.acted)
        rng_before = self.app.game.rng.getstate()

        self.app.mode = f"skill_{skill_id}"
        self.app.handle_cell(target.position)

        self.assertEqual(self.app.action_preview["preview_type"], "damage")
        self.assertEqual(before, (target.hp, actor.mana, actor.position, self.app.game.ball.holder_id, actor.acted))
        self.assertEqual(rng_before, self.app.game.rng.getstate())
        expected = self.app.action_preview["primary_value"]
        self.app.draw_match()
        confirm = next(button for button in self.app.buttons if button.key == "preview_confirm")
        self.app.handle_pointer(confirm.rect.center)
        self.assertEqual(target.hp, before[0] - expected)

    def test_power_up_uses_self_preview_without_duplicate_target_and_escape_is_safe(self) -> None:
        actor = self.app.game.characters["10"]
        self.app.game.turn_index = self.app.game.turn_order.index(actor.char_id)
        actor.mana = max(2, actor.max_mana)
        before = (self.app.game.effective_stat(actor, "power"), actor.mana, dict(actor.temporary_effects))
        rng_before = self.app.game.rng.getstate()

        self.app._start_skill_action(actor, "power_up")

        self.assertTrue(self.app.action_preview["self_target"])
        self.assertEqual(self.app.action_preview["before_value"], before[0])
        self.assertEqual(self.app.action_preview["after_value"], before[0] + 2)
        self.assertEqual(rng_before, self.app.game.rng.getstate())
        self.app.process_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)])
        self.assertIsNone(self.app.action_preview)
        self.assertEqual(before, (self.app.game.effective_stat(actor, "power"), actor.mana, actor.temporary_effects))

    def test_preview_portraits_use_actor_left_target_right_and_self_draws_once(self) -> None:
        actor = self.app.game.characters["10"]
        target = self.app.game.characters["20"]
        actor.position, target.position = (10, 2), (2, 2)
        slots = self.app._preview_portrait_slots(actor, target)
        self.assertEqual(slots, {"left": [actor], "right": [target]})
        self.assertEqual(self.app._preview_portrait_slots(actor, None), {"left": [actor], "right": []})
        portrait = pygame.Surface((200, 400), pygame.SRCALPHA)
        with patch.object(self.app, "preview_portrait", return_value=portrait) as loader:
            self.app._draw_preview_portraits(pygame.Rect(350, 150, 740, 600), actor, None)
        loader.assert_called_once_with(actor)

    def test_missing_enemy_preview_portrait_is_blank(self) -> None:
        enemy = self.app.game.characters["20"]
        enemy.enemy_master_id = "missing-preview-portrait"
        self.app.preview_portraits.pop(enemy.char_id, None)
        with patch("pygame.image.load", side_effect=FileNotFoundError("missing")):
            self.assertIsNone(self.app.preview_portrait(enemy))

    def test_push_strike_opens_common_preview_with_actor_portrait_data(self) -> None:
        actor = self.app.game.characters["10"]
        target = self.app.game.characters["20"]
        self.app.game.turn_index = self.app.game.turn_order.index(actor.char_id)
        actor.position, target.position = (5, 2), (6, 2)
        self.app.game.set_ball_holder(target.char_id, "test")
        self.app._start_skill_action(actor, "push_strike")
        self.app.handle_cell(target.position)
        self.assertIsNotNone(self.app.action_preview)
        self.assertEqual(self.app.action_preview["actor_id"], actor.char_id)
        self.assertEqual(self.app.action_preview["target_id"], target.char_id)
        self.app.draw_match()
        self.assertTrue(any(button.key == "preview_confirm" for button in self.app.buttons))

    def test_steal_and_ball_skills_keep_actor_and_target_roles(self) -> None:
        actor = self.app.game.characters["10"]
        target = self.app.game.characters["20"]
        for skill_id in ("steal", "single_technique_ball_cut"):
            with self.subTest(skill_id=skill_id):
                actor.position, target.position = (9, 2), (8, 2)
                preview = self.app.game.action_preview(actor.char_id, skill_id, target.char_id)
                self.assertEqual(preview["actor_id"], actor.char_id)
                self.assertEqual(preview["target_id"], target.char_id)

    def test_ball_holder_attack_command_uses_common_candidates(self) -> None:
        actor = self.app.game.characters["10"]
        target = self.app.game.characters["20"]
        actor.position = (5, 2)
        target.position = (6, 2)
        self.app.game.set_ball_holder(actor.char_id, "test")
        self.app.game.current_actor_id = actor.char_id
        specs = {key: enabled for _label, key, enabled, _reason in self.app._action_command_specs(actor)}
        self.assertTrue(specs["attack"])
        self.assertTrue(any(item.skill_id == "normal_physical_attack" for item in self.app.game.action_candidates(actor.char_id, "attack")))

    def test_non_holder_attack_command_is_enabled_during_ally_possession(self) -> None:
        actor = self.app.game.characters["10"]
        target = self.app.game.characters["20"]
        holder = self.app.game.characters["11"]
        actor.position, target.position, holder.position = (5, 2), (6, 2), (3, 2)
        self.app.game.set_ball_holder(holder.char_id, "test")
        specs = {key: (enabled, reason) for _label, key, enabled, reason in self.app._action_command_specs(actor)}
        self.assertTrue(specs["attack"][0], specs["attack"][1])

    def setUp(self) -> None:
        self.debug_save_dir = tempfile.TemporaryDirectory()
        self.debug_save_patcher = patch(
            "manaball.debug_save.DEFAULT_PATH",
            Path(self.debug_save_dir.name) / "debug_match_last.json",
        )
        self.debug_save_patcher.start()
        self.app = GameApp(start_match=True, seed=7)
        self.app.draw()
        self.app.handle_button("round_manual")
        self.app.draw()

    def tearDown(self) -> None:
        if self.app is not None:
            self.app.icons.clear()
            self.app.round_icons.clear()
            self.app.portraits.clear()
            self.app.preview_portraits.clear()
            self.app.battle_background_original = None
            self.app.battle_field_original = None
            self.app.battle_background = None
            self.app.battle_field_background = None
            self.app.fonts.clear()
        pygame.quit()
        self.app = None
        self.debug_save_patcher.stop()
        self.debug_save_dir.cleanup()
        gc.collect()

    def drain_presentation(self) -> None:
        for _ in range(30):
            if not self.app.input_locked:
                return
            if self.app.presentation_waiting_for_confirm:
                self.app.handle_pointer((0, 0))
                continue
            self.app.presentation_until = 0
            self.app._update_presentation()
        self.fail("presentation did not finish")

    def open_commands(self) -> None:
        actor = self.app.game.current_actor
        if not self.app.command_window_open:
            self.app.draw()
            self.app.handle_pointer(self.app.cell_rect(actor.position).center)
        self.app.draw()
        self.assertTrue(self.app.command_window_open)

    def choose_action(self, command_group: str, action_id: str) -> None:
        self.app.handle_button("skills" if command_group == "skill" else command_group)
        self.assertTrue(self.app.skill_menu)
        candidates = self.app._action_menu_candidates()
        index = next(index for index, candidate in enumerate(candidates) if candidate.action_id == action_id)
        self.app.handle_button(f"action_focus:{index}")
        self.app.handle_button("action_confirm")
        self.assertFalse(self.app.skill_menu)

    def test_shared_frame_routes_mouse_keyboard_and_touch_input(self) -> None:
        self.app.draw()
        speed = next(button for button in self.app.buttons if button.key == "speed")
        old_speed = self.app.speed_index
        mouse = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=speed.rect.center)
        self.assertTrue(self.app.run_frame([mouse]))
        self.assertNotEqual(self.app.speed_index, old_speed)

        self.app.draw()
        auto = next(button for button in self.app.buttons if button.key == "auto")
        width, height = self.app.screen.get_size()
        touch = pygame.event.Event(
            pygame.FINGERDOWN,
            x=auto.rect.centerx / width,
            y=auto.rect.centery / height,
        )
        self.assertTrue(self.app.run_frame([touch]))
        self.assertTrue(self.app.auto_player)

        escape = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)
        self.assertTrue(self.app.run_frame([escape]))
        self.assertIsNone(self.app.mode)

    def test_async_loop_yields_and_uses_shared_frame(self) -> None:
        with patch.object(self.app, "run_frame", side_effect=[True, True]) as run_frame:
            result = asyncio.run(self.app.run_async(max_frames=2))
        self.assertEqual(result, 0)
        self.assertEqual(run_frame.call_count, 2)

    def test_auto_and_speed_buttons_are_available(self) -> None:
        auto = next(button for button in self.app.buttons if button.key == "auto")
        speed = next(button for button in self.app.buttons if button.key == "speed")
        self.assertEqual(self.app.speed_levels, (1, 2, 5, 10, 20))
        self.app.handle_pointer(auto.rect.center)
        self.assertTrue(self.app.auto_player)
        self.assertEqual(self.app.speed_levels[self.app.speed_index], 1)
        seen = []
        for _ in range(5):
            self.app.handle_pointer(speed.rect.center)
            seen.append(self.app.speed_levels[self.app.speed_index])
            self.app.draw()
            speed = next(button for button in self.app.buttons if button.key == "speed")
        self.assertEqual(seen, [2, 5, 10, 20, 1])
        self.assertTrue(self.app.auto_player)

    def test_match_starts_directly_without_control_mode_or_skip(self) -> None:
        self.app.draw()
        self.assertTrue(self.app.control_mode_selected)
        self.assertFalse(self.app.show_control_mode_modal)
        self.assertFalse(self.app.auto_player)
        self.assertEqual(self.app.speed_levels[self.app.speed_index], 1)
        self.assertFalse(any(button.key in {"round_manual", "round_ai", "skip", "round_remaining_ai"} for button in self.app.buttons))

    def test_manual_enemy_ai_buttons_take_priority_over_slot_button(self) -> None:
        self.assertTrue(self.app.open_party_setup(debug_mode=True))
        self.app.party_free_enemy_mode = "manual"
        self.app.party_enemy_ids[0] = self.app.party_roster[0].char_id
        self.app.party_selected_slot = None
        self.app.draw()
        profile_button = next(button for button in self.app.buttons if button.key == "party_debug_enemy_ai:0")
        level_button = next(button for button in self.app.buttons if button.key == "party_debug_enemy_ai_level:0")
        old_profile = self.app._enemy_slot_profile_id(0)
        old_level = self.app.party_enemy_ai_levels[0]
        self.app.handle_pointer(profile_button.rect.center)
        self.assertNotEqual(self.app._enemy_slot_profile_id(0), old_profile)
        self.assertIsNone(self.app.party_selected_slot)
        self.app.draw()
        level_button = next(button for button in self.app.buttons if button.key == "party_debug_enemy_ai_level:0")
        self.app.handle_pointer(level_button.rect.center)
        self.assertEqual(self.app.party_enemy_ai_levels[0], old_level % 10 + 1)
        self.assertIsNone(self.app.party_selected_slot)
        self.app.round_control_round -= 1
        self.app.update()
        self.assertFalse(self.app.show_control_mode_modal)

    def test_battle_layout_uses_large_field_and_shared_click_geometry(self) -> None:
        self.assertEqual(CELL_SIZE, 104)
        self.assertEqual(FIELD_RECT.size, (1352, 520))
        self.assertEqual(self.app.cell_rect((0, 0)).topleft, FIELD_RECT.topleft)
        self.assertEqual(self.app.cell_rect((12, 4)).bottomright, FIELD_RECT.bottomright)
        for cell in ((0, 0), (6, 2), (12, 4)):
            self.assertEqual(self.app.screen_to_cell(self.app.cell_rect(cell).center), cell)
        self.assertIsNone(self.app.screen_to_cell((FIELD_RECT.right, FIELD_RECT.bottom)))
        self.assertEqual(SIDE_PANEL_RECT.width, 0)

    def test_large_field_uses_centered_square_cell_geometry(self) -> None:
        self.app.game = create_match(
            seed=7,
            quest_id="training_5v5",
            player_ids=("1", "2", "3", "4", "5", "10", "11"),
        )
        board_rect, cell_size = self.app._board_geometry()
        self.assertEqual(cell_size, 74)
        self.assertTrue(FIELD_RECT.contains(board_rect))
        self.assertEqual(board_rect.size, (15 * 74, 7 * 74))
        self.assertGreater(board_rect.x, FIELD_RECT.x)
        self.assertEqual(board_rect.y, FIELD_RECT.y + 1)
        for cell in ((0, 0), (7, 3), (14, 6)):
            self.assertEqual(self.app.screen_to_cell(self.app.cell_rect(cell).center), cell)
        self.assertIsNone(self.app.screen_to_cell((FIELD_RECT.x + 4, FIELD_RECT.y + 4)))

    def test_battle_backgrounds_are_cached_at_layout_sizes(self) -> None:
        self.assertEqual(self.app.battle_background.get_size(), self.app.screen.get_size())
        self.assertEqual(self.app.battle_field_background.get_size(), FIELD_RECT.size)
        actor_id = self.app.game.current_actor.char_id
        self.assertEqual(self.app.circular_icon(actor_id, 70).get_size(), (70, 70))

    def test_missing_battle_backgrounds_fall_back_without_stopping_match(self) -> None:
        with self.assertLogs("manaball.ui", level="WARNING") as logs:
            with patch("pygame.image.load", side_effect=FileNotFoundError("missing")):
                fallback_app = GameApp(start_match=True, seed=7)
                fallback_app.draw()
        self.assertIsNone(fallback_app.battle_background)
        self.assertIsNone(fallback_app.battle_field_background)
        self.assertEqual(fallback_app.state, "match")
        self.assertEqual(len(logs.output), 2)

    def test_actor_command_window_and_options_use_new_regions(self) -> None:
        self.assertTrue(any(button.key in {"attack", "move", "ball", "skills", "wait"} for button in self.app.buttons))
        self.open_commands()
        action_buttons = [button for button in self.app.buttons if button.key in {"attack", "move", "ball", "skills", "wait"}]
        self.assertEqual([button.key for button in action_buttons], ["attack", "move", "ball", "skills", "wait"])
        self.assertFalse(any(button.key == "pass" for button in self.app.buttons))
        self.assertTrue(all(FIELD_RECT.contains(button.rect) for button in action_buttons))
        self.assertFalse(any(button.key in {"cut", "steal", "defend", "keep"} for button in self.app.buttons))
        options = next(button for button in self.app.buttons if button.key == "options")
        self.app.handle_pointer(options.rect.center)
        self.app.draw()
        self.assertTrue(any(button.key == "options_full_log" for button in self.app.buttons))

    def test_other_character_selection_does_not_open_commands(self) -> None:
        other = next(character for character in self.app.game.active_characters() if character is not self.app.game.current_actor)
        self.app.handle_pointer(self.app.cell_rect(other.position).center)
        self.app.draw()
        self.assertEqual(self.app.selected_id, other.char_id)
        self.assertFalse(self.app.command_window_open)
        self.assertFalse(any(button.key in {"attack", "move", "ball", "skills", "wait"} for button in self.app.buttons))

    def test_shared_fullscreen_action_selector_confirms_and_back_preserves_match_state(self) -> None:
        actor = self.app.game.current_actor
        before = (actor.hp, actor.mana, actor.position, self.app.game.ball.holder_id, actor.acted, self.app.game.pending_move)
        self.open_commands()
        self.app.handle_button("move")
        self.app.draw()
        self.assertTrue(self.app.skill_menu)
        self.assertEqual(self.app.action_menu_command, "move")
        self.assertTrue(any(button.key == "action_confirm" for button in self.app.buttons))
        self.assertTrue(any(button.key == "action_back" for button in self.app.buttons))
        self.assertTrue(all(ACTION_LIST_RECT.contains(button.rect) for button in self.app.buttons if button.key.startswith("action_focus:")))
        self.assertFalse(any(button.key in {"auto", "speed", "options"} for button in self.app.buttons))
        self.app.handle_button("action_back")
        after = (actor.hp, actor.mana, actor.position, self.app.game.ball.holder_id, actor.acted, self.app.game.pending_move)
        self.assertEqual(after, before)
        self.assertTrue(self.app.command_window_open)

    def test_ability_modal_uses_effective_stats_and_is_side_effect_free(self) -> None:
        actor = self.app.game.current_actor
        self.assertIsNotNone(actor)
        actor = actor
        actor.injury_rate = 20
        actor.temporary_effects["stat_modifiers"] = [{
            "stat": "power", "mode": "add", "value": 2, "remaining": 2,
            "source_skill_id": "power_up", "source_skill_name": "パワーアップ",
        }]
        before = (
            actor.hp, actor.mana, actor.position, actor.acted,
            self.app.game.ball.holder_id, dict(actor.skill_cooldowns),
            dict(actor.skill_use_counts), self.app.game.rng.getstate(),
        )
        specs = self.app._action_command_specs(actor)
        self.assertEqual(specs[-1][:3], ("能力", "ability", True))
        self.app.handle_button("ability")
        self.assertEqual(self.app.ability_modal_actor_id, actor.char_id)
        self.assertIn("パワーアップ：パワー+2／残り2行動", self.app._ability_status_lines(actor))
        self.app.update()
        self.app.draw_match()
        self.app.process_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)])
        self.assertIsNone(self.app.ability_modal_actor_id)
        after = (
            actor.hp, actor.mana, actor.position, actor.acted,
            self.app.game.ball.holder_id, dict(actor.skill_cooldowns),
            dict(actor.skill_use_counts), self.app.game.rng.getstate(),
        )
        self.assertEqual(before, after)

    def test_touch_can_open_actor_commands_and_fullscreen_selector(self) -> None:
        width, height = self.app.screen.get_size()

        def tap(position: tuple[int, int]) -> None:
            event = pygame.event.Event(pygame.FINGERDOWN, x=position[0] / width, y=position[1] / height)
            self.assertTrue(self.app.run_frame([event]))

        actor = self.app.game.current_actor
        tap(self.app.cell_rect(actor.position).center)
        move = next(button for button in self.app.buttons if button.key == "move")
        tap(move.rect.center)
        self.assertTrue(self.app.skill_menu)
        focus = next(button for button in self.app.buttons if button.key.startswith("action_focus:"))
        tap(focus.rect.center)
        back = next(button for button in self.app.buttons if button.key == "action_back")
        tap(back.rect.center)
        self.assertFalse(self.app.skill_menu)
        self.assertTrue(self.app.command_window_open)

    def test_touch_and_synthetic_mouse_from_one_tap_are_processed_once(self) -> None:
        actor = self.app.game.current_actor
        position = self.app.cell_rect(actor.position).center
        width, height = self.app.screen.get_size()
        touch = pygame.event.Event(
            pygame.FINGERDOWN,
            x=position[0] / width,
            y=position[1] / height,
            timestamp=1000,
        )
        mouse = pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            button=1,
            pos=position,
            timestamp=1001,
        )
        with patch.object(self.app, "handle_pointer", wraps=self.app.handle_pointer) as handle_pointer:
            self.app.process_events([touch, mouse])
        self.assertEqual(handle_pointer.call_count, 1)
        self.assertTrue(self.app.command_window_open)
        self.assertEqual(self.app.command_window_actor_id, actor.char_id)

    def test_synthetic_mouse_on_next_frame_is_suppressed_but_pc_mouse_remains_available(self) -> None:
        actor = self.app.game.current_actor
        position = self.app.cell_rect(actor.position).center
        width, height = self.app.screen.get_size()
        touch = pygame.event.Event(
            pygame.FINGERDOWN,
            x=position[0] / width,
            y=position[1] / height,
            timestamp=2000,
        )
        synthetic_mouse = pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            button=1,
            pos=(0, 0),
            timestamp=2000 + TOUCH_MOUSE_SUPPRESSION_MS - 1,
        )
        self.app.process_events([touch])
        with patch.object(self.app, "handle_pointer", wraps=self.app.handle_pointer) as handle_pointer:
            self.app.process_events([synthetic_mouse])
        self.assertEqual(handle_pointer.call_count, 0)
        self.assertTrue(self.app.command_window_open)

        pc_mouse = pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            button=1,
            pos=position,
            timestamp=2000 + TOUCH_MOUSE_SUPPRESSION_MS + 1,
        )
        with patch.object(self.app, "handle_pointer", wraps=self.app.handle_pointer) as handle_pointer:
            self.app.process_events([pc_mouse])
        self.assertEqual(handle_pointer.call_count, 1)
        self.assertTrue(self.app.command_window_open)

    def test_command_window_is_recomputed_inside_field_and_survives_resize(self) -> None:
        actor = self.app.game.current_actor
        for position in ((0, 0), (12, 0), (0, 4), (12, 4)):
            actor.position = position
            self.app._open_command_window(actor)
            self.app.draw()
            self.assertIsNotNone(self.app.command_window_rect)
            self.assertTrue(FIELD_RECT.contains(self.app.command_window_rect))
        resize = pygame.event.Event(pygame.VIDEORESIZE, w=900, h=1440)
        self.app.process_events([resize])
        self.assertTrue(self.app.command_window_open)
        self.assertIsNone(self.app.command_window_rect)
        self.app.draw()
        self.assertTrue(FIELD_RECT.contains(self.app.command_window_rect))

    def test_command_button_touch_is_consumed_without_reaching_field(self) -> None:
        self.open_commands()
        move = next(button for button in self.app.buttons if button.key == "move" and button.enabled)
        width, height = self.app.screen.get_size()
        touch = pygame.event.Event(
            pygame.FINGERDOWN,
            x=move.rect.centerx / width,
            y=move.rect.centery / height,
            timestamp=3000,
        )
        with patch.object(self.app, "handle_cell", wraps=self.app.handle_cell) as handle_cell:
            self.app.process_events([touch])
        self.assertEqual(handle_cell.call_count, 0)
        self.assertTrue(self.app.skill_menu)

    def test_unusable_action_remains_selectable_for_reason_but_cannot_confirm(self) -> None:
        actor = self.app.game.current_actor
        skill_id = actor.skills[0]
        actor.skill_cooldowns[skill_id] = 2
        skill = self.app.game.skills[skill_id]
        self.app.skill_menu = True
        self.app.action_menu_command = skill.command_group
        candidates = self.app._action_menu_candidates()
        index = next(index for index, candidate in enumerate(candidates) if candidate.skill_id == skill_id)
        self.app.handle_button(f"action_focus:{index}")
        self.assertFalse(candidates[index].usable)
        self.app.draw()
        confirm = next(button for button in self.app.buttons if button.key == "action_confirm")
        self.assertFalse(confirm.enabled)
        self.app.handle_button("action_confirm")
        self.assertTrue(self.app.skill_menu)
        self.assertIn("クールタイム", self.app.message)

    def test_move_uses_presentation_lock_then_returns_to_action_selection(self) -> None:
        actor = self.app.game.current_actor
        origin = actor.position
        self.open_commands()
        self.choose_action("move", "standard:move")
        destination = sorted(self.app.game.reachable_positions(actor.char_id))[0]
        self.app.handle_pointer(self.app.cell_rect(destination).center)
        self.assertTrue(self.app.input_locked)
        self.assertGreater(len(self.app.presentation_steps), 0)
        self.assertEqual(self.app.visual_positions[actor.char_id], origin)
        self.assertEqual(actor.position, origin)
        self.drain_presentation()
        self.assertTrue(self.app.turn_moved)
        self.assertIs(self.app.game.current_actor, actor)
        self.assertEqual(actor.position, destination)
        self.open_commands()
        moved_commands = {
            button.key: button.enabled
            for button in self.app.buttons
            if button.key in {"attack", "move", "ball", "skills", "wait"}
        }
        self.assertEqual(list(moved_commands), ["attack", "move", "ball", "skills", "wait", "ability"])
        self.assertFalse(moved_commands["move"])
        self.assertTrue(moved_commands["wait"])
        self.assertTrue(moved_commands["ability"])
        self.assertEqual(moved_commands["attack"], bool(self.app.game.attack_targets(actor.char_id)))
        cancel = next(button for button in self.app.buttons if button.key == "cancel_move")
        self.app.handle_pointer(cancel.rect.center)
        self.assertEqual(actor.position, origin)
        self.assertFalse(self.app.turn_moved)
        self.assertIsNone(self.app.mode)

    def test_auto_uses_same_ai_result_and_advances_after_presentation(self) -> None:
        actor = self.app.game.current_actor
        self.app.auto_player = True
        self.app.ai_ready_at = 0
        self.app.update()
        self.assertTrue(self.app.input_locked)
        self.drain_presentation()
        self.assertIsNot(self.app.game.current_actor, actor)

    def test_auto_toggle_does_not_change_speed_and_cancels_pending_move(self) -> None:
        actor = self.app.game.current_actor
        origin = actor.position
        destination = next(iter(self.app.game.movement_paths(actor.char_id)))
        self.assertTrue(self.app.game.prepare_move(actor.char_id, destination).success)
        self.app.speed_index = 2
        self.app.handle_button("auto")
        self.assertTrue(self.app.auto_player)
        self.assertEqual(self.app.speed_levels[self.app.speed_index], 5)
        self.assertIsNone(self.app.game.pending_move)
        self.assertEqual(actor.position, origin)
        self.app.handle_button("auto")
        self.assertFalse(self.app.auto_player)
        self.assertEqual(self.app.speed_levels[self.app.speed_index], 5)

    def test_damage_result_is_presented_without_reroll(self) -> None:
        actor = self.app.game.current_actor
        target = self.app.game.characters["20"]
        actor.position = (3, 2)
        target.position = (4, 2)
        before_events = len(self.app.game.events)
        result = self.app.game.normal_attack(actor.char_id, target.char_id)
        calculation = dict(result.details["damage_calculation"])
        events_after_resolution = len(self.app.game.events)
        self.app._present_result(result, actor.char_id, advance_after=False)
        for _ in range(20):
            if self.app.presentation_details:
                break
            self.app.presentation_until = 0
            self.app._update_presentation()
        self.assertEqual(self.app.presentation_details["damage_calculation"], calculation)
        self.assertEqual(calculation["system_name"], "物理")
        self.assertEqual(len(self.app.game.events), events_after_resolution)
        self.assertGreater(events_after_resolution, before_events)
        self.app.draw()

    def test_manual_judgement_waits_for_new_pointer_but_auto_mode_uses_timer(self) -> None:
        actor = self.app.game.current_actor
        target = self.app.game.characters["20"]
        actor.position = (3, 2)
        target.position = (4, 2)
        result = self.app.game.normal_attack(actor.char_id, target.char_id)
        hp_after_resolution = target.hp
        events_after_resolution = len(self.app.game.events)

        self.app._present_result(result, actor.char_id, advance_after=False)
        self.app.presentation_until = 0
        self.app._update_presentation()
        self.assertTrue(self.app.presentation_waiting_for_confirm)
        for _ in range(5):
            self.app._update_presentation()
        self.assertTrue(self.app.presentation_waiting_for_confirm)
        self.assertEqual(target.hp, hp_after_resolution)
        self.assertEqual(len(self.app.game.events), events_after_resolution)

        self.app.handle_pointer((0, 0))
        self.assertFalse(self.app.presentation_waiting_for_confirm)
        self.drain_presentation()

        auto_app = GameApp(start_match=True, seed=7)
        try:
            auto_actor = auto_app.game.current_actor
            auto_target = auto_app.game.characters["20"]
            auto_actor.position = (3, 2)
            auto_target.position = (4, 2)
            auto_app.auto_player = True
            auto_result = auto_app.game.normal_attack(auto_actor.char_id, auto_target.char_id)
            auto_app._present_result(auto_result, auto_actor.char_id, advance_after=False)
            auto_app.presentation_until = 0
            auto_app._update_presentation()
            self.assertFalse(auto_app.presentation_waiting_for_confirm)
            self.assertGreater(auto_app.presentation_until, 0)
        finally:
            auto_app.icons.clear()
            auto_app.round_icons.clear()
            auto_app.portraits.clear()

    def test_skill_menu_selects_target_confirms_and_uses_common_result(self) -> None:
        actor = self.app.game.current_actor
        target = self.app.game.characters["20"]
        actor.position = (2, 2)
        target.position = (4, 2)
        self.app.game.drop_ball((8, 4), "test")
        before_hp = target.hp
        self.choose_action("attack", "skill:normal_magic_attack")
        self.app.handle_pointer(self.app.cell_rect(target.position).center)
        self.assertEqual(self.app.pending_skill_confirmation, ("normal_magic_attack", target.char_id))
        self.assertEqual(target.hp, before_hp)
        self.app.draw()
        confirm = next(button for button in self.app.buttons if button.key == "confirm_skill")
        self.app.handle_pointer(confirm.rect.center)
        self.assertLess(target.hp, before_hp)
        self.assertTrue(self.app.input_locked)
        self.assertFalse(actor.acted)
        self.assertIs(self.app.game.current_actor, actor)

    def test_reaction_skill_ui_enters_waiting_state(self) -> None:
        actor = self.app.game.current_actor
        actor.skills = (*actor.skills, "pass_cut")
        actor.mana = 3
        holder = self.app.game.characters["20"]
        self.app.game.set_ball_holder(holder.char_id, "test")
        self.choose_action("ball", "skill:pass_cut")
        self.assertEqual(self.app.pending_skill_confirmation, ("pass_cut", actor.char_id))
        self.app.draw()
        confirm = next(button for button in self.app.buttons if button.key == "confirm_skill")
        self.app.handle_pointer(confirm.rect.center)
        self.assertIsNotNone(actor.reaction_skill)
        self.assertEqual(actor.reaction_skill["skill_id"], "pass_cut")
        self.assertEqual(actor.mana, 1)
        self.assertTrue(self.app.input_locked)

    def test_forward_pass_requires_confirmation_then_locks_for_presentation(self) -> None:
        actor = self.app.game.current_actor
        receiver = self.app.game.characters["10"]
        actor.position = (3, 2)
        receiver.position = (5, 2)
        self.app.game.set_ball_holder(actor.char_id, "test")
        ball_candidates = self.app.game.action_candidates(actor.char_id, "ball")
        normal_pass = next(item for item in ball_candidates if item.action_id == "standard:pass")
        expected_name = "物理パス" if actor.primary_system == "physical" else "魔法パス"
        self.assertEqual(normal_pass.name, expected_name)
        self.assertNotIn("standard:cut", {item.action_id for item in ball_candidates})
        self.assertIn("standard:keep", {item.action_id for item in ball_candidates})
        self.choose_action("ball", "standard:pass")
        self.assertEqual(self.app.mode, "pass")
        self.assertEqual(self.app.pending_pass_system, actor.primary_system)
        self.app.handle_pointer(self.app.cell_rect(receiver.position).center)
        self.assertEqual(self.app.pending_pass_target, receiver.char_id)
        self.assertEqual(self.app.game.ball.holder_id, actor.char_id)
        self.app.draw()
        confirm = next(button for button in self.app.buttons if button.key == "confirm_pass")
        self.app.handle_pointer(confirm.rect.center)
        self.assertEqual(self.app.game.ball.holder_id, receiver.char_id)
        self.assertTrue(self.app.input_locked)
        self.drain_presentation()
        self.assertTrue(actor.acted)
        self.assertIsNot(self.app.game.current_actor, actor)

    def test_cut_requires_target_system_and_confirmation_with_shared_preview(self) -> None:
        actor = self.app.game.current_actor
        target = self.app.game.characters["20"]
        actor.position = (3, 2)
        target.position = (4, 2)
        self.app.game.set_ball_holder(target.char_id, "test")
        self.app.draw()
        self.app.handle_button("cut")
        self.app.draw()
        self.app.handle_pointer(self.app.cell_rect(target.position).center)
        self.assertEqual(self.app.pending_cut_target, target.char_id)
        self.app.draw()
        physical = next(button for button in self.app.buttons if button.key == "cut_physical")
        self.app.handle_pointer(physical.rect.center)
        preview = self.app.game.cut_preview(actor.char_id, target.char_id, "physical")
        self.assertEqual(self.app.pending_cut_system, "physical")
        self.app.draw()
        confirm = next(button for button in self.app.buttons if button.key == "confirm_cut")
        self.app.handle_pointer(confirm.rect.center)
        self.assertEqual(self.app.game.last_result.details["success_rate"], preview["success_rate"])
        self.assertTrue(self.app.input_locked)

    def test_attack_after_move_commits_position_and_ends_action(self) -> None:
        actor = self.app.game.current_actor
        target = self.app.game.active_characters("enemy")[0]
        actor.position = (2, 2)
        self.app.game.characters["10"].position = (0, 0)
        self.app.game.characters["11"].position = (0, 4)
        target.position = (5, 2)
        self.app.game.drop_ball((8, 4), "test")
        self.app.game.prepare_move(actor.char_id, (3, 2))
        self.app.game.arrive_prepared_move(actor.char_id)
        self.app.turn_moved = True
        self.choose_action("attack", f"skill:normal_{actor.primary_system}_attack")
        self.app.handle_pointer(self.app.cell_rect(target.position).center)
        self.assertEqual(self.app.pending_skill_confirmation, (f"normal_{actor.primary_system}_attack", target.char_id))
        self.app.draw()
        confirm = next(button for button in self.app.buttons if button.key == "confirm_skill")
        self.app.handle_pointer(confirm.rect.center)
        self.assertTrue(self.app.input_locked)
        self.drain_presentation()
        self.assertEqual(actor.position, (3, 2))
        self.assertTrue(actor.acted)
        self.assertIsNot(self.app.game.current_actor, actor)

    def test_repeated_wait_click_advances_only_once(self) -> None:
        actor = self.app.game.current_actor
        self.app.handle_button("wait")
        self.app.handle_button("wait")
        self.assertTrue(self.app.input_locked)
        self.drain_presentation()
        self.assertTrue(actor.acted)
        self.assertEqual(self.app.game.turn_index, 1)

    def test_full_log_modal_pauses_update_and_can_copy_and_return(self) -> None:
        actor = self.app.game.current_actor
        self.app.draw()
        options = next(button for button in self.app.buttons if button.key == "options")
        self.app.handle_pointer(options.rect.center)
        self.app.draw()
        full_log = next(button for button in self.app.buttons if button.key == "options_full_log")
        self.app.handle_pointer(full_log.rect.center)
        self.assertTrue(self.app.full_log_open)
        self.app.draw()
        with patch("pygame.scrap.get_init", return_value=True), patch("pygame.scrap.put") as put:
            copy_button = next(button for button in self.app.buttons if button.key == "log_copy")
            self.app.handle_pointer(copy_button.rect.center)
            put.assert_called_once()
        self.assertEqual(self.app.log_notice, "ログをコピーしました。")
        self.app.auto_player = True
        self.app.ai_ready_at = 0
        self.app.update()
        self.assertIs(self.app.game.current_actor, actor)
        self.app.draw()
        back = next(button for button in self.app.buttons if button.key == "log_back")
        self.app.handle_pointer(back.rect.center)
        self.assertFalse(self.app.full_log_open)

    def test_loose_ball_move_is_not_applied_until_animation_arrival_and_cancel_restores_it(self) -> None:
        actor = self.app.game.current_actor
        actor.position = (3, 2)
        self.app.game.drop_ball((4, 2), "test")
        self.choose_action("move", "standard:move")
        self.app.handle_pointer(self.app.cell_rect((4, 2)).center)
        self.assertEqual(actor.position, (3, 2))
        self.assertIsNone(self.app.game.ball.holder_id)
        self.drain_presentation()
        self.assertEqual(actor.position, (4, 2))
        self.assertEqual(self.app.game.ball.holder_id, actor.char_id)
        self.app.handle_button("cancel_move")
        self.assertEqual(actor.position, (3, 2))
        self.assertIsNone(self.app.game.ball.holder_id)
        self.assertEqual(self.app.game.ball_position, (4, 2))

    def test_escape_cancel_path_restores_pending_move_to_command_selection(self) -> None:
        actor = self.app.game.current_actor
        origin = actor.position
        destination = sorted(self.app.game.reachable_positions(actor.char_id))[0]
        self.app.game.prepare_move(actor.char_id, destination)
        self.app.game.arrive_prepared_move(actor.char_id)
        self.app.turn_moved = True
        self.app.mode = "attack"

        self.app._cancel_current_selection()
        self.assertEqual(actor.position, destination)
        self.assertIsNone(self.app.mode)
        self.app._cancel_current_selection()
        self.assertEqual(actor.position, origin)
        self.assertFalse(self.app.turn_moved)
        self.assertIsNone(self.app.game.pending_move)

    def test_score_presentation_precedes_manual_restart_holder_selection(self) -> None:
        game = self.app.game
        scorer = game.characters["21"]
        scorer.position = (1, 2)
        game.characters["10"].position = (2, 1)
        game.set_ball_holder(scorer.char_id, "test")
        scored = game.move_character(scorer.char_id, (0, 2))
        self.assertTrue(scored.scored)
        enemy_before = game.characters["20"].position
        self.app._present_result(scored, scorer.char_id, advance_after=False)
        self.assertEqual(game.characters["20"].position, enemy_before)
        self.drain_presentation()
        self.assertTrue(game.restart_prepared)
        self.assertEqual(self.app.mode, "restart_holder")
        self.assertIsNone(game.current_actor)
        selected = game.restart_candidates()[0]
        self.app.handle_pointer(self.app.cell_rect(selected.position).center)
        self.assertEqual(game.ball.holder_id, selected.char_id)
        self.assertIsNotNone(game.current_actor)

    def test_deep_goal_move_animates_once_and_scores_at_selected_destination(self) -> None:
        game = self.app.game
        actor = game.current_actor
        self.assertEqual(actor.team, PLAYER)
        actor.position = (9, 2)
        safe_positions = iter(((3, 0), (3, 4), (6, 0), (6, 2), (6, 4)))
        for character in game.active_characters():
            if character is not actor:
                character.position = next(safe_positions)
        game.set_ball_holder(actor.char_id, "test")

        self.choose_action("move", "standard:move")
        self.app.handle_pointer(self.app.cell_rect((12, 2)).center)
        self.assertEqual(actor.position, (9, 2))
        self.assertEqual(game.scores[PLAYER], 0)

        for _ in range(20):
            if game.scores[PLAYER] == 1:
                break
            self.app.presentation_until = 0
            self.app._update_presentation()
        self.assertEqual(game.scores[PLAYER], 1)
        self.assertEqual(actor.position, (12, 2))
        self.assertFalse(any("visual" in step for step in self.app.presentation_steps))
        self.assertFalse(self.app.command_window_open)
        self.drain_presentation()

    def test_home_opens_quest_select_then_party_setup_with_fixed_quest(self) -> None:
        app = GameApp(start_match=False, seed=7)
        app.draw()
        self.assertTrue(any(button.key == "quest" for button in app.buttons))
        app.handle_pointer((720, 466))
        self.assertEqual(app.state, "quest_select")
        app.draw()
        self.assertTrue(any(button.key == "quest_item:training_3v3" for button in app.buttons))
        self.assertTrue(any(button.key == "quest_confirm" and button.enabled for button in app.buttons))
        confirm = next(button for button in app.buttons if button.key == "quest_confirm")
        app.handle_pointer(confirm.rect.center)
        self.assertEqual(app.state, "party")
        self.assertIsNone(app.game)
        self.assertEqual([char_id for char_id in app.party_player_ids if char_id], ["10", "11", "12"])
        self.assertEqual(app.party_selected_enemy_group_id, "training_3v3_group")
        self.assertEqual(app.party_selected_quest_id, "training_3v3")
        app.draw()
        self.assertTrue(any(button.key == "party_start" and button.enabled for button in app.buttons))
        self.assertTrue(any(button.key.startswith("party_roster:") for button in app.buttons))
        self.assertFalse(any(button.key == "party_enemy_group_open" for button in app.buttons))
        self.assertFalse(any(button.key == "party_add_enemy" for button in app.buttons))

    def test_party_filters_sort_scroll_and_totals_use_roster_data(self) -> None:
        app = GameApp(start_match=False, seed=7)
        self.assertTrue(app.open_party_setup())
        app._handle_party_button("party_class")
        filtered = app._party_filtered_roster()
        self.assertTrue(filtered)
        self.assertTrue(all(member.class_id == app.party_class_filter for member in filtered))
        app.party_class_filter = ""
        app._handle_party_button("party_element")
        filtered = app._party_filtered_roster()
        self.assertTrue(all(member.element_id == app.party_element_filter for member in filtered))
        app.party_element_filter = ""
        app.party_sort_key = "physical"
        values = [member.physical for member in app._party_filtered_roster()]
        self.assertEqual(values, sorted(values, reverse=True))
        app._scroll_party(99)
        self.assertEqual(app.party_scroll, max(0, len(app.party_roster) - 8))
        player_totals = app._party_totals(app.party_player_ids)
        self.assertEqual(
            player_totals["max_hp"],
            sum(app._party_member(char_id).max_hp for char_id in app.party_player_ids if char_id),
        )

    def test_quest_select_scroll_touch_and_party_hides_enemy_group_selector(self) -> None:
        app = GameApp(start_match=False, seed=7)
        self.assertTrue(app.open_quest_select())
        self.assertGreaterEqual(len(app.quest_select_ids), 6)
        app._scroll_quest_select(99)
        self.assertEqual(app.quest_select_scroll, max(0, len(app.quest_select_ids) - 5))
        app._scroll_quest_select(-99)
        self.assertEqual(app.quest_select_scroll, 0)
        app.draw()
        item = next(button for button in app.buttons if button.key == "quest_item:training_1v1")
        app.process_events([pygame.event.Event(
            pygame.FINGERDOWN,
            x=item.rect.centerx / app.screen.get_width(),
            y=item.rect.centery / app.screen.get_height(),
            timestamp=pygame.time.get_ticks(),
        )])
        self.assertEqual(app.quest_selected_id, "training_1v1")
        confirm = next(button for button in app.buttons if button.key == "quest_confirm")
        app.handle_pointer(confirm.rect.center)
        self.assertEqual(app.state, "party")
        self.assertEqual(app.party_selected_quest_id, "training_1v1")
        app.draw()
        self.assertFalse(any(button.key == "party_enemy_group_open" for button in app.buttons))
        self.assertEqual(app.party_selected_enemy_group_id, "training_group")

    def test_quest_party_uses_fixed_group_even_if_group_selector_has_no_candidates(self) -> None:
        with patch("manaball.ui.load_valid_enemy_group_match_inputs", return_value=[]):
            app = GameApp(start_match=False, seed=7)
            self.assertTrue(app.open_party_setup())
        self.assertEqual(app.party_selected_enemy_group_id, "training_3v3_group")
        app.draw()
        start = next(button for button in app.buttons if button.key == "party_start")
        self.assertTrue(start.enabled)
        self.assertFalse(any(button.key == "party_enemy_group_open" for button in app.buttons))
        self.assertTrue(app.open_party_setup(debug_mode=True))
        self.assertTrue(app.party_debug_mode)

    def test_quest_result_restart_party_quest_select_and_home_transitions_reset_match_state(self) -> None:
        app = GameApp(start_match=False, seed=7)
        app.open_party_setup()
        app._handle_party_button("party_start")
        self.assertEqual(app.state, "match")
        old_enemy = app.game.characters["enemy:training_3v3_group:1"]
        safety = 0
        while not app.game.match_over and safety < 300:
            app.game.ai_take_turn()
            safety += 1
        self.assertTrue(app.game.match_over)
        old_enemy.hp = 1
        old_enemy.skill_cooldowns["push_strike"] = 3
        labels: list[str] = []
        original_center_text = app._center_text

        def record(text, *args, **kwargs):
            labels.append(text)
            return original_center_text(text, *args, **kwargs)

        app._center_text = record
        app.draw()
        self.assertTrue(any("通常訓練" in text for text in labels))
        restart = next(button for button in app.buttons if button.key == "restart")
        app.handle_pointer(restart.rect.center)
        new_enemy = app.game.characters["enemy:training_3v3_group:1"]
        self.assertIsNot(new_enemy, old_enemy)
        self.assertEqual(new_enemy.hp, new_enemy.max_hp)
        self.assertEqual(new_enemy.skill_cooldowns, {})
        self.assertEqual(app.active_enemy_group_id, "training_3v3_group")

        app.game.retire(PLAYER)
        app.draw()
        party_button = next(button for button in app.buttons if button.key == "result_party")
        app.handle_pointer(party_button.rect.center)
        self.assertEqual(app.state, "party")
        self.assertEqual(app.party_selected_enemy_group_id, "training_3v3_group")
        self.assertEqual([char_id for char_id in app.party_player_ids if char_id], ["10", "11", "12"])
        app._handle_party_button("party_start")
        app.game.retire(PLAYER)
        app.draw()
        quest_button = next(button for button in app.buttons if button.key == "result_quest_select")
        app.handle_pointer(quest_button.rect.center)
        self.assertEqual(app.state, "quest_select")
        self.assertEqual(app.quest_selected_id, "training_3v3")
        self.assertIsNone(app.game)
        app.open_party_setup(quest_id="training_3v3")
        app._handle_party_button("party_start")
        app.game.retire(PLAYER)
        app.draw()
        home_button = next(button for button in app.buttons if button.key == "title")
        app.handle_pointer(home_button.rect.center)
        self.assertEqual(app.state, "title")
        self.assertIsNone(app.active_quest_id)

    def test_party_duplicate_incomplete_reset_clear_and_selected_start(self) -> None:
        app = GameApp(start_match=False, seed=7)
        app.open_party_setup()
        app._handle_party_button("party_clear")
        self.assertFalse(app._party_is_complete())
        app._handle_party_button("party_start")
        self.assertEqual(app.state, "party")
        self.assertIn("必要 3人", app.party_message)

        player_ids = ("32", "1", "31")
        for char_id in player_ids:
            app.party_selected_character_id = char_id
            app._handle_party_button("party_add_ally")
        app.party_selected_character_id = player_ids[0]
        app._handle_party_button("party_add_ally")
        self.assertIn("重複", app.party_message)
        self.assertTrue(app._party_is_complete())
        app._handle_party_button("party_start")
        self.assertEqual(app.state, "match")
        self.assertEqual(app.active_player_ids, player_ids)
        self.assertIsNone(app.active_enemy_ids)
        self.assertEqual(app.active_enemy_group_id, "training_3v3_group")
        for index, char_id in enumerate(player_ids):
            self.assertEqual(app.game.characters[char_id].initial_position, app.game.config.player_positions[index])
        enemies = sorted(
            (character for character in app.game.characters.values() if character.team == ENEMY),
            key=lambda character: character.enemy_group_slot,
        )
        self.assertEqual([enemy.enemy_master_id for enemy in enemies], [
            "training_power", "training_magic", "training_support", "training_power", "training_magic",
        ])
        for index, enemy in enumerate(enemies):
            self.assertEqual(enemy.initial_position, app.game.config.enemy_positions[index])

        app.state = "party"
        app._handle_party_button("party_default")
        self.assertEqual([char_id for char_id in app.party_player_ids if char_id], ["10", "11", "12"])
        self.assertEqual(app.party_selected_enemy_group_id, "training_3v3_group")

    def test_related_default_enemy_ids_do_not_block_player_party(self) -> None:
        app = GameApp(start_match=False, seed=7)
        self.assertTrue(app.open_party_setup())
        app._handle_party_button("party_clear")
        for char_id in ("20", "21", "22"):
            app.party_selected_character_id = char_id
            app._handle_party_button("party_add_ally")
        self.assertEqual([char_id for char_id in app.party_player_ids if char_id], ["20", "21", "22"])
        self.assertTrue(app._party_is_complete())
        app._handle_party_button("party_start")
        self.assertEqual(app.state, "match")

    def test_party_skill_editor_swaps_resets_and_passes_loadout_to_match(self) -> None:
        app = GameApp(start_match=False, seed=7)
        app.open_party_setup()
        app.party_selected_character_id = "10"
        candidates = app._party_skill_candidates()
        self.assertGreaterEqual(len(candidates), 24)
        self.assertTrue({
            "闘気強化", "衝撃打", "魔力集中", "魔力循環", "力溜め", "押し退け",
            "加速", "鈍足", "精密操作", "ロングパス", "自己回復", "防御姿勢",
        }.issubset({skill.name for skill in candidates}))
        self.assertNotIn("normal_attack", {skill.skill_id for skill in candidates})
        self.assertNotIn("normal_pass", {skill.skill_id for skill in candidates})

        original = app._party_equipped_skills("10")
        app.draw()
        skill_button = next(button for button in app.buttons if button.key == "party_skill_open")
        app.handle_pointer(skill_button.rect.center)
        self.assertTrue(app.party_skill_editor_open)
        app._handle_party_button("party_skill_slot:0")
        app._handle_party_button("party_skill_candidate:heal_hp")
        app._handle_party_button("party_skill_apply")
        swapped = app._party_equipped_skills("10")
        self.assertEqual(swapped[0], "heal_hp")
        self.assertEqual(swapped[1:], original[1:])

        app._handle_party_button(f"party_skill_candidate:{swapped[1]}")
        app._handle_party_button("party_skill_apply")
        self.assertIn("複数", app.party_message)
        self.assertEqual(app._party_equipped_skills("10"), swapped)
        app._handle_party_button("party_skill_reset")
        self.assertEqual(app._party_equipped_skills("10"), original)

        app._handle_party_button("party_skill_candidate:heal_hp")
        app._handle_party_button("party_skill_apply")
        app._handle_party_button("party_skill_close")
        app._handle_party_button("party_start")
        self.assertEqual(app.state, "match")
        self.assertEqual(app.game.characters["10"].skills[0], "heal_hp")
        self.assertEqual(app.active_skill_overrides["10"][0], "heal_hp")
        app.start_new_match()
        self.assertEqual(app.game.characters["10"].skills[0], "heal_hp")

    def test_long_pass_ui_uses_system_target_preview_and_confirmation_flow(self) -> None:
        actor = self.app.game.current_actor
        teammate = next(unit for unit in self.app.game.active_characters(actor.team) if unit is not actor)
        actor.skills = ("single_technique_long_pass",)
        actor.mana = actor.max_mana
        normal_range = self.app.game.pass_range_for(actor, actor.primary_system)
        actor.position = (0, 2)
        teammate.position = (min(8, normal_range + 1), 2)
        self.app.game.set_ball_holder(actor.char_id, "test")
        self.app._start_skill_action(actor, "single_technique_long_pass", "ボール")
        self.assertEqual(self.app.mode, "pass")
        self.assertEqual(self.app.pending_pass_skill_id, "single_technique_long_pass")
        self.assertEqual(self.app.pending_pass_system, actor.primary_system)
        self.assertIn(teammate, self.app._pass_targets(actor.char_id, actor.primary_system))
        self.app.handle_cell(teammate.position)
        self.assertEqual(self.app.pending_pass_target, teammate.char_id)
        preview = self.app._pass_preview(actor.char_id, teammate.char_id, actor.primary_system)
        self.assertEqual(preview["pass_range"], normal_range + 1)

    def test_retire_confirmation_cancels_blocks_background_and_finishes_match(self) -> None:
        actor = self.app.game.current_actor
        origin = actor.position
        self.app.draw()
        options = next(button for button in self.app.buttons if button.key == "options")
        self.app.handle_pointer(options.rect.center)
        self.app.draw()
        retire = next(button for button in self.app.buttons if button.key == "options_retire")
        self.app.handle_pointer(retire.rect.center)
        self.assertTrue(self.app.retire_confirmation)
        self.app.draw()
        self.app.handle_pointer(self.app.cell_rect(origin).center)
        self.assertEqual(actor.position, origin)
        cancel = next(button for button in self.app.buttons if button.key == "retire_cancel")
        self.app.handle_pointer(cancel.rect.center)
        self.assertFalse(self.app.retire_confirmation)
        self.assertFalse(self.app.game.match_over)

        self.app.handle_button("retire")
        self.app.draw()
        confirm = next(button for button in self.app.buttons if button.key == "retire_confirm")
        self.app.handle_pointer(confirm.rect.center)
        self.assertTrue(self.app.game.match_over)
        self.assertEqual(self.app.game.winner, ENEMY)
        self.assertEqual(self.app.game.end_reason, "retire")

    def test_direct_retire_button_is_visible_and_finishes_match(self) -> None:
        self.app.draw()
        retire = next(button for button in self.app.buttons if button.key == "retire")
        self.assertTrue(retire.enabled)
        self.assertTrue(self.app.screen.get_rect().contains(retire.rect))

        self.app.handle_pointer(retire.rect.center)
        self.assertTrue(self.app.retire_confirmation)
        self.app.draw()
        confirm = next(button for button in self.app.buttons if button.key == "retire_confirm")
        self.app.handle_pointer(confirm.rect.center)

        self.assertTrue(self.app.game.match_over)
        self.assertEqual(self.app.game.end_reason, "retire")

    def test_free_match_settings_start_temporary_asymmetric_manual_match(self) -> None:
        app = GameApp(start_match=False, seed=7)
        master = {member.char_id: member for member in load_character_roster()}
        self.assertTrue(app.open_party_setup(debug_mode=True))
        self.assertTrue(app.party_debug_mode)
        self.assertEqual(app.party_free_enemy_mode, "group")
        app.draw()
        self.assertTrue(any(button.key == "party_add_enemy" for button in app.buttons))
        self.assertTrue(any(button.key == "party_enemy_group_open" for button in app.buttons))
        app._handle_party_button("party_free_enemy_mode")
        self.assertEqual(app.party_free_enemy_mode, "manual")
        app.party_selected_character_id = app.party_player_ids[0]
        app.party_selected_slot = (PLAYER, 0)
        app.draw()
        debug_button = next(button for button in app.buttons if button.key == "party_debug_open")
        app.handle_pointer(debug_button.rect.center)
        self.assertTrue(app.party_debug_settings_open)
        app.draw()
        self.assertTrue(any(button.key.startswith("party_debug_param:") for button in app.buttons))
        self.assertTrue(any(button.key.startswith("party_debug_match:") for button in app.buttons))
        app._handle_party_button("party_debug_count:player:-1")
        app._handle_party_button("party_debug_count:enemy:-1")
        app._handle_party_button("party_debug_count:enemy:-1")
        app._handle_party_button("party_debug_param:physical:1")
        app._handle_party_button("party_debug_param:stamina:-1")
        app._handle_party_button("party_debug_match:score:1")
        app._handle_party_button("party_debug_match:turn:-1")
        app._handle_party_button("party_debug_position:x:1")
        app._handle_party_button("party_debug_toggle:substitution")
        app._handle_party_button("party_debug_toggle:injury")
        selected_id = app.party_player_ids[0]
        expected_physical = min(10, master[selected_id].physical + 1)
        expected_stamina = max(1, master[selected_id].stamina - 1)
        app._handle_party_button("party_debug_close")
        app._handle_party_button("party_start")
        self.assertEqual(app.state, "match")
        self.assertTrue(app.game.config.debug_mode)
        self.assertEqual((app.game.config.player_team_size, app.game.config.enemy_team_size), (2, 1))
        self.assertEqual((app.game.config.player_party_limit, app.game.config.enemy_party_limit), (3, 3))
        self.assertEqual(app.game.config.target_score, 3)
        self.assertEqual(app.game.config.turn_limit, 7)
        self.assertTrue(app.game.config.substitution_enabled)
        self.assertTrue(app.game.config.injury_enabled)
        self.assertEqual(app.game.characters[selected_id].physical, expected_physical)
        self.assertEqual(app.game.characters[selected_id].stamina, expected_stamina)
        self.assertEqual(app.game.characters[selected_id].position, (4, 1))
        self.assertEqual(app.game.characters[selected_id].ai_profile_id, "")
        self.assertEqual(load_character_roster()[0], master[load_character_roster()[0].char_id])
        app.handle_button("retire")
        app.draw()
        confirm = next(button for button in app.buttons if button.key == "retire_confirm")
        app.handle_pointer(confirm.rect.center)
        self.assertEqual(app.game.end_reason, "debug_interrupt")
        self.assertIsNone(app.game.winner)

    def test_free_match_group_mode_uses_enemy_group_without_manual_enemy_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            save_path = Path(directory) / "debug_match_last.json"
            with patch("manaball.debug_save.DEFAULT_PATH", save_path):
                app = GameApp(start_match=False, seed=7)
                self.assertTrue(app.open_party_setup(debug_mode=True))
                self.assertEqual(app.party_free_enemy_mode, "group")
                app.party_enemy_ids = [None] * len(app.party_enemy_ids)
                self.assertTrue(app._party_is_complete())
                app._handle_party_button("party_start")
                self.assertEqual(app.state, "match")
                self.assertIsNone(app.active_enemy_ids)
                self.assertEqual(app.active_enemy_group_id, "training_group")
                self.assertTrue(app.active_debug_mode)
                enemies = [character for character in app.game.characters.values() if character.team == ENEMY]
                self.assertTrue(all(character.enemy_group_id == "training_group" for character in enemies))

    def test_free_match_settings_save_reload_auto_load_and_corrupt_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            save_path = Path(directory) / "debug_match_last.json"
            with patch("manaball.debug_save.DEFAULT_PATH", save_path):
                app = GameApp(start_match=False, seed=7)
                self.assertTrue(app.open_party_setup(debug_mode=True))
                app._handle_party_button("party_free_enemy_mode")
                shared_id = app.party_player_ids[0]
                app.party_enemy_ids[0] = shared_id
                app.party_selected_slot = (PLAYER, 0)
                app.party_selected_character_id = shared_id
                app._handle_party_button("party_debug_param:physical:1")
                app._handle_party_button("party_debug_match:score:1")
                app._handle_party_button("party_debug_position:x:1")
                app._handle_party_button("party_debug_party_count:player:1")
                app._handle_party_button("party_debug_save")

                document = json.loads(save_path.read_text(encoding="utf-8"))
                self.assertEqual(document["format_version"], 2)
                self.assertEqual(document["enemy_formation_mode"], "manual")
                self.assertEqual(document["player_party_size"], 4)
                self.assertTrue(document["saved_at"])
                self.assertEqual(document["player_slots"][0]["base_character_id"], shared_id)
                self.assertEqual(document["enemy_slots"][0]["base_character_id"], shared_id)

                app.party_debug_target_score = 9
                app._handle_party_button("party_debug_reload")
                self.assertEqual(app.party_debug_target_score, 3)
                self.assertEqual(app.party_enemy_ids[0], shared_id)
                self.assertEqual(app.party_free_enemy_mode, "manual")

                restored = GameApp(start_match=False, seed=7)
                self.assertTrue(restored.open_party_setup(debug_mode=True))
                self.assertEqual(restored.party_debug_target_score, 3)
                self.assertEqual(restored.party_debug_player_party_count, 4)
                self.assertIn("前回", restored.party_message)
                restored.party_debug_target_score = 4
                restored._handle_party_button("party_start")
                self.assertEqual(restored.state, "match")
                self.assertEqual(
                    json.loads(save_path.read_text(encoding="utf-8"))["match_settings"]["score_to_win"],
                    4,
                )

                save_path.write_text("{broken", encoding="utf-8")
                fallback = GameApp(start_match=False, seed=7)
                self.assertTrue(fallback.open_party_setup(debug_mode=True))
                self.assertIn("初期値", fallback.party_message)

                legacy = {
                    "format_version": 1,
                    "player_team_size": 3,
                    "enemy_team_size": 3,
                    "player_slots": document["player_slots"][:3],
                    "enemy_slots": document["enemy_slots"][:3],
                    "match_settings": {"score_to_win": 2, "turn_limit": 8},
                }
                save_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
                legacy_app = GameApp(start_match=False, seed=7)
                self.assertTrue(legacy_app.open_party_setup(debug_mode=True))
                self.assertEqual(legacy_app.party_free_enemy_mode, "manual")

    def test_manual_actor_is_selected_and_commands_open_automatically(self) -> None:
        actor = self.app.game.current_actor
        self.assertEqual(self.app.selected_id, actor.char_id)
        self.assertTrue(self.app.command_window_open)
        self.assertEqual(self.app.command_window_actor_id, actor.char_id)

        self.app.handle_button("auto")
        self.assertFalse(self.app.command_window_open)

        self.app.handle_button("auto")
        self.app.update()
        self.assertTrue(self.app.command_window_open)

    def test_match_character_info_draws_only_hp_and_mp_resource_bars(self) -> None:
        labels: list[str] = []
        original = self.app._draw_resource_bar

        def record(label, *args, **kwargs):
            labels.append(label)
            return original(label, *args, **kwargs)

        self.app._draw_resource_bar = record
        self.app._draw_character_info(self.app.game.current_actor)
        self.assertEqual(labels, ["HP", "MP"])

    def test_party_has_no_player_ai_settings_and_enemy_profile_still_applies(self) -> None:
        app = GameApp(start_match=False, seed=7)
        app.open_party_setup()
        app.party_selected_character_id = "10"
        app.draw()
        self.assertFalse(any(button.key.startswith("party_ai_") for button in app.buttons))
        self.assertFalse(any(button.key.startswith("party_debug_player_ai:") for button in app.buttons))
        app._handle_party_button("party_start")
        self.assertEqual(app.state, "match")
        self.assertEqual(app.game.characters["10"].ai_profile_id, "")
        self.assertEqual(app.game.characters["10"].ai_settings["attack_aggression"], 50)
        self.assertEqual(app.game.characters["enemy:training_3v3_group:1"].ai_profile_id, "attack")
        self.assertEqual(app.game.characters["enemy:training_3v3_group:2"].ai_profile_id, "defense")

    def test_steal_failure_judgement_draws_without_effect_none_fallback(self) -> None:
        details = {
            "action_name": "スティール", "skill_id": "steal", "skill_success": False,
            "actor_name": "ゼフィリア", "target_name": "訓練兵", "actor_value": 8,
            "target_value": 6, "ability_difference": 2, "base_rate": 55,
            "ability_rate_bonus": 10, "success_rate": 65, "random_roll": 81,
            "holder_before": "20", "holder_after": "20", "effect_results": [],
        }
        drawn: list[str] = []
        original = self.app._draw_wrapped
        self.app._draw_wrapped = lambda text, *args, **kwargs: drawn.append(text)
        try:
            self.app._draw_judgement_panel(details)
        finally:
            self.app._draw_wrapped = original
        self.assertTrue(any("スティール失敗" in text for text in drawn))
        self.assertFalse(any("効果なし" in text for text in drawn))


if __name__ == "__main__":
    unittest.main()

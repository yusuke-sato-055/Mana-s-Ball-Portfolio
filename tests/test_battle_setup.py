from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from manaball.battle_setup import load_battle_setup, load_field_layouts, load_field_settings, load_quests
from manaball.core import ENEMY, PLAYER
from manaball.data import create_match, load_enemy_groups
from manaball.ui import GameApp


class BattleSetupTests(unittest.TestCase):
    def test_quest_rule_data_and_initial_ball_holders(self) -> None:
        purification = create_match(seed=1, quest_id="training_3v3")
        recapture = create_match(seed=1, quest_id="training_recapture")
        ritual_three = create_match(seed=1, quest_id="training_ritual_3")
        ritual_five = create_match(seed=1, quest_id="training_ritual_5")

        self.assertEqual(purification.config.rule_type, 1)
        self.assertEqual(purification.ball_team, "player")
        self.assertEqual(recapture.config.rule_type, 2)
        self.assertEqual(recapture.ball_team, "enemy")
        self.assertEqual(ritual_three.config.required_hold_turns, 3)
        self.assertEqual(ritual_five.config.required_hold_turns, 5)
        self.assertEqual(ritual_three.ball_team, "player")

    def test_legacy_quest_csv_without_rule_columns_defaults_to_purification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quests.csv"
            path.write_text(
                "quest_id,quest_name,field_id,match_setting_id,enemy_group_id,enabled\n"
                "legacy,旧クエスト,standard,standard_3v3,training_3v3_group,true\n",
                encoding="utf-8",
            )
            quest = load_quests(path)["legacy"]
        self.assertEqual(quest.rule_type, 1)
        self.assertEqual(quest.required_hold_turns, 0)

    def test_recapture_finishes_only_when_player_obtains_ball(self) -> None:
        game = create_match(seed=1, quest_id="training_recapture")
        enemy_holder = game.ball.holder_id
        self.assertIsNotNone(enemy_holder)
        game.drop_ball(game.ball_position, "test_drop")
        self.assertFalse(game.match_over)
        other_enemy = next(unit for unit in game.active_characters("enemy") if unit.char_id != enemy_holder)
        game.set_ball_holder(other_enemy.char_id, "enemy_pass")
        self.assertFalse(game.match_over)
        player = game.active_characters("player")[0]
        game.set_ball_holder(player.char_id, "test_pickup")
        self.assertTrue(game.match_over)
        self.assertEqual(game.winner, "player")

    def test_ritual_counts_once_per_round_and_resets_on_loss(self) -> None:
        game = create_match(seed=1, quest_id="training_ritual_3")

        while game.round == 1:
            actor = game.current_actor
            self.assertIsNotNone(actor)
            game.advance_turn(actor.char_id)
        self.assertEqual(game.ritual_hold_turns, 1)

        enemy = game.active_characters("enemy")[0]
        game.set_ball_holder(enemy.char_id, "test_steal")
        self.assertEqual(game.ritual_hold_turns, 0)
        game.drop_ball(enemy.position, "test_drop")
        self.assertEqual(game.ritual_hold_turns, 0)
        player = game.active_characters("player")[1]
        game.set_ball_holder(player.char_id, "test_recover")
        while game.round == 2:
            actor = game.current_actor
            self.assertIsNotNone(actor)
            game.advance_turn(actor.char_id)
        self.assertEqual(game.ritual_hold_turns, 1)

    def test_ritual_completes_at_configured_round_count(self) -> None:
        for quest_id, required in (("training_ritual_3", 3), ("training_ritual_5", 5)):
            game = create_match(seed=1, quest_id=quest_id)
            while not game.match_over:
                actor = game.current_actor
                self.assertIsNotNone(actor)
                game.advance_turn(actor.char_id)
            self.assertEqual(game.ritual_hold_turns, required)
            self.assertEqual(game.winner, "player")

    def test_fields_reuse_layout_and_quests_reuse_match_setting(self) -> None:
        fields = load_field_settings()
        quests = load_quests()
        self.assertEqual(fields["standard"].layout_id, fields["standard_alt"].layout_id)
        self.assertEqual(quests["training_3v3"].match_setting_id, quests["training_alt"].match_setting_id)

    def test_quest_builds_asymmetric_and_large_matches(self) -> None:
        one = create_match(seed=1, quest_id="training_1v1")
        asymmetric = create_match(seed=1, quest_id="training_1v3")
        large = create_match(
            seed=1,
            quest_id="training_4v4",
            player_ids=("1", "2", "3", "4"),
        )
        six = create_match(
            seed=1,
            quest_id="training_6v6",
            player_ids=("1", "2", "3", "4", "5", "10"),
        )
        five_with_bench = create_match(
            seed=1,
            quest_id="training_5v5",
            player_ids=("1", "2", "3", "4", "5", "10", "11"),
        )
        self.assertEqual((len(one.active_characters(PLAYER)), len(one.active_characters(ENEMY))), (1, 1))
        self.assertEqual((len(asymmetric.active_characters(PLAYER)), len(asymmetric.active_characters(ENEMY))), (1, 3))
        self.assertEqual((len(large.active_characters(PLAYER)), len(large.active_characters(ENEMY))), (4, 4))
        self.assertEqual((len(six.active_characters(PLAYER)), len(six.active_characters(ENEMY))), (6, 6))
        self.assertEqual((len(five_with_bench.active_characters(PLAYER)), len(five_with_bench.active_characters(ENEMY))), (5, 5))
        self.assertEqual((len(five_with_bench.bench_characters(PLAYER)), len(five_with_bench.bench_characters(ENEMY))), (2, 2))
        self.assertEqual((five_with_bench.config.field_width, five_with_bench.config.field_height), (15, 7))

    def test_quest_party_limits_and_layouts_are_separate_from_field_counts(self) -> None:
        two = create_match(seed=1, quest_id="training_2v2", player_ids=("1", "2", "3", "4"))
        three = create_match(seed=1, quest_id="training_3v3", player_ids=("1", "2", "3", "4", "5"))
        self.assertEqual((len(two.active_characters(PLAYER)), len(two.bench_characters(PLAYER))), (2, 2))
        self.assertEqual((len(two.active_characters(ENEMY)), len(two.bench_characters(ENEMY))), (2, 2))
        self.assertEqual((two.config.field_width, two.config.field_height), (11, 5))
        self.assertEqual((len(three.active_characters(PLAYER)), len(three.bench_characters(PLAYER))), (3, 2))
        self.assertEqual((len(three.active_characters(ENEMY)), len(three.bench_characters(ENEMY))), (3, 2))
        self.assertEqual((three.config.field_width, three.config.field_height), (13, 5))
        with self.assertRaisesRegex(ValueError, "パーティー人数上限"):
            create_match(seed=1, quest_id="training_5v5", player_ids=("1", "2", "3", "4", "5", "10", "11", "12"))
        with self.assertRaisesRegex(ValueError, "5 人以上"):
            create_match(seed=1, quest_id="training_5v5", player_ids=("1", "2", "3", "4"))

    def test_enemy_groups_accept_seven_slots_and_keep_old_groups_compatible(self) -> None:
        groups = load_enemy_groups()
        self.assertEqual(len(groups["training_group"].enemy_ids), 3)
        self.assertEqual(len(groups["training_large"].enemy_ids), 6)
        self.assertEqual(len(groups["training_5v5_group"].enemy_ids), 7)
        game = create_match(seed=2, quest_id="training_5v5", player_ids=("1", "2", "3", "4", "5", "10", "11"))
        enemies = [character for character in game.characters.values() if character.team == ENEMY]
        self.assertEqual(len(enemies), 7)
        self.assertEqual(len({enemy.char_id for enemy in enemies}), 7)

    def test_five_vs_five_report_start_snapshot_contains_all_fourteen_participants(self) -> None:
        game = create_match(seed=3, quest_id="training_5v5", player_ids=("1", "2", "3", "4", "5", "10", "11"))
        participants = game.report.start_snapshot["characters"]
        self.assertEqual(len(participants), 14)
        self.assertEqual(
            len([participant for participant in participants if participant["starting_role"] == "bench"]),
            4,
        )

    def test_free_match_generation_supports_seven_party_group_and_manual_modes(self) -> None:
        layout = load_field_layouts()["large_15x7"]
        common = {
            "debug_mode": True,
            "field_width": layout.width,
            "field_height": layout.height,
            "left_goal_x_start": 0,
            "left_goal_x_end": 2,
            "right_goal_x_start": 12,
            "right_goal_x_end": 14,
            "goal_y_start": 2,
            "goal_y_end": 4,
            "player_team_size": 5,
            "enemy_team_size": 5,
            "player_party_limit": 7,
            "enemy_party_limit": 7,
            "player_positions": layout.ally_start_cells,
            "enemy_positions": layout.enemy_start_cells,
            "ball_position": layout.ball_start_cell,
        }
        player_ids = ("1", "2", "3", "4", "5", "10", "11")
        group = create_match(seed=4, player_ids=player_ids, enemy_group_id="training_5v5_group", config_overrides=common)
        self.assertEqual((len(group.active_characters(PLAYER)), len(group.bench_characters(PLAYER))), (5, 2))
        self.assertEqual((len(group.active_characters(ENEMY)), len(group.bench_characters(ENEMY))), (5, 2))
        manual = create_match(
            seed=4,
            player_ids=player_ids,
            enemy_ids=("20", "21", "22", "30", "31", "32", "12"),
            config_overrides=common,
        )
        self.assertEqual((len(manual.active_characters(ENEMY)), len(manual.bench_characters(ENEMY))), (5, 2))

    def test_bench_substitution_injury_recovery_and_forced_restart_order(self) -> None:
        game = create_match(
            seed=2,
            quest_id="training_3v3",
            player_ids=("1", "2", "3", "4"),
        )
        outgoing = game.field_characters(PLAYER)[0]
        incoming = game.bench_characters(PLAYER)[0]
        game._knockout(outgoing)
        self.assertEqual((outgoing.injury_markers, outgoing.injury_markers_gained), (1, 1))
        self.assertEqual(game.injury_multiplier(outgoing), 0.8)
        game.restart_team = ENEMY
        game.prepare_restart_after_goal()
        self.assertTrue(game.substitute(PLAYER, outgoing.char_id, incoming.char_id).success)
        self.assertEqual(outgoing.injury_markers, 1)
        game.select_restart_holder(game.restart_candidates()[0].char_id)
        game.restart_team = PLAYER
        game.restart_prepared = False
        game.prepare_restart_after_goal()
        self.assertEqual(outgoing.injury_markers, 1)
        self.assertEqual(outgoing.injury_rate, 20)
        selected = game.restart_candidates()[-1]
        game.select_restart_holder(selected.char_id)
        self.assertEqual(game.turn_order[0], selected.char_id)

    def test_cost_limit_rejects_before_match_creation(self) -> None:
        with self.assertRaisesRegex(ValueError, "総コスト"):
            create_match(config_overrides={"player_cost_limit": 2})

    def test_missing_quest_reports_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing"):
            load_battle_setup("missing")

    def test_normal_party_screen_can_start_4v4_and_3v3_with_bench(self) -> None:
        app = GameApp(start_match=False, seed=3)
        self.assertTrue(app.open_party_setup(quest_id="training_4v4"))
        app.draw()
        self.assertFalse(any(button.key == "party_quest_cycle" for button in app.buttons))
        self.assertFalse(any(button.key == "party_enemy_group_open" for button in app.buttons))
        self.assertTrue(app._party_is_complete())
        app._handle_party_button("party_start")
        self.assertEqual((len(app.game.active_characters(PLAYER)), len(app.game.active_characters(ENEMY))), (4, 4))

        self.assertTrue(app.open_party_setup(quest_id="training_6v6"))
        app.draw()
        app._handle_party_button("party_start")
        self.assertEqual((len(app.game.active_characters(PLAYER)), len(app.game.active_characters(ENEMY))), (6, 6))

        self.assertTrue(app.open_party_setup())
        extra = next(member.char_id for member in app.party_roster if member.char_id not in app.party_player_ids)
        app.party_selected_character_id = extra
        app._handle_party_button("party_add_ally")
        self.assertEqual(len([char_id for char_id in app.party_player_ids if char_id]), 4)
        app._handle_party_button("party_start")
        self.assertEqual(len(app.game.active_characters(PLAYER)), 3)
        self.assertEqual(len(app.game.bench_characters(PLAYER)), 1)
        app.game.restart_team = ENEMY
        app.game.restart_prepared = False
        app._begin_restart_after_score()
        self.assertEqual(app.mode, "substitution_out")
        app.draw()
        self.assertTrue(any(button.key == "substitution_skip" for button in app.buttons))


if __name__ == "__main__":
    unittest.main()

import unittest
from dataclasses import replace

from manaball.data import create_match, load_ai_profiles


class AIProfilesV2Tests(unittest.TestCase):
    def test_new_profiles_have_twelve_bounded_values(self):
        profiles = load_ai_profiles()
        self.assertEqual(len(profiles["standard"].values), 12)
        self.assertTrue(all(0 <= value <= 10 for profile in profiles.values() for value in profile.values.values()))

    def test_group_profile_and_level_precedence_reaches_enemy(self):
        game = create_match(seed=7, enemy_group_id="training_group")
        enemies = sorted((item for item in game.characters.values() if item.team == "enemy"), key=lambda item: item.enemy_group_slot)
        self.assertEqual((enemies[0].ai_profile_id, enemies[0].ai_level), ("attack", 7))
        self.assertEqual((enemies[1].ai_profile_id, enemies[1].ai_level), ("defense", 5))

    def test_zero_attack_and_mp_values_filter_legal_candidates(self):
        game = create_match(seed=11)
        actor = next(item for item in game.characters.values() if item.team == "enemy")
        actor.ai_settings = {**actor.ai_settings, "attack_priority": 0, "mp_usage": 0}
        plans = game._build_ai_plans(actor)
        self.assertFalse(any(item.main_ai_key == "attack_priority" for item in plans))
        self.assertFalse(any(item.skill_id and game.skills[item.skill_id].resource_cost > 0 for item in plans))

    def test_level_ten_selects_highest_evaluation(self):
        game = create_match(seed=13)
        actor = game.current_actor
        self.assertIsNotNone(actor)
        actor.ai_level = 10
        plans = game._build_ai_plans(actor)
        result = game.ai_take_turn(advance=False)
        self.assertTrue(result.success)
        self.assertEqual(result.details.get("ai_selection_pool_size"), 1)

    def test_wait_candidate_has_no_profile_mp_or_risk_adjustment(self):
        game = create_match(seed=17)
        actor = game.current_actor
        actor.ai_settings = {key: 10 for key in actor.ai_settings}
        wait = next(item for item in game._build_ai_plans(actor) if item.action_type == "wait")
        self.assertEqual((wait.profile_adjustment, wait.mp_adjustment, wait.risk_adjustment), (0.0, 0.0, 0.0))
        self.assertEqual(wait.base_score, wait.score)

    def test_markdown_formats_ai_candidates_as_table_not_python_data(self):
        game = create_match(seed=19)
        game.current_actor.ai_level = 10
        game.ai_take_turn(advance=False)
        text = game.report._build_markdown(game, "ai-format")
        self.assertIn("### 敵AI設定", text)
        self.assertIn("| 順位 | 移動目的 | 移動先 |", text)
        self.assertNotIn("ai_candidates=", text)

    def test_player_auto_profiles_are_resolved_from_roles(self):
        game = create_match(seed=23)
        players = [item for item in game.characters.values() if item.team == "player"]
        self.assertTrue(all(item.ai_profile_id in game.ai_profiles for item in players))
        self.assertTrue(all(item.ai_profile_name and item.ai_profile_source == "role_mapping" for item in players))
        self.assertTrue(any(item.ai_settings != game.ai_profiles["standard"].values for item in players))

    def test_each_team_has_one_recovery_actor_and_recovery_move_beats_unrelated_attack(self):
        game = create_match(seed=29)
        recovery_id = game._resolve_loose_ball_recovery("player")
        self.assertTrue(recovery_id)
        actor = game.characters[recovery_id]
        plans = game._build_ai_plans(actor)
        recovery = [item for item in plans if item.team_ball_role == "recovery" and item.action_type == "move"]
        unrelated = [item for item in plans if item.main_ai_key == "attack_priority" and item.target_id != game.ball.holder_id]
        self.assertTrue(recovery)
        if unrelated:
            self.assertGreater(max(item.score for item in recovery), max(item.score for item in unrelated))
        self.assertEqual(sum(item.char_id == game.loose_ball_recovery_actor_id["player"] for item in game.active_characters("player")), 1)

    def test_decision_ball_state_and_context_adjustment_are_reported(self):
        game = create_match(seed=31)
        actor = game.current_actor
        game.ai_take_turn(advance=False)
        details = next(record for record in reversed(game.report.action_records) if record.get("record_type") == "character_action")["result"]["details"]
        self.assertEqual(details["ai_ball_state"], "loose_ball")
        self.assertEqual(details["ai_profile_id"], actor.ai_profile_id)
        self.assertIn("ball_context_adjustment", details["ai_candidates"][0])

    def test_recovery_approach_bonus_is_not_copied_to_mp_recovery_action(self):
        game = create_match(seed=41, enemy_group_id="training_group")
        actor = game.characters["enemy:training_group:2"]
        for unit in game.active_characters("enemy"):
            unit.ai_settings["loose_ball_priority"] = 0
        actor.ai_settings["loose_ball_priority"] = 10
        game.ball.loose_position = (2, 4)
        self.assertEqual(game._resolve_loose_ball_recovery("enemy"), actor.char_id)
        plans = [
            item
            for item in game._build_ai_plans(actor)
            if item.purpose == "mp_support"
            and item.destination != actor.position
            and item.movement_context_adjustment > 0
        ]
        self.assertTrue(plans)
        self.assertTrue(all(item.movement_context_adjustment >= 120 for item in plans))
        self.assertTrue(all(item.main_action_context_adjustment == 0 for item in plans))
        self.assertTrue(all(item.main_action_purpose == "mp_support" for item in plans))

    def test_recovery_path_clear_attack_uses_small_main_action_bonus(self):
        game = create_match(seed=43, enemy_group_id="training_group")
        actor = game.characters["enemy:training_group:1"]
        for unit in game.active_characters("enemy"):
            unit.ai_settings["loose_ball_priority"] = 0
        actor.ai_settings["loose_ball_priority"] = 10
        actor.position = (8, 2)
        game.ball.loose_position = (3, 2)
        target = game.characters["10"]
        target.position = (6, 2)
        target.hp = 1
        game._resolve_loose_ball_recovery("enemy")
        clear_plans = [item for item in game._build_ai_plans(actor) if item.target_id == target.char_id and item.is_recovery_path_clear_action]
        self.assertTrue(clear_plans)
        self.assertTrue(all(item.main_action_context_adjustment == 20 for item in clear_plans))
        self.assertTrue(all(item.main_action_context_adjustment < item.movement_context_adjustment or item.movement_context_adjustment == 0 for item in clear_plans))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import csv
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from manaball.core import ENEMY, PLAYER, SkillEffect, manhattan, straight_line_cells
from manaball.data import (
    CSV_ROOT, build_enemy_group_match_inputs, create_enemy_team, create_match, load_characters,
    load_classes, load_config, load_ai_profiles, load_character_roster, load_elements,
    load_enemy_groups, load_enemy_masters, load_skill_effects, load_skills,
    load_valid_enemy_group_match_inputs,
)


class StubRandom:
    def __init__(self, rolls: list[int] | None = None) -> None:
        self.rolls = list(rolls or [])

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.rolls.pop(0) if self.rolls else minimum
        return max(minimum, min(maximum, value))

    def random(self) -> float:
        return 0.5


def fresh(rolls: list[int] | None = None):
    game = create_match(seed=3)
    game.rng = StubRandom(rolls)
    return game


def position_for_action(game, mapping: dict[str, tuple[int, int]]) -> None:
    defaults = {
        "10": (0, 0),
        "11": (0, 2),
        "12": (0, 4),
        "20": (8, 0),
        "21": (8, 2),
        "22": (8, 4),
    }
    defaults.update(mapping)
    assert len(set(defaults.values())) == len(defaults), defaults
    for char_id, position in defaults.items():
        character = game.characters[char_id]
        character.position = position
        character.off_field = False


class MatchRuleTests(unittest.TestCase):
    def test_enemy_master_and_group_csv_load_with_three_skill_slots(self) -> None:
        config = load_config()
        skills = load_skills(config=config)
        profiles = load_ai_profiles()
        masters = load_enemy_masters(
            config=config,
            classes=load_classes(),
            elements=load_elements(),
            skills=skills,
            ai_profiles=profiles,
        )
        groups = load_enemy_groups(ai_profiles=profiles)

        self.assertGreaterEqual(len(masters), 3)
        self.assertIn("training_group", groups)
        self.assertEqual(config.default_enemy_group_id, "training_group")
        self.assertEqual(
            [item.group.enemy_group_id for item in load_valid_enemy_group_match_inputs(
                config=config,
                classes=load_classes(),
                elements=load_elements(),
                skills=skills,
                ai_profiles=profiles,
            )],
            ["training_group", "training_2v2_group", "training_3v3_group", "training_5v5_group"],
        )
        self.assertTrue(all(not master.validation_errors for master in masters.values()))
        self.assertEqual(len(masters["training_power"].skills), 3)
        self.assertEqual(len(create_enemy_team("training_group", seed=2)), 3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "enemies.csv"
            with (CSV_ROOT / "enemies.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["skill_slot_2"] = ""
            rows[0]["skill_slot_3"] = ""
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            sparse = load_enemy_masters(
                path, config=config, classes=load_classes(), elements=load_elements(),
                skills=skills, ai_profiles=profiles,
            )
        self.assertEqual(sparse["training_power"].skills, ("push_strike",))

    def test_enemy_group_match_applies_scale_ai_precedence_and_metadata(self) -> None:
        baseline = load_enemy_masters()["training_power"]
        with tempfile.TemporaryDirectory() as directory:
            group_path = Path(directory) / "enemy_groups.csv"
            with (CSV_ROOT / "enemy_groups.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["enemy_scale"] = "1.2"
            with group_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            game = create_match(seed=4, enemy_group_id="training_group", enemy_group_path=group_path)

        enemies = sorted(
            (character for character in game.characters.values() if character.team == ENEMY),
            key=lambda character: character.enemy_group_slot,
        )
        self.assertEqual([enemy.char_id for enemy in enemies], [
            "enemy:training_group:1", "enemy:training_group:2", "enemy:training_group:3",
        ])
        self.assertEqual(enemies[0].max_hp, 38)
        self.assertEqual(enemies[0].physical, 10)
        self.assertEqual(enemies[0].ai_profile_id, "attack")
        self.assertEqual(enemies[1].ai_profile_id, "defense")
        self.assertEqual(enemies[2].ai_profile_id, "defense")
        self.assertEqual(enemies[0].enemy_master_id, "training_power")
        self.assertEqual(enemies[0].enemy_scale, 1.2)
        self.assertEqual(baseline.max_hp, 32)

    def test_duplicate_enemy_master_slots_create_independent_characters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            group_path = Path(directory) / "enemy_groups.csv"
            fieldnames = [
                "enemy_group_id", "enemy_group_name", "enemy_1_id", "enemy_2_id", "enemy_3_id",
                "enemy_scale", "group_ai_profile_id", "enemy_1_ai_profile_id",
                "enemy_2_ai_profile_id", "enemy_3_ai_profile_id", "display_order", "enabled", "description",
            ]
            with group_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow({
                    "enemy_group_id": "duplicates", "enemy_group_name": "重複確認",
                    "enemy_1_id": "training_power", "enemy_2_id": "training_power",
                    "enemy_3_id": "training_power", "enemy_scale": "1.0", "enabled": "true",
                })
            team = create_match(seed=5, enemy_group_id="duplicates", enemy_group_path=group_path)
        enemies = [character for character in team.characters.values() if character.team == ENEMY]
        self.assertEqual(len({enemy.char_id for enemy in enemies}), 3)
        self.assertEqual({enemy.enemy_master_id for enemy in enemies}, {"training_power"})
        enemies[0].hp -= 5
        enemies[0].skill_cooldowns["push_strike"] = 2
        self.assertNotEqual(enemies[0].hp, enemies[1].hp)
        self.assertNotIn("push_strike", enemies[1].skill_cooldowns)

    def test_enemy_group_rejects_invalid_scale_enemy_and_skill(self) -> None:
        config = load_config()
        classes = load_classes()
        elements = load_elements()
        profiles = load_ai_profiles()
        skills = load_skills(config=config)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group_path = root / "groups.csv"
            group_path.write_text(
                "enemy_group_id,enemy_group_name,enemy_1_id,enemy_2_id,enemy_3_id,enemy_scale,enabled\n"
                "bad,不正,missing,training_magic,training_support,1.0,true\n",
                encoding="utf-8-sig",
            )
            with self.assertRaisesRegex(ValueError, "missing"):
                build_enemy_group_match_inputs(
                    "bad", config=config, classes=classes, elements=elements, skills=skills,
                    ai_profiles=profiles, group_path=group_path,
                )
            group_path.write_text(
                "enemy_group_id,enemy_group_name,enemy_1_id,enemy_2_id,enemy_3_id,enemy_scale,enabled\n"
                "bad,不正,training_power,training_magic,training_support,0,true\n",
                encoding="utf-8-sig",
            )
            with self.assertRaisesRegex(ValueError, "enemy_scale"):
                build_enemy_group_match_inputs(
                    "bad", config=config, classes=classes, elements=elements, skills=skills,
                    ai_profiles=profiles, group_path=group_path,
                )
            enemy_path = root / "enemies.csv"
            with (CSV_ROOT / "enemies.csv").open(encoding="utf-8-sig", newline="") as handle:
                enemy_rows = list(csv.DictReader(handle))
            enemy_rows[0]["skill_slot_1"] = "missing_skill"
            with enemy_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(enemy_rows[0]))
                writer.writeheader()
                writer.writerows(enemy_rows)
            with self.assertRaisesRegex(ValueError, "missing_skill"):
                create_match(seed=1, enemy_group_id="training_group", enemy_master_path=enemy_path)

    def test_enemy_group_unknown_ai_falls_back_and_headless_match_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group_path = root / "groups.csv"
            group_path.write_text(
                "enemy_group_id,enemy_group_name,enemy_1_id,enemy_2_id,enemy_3_id,enemy_scale,"
                "group_ai_profile_id,enemy_1_ai_profile_id,enabled\n"
                "fallback,AI確認,training_power,training_magic,training_support,1.0,missing,missing,true\n",
                encoding="utf-8-sig",
            )
            with self.assertLogs("manaball.data", level="WARNING"):
                game = create_match(seed=9, enemy_group_id="fallback", enemy_group_path=group_path)
            self.assertEqual(game.characters["enemy:fallback:1"].ai_profile_id, "attack")
            with patch("manaball.reporting.PROJECT_ROOT", root):
                game.report.set_execution_mode("headless")
                safety = 0
                while not game.match_over and safety < 300:
                    game.ai_take_turn()
                    safety += 1
            self.assertTrue(game.match_over)
            self.assertTrue(game.report.match_finished)

    def test_selected_party_ids_create_fresh_units_in_slot_position_order(self) -> None:
        player_ids = ("32", "1", "31")
        enemy_ids = ("30", "5", "2")
        game = create_match(seed=4, player_ids=player_ids, enemy_ids=enemy_ids)
        for index, char_id in enumerate(player_ids):
            self.assertEqual(game.characters[char_id].team, PLAYER)
            self.assertEqual(game.characters[char_id].position, game.config.player_positions[index])
            self.assertEqual(game.characters[char_id].initial_position, game.config.player_positions[index])
        for index, char_id in enumerate(enemy_ids):
            self.assertEqual(game.characters[char_id].team, ENEMY)
            self.assertEqual(game.characters[char_id].position, game.config.enemy_positions[index])
        self.assertEqual(set(game.characters), set(player_ids + enemy_ids))
        game.characters["32"].hp = 1
        fresh_game = create_match(seed=4, player_ids=player_ids, enemy_ids=enemy_ids)
        self.assertEqual(fresh_game.characters["32"].hp, fresh_game.characters["32"].max_hp)

    def test_party_selection_rejects_same_team_duplicate_but_allows_cross_team(self) -> None:
        with self.assertRaisesRegex(ValueError, "それぞれ 3 人"):
            create_match(player_ids=("1", "2"), enemy_ids=("3", "4", "5"))
        with self.assertRaisesRegex(ValueError, "味方"):
            create_match(player_ids=("1", "1", "3"), enemy_ids=("3", "4", "5"))
        game = create_match(player_ids=("1", "2", "3"), enemy_ids=("3", "4", "5"))
        self.assertIn("3", game.characters)
        self.assertIn("enemy:3:1", game.characters)
        self.assertIsNot(game.characters["3"], game.characters["enemy:3:1"])
        game.characters["3"].hp = 1
        game.characters["3"].mana = 2
        game.characters["3"].position = (4, 4)
        game.characters["3"].temporary_effects["test"] = {"value": 1}
        self.assertNotEqual(game.characters["enemy:3:1"].hp, 1)
        self.assertNotEqual(game.characters["enemy:3:1"].mana, 2)
        self.assertNotEqual(game.characters["enemy:3:1"].position, (4, 4))
        self.assertNotIn("test", game.characters["enemy:3:1"].temporary_effects)
        with self.assertRaisesRegex(ValueError, "データが不足"):
            create_match(player_ids=("1", "2", "missing"), enemy_ids=("3", "4", "5"))

    def test_character_roster_loads_all_valid_csv_rows_for_party_display(self) -> None:
        roster = load_character_roster()
        with (CSV_ROOT / "characters.csv").open(encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        self.assertEqual(len(roster), len(csv_rows))
        self.assertEqual([member.char_id for member in roster], [row["char_id"] for row in csv_rows])
        self.assertTrue(all(member.class_name and member.element_name for member in roster))
        self.assertTrue(all(member.max_hp > 0 and member.stamina > 0 for member in roster))

    def test_party_skill_overrides_apply_to_fresh_match_and_reject_invalid_data(self) -> None:
        overrides = {"10": ("heal_hp", "power_up", "normal_magic_attack")}
        game = create_match(seed=5, skill_overrides=overrides)
        self.assertEqual(game.characters["10"].skills, overrides["10"])
        self.assertEqual(game.characters["11"].skills[:2], ("heal", "shield_guard"))
        fresh_game = create_match(seed=5, skill_overrides=overrides)
        self.assertIsNot(game.characters["10"], fresh_game.characters["10"])
        with self.assertRaisesRegex(ValueError, "重複"):
            create_match(skill_overrides={"10": ("heal_hp", "heal_hp")})
        with self.assertRaisesRegex(ValueError, "スキルが不正"):
            create_match(skill_overrides={"10": ("unknown_skill",)})

    def test_ai_profiles_apply_only_to_enemies(self) -> None:
        profiles = load_ai_profiles()
        self.assertIn("standard", profiles)
        self.assertIn("attack", profiles)
        self.assertEqual(profiles["standard"].values["attack_aggression"], 50)
        game = create_match(
            seed=6,
            enemy_ai_profile_ids=("support", "attack", "defense"),
        )
        self.assertEqual(game.characters["10"].ai_profile_id, "")
        self.assertEqual(game.characters["10"].ai_settings["attack_aggression"], 50)
        self.assertEqual(game.characters["10"].ai_settings["pass_priority"], 50)
        self.assertEqual(game.characters["20"].ai_profile_id, "support")
        self.assertGreater(game.characters["20"].ai_settings["support_priority"], 70)

    def test_unknown_enemy_ai_profile_falls_back_to_standard(self) -> None:
        with self.assertLogs("manaball.ai", level="WARNING"):
            game = create_match(seed=6, enemy_ai_profile_ids=("missing", "", ""))
        self.assertEqual(game.characters["20"].ai_profile_id, "standard")

    def test_initialization_loads_five_classes_three_elements_and_order(self) -> None:
        game = fresh()
        self.assertEqual((game.config.field_width, game.config.field_height), (13, 5))
        self.assertEqual(len(game.goal_cells(PLAYER)), 9)
        self.assertEqual(len(game.goal_cells(ENEMY)), 9)
        self.assertEqual(len(game.classes), 5)
        self.assertEqual({element.name for key, element in game.elements.items() if key != "0"}, {"火", "水", "風"})
        self.assertEqual({character.class_name for character in game.characters.values()}, {"戦士", "魔法使い", "僧侶", "盗賊", "格闘家"})
        for character in game.characters.values():
            for value in (character.physical, character.magic, character.power, character.speed, character.technique):
                self.assertGreaterEqual(value, 1)
                self.assertLessEqual(value, 10)
            self.assertGreaterEqual(character.stamina, 1)
            self.assertLessEqual(character.stamina, 10)
            self.assertGreaterEqual(character.physical_skill_level, 0)
            self.assertLessEqual(character.magic_skill_level, 6)
        speeds = [game.characters[char_id].speed for char_id in game.turn_order]
        self.assertEqual(speeds, sorted(speeds, reverse=True))
        self.assertEqual(game.current_actor.mana, 1)

    def test_legacy_character_csv_without_new_columns_uses_safe_defaults(self) -> None:
        omitted = {
            "stamina",
            "physical",
            "power",
            "physical_skill_level",
            "magic_skill_level",
        }
        with (CSV_ROOT / "characters.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        fieldnames = [name for name in rows[0] if name not in omitted]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "characters.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            config = load_config()
            with self.assertLogs("manaball.data", level="WARNING"):
                characters = load_characters(config, load_classes(), load_elements(), path)
        self.assertEqual(len(characters), 6)
        self.assertTrue(all(character.stamina > 0 for character in characters))
        self.assertTrue(all(character.physical >= 1 and character.power >= 1 for character in characters))

    def test_ability_dice_uses_two_stat_total_and_tie_defends(self) -> None:
        game = fresh([8, 1])
        actor = game.characters["10"]
        target = game.characters["20"]
        success, details = game.contest(
            "test", actor, target, "physical", "power", "physical", "power"
        )
        self.assertTrue(success)
        self.assertEqual(details["contest"]["offense_value"], actor.physical + actor.power)
        self.assertEqual(details["contest"]["offense_stats"]["primary_name"], "物理")
        self.assertEqual(details["contest"]["offense_stats"]["secondary_name"], "パワー")
        self.assertEqual(details["contest"]["offense_roll"], 8)
        game.rng = StubRandom([3, 3])
        success, _ = game.contest("test", actor, target, "physical", "power", "physical", "power")
        self.assertFalse(success)

    def test_turn_mana_persists_and_ball_holder_gets_bonus(self) -> None:
        game = fresh()
        first = game.current_actor
        second = game.characters[game.turn_order[1]]
        second.mana = 2
        second.stamina = 0
        game.set_ball_holder(second.char_id, "test")
        game.advance_turn(first.char_id)
        self.assertEqual(second.mana, 4)
        self.assertEqual(second.stamina, 0)

    def test_friendly_units_can_be_crossed_but_not_destination(self) -> None:
        game = fresh()
        position_for_action(game, {"10": (1, 2), "11": (2, 2), "20": (7, 0)})
        paths = game.movement_paths("10")
        self.assertNotIn((2, 2), paths)
        self.assertIn((3, 2), paths)
        self.assertEqual(paths[(3, 2)][:3], [(1, 2), (2, 2), (3, 2)])

    def test_zoc_stops_movement_and_knocked_out_enemy_has_no_zoc(self) -> None:
        game = fresh()
        position_for_action(game, {"12": (1, 2), "20": (3, 2)})
        zoc = game.zoc_cells(PLAYER)
        self.assertIn((2, 2), zoc)
        paths = game.movement_paths("12")
        for destination, path in paths.items():
            self.assertFalse(any(cell in zoc for cell in path[1:-1]), (destination, path))
        game.characters["20"].off_field = True
        game.characters["20"].position = None
        self.assertNotIn((2, 2), game.zoc_cells(PLAYER))

    def test_shadow_step_ignores_zoc_without_spending_stamina(self) -> None:
        game = fresh()
        position_for_action(game, {"12": (1, 2), "20": (3, 2)})
        rogue = game.characters["12"]
        rogue.stamina = 1
        mana = rogue.mana
        normal = game.reachable_positions("12")
        ignored = game.reachable_positions("12", ignore_zoc=True)
        self.assertGreater(len(ignored), len(normal))
        result = game.activate_shadow_step("12")
        self.assertTrue(result.success)
        self.assertEqual(rogue.stamina, 1)
        self.assertEqual(rogue.mana, mana)
        self.assertFalse(result.consumed)

    def test_forward_pass_to_acted_teammate_is_legal(self) -> None:
        game = fresh()
        position_for_action(game, {"10": (3, 2), "11": (5, 2), "20": (8, 0)})
        receiver = game.characters["11"]
        receiver.acted = True
        game.set_ball_holder("10", "test")
        self.assertTrue(game.pass_validation("10", "11")[0])
        result = game.pass_ball("10", "11")
        self.assertTrue(result.success)
        self.assertEqual(game.ball.holder_id, "11")


class BalanceReportTests(unittest.TestCase):
    def test_ball_holder_gets_basic_and_equipped_attacks_and_keeps_ball(self) -> None:
        game = create_match(seed=3)
        actor = game.characters["10"]
        target = game.characters["20"]
        actor.position = (5, 2)
        target.position = (6, 2)
        self.assertNotIn("normal_physical_attack", actor.skills)
        game.set_ball_holder(actor.char_id, "test")
        attacks = {item.skill_id: item for item in game.action_candidates(actor.char_id, "attack")}
        self.assertTrue(attacks["normal_physical_attack"].usable)
        self.assertTrue(attacks["push_strike"].usable)
        self.assertIn(target, game.basic_attack_targets(actor.char_id))
        result = game.use_basic_attack(actor.char_id, target.char_id)
        self.assertTrue(result.success)
        self.assertEqual(game.ball.holder_id, actor.char_id)

    def test_ball_holder_can_attack_after_pending_move(self) -> None:
        game = create_match(seed=3)
        actor = game.characters["10"]
        target = game.characters["20"]
        actor.position = (5, 2)
        target.position = (7, 2)
        game.set_ball_holder(actor.char_id, "test")
        self.assertTrue(game.prepare_move(actor.char_id, (6, 2)).success)
        self.assertTrue(game.arrive_prepared_move(actor.char_id).success)
        attacks = {item.skill_id: item for item in game.action_candidates(actor.char_id, "attack")}
        self.assertTrue(attacks["normal_physical_attack"].usable)

    def test_attack_candidates_ignore_all_ball_ownership_states(self) -> None:
        scenarios = ("actor", "target", "ally", "enemy", "loose", "none")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                game = create_match(seed=3)
                actor = game.characters["10"]
                target = game.characters["20"]
                ally = game.characters["11"]
                enemy = game.characters["21"]
                actor.position, target.position = (5, 2), (6, 2)
                ally.position, enemy.position = (3, 2), (9, 2)
                if scenario == "actor":
                    game.set_ball_holder(actor.char_id, "test")
                elif scenario == "target":
                    game.set_ball_holder(target.char_id, "test")
                elif scenario == "ally":
                    game.set_ball_holder(ally.char_id, "test")
                elif scenario == "enemy":
                    game.set_ball_holder(enemy.char_id, "test")
                elif scenario == "loose":
                    game.drop_ball((7, 4), "test")
                else:
                    game.ball.holder_id = None
                    game.ball.loose_position = None
                attacks = {item.skill_id: item for item in game.action_candidates(actor.char_id, "attack")}
                self.assertTrue(attacks["normal_physical_attack"].usable)
                self.assertTrue(attacks["push_strike"].usable)
                self.assertIn(target, game.basic_attack_targets(actor.char_id))

    def test_ai_keeps_non_holder_attack_plans_during_team_possession(self) -> None:
        game = create_match(seed=3)
        actor, target, holder = game.characters["10"], game.characters["20"], game.characters["11"]
        actor.position, target.position, holder.position = (5, 2), (6, 2), (3, 2)
        game.set_ball_holder(holder.char_id, "test")
        plans = game._build_ai_plans(actor, allow_move=False)
        attack_skills = {
            plan.skill_id for plan in plans
            if plan.action_type == "skill" and plan.target_id == target.char_id
        }
        self.assertIn("normal_physical_attack", attack_skills)
        self.assertIn("push_strike", attack_skills)

    def test_non_holder_attack_does_not_change_third_party_holder(self) -> None:
        game = create_match(seed=3)
        actor, target, holder = game.characters["10"], game.characters["20"], game.characters["11"]
        actor.position, target.position, holder.position = (5, 2), (6, 2), (3, 2)
        game.set_ball_holder(holder.char_id, "test")
        result = game.use_basic_attack(actor.char_id, target.char_id)
        self.assertTrue(result.success)
        self.assertEqual(game.ball.holder_id, holder.char_id)

    def _find_record(self, records: list[dict], **conditions: object) -> dict:
        for record in records:
            if all(record.get(key) == value for key, value in conditions.items()):
                return record
        raise AssertionError(f"record not found: {conditions}")

    def _run_match_with_temp_log_root(self, action: str = "wait") -> tuple[Path, str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("manaball.reporting.PROJECT_ROOT", root):
                game = create_match(seed=7)
                game.report.set_execution_mode("headless")
                actor = game.current_actor
                if action == "wait":
                    game.wait(actor.char_id)
                elif action == "attack":
                    target = next(character for character in game.characters.values() if character.team != actor.team)
                    actor.position = (3, 2)
                    target.position = (4, 2)
                    game.normal_attack(actor.char_id, target.char_id)
                game.retire(actor.team)
                report_files = sorted((root / "log").glob("*.md"))
                self.assertEqual(len(report_files), 1)
                return report_files[0], report_files[0].read_text(encoding="utf-8")

    def test_match_balance_report_is_written_with_expected_headings(self) -> None:
        report_path, text = self._run_match_with_temp_log_root()
        self.assertRegex(report_path.name, r"^\d{4}_\d{2}_\d{2}_\d{6}_\d{3}\.md$")
        self.assertNotIn("match_balance_report_", report_path.name)
        headings = [
            "# Mana's Ball 試合バランスレポート",
            "## 1．試合概要",
            "## 2．試合条件",
            "## 3．試合開始時のキャラクター情報",
            "## 4．試合結果",
            "## 5．キャラクター別集計",
            "## 6．スキル別集計",
            "## 7．行動ログ",
            "## 8．試合全体集計",
        ]
        cursor = -1
        for heading in headings:
            position = text.find(heading)
            self.assertGreater(position, cursor)
            cursor = position
        self.assertIn("- 実行形式: headless", text)
        self.assertNotIn("レポート出力:", text)
        self.assertNotIn("レポート出力済み:", text)

    def test_overkill_damage_is_separated_from_effective_damage(self) -> None:
        game = fresh()
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        attacker = game.characters["10"]
        target = game.characters["20"]
        attacker.physical = 2
        attacker.power = 10
        target.physical = 0
        target.stamina = 2
        target.hp = 4

        result = game.normal_attack("10", "20")

        self.assertTrue(result.success)
        record = self._find_record(game.report.action_records, record_type="character_action", action_name="通常攻撃")
        self.assertEqual(record["calculated_damage"], 5)
        self.assertEqual(record["effective_damage"], 4)
        self.assertEqual(record["overkill_damage"], 1)

    def test_non_overkill_damage_keeps_effective_damage_equal_to_calculation(self) -> None:
        game = fresh()
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        attacker = game.characters["10"]
        target = game.characters["20"]
        attacker.physical = 1
        attacker.power = 5
        target.physical = 0
        target.stamina = 2
        target.hp = 20

        result = game.normal_attack("10", "20")

        self.assertTrue(result.success)
        record = self._find_record(game.report.action_records, record_type="character_action", action_name="通常攻撃")
        self.assertEqual(record["calculated_damage"], 2)
        self.assertEqual(record["effective_damage"], 2)
        self.assertEqual(record["overkill_damage"], 0)

    def test_score_event_is_separate_from_score_reset_and_counts_scorer(self) -> None:
        game = fresh()
        position_for_action(game, {
            "10": (6, 2),
            "11": (10, 2),
            "12": (1, 0),
            "20": (0, 0),
            "21": (0, 4),
            "22": (1, 4),
        })
        game.set_ball_holder("10", "test")

        result = game.pass_ball("10", "11", "physical")
        self.assertTrue(result.scored)
        restart = game.prepare_restart_after_goal()
        self.assertTrue(restart.success)

        score_record = self._find_record(game.report.action_records, record_type="system_event", event_type="score")
        self.assertEqual(score_record["scorer_id"], "11")
        self.assertEqual(score_record["scoring_team"], PLAYER)
        self.assertEqual(score_record["score_before"], {PLAYER: 0, ENEMY: 0})
        self.assertEqual(score_record["score_after"], {PLAYER: 1, ENEMY: 0})
        self.assertEqual(score_record["score_method"], "pass_receive")

        score_reset_records = [
            record for record in game.report.action_records
            if record.get("record_type") == "system_event" and record.get("event_type") == "score_reset"
        ]
        self.assertTrue(score_reset_records)
        self.assertTrue(all(record.get("scorer_id") == "11" for record in score_reset_records))

        character_aggregate = next(item for item in game.report._character_aggregates(game) if item["char_id"] == "11")
        self.assertEqual(character_aggregate["scores"], 1)

    def test_winning_score_is_recorded_without_score_reset(self) -> None:
        game = fresh()
        game.scores[PLAYER] = game.config.target_score - 1
        actor = game.characters["10"]
        actor.position = (game.config.field_width - 1, 2)
        game.set_ball_holder("10", "test")

        result = game.score_holder_in_goal()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.match_ended)
        score_record = self._find_record(game.report.action_records, record_type="system_event", event_type="score")
        self.assertTrue(score_record["is_winning_score"])
        self.assertFalse(score_record["score_reset_performed"])
        self.assertEqual(score_record["score_after"], {PLAYER: game.config.target_score, ENEMY: 0})

    def test_all_scores_are_recorded_before_reset_or_match_end(self) -> None:
        game = fresh()
        scorer = game.characters["10"]
        scorer.position = (game.config.field_width - 1, 2)
        game.set_ball_holder(scorer.char_id, "test")

        first = game.score_holder_in_goal()
        self.assertIsNotNone(first)
        self.assertFalse(first.match_ended)
        self.assertTrue(game.prepare_restart_after_goal().success)
        self.assertTrue(game.auto_select_restart_holder().success)

        scorer.position = (game.config.field_width - 1, 2)
        game.set_ball_holder(scorer.char_id, "test")
        second = game.score_holder_in_goal()
        self.assertIsNotNone(second)
        self.assertTrue(second.match_ended)

        score_records = [
            record for record in game.report.action_records
            if record.get("record_type") == "system_event" and record.get("event_type") == "score"
        ]
        self.assertEqual(len(score_records), 2)
        self.assertEqual(score_records[0]["score_before"], {PLAYER: 0, ENEMY: 0})
        self.assertEqual(score_records[0]["score_after"], {PLAYER: 1, ENEMY: 0})
        self.assertEqual(score_records[1]["score_before"], {PLAYER: 1, ENEMY: 0})
        self.assertEqual(score_records[1]["score_after"], {PLAYER: 2, ENEMY: 0})
        self.assertTrue(score_records[1]["is_winning_score"])

        reset_sequence = min(
            record["sequence"] for record in game.report.action_records
            if record.get("event_type") == "score_reset"
        )
        match_end_sequence = self._find_record(
            game.report.action_records, record_type="system_event", event_type="match_end"
        )["sequence"]
        self.assertLess(score_records[0]["sequence"], reset_sequence)
        self.assertLess(score_records[1]["sequence"], match_end_sequence)

        aggregate = next(item for item in game.report._character_aggregates(game) if item["char_id"] == scorer.char_id)
        self.assertEqual(aggregate["scores"], game.scores[PLAYER])

    def test_committed_normal_move_records_path_distance_and_cancel_does_not(self) -> None:
        game = fresh()
        actor = game.current_actor
        self.assertIsNotNone(actor)
        assert actor is not None
        destination, path = next(iter(game.movement_paths(actor.char_id).items()))

        self.assertTrue(game.prepare_move(actor.char_id, destination).success)
        self.assertTrue(game.arrive_prepared_move(actor.char_id).success)
        self.assertTrue(game.cancel_pending_move(actor.char_id).success)
        self.assertFalse(any(record.get("movement_type") == "normal" for record in game.report.action_records))

        self.assertTrue(game.prepare_move(actor.char_id, destination).success)
        self.assertTrue(game.arrive_prepared_move(actor.char_id).success)
        committed = game.commit_pending_move(actor.char_id)
        self.assertTrue(committed.success)

        movement = self._find_record(
            game.report.action_records,
            record_type="character_action",
            action_name="通常移動",
        )
        self.assertEqual(movement["position_before"], path[0])
        self.assertEqual(movement["position_after"], path[-1])
        self.assertEqual(movement["movement_path"], path)
        self.assertEqual(movement["movement_distance"], len(path) - 1)
        self.assertEqual(movement["movement_type"], "normal")

        aggregate = next(item for item in game.report._character_aggregates(game) if item["char_id"] == actor.char_id)
        self.assertEqual(aggregate["normal_moves"], 1)
        self.assertEqual(aggregate["normal_move_distance"], len(path) - 1)

    def test_breakthrough_skill_aggregate_tracks_success_and_failure(self) -> None:
        game = fresh([100, 1, 1, 100, 100, 1])
        position_for_action(game, {"20": (4, 2), "21": (5, 2), "10": (3, 2), "11": (0, 2), "12": (0, 4), "22": (8, 4)})

        for _ in range(3):
            game.set_ball_holder("21", "test")
            result = game.breakthrough_skill("20", "10")
            self.assertTrue(result.success)
            game.characters["10"].position = (3, 2)
            game.characters["20"].position = (4, 2)
            game.characters["10"].off_field = False

        aggregate = next(
            item for item in game.report._skill_aggregates(game)
            if item["owner_id"] == "20" and item["skill_id"] == "breakthrough"
        )
        self.assertEqual(aggregate["uses"], 3)
        self.assertEqual(aggregate["successes"], 2)
        self.assertEqual(aggregate["failures"], 1)

    def test_breakthrough_records_actor_skill_move_and_target_forced_move(self) -> None:
        game = fresh([100])
        position_for_action(game, {
            "20": (4, 2), "21": (5, 2), "10": (3, 2),
            "11": (0, 2), "12": (0, 4), "22": (8, 4),
        })
        game.set_ball_holder("21", "test")

        result = game.breakthrough_skill("20", "10")

        self.assertTrue(result.success)
        self.assertEqual(result.message, "突破成功")
        movement_records = [
            record for record in game.report.action_records
            if record.get("record_type") == "system_event"
            and record.get("event_type") == "movement"
            and record.get("result", {}).get("details", {}).get("action_name") == "ブレイクスルー"
        ]
        self.assertEqual(len(movement_records), 2)
        actor_move = next(record for record in movement_records if record["actor_id"] == "20")
        target_move = next(record for record in movement_records if record["actor_id"] == "10")
        self.assertEqual(actor_move["movement_type"], "skill")
        self.assertEqual(target_move["movement_type"], "forced")
        self.assertEqual(actor_move["movement_distance"], 1)
        self.assertEqual(target_move["movement_distance"], 1)
        self.assertEqual(len(actor_move["movement_path"]), 2)
        self.assertEqual(len(target_move["movement_path"]), 2)

    def test_turn_start_mp_recovery_keeps_distinct_before_and_after_states(self) -> None:
        game = fresh()
        actor = game.current_actor
        self.assertIsNotNone(actor)
        assert actor is not None
        game.ball.holder_id = None
        actor.mana = 0

        game._begin_current_turn()

        regeneration = [
            record for record in game.report.action_records
            if record.get("event_type") == "turn_regeneration" and record.get("actor_id") == actor.char_id
        ][-1]
        before = game.report._participant_lookup(regeneration["before"]["participants"])[actor.char_id]
        after = game.report._participant_lookup(regeneration["after"]["participants"])[actor.char_id]
        self.assertEqual(before["mp"], 0)
        self.assertEqual(after["mp"], game.config.mana_per_turn)
        self.assertEqual(regeneration["effective_mp_recovery"], after["mp"] - before["mp"])

    def test_mp_spending_and_mp_recovery_are_separated(self) -> None:
        game = fresh()
        position_for_action(game, {"11": (3, 2), "10": (4, 2), "20": (8, 0)})
        healer = game.characters["11"]
        healer.mana = 2

        result = game.execute_common_skill("11", "recover_mp", "11")

        self.assertTrue(result.success)
        record = self._find_record(game.report.action_records, record_type="character_action", command_group="skill")
        self.assertEqual(record["calculated_mp_recovery"], 9)
        self.assertEqual(record["effective_mp_recovery"], 8)
        self.assertEqual(record["overheal_mp"], 1)
        self.assertEqual(game.report._extract_mp_spent(record), 2)

    def test_match_balance_report_includes_post_goal_reset_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("manaball.reporting.PROJECT_ROOT", root):
                game = create_match(seed=13)
                game.report.set_execution_mode("headless")
                actor = game.current_actor
                assert actor is not None
                goal_x = game.config.field_width - 1 if actor.team == PLAYER else 0
                actor.position = (goal_x, 2)
                game.set_ball_holder(actor.char_id, "test")
                scored = game.score_holder_in_goal()
                self.assertIsNotNone(scored)
                self.assertTrue(scored.scored)
                restart = game.prepare_restart_after_goal()
                self.assertTrue(restart.success)
                game.auto_select_restart_holder()
                if game.current_actor is not None:
                    game.retire(game.current_actor.team)
                report_files = sorted((root / "log").glob("*.md"))
                self.assertEqual(len(report_files), 1)
                text = report_files[0].read_text(encoding="utf-8")
        self.assertIn("得点後再配置", text)
        self.assertIn("## 7．行動ログ", text)

    def test_two_matches_create_distinct_report_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("manaball.reporting.PROJECT_ROOT", root):
                first = create_match(seed=11)
                first.report.set_execution_mode("headless")
                first.retire(first.current_actor.team)
                second = create_match(seed=12)
                second.report.set_execution_mode("headless")
                second.retire(second.current_actor.team)
                report_files = sorted((root / "log").glob("*.md"))
                self.assertEqual(len(report_files), 2)
                self.assertNotEqual(report_files[0].name, report_files[1].name)
                self.assertTrue(all(re.match(r"^\d{4}_\d{2}_\d{2}_\d{6}_\d{3}\.md$", path.name) for path in report_files))

    def test_pass_to_teammate_on_goal_scores_immediately(self) -> None:
        game = fresh()
        position_for_action(game, {
            "10": (6, 2),
            "11": (10, 2),
            "12": (1, 0),
            "20": (0, 0),
            "21": (0, 4),
            "22": (1, 4),
        })
        game.set_ball_holder("10", "test")

        result = game.pass_ball("10", "11", "physical")

        self.assertTrue(result.scored)
        self.assertEqual(game.scores[PLAYER], 1)
        self.assertEqual(game.restart_team, ENEMY)
        self.assertEqual(game.ball.holder_id, "11")
        self.assertEqual(result.details["action_name"], "パス")
        self.assertEqual(result.details["final_holder_id"], "11")

    def test_quick_pass_is_strengthened_pass_but_grants_no_extra_action(self) -> None:
        game = fresh()
        position_for_action(game, {"12": (3, 2), "10": (5, 2), "20": (8, 0)})
        rogue = game.characters["12"]
        rogue.mana = 1
        game.set_ball_holder("12", "test")
        result = game.pass_ball("12", "10", quick=True)
        self.assertTrue(result.success)
        self.assertTrue(result.consumed)
        self.assertFalse(result.extra_action)
        self.assertEqual(game.ball.holder_id, "10")
        self.assertEqual(rogue.mana, 0)

    def test_multiple_pass_cut_candidates_roll_in_route_order_until_success(self) -> None:
        game = fresh([100, 1])
        position_for_action(game, {"12": (1, 2), "10": (5, 2), "20": (2, 2), "21": (4, 2)})
        game.set_ball_holder("12", "test")
        preview = game.pass_preview("12", "10", "physical")
        self.assertEqual(preview["line"][0], (1, 2))
        self.assertEqual([item["character_id"] for item in preview["candidates"]], ["20", "21"])
        result = game.pass_ball("12", "10", "physical")
        self.assertEqual(game.ball.holder_id, "21")
        self.assertEqual([item["success"] for item in result.details["pass_cut_results"]], [False, True])
        self.assertEqual([item["roll"] for item in result.details["pass_cut_results"]], [100, 1])

        game = fresh([1, 100])
        position_for_action(game, {"12": (1, 2), "10": (5, 2), "20": (2, 2), "21": (4, 2)})
        game.set_ball_holder("12", "test")
        result = game.pass_ball("12", "10", "physical")
        self.assertEqual(game.ball.holder_id, "20")
        self.assertEqual(len(result.details["pass_cut_results"]), 1)
        self.assertEqual(game.rng.rolls, [100])

        game = fresh([100, 100])
        position_for_action(game, {"12": (1, 2), "10": (5, 2), "20": (2, 2), "21": (4, 2)})
        game.set_ball_holder("12", "test")
        result = game.pass_ball("12", "10", "physical")
        self.assertEqual(game.ball.holder_id, "10")
        self.assertTrue(all(not item["success"] for item in result.details["pass_cut_results"]))

    def test_attack_targets_acted_non_holder_and_is_automatic_hit(self) -> None:
        game = fresh([100])
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        target = game.characters["20"]
        target.acted = True
        target.waiting = True
        self.assertIn(target, game.attack_targets("10"))
        before = target.hp
        result = game.normal_attack("10", "20")
        self.assertTrue(result.details["automatic_hit"])
        self.assertLess(target.hp, before)
        self.assertEqual(game.rng.rolls, [100])

    def test_non_holder_can_attack_regardless_of_team_possession(self) -> None:
        game = fresh()
        position_for_action(game, {"10": (4, 1), "11": (3, 2), "20": (4, 2)})
        game.drop_ball((6, 4), "test")
        self.assertEqual(game.team_state(PLAYER), "contest")
        self.assertIn(game.characters["20"], game.attack_targets("11"))
        game.set_ball_holder("10", "test")
        self.assertEqual(game.team_state(PLAYER), "possession")
        self.assertEqual(game.team_state(ENEMY), "acquisition")
        self.assertIn(game.characters["20"], game.attack_targets("11"))
        self.assertIn(game.characters["20"], game.attack_targets("10"))
        self.assertIn(game.characters["11"], game.attack_targets("20"))

    def test_physical_skill_checks_level_and_match_state_without_stamina_cost(self) -> None:
        game = fresh([20, 1])
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        warrior = game.characters["10"]
        game.set_ball_holder("20", "test")
        warrior.physical_skill_level = 1
        self.assertIn("レベル不足", game.can_use_skill("10", "push_strike")[1])
        warrior.physical_skill_level = 6
        warrior.stamina = 1
        self.assertTrue(game.can_use_skill("10", "push_strike")[0])
        mana = warrior.mana
        result = game.push_strike("10", "20")
        self.assertTrue(result.success)
        self.assertEqual(warrior.stamina, 1)
        self.assertEqual(warrior.mana, mana)
        game.set_ball_holder("11", "test")
        self.assertIn("保持状態", game.can_use_skill("10", "push_strike")[1])

    def test_magic_skill_checks_level_and_mp(self) -> None:
        game = fresh([20, 1, 3, 6])
        position_for_action(game, {"22": (4, 2), "10": (2, 2)})
        mage = game.characters["22"]
        game.set_ball_holder("10", "test")
        mage.magic_skill_level = 1
        self.assertIn("レベル不足", game.can_use_skill("22", "elemental_bolt")[1])
        mage.magic_skill_level = 6
        mage.mana = 1
        self.assertIn("MP不足", game.can_use_skill("22", "elemental_bolt")[1])
        target_hp = game.characters["10"].hp
        failed = game.elemental_bolt("22", "10")
        self.assertFalse(failed.success)
        self.assertEqual(mage.mana, 1)
        self.assertEqual(game.characters["10"].hp, target_hp)
        mage.mana = 2
        stamina = mage.stamina
        result = game.elemental_bolt("22", "10")
        self.assertTrue(result.success)
        self.assertEqual(mage.mana, 0)
        self.assertEqual(mage.stamina, stamina)

    def test_new_damage_formula_uses_ability_difference_without_randomness(self) -> None:
        game = fresh([100])
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        attacker = game.characters["10"]
        target = game.characters["20"]
        attacker.attack = 999
        attacker.accuracy = 1
        target.defense = 999
        target.evasion = 999
        result = game.normal_attack("10", "20")
        expected = max(
            game.config.minimum_damage,
            (attacker.physical + attacker.power - (target.physical + target.stamina * 0.5))
            * game.config.damage_difference_multiplier,
        )
        self.assertEqual(result.damage, int(expected))
        calculation = result.details["damage_calculation"]
        self.assertEqual(calculation["attack_value"], attacker.physical + attacker.power)
        self.assertEqual(calculation["defense_value"], target.physical + target.stamina * 0.5)
        self.assertEqual(calculation["damage_die"], 0)
        self.assertEqual(game.rng.rolls, [100])

    def test_damage_multiplier_is_float_configured_and_shared_by_direct_damage(self) -> None:
        game = fresh()
        self.assertEqual(game.config.damage_difference_multiplier, 0.5)
        self.assertIsInstance(game.config.damage_difference_multiplier, float)
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        game.drop_ball((8, 4), "test")
        attacker = game.characters["10"]
        target = game.characters["20"]
        attacker.physical = 10
        attacker.power = 7
        target.physical = 4
        target.stamina = 6

        normal_preview = game.damage_preview("10", "20", base_power=12, system="physical")
        skill_preview = game.common_skill_damage_preview("10", "20", "normal_physical_attack")
        self.assertEqual(normal_preview["ability_difference"], 10)
        self.assertEqual(normal_preview["basic_damage"], 17)
        self.assertEqual(skill_preview["basic_damage"], normal_preview["basic_damage"])

        result = game.use_skill("10", "normal_physical_attack", "20")
        calculation = result.details["damage_calculation"]
        self.assertEqual(result.damage, skill_preview["predicted_damage"])
        self.assertEqual(calculation["base_power"], 12)
        self.assertEqual(calculation["attack_value"], 17)
        self.assertEqual(calculation["defense_value"], 7)
        self.assertEqual(calculation["difference_multiplier"], 0.5)
        self.assertEqual(calculation["ability_correction"], 5)
        self.assertEqual(result.details["effect_results"][0]["references"], [])

    def test_shared_direct_damage_handles_zero_negative_and_magic_difference(self) -> None:
        game = fresh()
        position_for_action(game, {"12": (2, 2), "20": (4, 2)})
        game.drop_ball((8, 4), "test")
        attacker = game.characters["12"]
        target = game.characters["20"]
        attacker.magic = 5
        attacker.power = 3
        target.magic = 5
        target.stamina = 6
        zero = game.common_skill_damage_preview("12", "20", "normal_magic_attack")
        self.assertEqual(zero["ability_difference"], 0)
        self.assertEqual(zero["basic_damage"], 9)
        attacker.magic = 1
        attacker.power = 1
        target.magic = 10
        target.stamina = 10
        negative = game.common_skill_damage_preview("12", "20", "normal_magic_attack")
        self.assertLess(negative["ability_difference"], 0)
        self.assertEqual(negative["basic_damage"], 2)

    def test_common_skill_data_loads_seven_representative_skills(self) -> None:
        skills = load_skills()
        representative = {
            "normal_physical_attack", "normal_magic_attack", "power_up",
            "heal_hp", "recover_mp", "pass_cut", "steal",
        }
        self.assertTrue(representative.issubset(skills))
        for skill_id in representative:
            self.assertEqual(skills[skill_id].effect_mode, "common")
            self.assertGreaterEqual(len(skills[skill_id].effects), 1)
            self.assertLessEqual(len(skills[skill_id].effects), 5)
            self.assertFalse(skills[skill_id].validation_error)
        self.assertEqual(skills["normal_physical_attack"].command_group, "attack")
        self.assertEqual(skills["quick_pass"].command_group, "ball")
        self.assertEqual(skills["pass_cut"].command_group, "ball")
        self.assertEqual(skills["steal"].command_group, "ball")
        self.assertTrue(skills["power_up"].usable_after_move)
        self.assertTrue(skills["single_speed_boost"].usable_after_move)
        self.assertFalse(skills["shadow_step"].usable_after_move)
        self.assertFalse(skills["breakthrough"].usable_after_move)

    def test_twelve_single_active_skills_load_as_valid_equipment(self) -> None:
        skills = load_skills()
        expected = {
            "single_physical_focus": "闘気強化",
            "single_physical_impact": "衝撃打",
            "single_magic_focus": "魔力集中",
            "single_magic_cycle": "魔力循環",
            "single_power_charge": "力溜め",
            "single_power_push": "押し退け",
            "single_speed_boost": "加速",
            "single_speed_slow": "鈍足",
            "single_technique_focus": "精密操作",
            "single_technique_long_pass": "ロングパス",
            "single_stamina_recover": "自己回復",
            "single_stamina_guard": "防御姿勢",
        }
        self.assertEqual({skill_id: skills[skill_id].name for skill_id in expected}, expected)
        for skill_id in expected:
            skill = skills[skill_id]
            self.assertEqual(skill.activation, "active")
            self.assertEqual(skill.category, "single")
            self.assertEqual(skill.effect_mode, "common")
            self.assertTrue(skill.enabled)
            self.assertFalse(skill.validation_error)
            self.assertEqual(len(skill.effects), 1)

    def test_single_stat_changes_recovery_and_impact_follow_csv_values(self) -> None:
        skill_ids = (
            "single_physical_focus", "single_physical_impact", "single_magic_focus",
            "single_magic_cycle", "single_power_charge", "single_speed_boost",
            "single_speed_slow", "single_technique_focus", "single_stamina_recover",
        )
        game = create_match(seed=3, skill_overrides={"10": skill_ids})
        game.rng = StubRandom([100])
        actor = game.characters["10"]
        target = game.characters["20"]
        actor.mana = 30
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        game.drop_ball((8, 4), "test")

        bases = {stat: game.effective_stat(actor, stat) for stat in ("physical", "magic", "power", "speed", "technique")}
        for skill_id, stat in (
            ("single_physical_focus", "physical"),
            ("single_magic_focus", "magic"),
            ("single_power_charge", "power"),
            ("single_speed_boost", "speed"),
            ("single_technique_focus", "technique"),
        ):
            actor.skill_cooldowns.clear()
            result = game.use_skill("10", skill_id, "10")
            self.assertTrue(result.success)
            self.assertEqual(game.effective_stat(actor, stat), min(10, bases[stat] + 2))

        target_base_speed = game.effective_stat(target, "speed")
        actor.skill_cooldowns.clear()
        slow = game.use_skill("10", "single_speed_slow", "20")
        self.assertTrue(slow.success)
        self.assertEqual(game.effective_stat(target, "speed"), max(1, target_base_speed - 2))

        actor.skill_cooldowns.clear()
        actor.mana = 0
        cycle = game.use_skill("10", "single_magic_cycle", "10")
        self.assertTrue(cycle.success)
        self.assertEqual(actor.mana, 2)
        actor.skill_cooldowns.clear()
        actor.mana = 30
        actor.hp = 1
        recovered = game.use_skill("10", "single_stamina_recover", "10")
        self.assertTrue(recovered.success)
        self.assertEqual(recovered.healing, min(actor.max_hp - 1, 3 + actor.stamina))

        actor.skill_cooldowns.clear()
        target.hp = target.max_hp
        impact = game.use_skill("10", "single_physical_impact", "20")
        calculation = impact.details["damage_calculation"]
        self.assertEqual(calculation["attack_value"], game.effective_stat(actor, "physical") + game.effective_stat(actor, "power"))
        self.assertEqual(calculation["base_power"], 15)

    def test_single_push_long_pass_and_guard_reuse_shared_rules(self) -> None:
        skills = ("single_power_push", "single_technique_long_pass", "single_stamina_guard")
        game = create_match(seed=3, skill_overrides={"10": skills})
        game.rng = StubRandom([100])
        actor = game.characters["10"]
        target = game.characters["20"]
        actor.mana = 20
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        game.set_ball_holder("20", "test")
        pushed = game.use_skill("10", "single_power_push", "20")
        self.assertTrue(pushed.success)
        self.assertEqual(target.position, (5, 2))
        self.assertEqual(game.ball.holder_id, "20")
        self.assertEqual(pushed.damage, 0)

        game = create_match(seed=3, skill_overrides={"10": skills})
        game.rng = StubRandom([100, 100, 100])
        actor = game.characters["10"]
        receiver = game.characters["11"]
        actor.mana = 20
        normal_range = game.pass_range_for(actor, "physical")
        receiver_x = min(game.config.field_width - 1, normal_range + 1)
        position_for_action(game, {"10": (0, 2), "11": (receiver_x, 2), "21": (7, 0)})
        game.set_ball_holder("10", "test")
        self.assertNotIn(receiver, game.valid_pass_targets("10", "physical"))
        self.assertIn(receiver, game.skill_pass_targets("10", "single_technique_long_pass", "physical"))
        preview = game.skill_pass_preview("10", "11", "single_technique_long_pass", "physical")
        self.assertEqual(preview["pass_range"], normal_range + 1)
        passed = game.use_skill("10", "single_technique_long_pass", "11", "physical")
        self.assertTrue(passed.success)
        self.assertEqual(passed.details["skill_id"], "single_technique_long_pass")
        self.assertEqual(actor.skill_cooldowns["single_technique_long_pass"], 1)

        game = create_match(seed=3, skill_overrides={"10": skills})
        actor = game.characters["10"]
        actor.mana = 20
        guarded = game.use_skill("10", "single_stamina_guard", "10")
        self.assertTrue(guarded.success)
        self.assertTrue(actor.defending)
        game.turn_order = ["10"]
        game.turn_index = 0
        game._begin_current_turn()
        self.assertFalse(actor.defending)

    def test_single_push_invalid_destination_and_full_self_heal_do_not_change_state(self) -> None:
        game = create_match(
            seed=3,
            skill_overrides={"10": ("single_power_push", "single_stamina_recover")},
        )
        actor = game.characters["10"]
        actor.mana = 10
        position_for_action(game, {"10": (6, 2), "20": (7, 2), "21": (8, 2)})
        before = (actor.mana, dict(actor.skill_cooldowns), dict(actor.skill_use_counts), target_position := game.characters["20"].position)
        failed = game.use_skill("10", "single_power_push", "20")
        self.assertFalse(failed.success)
        self.assertEqual((actor.mana, actor.skill_cooldowns, actor.skill_use_counts, game.characters["20"].position), before)
        self.assertEqual(target_position, (7, 2))
        before_mana = actor.mana
        failed = game.use_skill("10", "single_stamina_recover", "10")
        self.assertFalse(failed.success)
        self.assertIn("HPが最大", failed.message)
        self.assertEqual(actor.mana, before_mana)

    def test_legacy_skill_csv_without_command_columns_uses_safe_defaults(self) -> None:
        omitted = {"command_group", "usable_after_move", "display_order"}
        with (CSV_ROOT / "skills.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        fieldnames = [name for name in rows[0] if name not in omitted]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skills.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            with self.assertLogs("manaball.data", level="WARNING"):
                skills = load_skills(path=path)
        self.assertEqual(skills["normal_physical_attack"].command_group, "attack")
        self.assertEqual(skills["quick_pass"].command_group, "ball")
        self.assertEqual(skills["pass_cut"].command_group, "ball")
        self.assertTrue(skills["power_up"].usable_after_move)

    def test_legacy_pass_command_group_is_migrated_to_ball_with_warning(self) -> None:
        with (CSV_ROOT / "skills.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
            fieldnames = list(rows[0])
        next(row for row in rows if row["skill_id"] == "quick_pass")["command_group"] = "pass"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skills.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            with self.assertLogs("manaball.data", level="WARNING") as logs:
                skills = load_skills(path=path)

        self.assertEqual(skills["quick_pass"].command_group, "ball")
        self.assertTrue(any("command_group=pass" in message for message in logs.output))

    def test_action_candidates_group_standard_actions_and_skills(self) -> None:
        game = fresh()
        actor = game.characters["12"]
        actor.position = (2, 2)
        game.characters["20"].position = (4, 2)
        game.drop_ball((8, 4), "test")
        attack_ids = [candidate.action_id for candidate in game.action_candidates(actor.char_id, "attack")]
        move_ids = [candidate.action_id for candidate in game.action_candidates(actor.char_id, "move")]
        ball_candidates = game.action_candidates(actor.char_id, "ball")
        ball_ids = [candidate.action_id for candidate in ball_candidates]
        skill_ids = [candidate.action_id for candidate in game.action_candidates(actor.char_id, "skill")]
        self.assertNotIn("standard:attack", attack_ids)
        self.assertIn("skill:normal_magic_attack", attack_ids)
        self.assertIn("standard:move", move_ids)
        self.assertIn("skill:shadow_step", move_ids)
        self.assertIn("standard:pass:physical", ball_ids)
        self.assertIn("standard:pass:magic", ball_ids)
        self.assertIn("standard:keep", ball_ids)
        self.assertNotIn("standard:cut", ball_ids)
        self.assertIn("skill:quick_pass", ball_ids)
        self.assertIn("skill:steal", ball_ids)
        self.assertNotIn("skill:steal", skill_ids)

    def test_common_physical_and_magic_attacks_are_csv_driven_and_automatic(self) -> None:
        game = fresh([100])
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        game.drop_ball((8, 4), "test")
        attacker = game.characters["10"]
        target = game.characters["20"]
        before = target.hp
        result = game.use_skill("10", "normal_physical_attack", "20")
        self.assertTrue(result.success)
        self.assertGreater(result.damage, 0)
        self.assertEqual(before - target.hp, result.damage)
        self.assertEqual(result.details["damage_calculation"]["system"], "physical")
        self.assertEqual(game.rng.rolls, [100])
        first_damage = result.damage
        target.hp = target.max_hp
        attacker.power = 100
        second = game.use_skill("10", "normal_physical_attack", "20")
        self.assertGreater(second.damage, first_damage)

        game = fresh([100])
        position_for_action(game, {"12": (2, 2), "20": (4, 2)})
        game.drop_ball((8, 4), "test")
        result = game.use_skill("12", "normal_magic_attack", "20")
        self.assertTrue(result.success)
        self.assertEqual(result.details["damage_calculation"]["system"], "magic")
        self.assertEqual(game.rng.rolls, [100])

    def test_attack_ball_drop_uses_power_rate_and_match_rng(self) -> None:
        game = fresh()
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        game.set_ball_holder("20", "test")
        game.rng = StubRandom([1, 1])

        result = game.use_skill("10", "normal_physical_attack", "20")

        effect = result.details["ball_effect"]
        self.assertEqual(effect["effect_type"], "drop")
        self.assertEqual(effect["actor_stat"], "power")
        self.assertTrue(effect["success"])
        self.assertIsNone(game.ball.holder_id)
        self.assertIsNotNone(game.ball.loose_position)

    def test_technique_cut_attack_damages_and_directly_takes_ball(self) -> None:
        game = fresh()
        actor = game.characters["12"]
        target = game.characters["20"]
        actor.mana = 2
        position_for_action(game, {actor.char_id: (3, 2), target.char_id: (4, 2)})
        game.set_ball_holder(target.char_id, "test")
        game.rng = StubRandom([1])

        result = game.use_skill(actor.char_id, "single_technique_ball_cut", target.char_id)

        self.assertGreater(result.damage, 0)
        effect = result.details["ball_effect"]
        self.assertEqual(effect["effect_type"], "cut")
        self.assertEqual(effect["actor_stat"], "technique")
        self.assertTrue(effect["success"])
        self.assertEqual(game.ball.holder_id, actor.char_id)

    def test_ball_hold_passives_follow_holder_and_manhattan_range(self) -> None:
        game = fresh()
        holder = game.characters["10"]
        ally = game.characters["11"]
        position_for_action(game, {holder.char_id: (3, 2), ally.char_id: (5, 2)})
        base_holder_power = holder.power
        base_holder_stamina = holder.stamina
        base_ally_power = ally.power

        game.set_ball_holder(holder.char_id, "test_hold")

        self.assertEqual(game.effective_stat(holder, "power"), min(10, base_holder_power + 2))
        self.assertEqual(game.effective_stat(holder, "stamina"), min(10, base_holder_stamina + 3))
        self.assertEqual(game.effective_stat(ally, "power"), min(10, base_ally_power + 2))
        ally.position = (6, 2)
        self.assertEqual(game.effective_stat(ally, "power"), base_ally_power)
        ally.position = (4, 2)
        self.assertEqual(game.effective_stat(ally, "power"), min(10, base_ally_power + 2))
        game.drop_ball((6, 2), "test_drop")
        self.assertEqual(game.effective_stat(holder, "power"), base_holder_power)
        self.assertEqual(game.effective_stat(ally, "power"), base_ally_power)
        self.assertIn("ボール保持スキル発動", game.full_log_text())
        self.assertIn("ボール保持スキル解除", game.full_log_text())

    def test_ball_hold_modifier_uses_best_positive_and_negative_separately(self) -> None:
        game = fresh()
        holder = game.characters["10"]
        ally = game.characters["11"]
        position_for_action(game, {holder.char_id: (3, 2), ally.char_id: (4, 2)})
        source = game.skills["ball_hold_power_banner"]
        plus_three = replace(
            source, skill_id="test_hold_plus_three", name="test+3",
            effects=(replace(source.effects[0], skill_id="test_hold_plus_three", base_value=3),),
        )
        minus_one = replace(
            source, skill_id="test_hold_minus_one", name="test-1",
            effects=(replace(source.effects[0], skill_id="test_hold_minus_one", base_value=1, aux2="subtract"),),
        )
        minus_two = replace(
            source, skill_id="test_hold_minus_two", name="test-2",
            effects=(replace(source.effects[0], skill_id="test_hold_minus_two", base_value=2, aux2="subtract"),),
        )
        game.skills.update({skill.skill_id: skill for skill in (source, plus_three, minus_one, minus_two)})
        holder.skills = (source.skill_id, plus_three.skill_id, minus_one.skill_id, minus_two.skill_id)
        ally.temporary_effects["stat_modifiers"] = [{"stat": "power", "mode": "add", "value": 1}]
        game.set_ball_holder(holder.char_id, "test_stack")

        self.assertEqual(game.ball_hold_modifier(ally, "power"), 1)
        self.assertEqual(game.effective_stat(ally, "power"), min(10, ally.power + 1 + 1))

    def test_holder_damage_multiplier_and_move_passive_use_shared_previews(self) -> None:
        game = fresh()
        attacker = game.characters["10"]
        target = game.characters["20"]
        position_for_action(game, {attacker.char_id: (3, 2), target.char_id: (4, 2)})
        normal = game.damage_preview(attacker.char_id, target.char_id, 12, "physical")
        game.set_ball_holder(target.char_id, "test_damage")
        holding = game.damage_preview(attacker.char_id, target.char_id, 12, "physical")
        self.assertEqual(holding["ball_holder_damage_multiplier"], 1.5)
        self.assertEqual(holding["predicted_damage"], max(1, int(normal["predicted_damage"] * 1.5)))

        march_holder = game.characters["10"]
        ally = game.characters["11"]
        march_holder.skills = ("ball_hold_march_banner",)
        position_for_action(game, {march_holder.char_id: (3, 2), ally.char_id: (4, 2)})
        game.drop_ball((6, 2), "reset")
        base_move = game.effective_move_range(ally)
        game.set_ball_holder(march_holder.char_id, "test_march")
        self.assertEqual(game.effective_move_range(ally), base_move + 1)
        self.assertEqual(game.effective_stat(ally, "speed"), ally.speed)
        self.assertNotIn(
            "skill:ball_hold_march_banner",
            {candidate.action_id for group in ("attack", "move", "ball", "skill", "wait") for candidate in game.action_candidates(march_holder.char_id, group)},
        )

    def test_power_up_refreshes_without_stacking_and_expires_by_own_actions(self) -> None:
        game = fresh()
        actor = game.characters["10"]
        actor.mana = 5
        base_power = actor.power
        result = game.use_skill("10", "power_up", "10")
        self.assertTrue(result.success)
        self.assertEqual(actor.mana, 3)
        self.assertEqual(game.effective_stat(actor, "power"), base_power + 2)
        self.assertEqual(actor.skill_cooldowns["power_up"], 2)
        self.assertIn("クールタイム", game.can_use_skill("10", "power_up")[1])
        game.turn_order = ["10"]
        game.turn_index = 0
        game._begin_current_turn()
        self.assertEqual(actor.skill_cooldowns["power_up"], 1)
        game._begin_current_turn()
        self.assertNotIn("power_up", actor.skill_cooldowns)
        actor.skill_cooldowns.clear()
        actor.mana = 5
        game.use_skill("10", "power_up", "10")
        modifiers = actor.temporary_effects["stat_modifiers"]
        self.assertEqual(len(modifiers), 1)
        self.assertEqual(modifiers[0]["remaining"], 2)
        game._advance_temporary_effects(actor)
        game._advance_temporary_effects(actor)
        self.assertEqual(game.effective_stat(actor, "power"), base_power + 2)
        game._advance_temporary_effects(actor)
        self.assertEqual(game.effective_stat(actor, "power"), base_power)

    def test_common_hp_and_mp_recovery_apply_caps_cost_and_cooldown(self) -> None:
        game = fresh()
        position_for_action(game, {"11": (2, 2), "10": (2, 3)})
        healer = game.characters["11"]
        target = game.characters["10"]
        healer.mana = 8
        healer_stamina = healer.stamina
        target.hp = 1
        result = game.use_skill("11", "heal_hp", "10")
        self.assertTrue(result.success)
        self.assertGreater(result.healing, 0)
        self.assertLessEqual(target.hp, target.max_hp)
        self.assertEqual(healer.mana, 5)
        self.assertEqual(healer.stamina, healer_stamina)

        target.mana = 0
        result = game.use_skill("11", "recover_mp", "10")
        self.assertTrue(result.success)
        self.assertGreater(result.details["mana_recovery"], 0)
        self.assertLessEqual(target.mana, target.max_mana)
        self.assertEqual(healer.mana, 3)
        self.assertEqual(healer.stamina, healer_stamina)
        self.assertEqual(healer.skill_cooldowns["recover_mp"], 3)

    def test_pass_cut_reaction_waits_then_drops_ball_or_allows_pass(self) -> None:
        game = fresh([1, 11])
        position_for_action(game, {"11": (3, 1), "20": (1, 2), "21": (5, 2)})
        defender = game.characters["11"]
        defender.mana = 3
        defender.speed = 1
        game.set_ball_holder("20", "test")
        prepared = game.use_skill("11", "pass_cut", "11")
        self.assertTrue(prepared.success)
        self.assertIsNotNone(defender.reaction_skill)
        self.assertEqual(defender.mana, 1)
        result = game.pass_ball("20", "21", "physical")
        self.assertTrue(result.details["reaction_result"]["success"])
        self.assertIsNone(game.ball.holder_id)
        self.assertEqual(game.ball.loose_position, (3, 2))
        self.assertIsNone(defender.reaction_skill)

        game = fresh([11, 1])
        position_for_action(game, {"11": (3, 1), "20": (1, 2), "21": (5, 2)})
        defender = game.characters["11"]
        defender.mana = 3
        defender.speed = 1
        game.set_ball_holder("20", "test")
        game.use_skill("11", "pass_cut", "11")
        result = game.pass_ball("20", "21", "physical")
        self.assertFalse(result.details["reaction_result"]["success"])
        self.assertEqual(game.ball.holder_id, "21")
        self.assertIsNone(defender.reaction_skill)

        game = fresh()
        position_for_action(game, {"11": (3, 1)})
        defender = game.characters["11"]
        defender.mana = 3
        game.set_ball_holder("20", "test")
        game.use_skill("11", "pass_cut", "11")
        game.turn_order = ["11"]
        game.turn_index = 0
        game._begin_current_turn()
        self.assertIsNone(defender.reaction_skill)
        self.assertNotIn("pass_cut", defender.skill_cooldowns)

    def test_common_effect_order_use_limit_and_invalid_target_are_safe(self) -> None:
        game = fresh()
        actor = game.characters["10"]
        actor.mana = actor.max_mana - 1
        original = game.skills["power_up"]
        effects = (
            SkillEffect("power_up", 1, "success", "recover_mp", "self", base_value=1),
            SkillEffect("power_up", 2, "success", "modify_stat", "self", base_value=2, duration=2, aux1="power", aux2="add"),
        )
        game.skills["power_up"] = replace(original, effects=effects, cooldown=0, max_uses=1)
        result = game.use_skill("10", "power_up", "10")
        self.assertEqual([item["effect_order"] for item in result.details["effect_results"]], [1, 2])
        self.assertEqual(actor.skill_use_counts["power_up"], 1)
        self.assertIn("使用回数上限", game.can_use_skill("10", "power_up")[1])

        healer = game.characters["11"]
        healer.mana = 3
        full_hp = game.characters["10"]
        full_hp.hp = full_hp.max_hp
        before = healer.mana
        failed = game.use_skill("11", "heal_hp", "10")
        self.assertFalse(failed.success)
        self.assertEqual(healer.mana, before)
        self.assertEqual(healer.skill_use_counts.get("heal_hp", 0), 0)

        game = fresh()
        actor = game.characters["10"]
        actor.mana = actor.max_mana
        original = game.skills["power_up"]
        invalid_effect = SkillEffect("power_up", 1, "success", "unexpected", "self")
        game.skills["power_up"] = replace(original, effects=(invalid_effect,), cooldown=0)
        before_state = (actor.mana, dict(actor.skill_use_counts), dict(actor.temporary_effects))
        with self.assertLogs("manaball.match", level="ERROR"):
            failed = game.use_skill("10", "power_up", "10")
        self.assertFalse(failed.success)
        self.assertEqual((actor.mana, actor.skill_use_counts, actor.temporary_effects), before_state)

    def test_skill_effect_csv_rejects_more_than_five_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skill_effects.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "skill_id", "effect_order", "timing", "effect_type", "target", "condition",
                    "base_value", "stat1", "rate1", "stat2", "rate2", "duration", "distance", "aux1", "aux2",
                ])
                for order in range(1, 7):
                    writer.writerow(["too_many", order, "success", "heal_hp", "self", "always", 1, "", 0, "", 0, 0, 0, "", ""])
            with self.assertLogs("manaball.data", level="ERROR"):
                effects = load_skill_effects(path)
        self.assertEqual(effects["too_many"], ())

    def test_ai_planner_generates_and_executes_common_skill_path(self) -> None:
        game = fresh()
        actor = game.characters["10"]
        actor.mana = actor.max_mana
        position_for_action(game, {"10": (0, 0)})
        game.drop_ball((8, 4), "test")
        plan = next(item for item in game._build_ai_plans(actor) if item.skill_id == "power_up" and item.destination == actor.position)
        result = game._execute_ai_plan(actor, plan)
        self.assertEqual(result.details["skill_id"], "power_up")

    def test_ai_uses_new_push_and_long_pass_through_common_paths(self) -> None:
        game = create_match(seed=3, skill_overrides={"10": ("single_power_push",)})
        actor = game.characters["10"]
        actor.mana = actor.max_mana
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        game.drop_ball((8, 4), "test")
        push_plan = next(
            item for item in game._build_ai_plans(actor)
            if item.skill_id == "single_power_push" and item.destination == actor.position
        )
        pushed = game._execute_ai_plan(actor, push_plan)
        self.assertEqual(pushed.details["skill_id"], "single_power_push")
        self.assertEqual(game.characters["20"].position, (5, 2))

        game = create_match(seed=3, skill_overrides={"10": ("single_technique_long_pass",)})
        game.rng = StubRandom([100, 100, 100])
        actor = game.characters["10"]
        receiver = game.characters["11"]
        actor.mana = actor.max_mana
        position_for_action(game, {"10": (3, 2), "11": (7, 2), "21": (9, 0)})
        game.set_ball_holder("10", "test")
        pass_plan = next(item for item in game._build_ai_plans(actor) if item.skill_id == "single_technique_long_pass")
        if pass_plan.destination != actor.position:
            self.assertTrue(game.move_character(actor.char_id, pass_plan.destination).success)
        passed = game._execute_ai_plan(actor, pass_plan)
        self.assertEqual(passed.details["skill_id"], "single_technique_long_pass")
        self.assertTrue(passed.consumed)

    def test_defend_and_shield_reduce_damage_after_basic_damage(self) -> None:
        game = fresh()
        position_for_action(game, {"10": (3, 2), "20": (4, 2), "21": (4, 3)})
        target = game.characters["20"]
        protector = game.characters["21"]
        target.defending = True
        protector.mana = 1
        game.set_ball_holder("20", "test")
        preview = game.damage_preview("10", "20")
        expected = max(1, int(preview["basic_damage"] * game.config.defend_multiplier))
        expected = max(1, int(expected * game.config.shield_multiplier))
        result = game.normal_attack("10", "20")
        self.assertEqual(result.damage, expected)
        self.assertEqual(
            [item["name"] for item in result.details["damage_calculation"]["reductions"]],
            ["防御", "シールドガード", "ボール保持補正"],
        )
        self.assertEqual(protector.mana, 0)

    def test_cut_preview_and_resolution_share_values_and_preserve_holder_on_failure(self) -> None:
        game = fresh([1])
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        game.set_ball_holder("20", "test")
        preview = game.cut_preview("10", "20", "physical")
        self.assertEqual(
            preview["cut_value"],
            game.characters["10"].technique + game.characters["10"].physical // 2 + game.characters["10"].ball_cut,
        )
        result = game.cut_ball("10", "20", "physical")
        self.assertEqual(result.details["success_rate"], preview["success_rate"])
        self.assertEqual(result.details["random_roll"], 1)
        self.assertEqual(game.ball.holder_id, "10")

        game = fresh([100])
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        game.set_ball_holder("20", "test")
        result = game.cut_ball("10", "20", "magic")
        self.assertFalse(result.details["success"])
        self.assertEqual(game.ball.holder_id, "20")
        self.assertTrue(result.consumed)

        game = fresh()
        position_for_action(game, {"10": (3, 2), "20": (4, 3)})
        game.set_ball_holder("20", "test")
        result = game.cut_ball("10", "20", "physical")
        self.assertFalse(result.success)
        self.assertFalse(result.consumed)
        self.assertEqual(game.ball.holder_id, "20")

    def test_pass_range_and_distance_penalty_use_primary_system(self) -> None:
        game = fresh()
        passer = game.characters["10"]
        passer.physical = 1
        passer.magic = 10
        self.assertEqual(game.pass_range_for(passer, "physical"), 3)
        self.assertEqual(game.pass_range_for(passer, "magic"), 7)
        position_for_action(game, {"10": (3, 2), "11": (8, 2), "21": (9, 0)})
        game.set_ball_holder("10", "test")
        self.assertFalse(game.pass_validation("10", "11", "physical")[0])
        self.assertFalse(game.pass_validation("10", "11", "magic")[0])
        passer.primary_system = "magic"
        self.assertTrue(game.pass_validation("10", "11", "physical")[0])
        preview = game.pass_preview("10", "11", "magic")
        self.assertEqual(preview["distance"], 5)
        self.assertEqual(preview["distance_penalty"], 2)
        self.assertEqual(
            preview["pass_value"],
            passer.technique + passer.magic // 2 + passer.pass_power - 2,
        )

    def test_pass_cut_area_is_orthogonal_and_independent_of_speed(self) -> None:
        game = fresh()
        position_for_action(game, {"12": (1, 2), "10": (5, 2), "20": (3, 1)})
        game.set_ball_holder("12", "test")
        defender = game.characters["20"]
        defender.speed = 3
        self.assertIn("20", [item["character_id"] for item in game.pass_preview("12", "10")["candidates"]])
        defender.speed = 4
        self.assertIn("20", [item["character_id"] for item in game.pass_preview("12", "10")["candidates"]])
        defender.position = (3, 4)
        defender.speed = 8
        self.assertNotIn("20", [item["character_id"] for item in game.pass_preview("12", "10")["candidates"]])
        defender.position = (3, 0)
        defender.pass_cut_range = 2
        self.assertIn("20", [item["character_id"] for item in game.pass_preview("12", "10")["candidates"]])
        defender.pass_cut_range = 1
        self.assertNotIn("20", [item["character_id"] for item in game.pass_preview("12", "10")["candidates"]])

    def test_pass_cut_candidate_ties_use_speed_turn_order_then_fixed_id(self) -> None:
        game = fresh()
        position_for_action(game, {"12": (1, 2), "10": (5, 2), "20": (3, 1), "21": (3, 3)})
        game.set_ball_holder("12", "test")
        game.characters["20"].speed = 4
        game.characters["21"].speed = 7
        candidates = game.pass_preview("12", "10")["candidates"]
        self.assertEqual(candidates[0]["character_id"], "21")
        game.characters["20"].speed = game.characters["21"].speed = 5
        expected = min(("20", "21"), key=game.turn_order.index)
        candidates = game.pass_preview("12", "10")["candidates"]
        self.assertEqual(candidates[0]["character_id"], expected)
        game.turn_order = [char_id for char_id in game.turn_order if char_id not in {"20", "21"}]
        candidates = game.pass_preview("12", "10")["candidates"]
        self.assertEqual(candidates[0]["character_id"], "20")

    def test_pass_without_cut_candidates_is_certain_and_uses_no_random_roll(self) -> None:
        game = fresh([100])
        position_for_action(game, {"12": (1, 2), "10": (3, 2)})
        game.set_ball_holder("12", "test")
        result = game.pass_ball("12", "10", "physical")
        self.assertEqual(result.details["candidates"], [])
        self.assertEqual(result.details["pass_cut_results"], [])
        self.assertEqual(game.ball.holder_id, "10")
        self.assertEqual(game.rng.rolls, [100])

    def test_wait_does_not_change_stamina_ability(self) -> None:
        game = fresh()
        actor = game.current_actor
        actor.stamina = 0
        result = game.wait(actor.char_id)
        self.assertTrue(result.success)
        self.assertEqual(actor.stamina, 0)
        actor.stamina = 7
        game.wait(actor.char_id)
        self.assertEqual(actor.stamina, 7)

    def test_steal_ends_action_without_damage_or_extra_move(self) -> None:
        game = fresh([10, 1])
        position_for_action(game, {"12": (3, 2), "20": (4, 2)})
        rogue = game.characters["12"]
        holder = game.characters["20"]
        rogue.mana = 2
        stamina = rogue.stamina
        game.set_ball_holder("20", "test")
        hp = holder.hp
        result = game.use_steal("12", "20")
        self.assertTrue(result.success)
        self.assertTrue(result.consumed)
        self.assertFalse(result.extra_action)
        self.assertEqual(holder.hp, hp)
        self.assertEqual(game.ball.holder_id, "12")
        self.assertEqual(rogue.stamina, stamina)
        self.assertEqual(rogue.mana, 0)

        game = fresh([1, 10])
        position_for_action(game, {"12": (3, 2), "20": (4, 2)})
        rogue = game.characters["12"]
        rogue.mana = 2
        game.set_ball_holder("20", "test")
        result = game.use_steal("12", "20")
        self.assertTrue(result.success)
        self.assertFalse(result.details["skill_success"])
        self.assertEqual(rogue.mana, 0)
        self.assertEqual(rogue.skill_use_counts["steal"], 1)
        self.assertEqual(game.ball.holder_id, "20")

    def test_steal_to_actor_on_goal_scores_immediately(self) -> None:
        game = fresh([100, 1])
        position_for_action(game, {
            "12": (10, 2),
            "20": (9, 2),
            "10": (0, 0),
            "11": (0, 1),
            "21": (0, 4),
            "22": (1, 4),
        })
        rogue = game.characters["12"]
        rogue.mana = 2
        game.set_ball_holder("20", "test")

        result = game.use_skill("12", "steal", "20")

        self.assertTrue(result.scored)
        self.assertEqual(game.scores[PLAYER], 1)
        self.assertEqual(game.restart_team, ENEMY)
        self.assertEqual(game.ball.holder_id, "12")
        self.assertEqual(result.details["skill_id"], "steal")

    def test_turn_start_goal_holder_check_scores_existing_holder(self) -> None:
        game = fresh()
        actor = game.current_actor
        goal_x = game.config.right_goal_x_start if actor.team == PLAYER else game.config.left_goal_x_end
        position_for_action(game, {actor.char_id: (goal_x, 2), "21": (0, 4), "22": (1, 4)})
        game.set_ball_holder(actor.char_id, "test")

        result = game.ai_take_turn(advance=False, allow_move=False)

        self.assertTrue(result.scored)
        self.assertEqual(game.scores[PLAYER], 1)
        self.assertEqual(result.details["action_name"], "得点")

    def test_push_strike_and_breakthrough_apply_class_features(self) -> None:
        game = fresh([8, 1, 1])
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        game.characters["10"].stamina = 2
        result = game.push_strike("10", "20")
        self.assertTrue(result.details["automatic_hit"])
        self.assertEqual(game.characters["20"].position, (5, 2))

        game = fresh([10, 1, 1])
        position_for_action(game, {"20": (4, 2), "10": (3, 2)})
        game.characters["20"].stamina = 2
        game.set_ball_holder("21", "test")
        result = game.breakthrough_skill("20", "10")
        self.assertTrue(result.details["contest"]["success"])
        self.assertEqual(game.characters["10"].position, (2, 2))
        self.assertEqual(game.characters["20"].position, (3, 2))

    def test_attribute_advantage_uses_high_and_disadvantage_low(self) -> None:
        game = fresh([1, 3])
        fire = game.characters["10"]
        wind = game.characters["12"]
        advantage = game._attribute_dice(fire, wind)
        self.assertEqual(advantage["relation"], "advantage")
        self.assertEqual(advantage["bonus"], 3)
        game.rng = StubRandom([1, 3])
        water = game.characters["11"]
        disadvantage = game._attribute_dice(fire, water)
        self.assertEqual(disadvantage["relation"], "disadvantage")
        self.assertEqual(disadvantage["bonus"], 1)

    def test_elemental_magic_is_automatic_hit_and_applies_movement_down(self) -> None:
        game = fresh([100])
        position_for_action(game, {"22": (4, 2), "10": (2, 2)})
        mage = game.characters["22"]
        mage.mana = 2
        result = game.elemental_bolt("22", "10")
        self.assertTrue(result.details["automatic_hit"])
        self.assertEqual(result.details["damage_calculation"]["system"], "magic")
        self.assertEqual(game.characters["10"].temporary_effects["movement_down"], 1)
        self.assertEqual(game.rng.rolls, [100])

    def test_heal_is_available_while_holding_ball(self) -> None:
        game = fresh([4])
        position_for_action(game, {"11": (2, 2), "10": (2, 3)})
        healer = game.characters["11"]
        target = game.characters["10"]
        healer.mana = 2
        target.hp -= 5
        game.set_ball_holder("11", "test")
        self.assertTrue(game.can_use_skill("11", "heal")[0])
        result = game.heal("11", "10")
        self.assertTrue(result.success)
        self.assertEqual(target.hp, target.max_hp)

    def test_hp_mp_persist_between_rounds_and_score_recovers_half(self) -> None:
        game = fresh()
        player = game.characters["10"]
        knocked_out = game.characters["20"]
        player.hp = 5
        player.mana = 0
        player.stamina = 0
        knocked_out.hp = 0
        knocked_out.mana = 0
        knocked_out.stamina = 0
        knocked_out.off_field = True
        knocked_out.position = None
        game.round += 1
        game.start_round()
        self.assertEqual(player.hp, 5)
        self.assertTrue(knocked_out.off_field)

        position_for_action(game, {"12": (9, 2), "21": (8, 3)})
        knocked_out.off_field = True
        knocked_out.position = None
        game.set_ball_holder("12", "test")
        result = game.move_character("12", (10, 2))
        self.assertTrue(result.scored)
        self.assertEqual(player.hp, 5)
        self.assertEqual(game.restart_team, "enemy")
        game.prepare_restart_after_goal()
        self.assertEqual(player.hp, 5 + 17)
        self.assertEqual(player.mana, 3)
        self.assertEqual(player.stamina, 0)
        self.assertFalse(knocked_out.off_field)
        self.assertEqual(knocked_out.hp, 16)
        self.assertEqual(knocked_out.mana, 3)
        self.assertEqual(knocked_out.stamina, 0)
        self.assertIsNone(game.ball.holder_id)
        self.assertIsNone(game.ball_position)
        restart = game.auto_select_restart_holder()
        self.assertTrue(restart.success)
        self.assertEqual(game.ball_team, "enemy")

    def test_round_cycle_continues_past_legacy_max_without_reset(self) -> None:
        game = fresh()
        game.round = game.config.max_rounds
        game.characters["10"].position = (2, 1)
        game.set_ball_holder("10", "test")
        positions = {char_id: character.position for char_id, character in game.characters.items()}
        round_before = game.round
        while game.round == round_before:
            actor = game.current_actor
            self.assertIsNotNone(actor)
            game.advance_turn(actor.char_id)
        self.assertFalse(game.match_over)
        self.assertEqual(game.round, round_before + 1)
        self.assertEqual({char_id: character.position for char_id, character in game.characters.items()}, positions)
        self.assertEqual(game.ball.holder_id, "10")

    def test_staged_move_delays_pickup_and_can_restore_snapshot(self) -> None:
        game = fresh()
        actor = game.current_actor
        position_for_action(game, {actor.char_id: (3, 2)})
        game.drop_ball((4, 2), "test")
        prepared = game.prepare_move(actor.char_id, (4, 2))
        self.assertTrue(prepared.success)
        self.assertEqual(actor.position, (3, 2))
        self.assertIsNone(game.ball.holder_id)
        arrived = game.arrive_prepared_move(actor.char_id)
        self.assertTrue(arrived.success)
        self.assertEqual(actor.position, (4, 2))
        self.assertEqual(game.ball.holder_id, actor.char_id)
        cancelled = game.cancel_pending_move(actor.char_id)
        self.assertTrue(cancelled.success)
        self.assertEqual(actor.position, (3, 2))
        self.assertIsNone(game.ball.holder_id)
        self.assertEqual(game.ball_position, (4, 2))

    def test_normal_pass_is_allowed_while_move_is_pending(self) -> None:
        game = fresh()
        actor = game.current_actor
        receiver = next(character for character in game.active_characters(actor.team) if character is not actor)
        position_for_action(game, {actor.char_id: (2, 2), receiver.char_id: (4, 2)})
        game.set_ball_holder(actor.char_id, "test")
        destination = (3, 2)
        self.assertIn(destination, game.reachable_positions(actor.char_id))
        game.prepare_move(actor.char_id, destination)
        game.arrive_prepared_move(actor.char_id)

        valid, reason = game.pass_validation(actor.char_id, receiver.char_id)
        self.assertTrue(valid, reason)
        candidates = game.action_candidates(actor.char_id, "ball")
        normal_pass = next(candidate for candidate in candidates if candidate.action_id.startswith("standard:pass:"))
        self.assertTrue(normal_pass.usable)
        self.assertTrue(normal_pass.usable_after_move)

        system = actor.primary_system if actor.primary_system in {"physical", "magic"} else "physical"
        result = game.pass_ball(actor.char_id, receiver.char_id, system)
        self.assertTrue(result.success)
        self.assertIsNone(game.pending_move)
        self.assertTrue(result.consumed)
        self.assertTrue(result.details["move_before_pass"])
        self.assertEqual(result.details["move_path"][-1], destination)
        self.assertEqual(game.ball.holder_id, receiver.char_id)

    def test_pass_skill_is_allowed_and_commits_move_only_on_execution(self) -> None:
        game = fresh()
        actor = game.current_actor
        receiver = next(character for character in game.active_characters(actor.team) if character is not actor)
        actor.skills = ("single_technique_long_pass",)
        actor.primary_system = "physical"
        actor.mana = actor.max_mana
        position_for_action(game, {actor.char_id: (2, 2), receiver.char_id: (5, 2)})
        game.set_ball_holder(actor.char_id, "test")
        game.prepare_move(actor.char_id, (3, 2))
        game.arrive_prepared_move(actor.char_id)

        self.assertTrue(game.can_use_skill(actor.char_id, "single_technique_long_pass")[0])
        preview = game.skill_pass_preview(actor.char_id, receiver.char_id, "single_technique_long_pass", actor.primary_system)
        self.assertTrue(preview["valid"])
        self.assertIsNotNone(game.pending_move)
        result = game.use_skill(actor.char_id, "single_technique_long_pass", receiver.char_id, actor.primary_system)
        self.assertTrue(result.success)
        self.assertIsNone(game.pending_move)
        self.assertTrue(result.details["move_before_pass"])

    def test_skill_usable_after_move_false_blocks_without_committing_pending_move(self) -> None:
        game = fresh()
        actor = game.current_actor
        actor.skills = ("power_up",)
        actor.mana = 5
        original = game.skills["power_up"]
        game.skills["power_up"] = replace(original, usable_after_move=False)
        position_for_action(game, {actor.char_id: (2, 2)})
        game.prepare_move(actor.char_id, (3, 2))
        game.arrive_prepared_move(actor.char_id)

        usable, reason = game.can_use_skill(actor.char_id, "power_up")
        result = game.use_skill(actor.char_id, "power_up", actor.char_id)
        self.assertFalse(usable)
        self.assertIn("移動後", reason)
        self.assertFalse(result.success)
        self.assertEqual(actor.position, (3, 2))
        self.assertIsNotNone(game.pending_move)
        self.assertEqual(actor.mana, 5)

    def test_goal_is_confirmed_after_arrival_and_restart_holder_selection(self) -> None:
        game = fresh()
        scorer = game.characters["12"]
        enemy = game.characters["20"]
        position_for_action(game, {"12": (9, 2), "20": (7, 4), "21": (8, 3)})
        game.set_ball_holder("12", "test")
        game.prepare_move("12", (10, 2))
        self.assertEqual(game.scores["player"], 0)
        self.assertEqual(enemy.position, (7, 4))
        game.arrive_prepared_move("12")
        self.assertEqual(game.scores["player"], 0)
        game.cancel_pending_move("12")
        self.assertEqual(game.scores["player"], 0)
        game.prepare_move("12", (10, 2))
        game.arrive_prepared_move("12")
        scored = game.commit_pending_move("12")
        self.assertTrue(scored.scored)
        self.assertEqual(game.scores["player"], 1)
        self.assertEqual(enemy.position, (7, 4))
        game.prepare_restart_after_goal()
        self.assertEqual(enemy.position, enemy.initial_position)
        candidates = game.restart_candidates()
        selected = max(
            candidates,
            key=lambda unit: (
                max(unit.physical + unit.technique, unit.magic + unit.technique),
                unit.max_hp,
                -list(game.characters).index(unit.char_id),
            ),
        )
        restarted = game.auto_select_restart_holder()
        self.assertTrue(restarted.success)
        self.assertEqual(game.ball.holder_id, selected.char_id)
        self.assertIsNotNone(game.current_actor)

    def test_all_nine_goal_cells_score_and_deep_goal_move_reaches_selected_cell(self) -> None:
        probe = fresh()
        for goal in probe.goal_cells(PLAYER):
            game = fresh()
            scorer = game.characters["12"]
            scorer.position = goal
            game.set_ball_holder(scorer.char_id, "test")
            result = game.score_holder_in_goal()
            self.assertIsNotNone(result, goal)
            self.assertTrue(result.scored, goal)

        game = fresh()
        position_for_action(game, {"12": (9, 2), "21": (8, 3)})
        game.set_ball_holder("12", "test")
        prepared = game.prepare_move("12", (12, 2), ignore_zoc=True)
        self.assertTrue(prepared.success)
        self.assertEqual(game.prepared_move.destination, (12, 2))
        self.assertEqual(game.prepared_move.path[-1], (12, 2))
        self.assertEqual(game.scores[PLAYER], 0)
        self.assertTrue(game.arrive_prepared_move("12").success)
        self.assertEqual(game.characters["12"].position, (12, 2))
        self.assertEqual(game.scores[PLAYER], 0)
        result = game.commit_pending_move("12")
        self.assertTrue(result.scored)
        self.assertEqual(game.characters["12"].position, (12, 2))
        self.assertFalse(game.cancel_pending_move("12").success)

    def test_push_and_breakthrough_score_through_shared_goal_check(self) -> None:
        pushed = fresh()
        pushed.rng = StubRandom([100])
        position_for_action(pushed, {"21": (8, 2), "12": (9, 2)})
        pushed.set_ball_holder("12", "test")
        push_result = pushed.push_strike("21", "12")
        self.assertTrue(push_result.scored)
        self.assertEqual(pushed.characters["12"].position, (10, 2))

        breakthrough = create_match(
            seed=3,
            player_ids=("5", "11", "12"),
            enemy_ids=("10", "21", "22"),
        )
        breakthrough.rng = StubRandom([100, 1])
        breakthrough.characters["5"].position = (9, 2)
        breakthrough.characters["10"].position = (10, 2)
        breakthrough.set_ball_holder("5", "test")
        breakthrough_result = breakthrough.breakthrough_skill("5", "10")
        self.assertTrue(breakthrough_result.scored)
        self.assertEqual(breakthrough.characters["5"].position, (10, 2))

    def test_ball_holder_can_attack_and_keeps_ball(self) -> None:
        game = fresh()
        actor = game.characters["10"]
        target = game.characters["20"]
        position_for_action(game, {actor.char_id: (3, 2), target.char_id: (4, 2)})
        game.set_ball_holder(actor.char_id, "test")

        self.assertIn(target, game.basic_attack_targets(actor.char_id))
        result = game.use_basic_attack(actor.char_id, target.char_id)

        self.assertTrue(result.success)
        self.assertEqual(game.ball.holder_id, actor.char_id)
        self.assertIsNone(game.ball.loose_position)

    def test_ball_holder_knockout_always_drops_to_adjacent_legal_cell(self) -> None:
        ranged = fresh()
        attacker = ranged.characters["12"]
        holder = ranged.characters["20"]
        position_for_action(ranged, {attacker.char_id: (3, 2), holder.char_id: (5, 2)})
        ranged.set_ball_holder(holder.char_id, "test")
        holder.hp = 1

        result = ranged.use_skill(attacker.char_id, "normal_magic_attack", holder.char_id)

        self.assertTrue(result.success)
        self.assertTrue(holder.off_field)
        self.assertIsNone(ranged.ball.holder_id)
        self.assertLessEqual(max(abs(ranged.ball.loose_position[0] - 5), abs(ranged.ball.loose_position[1] - 2)), 1)
        self.assertEqual(result.details["ball_drop_reason"], "ranged_knockout")
        self.assertEqual(result.details["attack_distance"], 2)
        self.assertIn("遠距離撃破ドロップ", ranged.full_log_text())
        record = ranged.report.action_records[-1]
        self.assertEqual(record["result"]["details"]["ball_drop_position"], ranged.ball.loose_position)
        self.assertIsNone(ranged.score_holder_in_goal())

        adjacent = fresh()
        close_attacker = adjacent.characters["10"]
        close_holder = adjacent.characters["20"]
        position_for_action(adjacent, {close_attacker.char_id: (3, 2), close_holder.char_id: (4, 2)})
        adjacent.set_ball_holder(close_holder.char_id, "test")
        close_holder.hp = 1

        close_result = adjacent.use_skill(
            close_attacker.char_id,
            "normal_physical_attack",
            close_holder.char_id,
        )

        self.assertTrue(close_result.success)
        self.assertIsNone(adjacent.ball.holder_id)
        self.assertLessEqual(max(abs(adjacent.ball.loose_position[0] - 4), abs(adjacent.ball.loose_position[1] - 2)), 1)

    def test_move_skills_are_blocked_after_move_but_support_skills_remain_available(self) -> None:
        game = create_match(
            seed=3,
            skill_overrides={"10": ("power_up", "single_speed_boost", "shadow_step", "breakthrough")},
        )
        actor = game.characters["10"]
        actor.mana = actor.max_mana
        position_for_action(game, {actor.char_id: (3, 2), "20": (5, 2)})
        game.prepare_move(actor.char_id, (4, 2))
        game.arrive_prepared_move(actor.char_id)
        before = (actor.hp, actor.mana, actor.position, game.ball.holder_id, dict(actor.skill_cooldowns))

        self.assertTrue(game.can_use_skill(actor.char_id, "power_up")[0])
        self.assertTrue(game.can_use_skill(actor.char_id, "single_speed_boost")[0])
        for skill_id in ("shadow_step", "breakthrough"):
            usable, reason = game.can_use_skill(actor.char_id, skill_id)
            self.assertFalse(usable)
            self.assertEqual(reason, "移動後は使用できません")
            self.assertFalse(game.use_skill(actor.char_id, skill_id, "20").success)
            self.assertEqual(
                (actor.hp, actor.mana, actor.position, game.ball.holder_id, actor.skill_cooldowns),
                before,
            )

    def test_invalid_goal_configuration_and_goal_initial_position_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "重複"):
            create_match(config_overrides={"left_goal_x_end": 10})
        with self.assertRaisesRegex(ValueError, "ゴール領域内"):
            create_match(config_overrides={"player_positions": ((2, 1), (3, 2), (3, 3))})

    def test_knockout_does_not_return_before_score(self) -> None:
        game = fresh([8, 1, 6])
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})
        target = game.characters["20"]
        target.hp = 1
        game.normal_attack("10", "20")
        self.assertTrue(target.off_field)
        game.round += 1
        game.start_round()
        game.round += 1
        game.start_round()
        self.assertTrue(target.off_field)
        injury_rate = target.injury_rate
        game.restart_team = PLAYER
        self.assertTrue(game.prepare_restart_after_goal().success)
        self.assertFalse(target.off_field)
        self.assertEqual(target.injury_rate, injury_rate)
        self.assertEqual(target.return_rounds, 0)
        metrics = game.report.balance_metrics()
        self.assertEqual(metrics["total_knockouts"], 1)
        self.assertEqual(metrics["score_reset_returns"], 1)
        self.assertEqual(metrics["natural_returns"], 0)

    def test_invalid_pass_or_skill_does_not_spend_mp(self) -> None:
        game = fresh()
        position_for_action(game, {"12": (4, 2), "10": (8, 3), "21": (7, 2)})
        rogue = game.characters["12"]
        rogue.mana = 1
        game.set_ball_holder("12", "test")
        result = game.pass_ball("12", "10", quick=True)
        self.assertFalse(result.success)
        self.assertEqual(rogue.mana, 1)

    def test_ball_consistency_releases_invalid_holder(self) -> None:
        game = fresh()
        holder = game.characters["10"]
        game.set_ball_holder(holder.char_id, "test")
        holder.off_field = True
        holder.position = None
        game.ball.loose_position = (2, 2)
        game.ensure_ball_consistency()
        self.assertIsNone(game.ball.holder_id)
        self.assertEqual(game.ball_position, (2, 2))

    def test_full_log_keeps_the_whole_match(self) -> None:
        game = fresh()
        game.events.clear()
        for index in range(250):
            game._log(f"event-{index}")
        self.assertEqual(len(game.events), 250)
        self.assertTrue(game.full_log_text().startswith("event-0\n"))
        self.assertTrue(game.full_log_text().endswith("event-249"))

    def test_ai_completes_match_without_illegal_stall(self) -> None:
        game = create_match(seed=7)
        actions = 0
        while not game.match_over and actions < 200:
            result = game.ai_take_turn()
            self.assertTrue(result.success)
            actions += 1
        self.assertTrue(game.match_over)
        self.assertLess(actions, 200)
        self.assertIn(game.config.target_score, game.scores.values())

    def test_retire_distinguishes_normal_loss_and_debug_interrupt(self) -> None:
        game = fresh()
        result = game.retire(PLAYER)
        self.assertTrue(result.success)
        self.assertTrue(game.match_over)
        self.assertEqual(game.winner, ENEMY)
        self.assertEqual(game.end_reason, "retire")
        self.assertEqual(game.result_summary()["end_reason_name"], "リタイア")
        duplicate = game.retire(PLAYER)
        self.assertFalse(duplicate.success)
        self.assertEqual(game.end_reason, "retire")

        debug_game = create_match(seed=3, config_overrides={"debug_mode": True})
        interrupted = debug_game.retire(PLAYER)
        self.assertTrue(interrupted.success)
        self.assertTrue(debug_game.match_over)
        self.assertIsNone(debug_game.winner)
        self.assertEqual(debug_game.end_reason, "debug_interrupt")

    def test_debug_match_uses_temporary_asymmetric_teams_parameters_ai_and_positions(self) -> None:
        master_before = {member.char_id: member for member in load_character_roster()}
        config_overrides = {
            "debug_mode": True,
            "player_team_size": 2,
            "enemy_team_size": 1,
            "target_score": 4,
            "turn_limit": 1,
            "player_positions": ((4, 1), (4, 3)),
            "enemy_positions": ((8, 2),),
        }
        game = create_match(
            seed=4,
            player_ids=("10", "11"),
            enemy_ids=("20",),
            enemy_ai_profile_ids=("defense",),
            config_overrides=config_overrides,
            character_overrides={
                "10": {
                    "physical": 10, "magic": 1, "power": 9, "speed": 8,
                    "technique": 7, "stamina": 6, "max_hp": 99, "max_mana": 12,
                }
            },
        )
        self.assertTrue(game.config.debug_mode)
        self.assertEqual((len(game.active_characters(PLAYER)), len(game.active_characters(ENEMY))), (2, 1))
        character = game.characters["10"]
        self.assertEqual(
            (character.physical, character.magic, character.power, character.speed, character.technique, character.stamina),
            (10, 1, 9, 8, 7, 6),
        )
        self.assertEqual((character.max_hp, character.max_mana), (99, 12))
        self.assertEqual(character.position, (4, 1))
        self.assertEqual(game.characters["20"].position, (8, 2))
        self.assertEqual(character.ai_profile_id, "")
        self.assertEqual(game.characters["20"].ai_profile_id, "defense")
        self.assertEqual(game.config.target_score, 4)
        while not game.match_over:
            actor = game.current_actor
            self.assertIsNotNone(actor)
            game.advance_turn(actor.char_id)
        self.assertEqual(game.end_reason, "turn_limit")
        self.assertIsNone(game.winner)

        master_after = {member.char_id: member for member in load_character_roster()}
        self.assertEqual(master_after["10"], master_before["10"])
        self.assertNotEqual(master_after["10"].max_hp, character.max_hp)

    def test_stamina_is_fixed_ability_and_legacy_max_stamina_column_still_loads(self) -> None:
        game = fresh()
        actor = game.current_actor
        before = actor.stamina
        game.wait(actor.char_id)
        self.assertEqual(actor.stamina, before)
        game._begin_current_turn()
        self.assertEqual(actor.stamina, before)
        self.assertNotIn("stamina", {skill.resource_type for skill in game.skills.values()})

        with (CSV_ROOT / "characters.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        fieldnames = ["max_stamina" if name == "stamina" else name for name in rows[0]]
        legacy_rows = []
        for row in rows:
            legacy = {("max_stamina" if key == "stamina" else key): value for key, value in row.items()}
            legacy_rows.append(legacy)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy_characters.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(legacy_rows)
            characters = load_characters(load_config(), load_classes(), load_elements(), path)
        self.assertTrue(all(1 <= character.stamina <= 10 for character in characters))

    def test_grid_line_handles_diagonal_and_axis_paths(self) -> None:
        self.assertEqual(straight_line_cells((4, 2), (1, 2)), [(4, 2), (3, 2), (2, 2), (1, 2)])
        self.assertEqual(straight_line_cells((1, 1), (3, 3)), [(1, 1), (2, 2), (3, 3)])


class AIPlannerTests(unittest.TestCase):
    def test_ball_state_and_role_are_derived_from_live_match_data(self) -> None:
        game = fresh()
        actor = game.current_actor
        game.ball.holder_id = actor.char_id
        self.assertEqual(game.ai_ball_state(actor), "self_possession")
        ally = next(unit for unit in game.active_characters(actor.team) if unit is not actor)
        game.ball.holder_id = ally.char_id
        self.assertEqual(game.ai_ball_state(actor), "ally_possession")
        enemy = game.active_characters(game.opponent(actor.team))[0]
        game.ball.holder_id = enemy.char_id
        self.assertEqual(game.ai_ball_state(actor), "enemy_possession")
        game.ball.holder_id = None
        self.assertEqual(game.ai_ball_state(actor), "loose_ball")
        self.assertIn(game.ai_role(actor), {"power", "technique", "support"})

    def test_scoring_plan_outranks_every_other_candidate(self) -> None:
        game = fresh()
        actor = game.current_actor
        actor.position = (9, 2) if actor.team == PLAYER else (3, 2)
        game.set_ball_holder(actor.char_id, "test")
        plans = game._build_ai_plans(actor)
        selected = min(plans, key=lambda item: (item.priority, -item.score))
        self.assertEqual(selected.action_type, "score")
        self.assertEqual(selected.priority, 0)

    def test_wait_is_lower_priority_than_a_useful_move(self) -> None:
        game = fresh()
        actor = game.current_actor
        game.ball.holder_id = None
        game.ball.loose_position = game.config.ball_position
        plans = game._build_ai_plans(actor)
        wait = next(plan for plan in plans if plan.action_type == "wait")
        useful = [plan for plan in plans if plan.action_type != "wait"]
        self.assertTrue(useful)
        self.assertLess(min(plan.priority for plan in useful), wait.priority)

    def test_unproductive_immediate_return_pass_is_filtered(self) -> None:
        game = fresh()
        actor = game.current_actor
        ally = next(unit for unit in game.active_characters(actor.team) if unit is not actor)
        game.ai_pass_history.append({"passer_id": ally.char_id, "receiver_id": actor.char_id})
        # Put the receiver no farther forward and no safer than the actor.
        ally.position = (actor.position[0] - game.attack_direction(actor.team), actor.position[1])
        self.assertTrue(game._ai_pass_is_return(actor, ally))

    def test_goal_direction_is_team_relative_and_symmetric(self) -> None:
        game = fresh()
        self.assertEqual(game.attack_direction(PLAYER), 1)
        self.assertEqual(game.attack_direction(ENEMY), -1)
        self.assertEqual(game.movement_direction(PLAYER, (4, 2), (5, 2)), "forward")
        self.assertEqual(game.movement_direction(ENEMY, (8, 2), (7, 2)), "forward")
        self.assertEqual(game.goal_distance(PLAYER, (4, 2)), game.goal_distance(ENEMY, (8, 2)))

    def test_left_right_mirrored_ball_holders_choose_mirrored_forward_moves(self) -> None:
        destinations = []
        for team, char_id, origin in ((PLAYER, "10", (4, 2)), (ENEMY, "21", (8, 2))):
            game = fresh()
            actor = game.characters[char_id]
            for unit in game.characters.values():
                unit.off_field = unit is not actor
                unit.position = None if unit is not actor else origin
            game.ball.holder_id = actor.char_id
            game.ball.loose_position = None
            plans = game._build_ai_plans(actor)
            selected = min(plans, key=lambda item: (item.priority, -item.score, item.destination))
            self.assertNotEqual(game.movement_direction(team, origin, selected.destination), "backward")
            destinations.append(selected.destination)
        self.assertEqual(destinations[0][0] + destinations[1][0], 12)

    def test_power_unit_does_not_retreat_or_buff_when_enemy_holder_is_attackable(self) -> None:
        game = fresh()
        actor = game.characters["10"]
        holder = game.characters["20"]
        actor.position = (5, 2)
        holder.position = (6, 2)
        actor.mana = actor.max_mana
        game.ball.holder_id = holder.char_id
        game.turn_order = [actor.char_id]
        game.turn_index = 0
        plans = game._build_ai_plans(actor)
        self.assertFalse(any(plan.purpose == "buff" for plan in plans))
        selected = min(plans, key=lambda item: (item.priority, -item.score, item.destination))
        self.assertEqual(selected.target_id, holder.char_id)
        self.assertIn(selected.purpose, {"holder_kill", "holder_attack", "cut", "steal"})
        self.assertLessEqual(manhattan(selected.destination, holder.position), manhattan(actor.position, holder.position))

    def test_score_restart_discards_ai_history_and_keeps_team_direction(self) -> None:
        game = fresh()
        scorer = game.characters["10"]
        scorer.position = game.target_goal_cells(PLAYER)[0]
        game.set_ball_holder(scorer.char_id, "test")
        game.ai_pass_history.append({"passer_id": "10", "receiver_id": "11"})
        game.ai_action_history["10"] = [{"destination": (9, 2)}]
        game.ai_pending_plans["10"] = game._build_ai_plans(scorer)[0]
        result = game.score_holder_in_goal()
        self.assertTrue(result.scored)
        self.assertTrue(game.prepare_restart_after_goal().success)
        self.assertFalse(game.ai_pass_history)
        self.assertFalse(game.ai_action_history)
        self.assertFalse(game.ai_pending_plans)
        self.assertEqual(game.attack_direction(PLAYER), 1)
        self.assertEqual(game.attack_direction(ENEMY), -1)

    def test_ball_hold_state_and_calculation_reference_are_reported(self) -> None:
        game = fresh()
        holder = game.characters["20"]
        attacker = game.characters["10"]
        holder.skills = (*holder.skills, "ball_hold_iron_self")
        position_for_action(game, {"10": (3, 2), "20": (4, 2)})

        game.set_ball_holder(holder.char_id, "test")
        self.assertTrue(any(item["event"] == "適用" for item in game.ball_hold_events))
        result = game.normal_attack(attacker.char_id, holder.char_id)

        references = result.details["damage_calculation"]["stamina"]
        self.assertEqual(references, game.effective_stat(holder, "stamina"))
        record = game.report.action_records[-1]
        hold_references = record["result"]["details"]["ball_hold_modifiers"]
        stamina = next(item for item in hold_references if item["stat"] == "stamina")
        self.assertEqual(stamina["adopted_value"], 3)
        self.assertEqual(stamina["effective_value"], stamina["base_value"] + 3)

        game.drop_ball(holder.position, "test_drop")
        self.assertTrue(any(item["event"] == "解除" for item in game.ball_hold_events))

    def test_scoring_pass_keeps_pass_success_and_links_score_event(self) -> None:
        game = fresh()
        position_for_action(game, {
            "10": (6, 2), "11": (10, 2), "12": (1, 0),
            "20": (0, 0), "21": (0, 4), "22": (1, 4),
        })
        game.set_ball_holder("10", "test")

        result = game.pass_ball("10", "11")

        self.assertTrue(result.scored)
        action = next(record for record in game.report.action_records if record.get("record_type") == "character_action")
        score = next(record for record in game.report.action_records if record.get("record_type") == "system_event" and record.get("event_type") == "score")
        self.assertTrue(action["result"]["details"]["pass_success"])
        self.assertEqual(action["related_score_event_id"], score["score_event_id"])
        self.assertEqual(score["source_action_id"], action["source_action_id"])
        self.assertTrue(score["score_change_authoritative"])
        self.assertEqual(game.report._count_pass_successes(), 1)

    def test_report_distinguishes_start_resources_and_bench_counts(self) -> None:
        game = fresh()
        character = game.characters["10"]
        character.mana = 0
        snapshot = game.report._capture_start_snapshot(game)
        snapshot["characters"][0]["start_mp"] = 0
        game.report.start_snapshot = snapshot
        text = game.report._build_markdown(game, "test")

        self.assertIn("味方出場人数", text)
        self.assertIn("味方パーティー人数", text)
        self.assertIn("味方ベンチ人数", text)
        self.assertRegex(text, r"MP 0 / \d+")

    def test_steal_result_records_rate_roll_and_holder_transition_without_no_effect(self) -> None:
        for roll, expected_success in ((1, True), (100, False)):
            game = fresh([roll])
            position_for_action(game, {"12": (3, 2), "20": (4, 2)})
            actor, target = game.characters["12"], game.characters["20"]
            actor.mana = 2
            game.set_ball_holder(target.char_id, "test")
            result = game.use_skill(actor.char_id, "steal", target.char_id)

            self.assertNotIn("効果なし", result.message)
            self.assertEqual(result.details["skill_success"], expected_success)
            self.assertEqual(result.details["random_roll"], roll)
            self.assertEqual(result.details["holder_before"], target.char_id)
            self.assertEqual(
                result.details["holder_after"], actor.char_id if expected_success else target.char_id,
            )

    def test_pass_preview_is_direction_symmetric_and_does_not_advance_rng(self) -> None:
        game = fresh()
        pairs = [((2, 2), (8, 2)), ((8, 2), (2, 2)), ((5, 0), (5, 4)), ((5, 4), (5, 0))]
        for start, end in pairs:
            positions = {"12": start, "10": end, "20": (5, 2), "11": (0, 0), "21": (10, 0), "22": (10, 4)}
            for char_id, position in positions.items():
                game.characters[char_id].position = position
            before = list(game.rng.rolls)
            preview = game.pass_preview("12", "10", "physical")
            self.assertTrue(preview["valid"])
            self.assertEqual(game.rng.rolls, before)
            self.assertEqual(len({item["character_id"] for item in preview["candidates"]}), len(preview["candidates"]))


if __name__ == "__main__":
    unittest.main()

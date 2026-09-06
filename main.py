"""Launch Mana's Ball or run its non-interactive verification modes."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import json
from pathlib import Path
from datetime import datetime

# Keep this direct import visible to pygbag's dependency scanner.
import pygame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mana's Ball variable-team prototype")
    parser.add_argument("--smoke-test", action="store_true", help="render several frames with dummy video")
    parser.add_argument("--headless-sim", action="store_true", help="run one AI-only match")
    parser.add_argument("--balance-30", action="store_true", help="run reproducible auto matches with seeds 1..30")
    parser.add_argument("--seed", type=int, default=7, help="random seed for reproducible runs")
    parser.add_argument("--enemy-group", default=None, help="enemy group ID used by --headless-sim")
    parser.add_argument("--quest", default=None, help="quest ID used to load field, rules, and enemy group")
    parser.add_argument("--log-policy", choices=("play", "ci", "test"), default="test", help="match report output policy")
    return parser.parse_args()


def run_headless(seed: int, enemy_group_id: str | None = None, quest_id: str | None = None, log_policy: str = "test") -> int:
    from manaball.data import create_match

    game = create_match(seed, enemy_group_id=enemy_group_id, quest_id=quest_id, report_policy=log_policy)
    game.report.set_execution_mode("headless")
    safety = 0
    while not game.match_over and safety < 200:
        game.ai_take_turn()
        safety += 1
    summary = game.result_summary()
    print(
        "HEADLESS_RESULT",
        f"score={summary['player_score']}-{summary['enemy_score']}",
        f"round={summary['rounds']}",
        f"winner={summary['winner'] or 'draw'}",
        f"actions={safety}",
    )
    if not game.match_over:
        print("ERROR: safety limit reached before match end")
        return 1
    return 0


def run_balance_30(enemy_group_id: str | None = None, quest_id: str | None = None) -> int:
    from manaball.data import create_match

    batch_started = datetime.now()
    matches = []
    errors = []
    scorers: dict[str, int] = {}
    knockouts: dict[str, int] = {}
    for seed in range(1, 31):
        try:
            game = create_match(seed, enemy_group_id=enemy_group_id, quest_id=quest_id)
            game.report.set_execution_mode("headless")
            actions = 0
            while not game.match_over and actions < 1000:
                game.ai_take_turn()
                actions += 1
            if not game.match_over:
                raise RuntimeError("safety limit reached before match end")
            summary = game.result_summary()
            records = game.report.action_records
            metrics = game.report.balance_metrics()
            total_damage = sum(int(record.get("effective_damage", 0) or 0) for record in records)
            for team, char_id in summary.get("scorers", ()):
                scorers[char_id] = scorers.get(char_id, 0) + 1
            for target, target_count in metrics["knockouts_by_character"].items():
                knockouts[target] = knockouts.get(target, 0) + target_count
            matches.append({"seed": seed, "winner": summary["winner"] or "draw", "score": [summary["player_score"], summary["enemy_score"]], "round": summary["rounds"], "total_damage": total_damage, **metrics})
            print(f"BALANCE_PROGRESS match={seed}/30 seed={seed} player_wins={sum(m['winner']=='player' for m in matches)} enemy_wins={sum(m['winner']=='enemy' for m in matches)} errors={len(errors)}")
        except Exception as exc:
            logging.exception("balance match seed=%s failed", seed)
            errors.append({"seed": seed, "error": f"{type(exc).__name__}: {exc}"})
    count = len(matches)
    avg = lambda key: round(sum(float(m[key]) for m in matches) / count, 2) if count else 0
    player_wins = sum(m["winner"] == "player" for m in matches)
    enemy_wins = sum(m["winner"] == "enemy" for m in matches)
    batch_finished = datetime.now()
    created_at = datetime.now()
    summed = lambda key: sum(int(m[key]) for m in matches)
    report = {"created_at": created_at.isoformat(timespec="seconds"), "batch_started_at": batch_started.isoformat(timespec="seconds"), "batch_finished_at": batch_finished.isoformat(timespec="seconds"), "execution_mode": "headless_balance_30", "seed_range": [1, 30], "completed": count, "errors": errors, "player_wins": player_wins, "enemy_wins": enemy_wins, "player_win_rate": round(player_wins * 100 / count, 2) if count else 0, "average_round": avg("round"), "average_score": [round(sum(m["score"][0] for m in matches) / count, 2) if count else 0, round(sum(m["score"][1] for m in matches) / count, 2) if count else 0], "average_total_damage": avg("total_damage"), "total_knockouts": summed("total_knockouts"), "player_knockouts": summed("player_knockouts"), "enemy_knockouts": summed("enemy_knockouts"), "average_knockouts": avg("total_knockouts"), "score_reset_returns": summed("score_reset_returns"), "natural_returns": summed("natural_returns"), "forced_knockout_drops": summed("forced_knockout_drops"), "pass_attempts": summed("pass_attempts"), "pass_successes": summed("pass_successes"), "steal_attempts": summed("steal_attempts"), "steal_successes": summed("steal_successes"), "pass_cut_checks": summed("pass_cut_checks"), "pass_cut_successes": summed("pass_cut_successes"), "normal_drop_checks": summed("normal_drop_checks"), "normal_drop_successes": summed("normal_drop_successes"), "ball_cut_uses": summed("ball_cut_uses"), "ball_cut_checks": summed("ball_cut_checks"), "ball_cut_successes": summed("ball_cut_successes"), "heal_uses": summed("heal_uses"), "effective_healing": summed("effective_healing"), "scores_by_character": scorers, "knockouts_by_character": knockouts, "matches": matches}
    output_root = Path("log")
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = batch_started.strftime("%Y_%m_%d_%H%M%S")
    output = next((output_root / f"{timestamp}_{index:03d}_balance_30.json" for index in range(1, 1000) if not (output_root / f"{timestamp}_{index:03d}_balance_30.json").exists()), None)
    if output is None:
        raise RuntimeError("バランス検証JSONファイル名の採番に失敗しました")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BALANCE_30_RESULT", json.dumps(report, ensure_ascii=False), f"report={output}")
    print(f"保存完了：{output.name}")
    return 0 if not errors else 1


def main() -> int:
    args = parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.headless_sim:
        return run_headless(args.seed, args.enemy_group, args.quest, args.log_policy)
    if args.balance_30:
        return run_balance_30(args.enemy_group, args.quest)
    if args.smoke_test:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    from manaball.ui import GameApp

    app = GameApp(start_match=args.smoke_test, seed=args.seed)
    return app.run(max_frames=8 if args.smoke_test else None)


async def async_main() -> int:
    """Launch the same game application from pygbag's WebAssembly runtime."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from manaball.ui import GameApp

    app = GameApp()
    return await app.run_async()


if sys.platform == "emscripten":
    asyncio.run(async_main())
elif __name__ == "__main__":
    raise SystemExit(main())

"""Fail-safe shared BGM and sound-effect management for desktop and pygbag."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pygame


LOGGER = logging.getLogger("manaball.audio")


class AudioManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.bgm_volume = 0.6
        self.se_volume = 0.8
        self.muted = False
        self.current_bgm = ""
        self.pending_bgm = ""
        self.user_interacted = sys.platform != "emscripten"
        self.available = False
        self._sounds: dict[str, pygame.mixer.Sound | None] = {}
        self._warned: set[str] = set()
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init()
            self.available = pygame.mixer.get_init() is not None
        except pygame.error as error:
            LOGGER.warning("音声を初期化できないため無音で続行します: %s", error)

    def notify_user_interaction(self) -> None:
        self.user_interacted = True
        if self.pending_bgm:
            pending = self.pending_bgm
            self.pending_bgm = ""
            self.play_bgm(pending)

    def _path(self, value: str, folder: str) -> Path:
        path = Path(value)
        if not path.suffix:
            path = Path("assets") / folder / f"{value}.ogg"
        return path if path.is_absolute() else self.root / path

    def play_bgm(self, value: str) -> bool:
        if not value:
            self.stop_bgm()
            return False
        if value == self.current_bgm and pygame.mixer.music.get_busy():
            return True
        if not self.user_interacted:
            self.pending_bgm = value
            return False
        path = self._path(value, "bgm")
        if not self.available or not path.is_file():
            self._warn_missing(str(path))
            return False
        try:
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.set_volume(0.0 if self.muted else self.bgm_volume)
            pygame.mixer.music.play(-1)
            self.current_bgm = value
            return True
        except (OSError, pygame.error) as error:
            LOGGER.warning("BGM再生に失敗したため無音で続行します: %s (%s)", path, error)
            return False

    def stop_bgm(self) -> None:
        if self.available:
            pygame.mixer.music.stop()
        self.current_bgm = ""

    def play_se(self, value: str) -> bool:
        if not value or not self.available or self.muted:
            return False
        if value not in self._sounds:
            path = self._path(value, "se")
            if not path.is_file():
                self._warn_missing(str(path))
                self._sounds[value] = None
            else:
                try:
                    self._sounds[value] = pygame.mixer.Sound(str(path))
                except (OSError, pygame.error) as error:
                    LOGGER.warning("効果音を読み込めません: %s (%s)", path, error)
                    self._sounds[value] = None
        sound = self._sounds[value]
        if sound is None:
            return False
        sound.set_volume(self.se_volume)
        sound.play()
        return True

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        if self.available:
            pygame.mixer.music.set_volume(0.0 if self.muted else self.bgm_volume)

    def _warn_missing(self, path: str) -> None:
        if path not in self._warned:
            LOGGER.warning("音声ファイルがないため無音で続行します: %s", path)
            self._warned.add(path)


"""Persistence helpers for Claude Auto Studio."""
from __future__ import annotations

import json
from pathlib import Path


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, KeyError):
            return {}

    def save(self, state: dict) -> bool:
        try:
            self.path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except OSError:
            return False

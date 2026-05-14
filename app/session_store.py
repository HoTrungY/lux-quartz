from __future__ import annotations

from typing import Dict, List


class SessionStore:
    def __init__(self, max_turns: int = 10) -> None:
        self.max_turns = max(1, max_turns)
        self._sessions: Dict[str, List[dict]] = {}

    @staticmethod
    def make_key(market: str, session_id: str) -> str:
        return f"{(market or '').upper()}:{(session_id or '').strip()}"

    def get_history(self, key: str) -> List[dict]:
        return list(self._sessions.get(key, []))

    def append_turn(self, key: str, user_message: str, assistant_message: str) -> None:
        history = self._sessions.setdefault(key, [])
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": assistant_message})

        max_messages = self.max_turns * 2
        if len(history) > max_messages:
            self._sessions[key] = history[-max_messages:]

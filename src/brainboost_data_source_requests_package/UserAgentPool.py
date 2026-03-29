from __future__ import annotations

import random
from pathlib import Path

try:
    from configuration import storage_user_agent_pool_database_path
except Exception:
    storage_user_agent_pool_database_path = ""


class UserAgentPool:
    def __init__(self, user_agents_list_path: str | None = None) -> None:
        self.user_agent_list_path = user_agents_list_path
        self._cached_user_agents: list[str] | None = None

    @staticmethod
    def _default_user_agents_path() -> Path:
        return Path(__file__).resolve().parent / "resources" / "user_agents.txt"

    def _resolve_user_agents_path(self) -> Path:
        candidates = []
        if self.user_agent_list_path:
            candidates.append(Path(self.user_agent_list_path))
        if storage_user_agent_pool_database_path:
            candidates.append(Path(storage_user_agent_pool_database_path))
        candidates.append(self._default_user_agents_path())

        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return self._default_user_agents_path()

    def load_user_agents(self, *, force_reload: bool = False) -> list[str]:
        if self._cached_user_agents is not None and not force_reload:
            return list(self._cached_user_agents)

        file_path = self._resolve_user_agents_path()
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                user_agents = [line.strip() for line in file if line.strip()]
        except FileNotFoundError:
            user_agents = []
        except Exception:
            user_agents = []

        self._cached_user_agents = user_agents
        return list(user_agents)

    def get_random_user_agent(self) -> str | None:
        user_agents = self.load_user_agents()
        if not user_agents:
            return None
        return random.choice(user_agents)

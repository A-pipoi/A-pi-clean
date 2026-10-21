"""Request/job identity context and lock-owned per-user store assembly."""
from __future__ import annotations

import contextvars
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quant.execution import PaperStore
from quant.execution.research_account import ResearchPaperAccount
from quant.factors import FactorRegistryStore
from quant.research import ResearchLedger

from .auth_store import validate_username
from .runtime_cache import WorkspaceCache

CURRENT_USERNAME: contextvars.ContextVar[str | None] = contextvars.ContextVar("a_pi_username", default=None)


@contextmanager
def user_context(username: str) -> Iterator[None]:
    token = CURRENT_USERNAME.set(validate_username(username))
    try:
        yield
    finally:
        CURRENT_USERNAME.reset(token)


@dataclass
class ScopedStores:
    factor_store: FactorRegistryStore
    paper_store: PaperStore
    ledger: ResearchLedger
    workspace_path: Path
    mining_dir: Path
    workspace_cache: WorkspaceCache
    scheduler: Any = None
    scheduler_config: dict[str, str] | None = None
    research_account: ResearchPaperAccount | None = None
    research_scheduler: Any = None


class UserScopedStores:
    def __init__(self, data_root: Path) -> None:
        (data_root / "users").mkdir(parents=True, exist_ok=True)
        self.root = (data_root / "users").resolve()
        self.lock = threading.RLock()
        self.instances: dict[str, ScopedStores] = {}

    def get(self, username: str) -> ScopedStores:
        validate_username(username)
        with self.lock:
            directory = (self.root / username).resolve()
            if directory.parent != self.root or directory.name != username:
                raise ValueError("invalid user data path")
            if username not in self.instances:
                directory.mkdir(parents=True, exist_ok=True)
                mining = directory / "mining"
                mining.mkdir(exist_ok=True)
                self.instances[username] = ScopedStores(
                    FactorRegistryStore(directory / "factors.sqlite3"),
                    PaperStore(directory / "paper.sqlite3"),
                    ResearchLedger(directory / "research.sqlite3", budget=20),
                    directory / "workspace.json", mining, WorkspaceCache(),
                )
            return self.instances[username]

    def current(self) -> ScopedStores:
        username = CURRENT_USERNAME.get()
        if username is None:
            raise RuntimeError("authenticated user context is required")
        return self.get(username)

    def research_account(self, username: str) -> ResearchPaperAccount:
        with self.lock:
            scoped = self.get(username)
            if scoped.research_account is None:
                scoped.research_account = ResearchPaperAccount(scoped.paper_store.path)
            return scoped.research_account

    def close(self) -> None:
        with self.lock:
            for stores in self.instances.values():
                stores.workspace_cache.close()
                stores.paper_store.close()
                if stores.research_account is not None:
                    stores.research_account.store.close()


class ScopedStoreProxy:
    """Compatibility facade: each attribute resolves against the current identity.

    Bound methods passed into dependency records are resolved inside the request
    context; detached work must explicitly propagate its captured context.
    """
    def __init__(self, stores: UserScopedStores, attribute: str) -> None:
        self.stores = stores
        self.attribute = attribute

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(getattr(self.stores.current(), self.attribute), name)

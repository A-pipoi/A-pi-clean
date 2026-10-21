"""Single execution authority with window recovery and bounded concurrency."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

from quant.execution import PaperScheduler
from quant.execution.maintenance import in_window

from .auth_store import AuthStore
from .user_scope import UserScopedStores, user_context
from .user_settings import UserPaperSettings

SHANGHAI = timezone(timedelta(hours=8))


class UserPaperScheduler:
    def __init__(self, users: AuthStore, stores: UserScopedStores, factory: Callable[[str], PaperScheduler],
                 research_factory: Callable[[str], PaperScheduler] | None = None) -> None:
        self.users, self.stores, self.factory = users, stores, factory
        self.research_factory = research_factory
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="a-pi-account-maintenance")

    def for_user(self, username: str) -> PaperScheduler:
        with self.lock, user_context(username):
            instance = self.stores.get(username)
            config = UserPaperSettings(instance.paper_store.path).get()
            if instance.scheduler is None or instance.scheduler_config != config:
                if instance.scheduler is not None and instance.scheduler.status().get("execution_in_progress"):
                    return instance.scheduler
                instance.scheduler = self.factory(username)
                instance.scheduler_config = config
            instance.scheduler.managed = bool(self.thread and self.thread.is_alive() and not self.stop_event.is_set())
            return instance.scheduler

    def for_research_user(self, username: str) -> PaperScheduler | None:
        with self.lock, user_context(username):
            instance = self.stores.get(username)
            path = instance.paper_store.path.parent / "research-paper" / "configuration.sqlite3"
            if self.research_factory is None or not path.is_file():
                return None
            account = self.stores.research_account(username)
            if account.configuration().get("enabled") is not True:
                return None
            from .research_paper import execution_code_hash
            if account.configuration().get("code_hash") != execution_code_hash():
                raise ValueError("研究运行代码版本已变化，请显式复核；未自动改动冻结配置")
            if instance.research_scheduler is None:
                instance.research_scheduler = self.research_factory(username)
            instance.research_scheduler.managed = bool(self.thread and self.thread.is_alive() and not self.stop_event.is_set())
            return instance.research_scheduler

    def _run_user(self, username: str, research: bool = False) -> None:
        if self.stop_event.is_set():
            return
        try:
            with user_context(username), self.lock if research else nullcontext():
                scheduler = self.for_research_user(username) if research else self.for_user(username)
                if scheduler is None:
                    return
                now = datetime.now(SHANGHAI)
                # Recheck when dequeued: another user's long job never extends
                # this account's allowed start window.
                if in_window(now):
                    scheduler.run_once()
                elif now.weekday() < 5 and (now.hour, now.minute) >= (16, 11):
                    scheduler.check_missed_window(now)
        except Exception:
            logging.getLogger("a_pi.paper_scheduler").exception("paper cycle failed username=%s", username)

    def run_once(self) -> None:
        now = datetime.now(SHANGHAI)
        if self.stop_event.is_set() or now.weekday() >= 5 or (now.hour, now.minute) < (16, 0):
            return
        futures = [self.executor.submit(self._run_user, user.username, research)
                   for user in self.users.list_users() if not user.disabled
                   for research in (False, True)]
        for future in futures:
            future.result()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                logging.getLogger("a_pi.paper_scheduler").exception("paper coordinator failed; waiting for next local check")
            # Only local journals are polled; persisted retry deadlines prevent
            # repeated provider calls. No Tushare requests on weekends.
            if self.stop_event.wait(15):
                return

    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, name="a-pi-user-paper-scheduler", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        # API shutdown must not close stores while a transaction is active.
        self.executor.shutdown(wait=True, cancel_futures=True)

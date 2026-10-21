"""Authenticated research-paper operations, separate from formal account routes."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .user_scheduler import UserPaperScheduler
from .user_scope import CURRENT_USERNAME, UserScopedStores


def execution_code_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for folder in ("execution", "strategies", "factors", "portfolio"):
        for path in sorted((root / folder).glob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    for relative in ("backtest/costs.py", "data/strategy_fundamentals.py"):
        path = root / relative
        digest.update(relative.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def research_paper_request(action: str, stores: UserScopedStores,
                           coordinator: UserPaperScheduler | None,
                           payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if CURRENT_USERNAME.get() is None:
        raise RuntimeError("authenticated context required")
    if coordinator is not None:
        with coordinator.lock:
            return _research_paper_request_locked(action, stores, coordinator, payload)
    return _research_paper_request_locked(action, stores, coordinator, payload)


def _research_paper_request_locked(action: str, stores: UserScopedStores,
                                   coordinator: UserPaperScheduler | None,
                                   payload: dict[str, Any] | None) -> dict[str, Any]:
    username = CURRENT_USERNAME.get()
    if username is None:
        raise RuntimeError("authenticated context required")
    scoped = stores.get(username)
    if (scoped.paper_store.path.parent / "research-version-switch.json").exists():
        raise ValueError("研究版本切换未完成，请核对保留目录；未自动创建或恢复账户")
    config_path = scoped.paper_store.path.parent / "research-paper" / "configuration.sqlite3"
    if action == "status" and not config_path.is_file():
        return {"ok": True, "enabled": False, "execution_mode": "research", "conclusion_ready": False}
    if coordinator is None:
        raise ValueError("后端纸面维护未启用")
    account = stores.research_account(username)
    if action == "new-version":
        if not isinstance(payload, dict) or set(payload) != {"expected_config_hash", "confirm_new_version"} or payload.get("confirm_new_version") is not True:
            raise ValueError("建立新版本需要显式确认及旧配置哈希")
        active = scoped.research_scheduler
        if active is not None and active.status().get("execution_in_progress"):
            raise ValueError("研究账户正在执行，不能切换版本")
        account = account.start_new_version(code_hash=execution_code_hash(),
                                            expected_config_hash=str(payload.get("expected_config_hash", "")))
        scoped.research_account = account
        scoped.research_scheduler = None
    if action == "upgrade-financial":
        with coordinator.lock:
            active = scoped.research_scheduler
            if active is not None and active.status().get("execution_in_progress"):
                raise ValueError("研究账户正在执行，不能升级")
            account.upgrade_financial(code_hash=execution_code_hash())
            scoped.research_scheduler = None
    if action.startswith("orders:"):
        _, page, size = action.split(":")
        return {"ok": True, "execution_mode": "research", **account.store.orders_page(int(page), int(size))}
    if action == "pause":
        # Pausing must remain possible even when an upgrade invalidated the
        # frozen execution hash. It is never an authorization to resume.
        active = scoped.research_scheduler
        if active is not None and active.status().get("execution_in_progress"):
            raise ValueError("研究账户正在执行，请等本次事务完成后暂停")
        account.pause()
    if action == "enable":
        with coordinator.lock:
            active = scoped.research_scheduler
            if active is not None and active.status().get("execution_in_progress"):
                raise ValueError("研究账户正在执行，不能恢复")
            if account.configuration():
                # Existing financial candidates must not inherit live formal
                # strategy/factor changes when the user resumes the account.
                account.resume(code_hash=execution_code_hash())
            else:
                formal = coordinator.for_user(username)
                if not formal.universe_index:
                    raise ValueError("研究账户需要 PIT 指数池，不能沿用固定股票池")
                account.enable(strategy=formal.strategy, rebalance=formal.rebalance,
                               index=formal.universe_index,
                               factors=scoped.factor_store.definitions(enabled_only=True),
                               code_hash=execution_code_hash())
            scoped.research_scheduler = None
    configuration_error = None
    try:
        scheduler = coordinator.for_research_user(username)
    except ValueError as exc:
        if action not in ("status", "pause"):
            raise
        scheduler = None
        configuration_error = str(exc)
    cycle = None
    if action == "cycle":
        if scheduler is None:
            raise ValueError("请先显式启用研究纸面账户")
        if account.configuration().get("code_hash") != execution_code_hash():
            raise ValueError("研究运行代码版本已变化，需要显式复核，未继续运行")
        cycle = scheduler.run_once()
    state = account.store.state()
    state.pop("orders", None)
    return {"ok": True, "enabled": bool(scheduler), "execution_mode": "research",
            "version_mismatch": account.configuration().get("code_hash") != execution_code_hash(),
            "current_code_hash": execution_code_hash(),
            "configuration": account.configuration(), "paper": state,
            "scheduler": scheduler.status() if scheduler else None, "cycle": cycle,
            "configuration_error": configuration_error,
            "sealed_passed": False, "conclusion_ready": False,
            "warning": "研究结果已观察，不是独立密封验收；未改动正式账户"}

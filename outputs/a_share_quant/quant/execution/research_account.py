"""An explicitly enabled, frozen, unvalidated paper account. Never a broker."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant.factors.store import FactorDefinition

from .store import PaperStore


class ResearchPaperAccount:
    def __init__(self, formal_path: Path) -> None:
        if (formal_path.parent / "research-version-switch.json").exists():
            raise ValueError("研究版本切换未完成，请核对保留目录；未自动创建或恢复账户")
        self.directory = formal_path.parent / "research-paper"
        self.directory.mkdir(exist_ok=True)
        self.path = self.directory / "paper.sqlite3"
        self.config_path = self.directory / "configuration.sqlite3"
        self.store = PaperStore(self.path)
        with closing(sqlite3.connect(self.config_path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS configuration(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")

    def configuration(self) -> dict[str, Any]:
        with closing(sqlite3.connect(self.config_path)) as db:
            row = db.execute("SELECT payload FROM configuration WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {}

    def enable(self, *, strategy: str, rebalance: str, index: str,
               factors: list[FactorDefinition], code_hash: str) -> dict[str, Any]:
        if strategy not in {"baseline", "momentum", "defensive", "inverse_volatility", "baseline_cost_aware"}:
            raise ValueError("research paper supports registered factor strategies only")
        if rebalance not in {"daily", "weekly", "monthly"} or not index:
            raise ValueError("research paper requires explicit frequency and PIT index")
        frozen = {"strategy": strategy, "rebalance": rebalance, "universe_index": index,
                  "factors": [item.as_dict() for item in factors], "code_hash": code_hash,
                  "initial_cash": 1_000_000, "execution_mode": "research"}
        digest = hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        payload = {**frozen, "config_hash": digest, "enabled": True,
                   "created_at": datetime.now(UTC).isoformat(),
                   "sealed_passed": False, "conclusion_ready": False,
                   "label": "未验收研究纸面账户"}
        with closing(sqlite3.connect(self.config_path, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT payload FROM configuration WHERE id=1").fetchone()
            if old:
                original = json.loads(old[0])
                if original["config_hash"] != digest:
                    raise ValueError("研究纸面配置已冻结，不能静默覆盖或改参")
                original["enabled"] = True
                db.execute("UPDATE configuration SET payload=? WHERE id=1", (json.dumps(original, ensure_ascii=False),))
                return original
            db.execute("INSERT INTO configuration VALUES(1,?)", (json.dumps(payload, ensure_ascii=False),))
        return payload

    def factors(self) -> list[FactorDefinition]:
        return [FactorDefinition(**item) for item in self.configuration().get("factors", [])]

    def resume(self, *, code_hash: str) -> dict[str, Any]:
        """Resume the existing frozen experiment, never copy formal settings.

        A changed execution version requires the separate explicit unused-account
        review/upgrade action first. Resuming never updates the frozen hash.
        """
        with closing(sqlite3.connect(self.config_path, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM configuration WHERE id=1").fetchone()
            if row is None:
                raise ValueError("研究账户尚未建立")
            config = json.loads(row[0])
            if config.get("execution_mode") != "research":
                raise ValueError("不是隔离研究配置")
            if config.get("code_hash") != code_hash:
                raise ValueError("执行代码已变化，请先显式复核未使用的研究版本；未恢复")
            if config.get("strategy") not in {
                "baseline", "momentum", "defensive", "inverse_volatility",
                "baseline_cost_aware",
            }:
                raise ValueError("冻结研究策略不受支持")
            if config.get("rebalance") not in {"daily", "weekly", "monthly"}:
                raise ValueError("冻结调仓频率无效")
            if not config.get("universe_index"):
                raise ValueError("冻结 PIT 指数池缺失")
            frozen = {key: config[key] for key in (
                "strategy", "rebalance", "universe_index", "factors", "code_hash",
                "initial_cash", "execution_mode",
            )}
            digest = hashlib.sha256(
                json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if digest != config.get("config_hash"):
                raise ValueError("冻结配置校验失败；未恢复")
            config["enabled"] = True
            db.execute("UPDATE configuration SET payload=? WHERE id=1",
                       (json.dumps(config, ensure_ascii=False),))
            return config

    def upgrade_financial(self, *, code_hash: str) -> dict[str, Any]:
        """Explicit one-time upgrade only before any experiment activity.

        Never reset a used account, rewrite exposures, or overwrite old evidence.
        The API coordinator serializes this operation with scheduler creation.
        """
        from quant.strategies.cost_aware_baseline import CostAwareBaseline
        with self.store.transaction(), closing(sqlite3.connect(self.config_path, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM configuration WHERE id=1").fetchone()
            if row is None:
                raise ValueError("请先建立研究账户")
            original = json.loads(row[0])
            if self.store.has_experiment_activity():
                raise ValueError("研究账户已有活动，不能覆盖；需建立新的隔离实验版本")
            CostAwareBaseline(factor_definitions=[FactorDefinition(**item) for item in original["factors"]])
            frozen = {key: original[key] for key in ("rebalance", "universe_index", "factors", "initial_cash", "execution_mode")}
            frozen.update(strategy="baseline_cost_aware", code_hash=code_hash)
            digest = hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if original["config_hash"] == digest:
                return original
            db.execute("CREATE TABLE IF NOT EXISTS configuration_revisions(id INTEGER PRIMARY KEY, changed_at TEXT NOT NULL, old_payload TEXT NOT NULL, new_hash TEXT NOT NULL, reason TEXT NOT NULL)")
            changed_at = datetime.now(UTC).isoformat()
            db.execute("INSERT INTO configuration_revisions(changed_at,old_payload,new_hash,reason) VALUES(?,?,?,?)", (changed_at, row[0], digest, "explicit unused-account financial baseline upgrade"))
            payload = {**original, **frozen, "config_hash": digest, "upgraded_at": changed_at, "previous_config_hash": original["config_hash"], "sealed_passed": False, "conclusion_ready": False}
            db.execute("UPDATE configuration SET payload=? WHERE id=1", (json.dumps(payload, ensure_ascii=False),))
            return payload

    def pause(self) -> None:
        with closing(sqlite3.connect(self.config_path, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM configuration WHERE id=1").fetchone()
            if row:
                config = json.loads(row[0])
                config["enabled"] = False
                db.execute("UPDATE configuration SET payload=? WHERE id=1", (json.dumps(config, ensure_ascii=False),))

    def start_new_version(self, *, code_hash: str, expected_config_hash: str) -> ResearchPaperAccount:
        """Archive the complete old account; never migrate its orders or evidence.

        Caller holds the account coordinator lock, including scheduled execution.
        A durable marker fails closed if a process dies between directory moves.
        """
        original = self.configuration()
        if not original or original.get("config_hash") != expected_config_hash:
            raise ValueError("研究配置已变化，请刷新后重新确认；未建立新版本")
        frozen = {key: original[key] for key in (
            "strategy", "rebalance", "universe_index", "factors", "code_hash",
            "initial_cash", "execution_mode",
        )}
        digest = hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if digest != expected_config_hash or original.get("execution_mode") != "research":
            raise ValueError("旧冻结配置校验失败；未建立新版本")
        if original.get("code_hash") == code_hash:
            raise ValueError("当前研究版本已匹配，无需重复建立")
        if original.get("initial_cash") != 1_000_000:
            raise ValueError("初始资金配置不受支持；未建立新版本")
        parent = self.directory.parent.resolve()
        if self.directory.resolve() != parent / "research-paper":
            raise ValueError("研究账户目录不合法")
        version_id = uuid.uuid4().hex
        archive = parent / "research-paper-versions" / version_id
        if archive.parent.resolve() != parent / "research-paper-versions":
            raise ValueError("版本保留目录不合法；未切换")
        staging = parent / ("research-version-next-" + version_id)
        staging.mkdir()
        prepared = ResearchPaperAccount(staging / "paper.sqlite3")
        try:
            payload = prepared.enable(strategy=original["strategy"], rebalance=original["rebalance"],
                                      index=original["universe_index"], factors=self.factors(), code_hash=code_hash)
            payload.update(previous_config_hash=expected_config_hash, archive_id=version_id,
                           version_started_at=payload["created_at"], enabled=False)
            with closing(sqlite3.connect(prepared.config_path)) as db, db:
                db.execute("UPDATE configuration SET payload=? WHERE id=1", (json.dumps(payload, ensure_ascii=False),))
        finally:
            prepared.store.close()
        archive.parent.mkdir(exist_ok=True)
        marker = parent / "research-version-switch.json"
        with marker.open("x", encoding="utf-8") as stream:
            json.dump({"archive_id": version_id, "old_config_hash": expected_config_hash,
                       "new_config_hash": payload["config_hash"], "staging": staging.name}, stream)
        self.store.close()
        moved = False
        try:
            self.directory.rename(archive)
            moved = True
            prepared.directory.rename(self.directory)
        except OSError:
            if moved and not self.directory.exists():
                archive.rename(self.directory)
            marker.unlink()
            self.store = PaperStore(self.path)
            raise
        marker.unlink()
        staging.rmdir()
        return ResearchPaperAccount(parent / "paper.sqlite3")

    @staticmethod
    def verify_store(store: PaperStore) -> bool:
        path = store.path.parent / "configuration.sqlite3"
        if store.path.parent.name != "research-paper" or not path.is_file():
            return False
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT payload FROM configuration WHERE id=1").fetchone()
        return bool(row and json.loads(row[0]).get("execution_mode") == "research"
                    and json.loads(row[0]).get("enabled") is True)

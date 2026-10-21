import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from quant.api.research_paper import research_paper_request
from quant.api.user_scope import UserScopedStores, user_context
from quant.execution.research_account import ResearchPaperAccount
from quant.execution.store import PaperStore


def configured(tmp_path):
    account = ResearchPaperAccount(tmp_path / "paper.sqlite3")
    config = account.enable(strategy="baseline", rebalance="weekly", index="000300.SH", factors=[], code_hash="old")
    account.store.create_order("000001.SZ", "BUY", 100)
    return account, config


def test_used_version_is_preserved_and_new_account_is_empty_paused(tmp_path):
    formal = PaperStore(tmp_path / "paper.sqlite3")
    formal.create_order("000002.SZ", "BUY", 200)
    account, config = configured(tmp_path)
    old_order = account.store.state()["orders"][0]
    extra = account.directory / "maintenance-evidence.txt"
    extra.write_text("immutable old observation", encoding="utf-8")
    new = account.start_new_version(code_hash="new", expected_config_hash=config["config_hash"])
    try:
        payload = new.configuration()
        assert payload["code_hash"] == "new"
        assert payload["previous_config_hash"] == config["config_hash"]
        assert payload["config_hash"] != config["config_hash"]
        assert payload["enabled"] is False
        assert payload["sealed_passed"] is False and payload["conclusion_ready"] is False
        assert new.store.state()["orders"] == []
        assert new.store.state()["cash"] == 1_000_000
        archive = tmp_path / "research-paper-versions" / payload["archive_id"]
        with sqlite3.connect(archive / "configuration.sqlite3") as db:
            assert json.loads(db.execute("SELECT payload FROM configuration").fetchone()[0]) == config
        with sqlite3.connect(archive / "paper.sqlite3") as db:
            assert db.execute("SELECT order_id FROM paper_orders").fetchone()[0] == old_order["order_id"]
        assert (archive / extra.name).read_text(encoding="utf-8") == "immutable old observation"
        assert len(formal.state()["orders"]) == 1
        assert not (tmp_path / "research-version-switch.json").exists()
        new.resume(code_hash="new")
        assert new.configuration()["enabled"] is True
    finally:
        new.store.close()
        formal.close()


def test_stale_confirmation_and_same_code_do_not_archive(tmp_path):
    account, config = configured(tmp_path)
    try:
        with pytest.raises(ValueError, match="配置已变化"):
            account.start_new_version(code_hash="new", expected_config_hash="stale")
        with pytest.raises(ValueError, match="无需重复"):
            account.start_new_version(code_hash="old", expected_config_hash=config["config_hash"])
        assert account.configuration() == config
        assert len(account.store.state()["orders"]) == 1
        assert not (tmp_path / "research-paper-versions").exists()
    finally:
        account.store.close()


def test_failed_install_restores_original_directory(tmp_path):
    account, config = configured(tmp_path)
    rename = Path.rename

    def fail_prepared(source, target):
        if source.parent.name.startswith("research-version-next-"):
            raise OSError("simulated move failure")
        return rename(source, target)

    try:
        with patch.object(Path, "rename", fail_prepared), pytest.raises(OSError, match="simulated"):
            account.start_new_version(code_hash="new", expected_config_hash=config["config_hash"])
        assert account.configuration() == config
        assert len(account.store.state()["orders"]) == 1
        assert not (tmp_path / "research-version-switch.json").exists()
    finally:
        account.store.close()


def test_interrupted_switch_fails_closed(tmp_path):
    (tmp_path / "research-version-switch.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="切换未完成"):
        ResearchPaperAccount(tmp_path / "paper.sqlite3")
    assert not (tmp_path / "research-paper").exists()


def test_route_requires_confirmation_and_keeps_user_scope(tmp_path):
    stores = UserScopedStores(tmp_path)
    coordinator = SimpleNamespace(lock=threading.RLock(), for_research_user=Mock(return_value=None))
    try:
        with user_context("alice"):
            account = stores.research_account("alice")
            config = account.enable(strategy="baseline", rebalance="weekly", index="000300.SH", factors=[], code_hash="old")
            for payload in (None, {}, {"confirm_new_version": False}, {"confirm_new_version": True, "expected_config_hash": config["config_hash"], "strategy": "momentum"}):
                with pytest.raises(ValueError, match="显式确认"):
                    research_paper_request("new-version", stores, coordinator, payload)
            with patch("quant.api.research_paper.execution_code_hash", return_value="new"):
                result = research_paper_request("new-version", stores, coordinator, {"confirm_new_version": True, "expected_config_hash": config["config_hash"]})
                assert result["enabled"] is False and result["version_mismatch"] is False
                assert result["configuration"]["code_hash"] == "new"
                with pytest.raises(ValueError, match="配置已变化"):
                    research_paper_request("new-version", stores, coordinator, {"confirm_new_version": True, "expected_config_hash": config["config_hash"]})
        assert not (stores.root / "bob" / "research-paper").exists()
    finally:
        stores.close()

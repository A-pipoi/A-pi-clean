"""Account-isolated prospective daily IC collection, separate from paper execution."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections import defaultdict
from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from datetime import datetime
from pathlib import Path

from ..data.live_monitor import LiveMonitorProvider
from ..data.strategy_fundamentals import observed_financial_inputs
from ..factors.composite import FactorComposite
from ..factors.live_ic import SHANGHAI, LiveICStore, definition_version
from ..notifications import NotificationOutbox
from .auth_store import AuthStore
from .user_scope import UserScopedStores, user_context

LOGGER = logging.getLogger(__name__)


def valued_extension_signals(
    field_values: Mapping[str, float],
    symbols: AbstractSet[str],
    latest_prices: Mapping[str, object],
) -> dict[str, float]:
    """Missing valuations reduce coverage; never manufacture a close price."""
    return {
        symbol: value
        for symbol, value in field_values.items()
        if symbol in symbols and symbol in latest_prices
    }


class DailyICScheduler:
    def __init__(
        self,
        users: AuthStore,
        stores: UserScopedStores,
        cache: Path,
        token: Callable[[], str | None],
    ) -> None:
        self.users, self.stores, self.cache, self.token = users, stores, cache, token
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def run_once(self, now: datetime) -> None:
        for user in self.users.list_users():
            if user.disabled or self.stop_event.is_set():
                continue
            with user_context(user.username):
                scoped = self.stores.get(user.username)
                monitor = LiveICStore(scoped.ledger.path.with_name("live_ic.sqlite3"))
                if not monitor.claim(now):
                    continue
                stage = "market"
                try:
                    access = self.token()
                    if not access:
                        raise RuntimeError("token_unavailable")
                    market = LiveMonitorProvider(
                        self.cache / "live_ic", access, cancelled=self.stop_event.is_set
                    ).fetch(
                        now.astimezone(SHANGHAI).date(), now, monitor.signal_dates()
                    )
                    if market is None:
                        monitor.finish(now, "market_closed")
                        continue
                    if self.stop_event.is_set():
                        raise RuntimeError("monitor_cancelled")
                    observed = datetime.now(SHANGHAI)
                    stage = "standard_factors"
                    scoped.ledger.record_paper_exposure(
                        now.astimezone(SHANGHAI).date().isoformat(),
                        "live-ic-monitor",
                        market.source_hash,
                    )
                    history = defaultdict(list)
                    for bar in market.bars:
                        history[bar.symbol].append(bar)
                    financial_values, financial_proof = observed_financial_inputs(
                        now.astimezone(SHANGHAI).date(), market.symbols
                    )
                    if financial_proof.get("status") != "available":
                        financial_values = {}
                    for definition in scoped.factor_store.definitions(
                        enabled_only=True
                    ):
                        version = definition_version(
                            {
                                "key": definition.key,
                                "type": definition.factor_type,
                                "window": definition.window,
                                "metric": definition.metric,
                                "expression": definition.expression,
                                "pipeline": "live-ic-v2-observed-financial"
                                if definition.factor_type == "fundamental"
                                else "live-ic-v1",
                            }
                        )
                        # No fabricated financial PIT values: absent fields stay
                        # explicit empty coverage, and accepted DSL errors isolated.
                        try:
                            scored = FactorComposite([definition]).rank_scores(
                                history, fundamental_values=financial_values
                            )
                            sign = -1 if definition.factor_type == "volatility" else 1
                            values = {
                                score.symbol: sign * score.factor_values[definition.key]
                                for score in scored
                                if definition.key in score.factor_values
                                and score.symbol in market.prices[market.sessions[-1]]
                            }
                        except ValueError:
                            values = {}
                        monitor.freeze(
                            now.astimezone(SHANGHAI).date(),
                            version,
                            definition.name,
                            values,
                            {
                                symbol: market.prices[market.sessions[-1]][symbol]
                                for symbol in market.symbols
                                if symbol in market.prices[market.sessions[-1]]
                            },
                            len(market.symbols),
                            definition_version(
                                {
                                    "market": market.source_hash,
                                    "financial": financial_proof.get("source_hash"),
                                }
                            )
                            if definition.factor_type == "fundamental"
                            else market.source_hash,
                            now=observed,
                        )
                    # New fields are frozen prospectively, never populated into
                    # old sealed or old IC signals using today's backfilled data.
                    from ..data.extension_features import values_as_of
                    from ..data.extension_store import (
                        ExtensionStore,
                        default_store_path,
                    )

                    stage = "extension_factors"
                    raw_store = ExtensionStore(default_store_path())
                    if raw_store.path.exists():
                        cutoff = now.astimezone(SHANGHAI).replace(
                            hour=15, minute=0, second=0, microsecond=0
                        )
                        extension_values, extension_proof = values_as_of(
                            raw_store, cutoff
                        )
                        for field, field_values in extension_values.items():
                            values = valued_extension_signals(
                                field_values,
                                set(market.symbols),
                                market.prices[market.sessions[-1]],
                            )
                            monitor.freeze(
                                now.astimezone(SHANGHAI).date(),
                                definition_version(
                                    {
                                        "field": field,
                                        "pipeline": "observed-extension-v1",
                                    }
                                ),
                                field,
                                values,
                                {
                                    s: market.prices[market.sessions[-1]][s]
                                    for s in market.symbols
                                    if s in market.prices[market.sessions[-1]]
                                },
                                len(market.symbols),
                                definition_version(
                                    {
                                        "market": market.source_hash,
                                        "raw": extension_proof.get("source_hash"),
                                    }
                                ),
                                now=observed,
                            )
                    stage = "settlement"
                    monitor.settle(market.sessions, market.prices, now=observed)
                    stage = "report"
                    report = monitor.report()
                    monitors = report["monitors"]
                    assert isinstance(monitors, list)
                    for item in monitors:
                        if item["decay"].get("status") == "alert":
                            try:
                                NotificationOutbox(
                                    scoped.ledger.path.with_name(
                                        "notifications.sqlite3"
                                    )
                                ).enqueue(
                                    f"live-ic:{item['version']}:{item['horizon']}:{item['decay']['as_of']}",
                                    "A-pi 样本外 IC 衰减报警",
                                    "启用后冻结的样本外信号出现持续衰减，请检查日更 IC 报告；不会自动交易或关闭策略。",
                                )
                            except (OSError, sqlite3.Error):
                                LOGGER.warning(
                                    "Live IC notification queue unavailable; saved IC remains valid"
                                )
                    monitor.finish(now, "updated")
                except Exception as error:
                    logging.getLogger(__name__).exception("quant.api.ic_scheduler failed: %s", type(error).__name__, exc_info=None)
                    # Do not persist provider exception strings containing tokens.
                    safe_errors = {
                        "token_unavailable",
                        "calendar_unavailable",
                        "membership_unavailable",
                        "membership_coverage_failed",
                        "warming_market_cache",
                        "daily_or_adjustment_coverage_failed",
                        "validated_price_coverage_failed",
                        "latest_market_not_fresh",
                        "monitor_cancelled",
                    }
                    code = (
                        str(error)
                        if str(error) in safe_errors
                        else f"{stage}:{type(error).__name__}"
                    )
                    monitor.finish(
                        now,
                        "warming_cache"
                        if code == "warming_market_cache"
                        else "data_unavailable",
                        code,
                    )

    def start(self) -> None:
        if os.getenv("A_PI_IC_AUTO", "1").lower() in {"0", "false", "no"}:
            return

        def loop() -> None:
            while not self.stop_event.is_set():
                try:
                    self.run_once(datetime.now(SHANGHAI))
                except Exception as error:
                    logging.getLogger(__name__).exception("quant.api.ic_scheduler failed: %s", type(error).__name__, exc_info=None)
                if self.stop_event.wait(30):
                    break

        self.thread = threading.Thread(target=loop, name="a-pi-live-ic", daemon=True)
        self.thread.start()
        LOGGER.info(
            "Prospective daily IC scheduler started; Shanghai 17:00-19:45, no paper execution"
        )

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

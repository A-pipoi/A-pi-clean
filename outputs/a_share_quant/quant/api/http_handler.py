"""HTTP transport and route dispatch for the A-pi services."""

from __future__ import annotations

import importlib.util
import json
import logging
import platform
import re
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager as ContextManager
from dataclasses import dataclass
from datetime import date
from http.cookies import CookieError, SimpleCookie
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, BinaryIO, cast
from urllib.parse import parse_qs, urlparse

from quant.api.account_management import handle_account_request
from quant.api.auth import Auth, TokenClaims
from quant.api.auth_routes import auth_response, client_ip
from quant.api.backtest_service import run_backtest_request
from quant.api.contracts import (
    AcceptFactorMiningRunCallback,
    AsFloatCallback,
    BacktestDependenciesCallback,
    BacktestJobViewCallback,
    BacktestQueueStatusCallback,
    CacheWritableCallback,
    DataProbeStatusCallback,
    EvaluateFactorExpressionRequestCallback,
    FactorMiningStatusCallback,
    MarketApiDependenciesCallback,
    MaterializePitFeatureRequestCallback,
    ObjectListCallback,
    ObjectMappingCallback,
    PaperApiDependenciesCallback,
    PaperStatusDependenciesCallback,
    PortfolioApiDependenciesCallback,
    PromoteMinedFactorCallback,
    ResearchApiDependenciesCallback,
    ResearchMutationDependenciesCallback,
    RunFactorMiningRequestCallback,
    SealedValidationDependenciesCallback,
    SensitivityDependenciesCallback,
    SubmitBacktestJobCallback,
    WorkspaceCatalogCallback,
    WorkspaceResponseCallback,
)
from quant.api.factor_mining_routes import (
    FactorMiningRouteDependencies,
    handle_factor_mining_request,
)
from quant.api.feature_namespace import FeatureNamespace
from quant.api.http_security import allowed_request, is_direct_tls
from quant.api.market_routes import MARKET_GET_PATHS, handle_market_get
from quant.api.paper_routes import handle_paper_cycle, handle_paper_order
from quant.api.paper_status import (
    paper_scheduler_payload,
    paper_sop_payload,
    paper_state_payload,
)
from quant.api.parsing import MAX_RESEARCH_SYMBOLS
from quant.api.portfolio_routes import handle_portfolio_optimize
from quant.api.research_mutations import archive_research_cycle, register_factor
from quant.api.research_routes import RESEARCH_GET_PATHS, handle_research_get
from quant.api.resource_limits import (
    TUSHARE_RESOURCE_CONTEXT,
    ResourceLimitExceeded,
    get_resource_limits,
)
from quant.api.sealed_service import run_sealed_request
from quant.api.security_routes import handle_second_factor
from quant.api.sensitivity_service import run_sensitivity_request
from quant.api.user_scope import CURRENT_USERNAME
from quant.api.user_settings import UserPaperSettings
from quant.api.websocket import handle_backtest_websocket
from quant.backtest.queueing import queue_mode
from quant.data import TushareDailyAdapter
from quant.errors import InputTypeError
from quant.execution import PaperStore
from quant.features import FeatureStore
from quant.operations import latest_backup_summary
from quant.research import ResearchBudgetExceeded, ResearchLedger
from quant.strategies.rulebook import RecipeStore


@dataclass(frozen=True)
class HttpHandlerDependencies:
    BACKTEST_JOBS: dict[str, dict[str, object]]
    BACKTEST_JOBS_LOCK: ContextManager[Any]
    BACKTEST_QUEUE_LIMIT: int
    BACKUP_DIR: Path
    DATA_CACHE_DIR: Path
    FACTOR_DB_PATH: Path
    FEATURE_DB_PATH: Path
    FEATURE_STORE: FeatureStore
    LAST_BACKUP: dict[str, object] | None
    LOGGER: logging.Logger
    LOG_PATH: Path
    PAPER_DB_PATH: Path
    PAPER_STORE: PaperStore
    RESEARCH_DB_PATH: Path
    RESEARCH_LEDGER: ResearchLedger
    _accept_factor_mining_run: AcceptFactorMiningRunCallback
    _as_float: AsFloatCallback
    _backtest_dependencies: BacktestDependenciesCallback
    _backtest_job_view: BacktestJobViewCallback
    _backtest_queue_status: BacktestQueueStatusCallback
    _cache_writable: CacheWritableCallback
    _data_probe_status: DataProbeStatusCallback
    _evaluate_factor_expression_request: EvaluateFactorExpressionRequestCallback
    _factor_mining_status: FactorMiningStatusCallback
    _market_api_dependencies: MarketApiDependenciesCallback
    _materialize_pit_feature_request: MaterializePitFeatureRequestCallback
    _object_list: ObjectListCallback
    _object_mapping: ObjectMappingCallback
    _paper_api_dependencies: PaperApiDependenciesCallback
    _paper_status_dependencies: PaperStatusDependenciesCallback
    _portfolio_api_dependencies: PortfolioApiDependenciesCallback
    _promote_mined_factor: PromoteMinedFactorCallback
    _research_api_dependencies: ResearchApiDependenciesCallback
    _research_mutation_dependencies: ResearchMutationDependenciesCallback
    _run_factor_mining_request: RunFactorMiningRequestCallback
    _sealed_validation_dependencies: SealedValidationDependenciesCallback
    _sensitivity_dependencies: SensitivityDependenciesCallback
    _submit_backtest_job: SubmitBacktestJobCallback
    _workspace_catalog: WorkspaceCatalogCallback
    _workspace_response: WorkspaceResponseCallback
    _research_paper_request: Callable[..., dict[str, Any]] | None = None
    _cancel_factor_mining_job: Callable[[str], bool] | None = None
    _external_deep_request: Callable[[str, dict], dict] | None = None
    _evolution_package_request: Callable[[str, str, dict], dict] | None = None
    _arena_request: Callable[[str, str, dict], dict] | None = None
    _arena_identity: Callable[[str], dict] | None = None
    _arena_participant_request: Callable[[str, str, dict, dict], dict] | None = None
    _mining_portfolio_request: Callable[[str, dict], dict] | None = None
    _submit_feature_materialize_job: Callable[[dict[str, object]], dict[str, object]] | None = None
    _get_feature_materialize_job: Callable[[str], dict[str, object] | None] | None = None
    _cancel_feature_materialize_job: Callable[[str], bool] | None = None


class HttpApiHandler(SimpleHTTPRequestHandler):
    claims: TokenClaims | None = None

    def _auth(self) -> Auth:
        raise NotImplementedError("Provide authentication service")

    def handle_one_request(self) -> None:
        token = CURRENT_USERNAME.set(None)
        resource_token = TUSHARE_RESOURCE_CONTEXT.set(None)
        self.claims = None
        try:
            super().handle_one_request()
        finally:
            CURRENT_USERNAME.reset(token)
            TUSHARE_RESOURCE_CONTEXT.reset(resource_token)
            self.claims = None

    def _identity(self) -> TokenClaims | None:
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            session = cookies.get("a_pi_session")
            self.claims = self._auth().verify_token(session.value) if session else None
        except CookieError:
            self.claims = None
        if self.claims:
            CURRENT_USERNAME.set(self.claims.username)
            TUSHARE_RESOURCE_CONTEXT.set((get_resource_limits(), self.claims.username))
            if self.headers.get("X-A-Pi-Activity") == "1":
                self._auth().store.check_session(
                    self.claims.jti,
                    self.claims.sub,
                    time.time(),
                    idle_seconds=1800,
                    touch=True,
                )
        return self.claims

    def _authorize(self) -> bool:
        if not self._identity():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return False
        if self.command == "POST" and self.headers.get("X-A-Pi-Request") != "1":
            self._send_json(403, {"ok": False, "error": "csrf_header_required"})
            return False
        user = self._auth().store.get_by_id(self.claims.sub) if self.claims else None
        if (
            user
            and user.must_change_password
            and urlparse(self.path).path
            not in {
                "/api/auth/session",
                "/api/auth/change-password",
                "/api/auth/logout",
            }
        ):
            self._send_json(403, {"ok": False, "error": "password_change_required"})
            return False
        # A valid remembered session may perform authenticated business writes
        # for its absolute lifetime. CSRF, role, account state, resource limits,
        # and any endpoint-specific password/OTP proof remain independently enforced.
        return True

    def _request_boundary(self) -> bool:
        allowed, _secure = allowed_request(
            self.headers.get("Host", ""),
            self.headers.get("Origin"),
            self.client_address[0],
            self.headers.get("X-Forwarded-Proto"),
            direct_tls_connection=is_direct_tls(self.connection),
        )
        if not allowed:
            self._send_json(403, {"ok": False, "error": "origin_rejected"})
        return allowed

    def _auth_endpoint(self, path: str) -> None:
        try:
            payload = (
                self._read_payload()
                if self.command == "POST"
                or (path == "/api/admin/invites" and self.headers.get("Content-Length"))
                else {}
            )
            if path.startswith("/api/auth/2fa/") and self.claims:
                self._send_json(
                    *handle_second_factor(
                        self._auth(),
                        path,
                        payload,
                        self.claims,
                        client_ip(
                            self.client_address[0], self.headers.get("X-Forwarded-For")
                        ),
                    )
                )
                return
            if path.startswith("/api/admin/") or path in {
                "/api/auth/sessions",
                "/api/auth/revoke-session",
                "/api/auth/revoke-all",
            }:
                assert self.claims is not None
                _allowed, secure = allowed_request(
                    self.headers.get("Host", ""),
                    self.headers.get("Origin"),
                    self.client_address[0],
                    self.headers.get("X-Forwarded-Proto"),
                    direct_tls_connection=is_direct_tls(self.connection),
                )
                self._send_json(
                    *handle_account_request(
                        self._auth(),
                        path,
                        payload,
                        self.claims,
                        client_ip(
                            self.client_address[0], self.headers.get("X-Forwarded-For")
                        ),
                        self.headers.get("User-Agent", ""),
                        self.command,
                        secure=secure,
                    )
                )
                return
            _allowed, secure = allowed_request(
                self.headers.get("Host", ""),
                self.headers.get("Origin"),
                self.client_address[0],
                self.headers.get("X-Forwarded-Proto"),
                direct_tls_connection=is_direct_tls(self.connection),
            )
            status, result, headers = auth_response(
                self._auth(),
                path,
                payload,
                self.claims,
                client_ip(self.client_address[0], self.headers.get("X-Forwarded-For")),
                logging.getLogger("a_pi.auth"),
                self.headers.get("User-Agent", ""),
                secure=secure,
            )
            self._send_json(status, result, headers)
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "error": "invalid_request"})

    def _services(self) -> HttpHandlerDependencies:
        raise NotImplementedError("Provide HTTP service dependencies")

    def _send_json(
        self,
        status: int,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> None:
        encoded = (
            b""
            if status == 204
            else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def end_headers(self) -> None:
        if not urlparse(self.path).path.startswith("/api/"):
            immutable = re.fullmatch(
                r"/releases/[0-9a-f]{20}/assets/[^/]+", urlparse(self.path).path
            )
            self.send_header(
                "Cache-Control",
                "public, max-age=31536000, immutable" if immutable else "no-cache",
            )
            if immutable:
                self.send_header("Vary", "Accept-Encoding")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; script-src-attr 'none'; object-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'; require-trusted-types-for 'script'; trusted-types dompurify",
        )
        super().end_headers()

    def send_head(self) -> BinaryIO | None:
        """Serve precompressed immutable bundles; never compress auth/API responses."""
        path = urlparse(self.path).path
        encodings = self.headers.get("Accept-Encoding", "").split(",")
        gzip_allowed = any(token.strip().lower() == "gzip" for token in encodings)
        if gzip_allowed and re.fullmatch(
            r"/releases/[0-9a-f]{20}/assets/[^/]+\.(js|css)", path
        ):
            original = Path(self.translate_path(path))
            compressed = original.with_name(original.name + ".gz")
            if original.is_file() and compressed.is_file():
                stream = compressed.open("rb")
                self.send_response(200)
                self.send_header("Content-Type", self.guess_type(str(original)))
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(compressed.stat().st_size))
                self.end_headers()
                return stream
        return super().send_head()

    def do_OPTIONS(self) -> None:
        self._send_json(405, {"ok": False, "error": "cors_not_supported"})

    def do_GET(self) -> None:
        if not self._request_boundary():
            return
        path = urlparse(self.path).path
        if HttpApiHandler._participant_transport(self, path):
            return
        if path == "/api/health":
            identity = self._identity()
            user = self._auth().store.get_by_id(identity.sub) if identity else None
            if not identity or (user and user.must_change_password):
                self._send_json(200, {"ok": True})
                return
        elif path.startswith("/api/"):
            if not self._authorize():
                return
        if path in {
            "/api/auth/session",
            "/api/auth/sessions",
            "/api/auth/2fa/status",
            "/api/admin/users",
            "/api/admin/invites",
        }:
            self._auth_endpoint(path)
            return
        if path == "/api/paper/config":
            config = UserPaperSettings(self._services().PAPER_STORE.path).get()
            self._send_json(
                200,
                {
                    "ok": True,
                    "configuration": config,
                    "defaults_from_service": not bool(config),
                },
            )
            return
        arena_get = re.fullmatch(r"/api/mining/arena(?:/([0-9a-f]{32}))?", path)
        if arena_get:
            self._handle_arena(
                "report" if arena_get.group(1) else "list", arena_get.group(1) or "", {}
            )
            return
        if path == "/api/mining/portfolio/policy":
            self._handle_portfolio("get-policy", {})
            return
        if path == "/api/mining/external-deep":
            self._handle_external_deep("list", {})
            return
        trial_status = re.fullmatch(
            r"/api/mining/external-deep/trials/(deep-[0-9a-f]{32})", path
        )
        if trial_status:
            self._handle_external_deep(
                "trial-status", {"attempt_id": trial_status.group(1)}
            )
            return
        package_list = re.fullmatch(
            r"/api/mining/evolution/([0-9a-f]{32})/packages", path
        )
        if package_list:
            self._handle_evolution_package("list", package_list.group(1), {})
            return
        evolution_history = re.fullmatch(
            r"/api/mining/evolution/([0-9a-f]{32})/(history|feedback)", path
        )
        if evolution_history:
            try:
                query = parse_qs(urlparse(self.path).query)
                payload: dict[str, object] = {
                    "mode": "evolution_" + evolution_history.group(2),
                    "session_id": evolution_history.group(1),
                }
                if evolution_history.group(2) == "feedback":
                    payload.update(
                        {
                            "top_n": int(query.get("top_n", ["5"])[0]),
                            "horizon": int(query.get("horizon", ["5"])[0]),
                        }
                    )
                result = self._services()._evaluate_factor_expression_request(payload)
                self._send_json(200, {"ok": True, **result})
            except LookupError:
                self._send_json(
                    404, {"ok": False, "code": "evolution_session_not_found"}
                )
            except PermissionError:
                self._send_json(403, {"ok": False, "code": "forbidden"})
            except (ValueError, TypeError):
                self._send_json(
                    400, {"ok": False, "code": "invalid_evolution_feedback"}
                )
            return
        if path == "/api/features/rows":
            try:
                query = parse_qs(urlparse(self.path).query)
                assert self.claims is not None
                rows = FeatureNamespace(
                    self._services().FEATURE_STORE, self.claims.username
                ).read_rows(
                    query.get("name", []),
                    data_version=query.get("data_version", [""])[0],
                    before=date.fromisoformat(query.get("before", [""])[0]),
                )
                self._send_json(200, {"ok": True, "rows": rows})
            except LookupError:
                self._send_json(404, {"ok": False, "error": "not_found"})
            except (ValueError, TypeError):
                self._send_json(400, {"ok": False, "error": "invalid_feature_request"})
            return
        if path in MARKET_GET_PATHS:
            market_status, market_payload = handle_market_get(
                path,
                parse_qs(urlparse(self.path).query),
                self._services()._market_api_dependencies(),
            )
            self._send_json(market_status, market_payload)
            return
        if path in RESEARCH_GET_PATHS:
            research_status, research_payload = handle_research_get(
                path,
                parse_qs(urlparse(self.path).query),
                self._services()._research_api_dependencies(),
            )
            self._send_json(research_status, research_payload)
            return
        if path in {"/api/paper/research", "/api/paper/research/orders"}:
            callback = self._services()._research_paper_request
            if callback is None:
                self._send_json(
                    503, {"ok": False, "error": "research_paper_unavailable"}
                )
            else:
                try:
                    query = parse_qs(urlparse(self.path).query)
                    action = (
                        "status"
                        if path.endswith("research")
                        else "orders:" + query.get("page", ["1"])[0] + ":20"
                    )
                    self._send_json(200, callback(action))
                except (ValueError, RuntimeError) as error:
                    self._send_json(409, {"ok": False, "error": str(error)})
            return
        if path == "/api/paper/orders":
            query = parse_qs(urlparse(self.path).query)
            try:
                page = int(query.get("page", ["1"])[0])
                page_size = int(query.get("page_size", ["20"])[0])
                result = (
                    self._services()
                    ._paper_status_dependencies()
                    .store.orders_page(page, page_size)
                )
            except ValueError:
                self._send_json(400, {"ok": False, "error": "invalid_pagination"})
                return
            self._send_json(200, {"ok": True, **result})
            return
        if path in {"/api/paper/state", "/api/paper/scheduler", "/api/paper/sop"}:
            dependencies = self._services()._paper_status_dependencies()
            paper_payload = (
                paper_state_payload(dependencies)
                if path == "/api/paper/state"
                else paper_scheduler_payload(dependencies)
                if path == "/api/paper/scheduler"
                else paper_sop_payload(dependencies)
            )
            self._send_json(200, paper_payload)
            return
        if path.startswith("/api/ws/backtest/"):
            job_id = path.rsplit("/", 1)[-1].strip()
            if self._services().RESEARCH_LEDGER.get_backtest_job(job_id) is None:
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            handle_backtest_websocket(
                self,
                job_id,
                lambda key: (
                    self._services().RESEARCH_LEDGER.get_backtest_job(key)
                    if self._identity()
                    else None
                ),
            )
            return
        if path == "/api/health":
            probe = self._services()._data_probe_status()
            try:
                backtest_queue = self._services()._backtest_queue_status()
            except RuntimeError as error:
                backtest_queue = {
                    "mode": queue_mode(),
                    "status": "unavailable",
                    "limit": self._services().BACKTEST_QUEUE_LIMIT,
                    "active": None,
                    "error": str(error),
                }
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "a-pi-python-backtest",
                    "api_version": "governance-v5.5",
                    "data_extension_revision": "observed-extension-v2",
                    "api_features": [
                        "isolated-unvalidated-research-paper-v1",
                        "research-ledger-archive",
                        "factor-trial-accounting",
                        "factor-expression-mining",
                        "sealed-factor-acceptance",
                        "paper-factor-snapshot",
                        "sealed-test-holdout",
                        "persistent-backtest-jobs",
                        "benchmark-excess-report",
                        "adjustment-factor-health",
                        "sqlite-operations",
                        "paper-validation-history",
                        "websocket-backtest-events",
                        "rq-redis-distributed-backtests",
                        "versioned-pit-feature-store",
                        "pit-feature-store-materialize",
                        "walk-forward-lightgbm-research",
                        "strict-pit-brinson-attribution",
                        "sealed-pit-index-universe-v1",
                        "workspace-auto-retry-v1",
                        "pit-delisting-dates-v1",
                        "manual-paper-pit-seal-v1",
                        "paper-pit-status-recovery-v1",
                        "modular-runtime-v1",
                        "typed-runtime-contracts-v1",
                        "signed-user-sessions-v1",
                        "user-scoped-research-paper-v1",
                        "research-version-archive-v1",
                    ],
                    "tushare_available": probe.get("data_reachable") is True,
                    "tushare_client_available": TushareDailyAdapter.is_available(),
                    "tushare_token_configured": TushareDailyAdapter.token_configured(),
                    "data_probe": probe,
                    "max_research_symbols": MAX_RESEARCH_SYMBOLS,
                    "python": platform.python_version(),
                    "cache_dir": str(self._services().DATA_CACHE_DIR),
                    "cache_writable": self._services()._cache_writable(
                        self._services().DATA_CACHE_DIR
                    ),
                    "cache_fallback_policy": {
                        "decision_endpoints": "strict_live",
                        "discovery_endpoints": "stale_explicit",
                        "backtest_and_paper": "force_refresh_no_cache_fallback",
                    },
                    "backtest_queue": backtest_queue,
                    "feature_store": self._services().FEATURE_STORE.status(),
                    "model_capabilities": {
                        "lightgbm_installed": importlib.util.find_spec("lightgbm")
                        is not None,
                        "lightgbm_strategy": "research_only_not_paper_default",
                    },
                },
            )
            return
        if path == "/api/ops/status":
            if not self.claims or self.claims.role != "admin":
                self._send_json(403, {"ok": False, "error": "forbidden"})
                return
            self._send_json(
                200,
                {
                    "ok": True,
                    "log_path": str(self._services().LOG_PATH),
                    "backup_dir": str(self._services().BACKUP_DIR),
                    "last_backup": self._services().LAST_BACKUP
                    or latest_backup_summary(self._services().BACKUP_DIR),
                    "databases": [
                        str(self._services().PAPER_DB_PATH),
                        str(self._services().FACTOR_DB_PATH),
                        str(self._services().RESEARCH_DB_PATH),
                        str(self._services().FEATURE_DB_PATH),
                    ],
                    "backup_trigger": "每次 A 股工作日 16:00 纸面交易周期完成后",
                },
            )
            return
        if path == "/api/features/status":
            self._send_json(
                200,
                {"ok": True, "feature_store": self._services().FEATURE_STORE.status()},
            )
            return
        feature_job = re.fullmatch(
            r"/api/features/jobs/(FM-[\w-]{1,80})", path
        )
        if feature_job:
            getter = self._services()._get_feature_materialize_job
            if getter is None:
                self._send_json(501, {"ok": False, "error": "feature_jobs_unavailable"})
                return
            job = getter(feature_job.group(1))
            if job is None:
                self._send_json(404, {"ok": False, "error": "job_not_found"})
                return
            self._send_json(200, job)
            return
        if path == "/api/backtest/default-display":
            self._send_json(200, {"ok": True, "display": self._services().RESEARCH_LEDGER.get_default_backtest_display()})
            return
        if path == "/api/backtest/jobs":
            self._send_json(
                200,
                {
                    "ok": True,
                    "jobs": self._services().RESEARCH_LEDGER.list_backtest_jobs(),
                },
            )
            return
        if path.startswith("/api/backtest/"):
            job_id = path.rsplit("/", 1)[-1].strip()
            persisted = self._services().RESEARCH_LEDGER.get_backtest_job(job_id)
            if persisted is not None:
                self._send_json(200, self._services()._backtest_job_view(persisted))
                return
            with self._services().BACKTEST_JOBS_LOCK:
                job = self._services().BACKTEST_JOBS.get(job_id)
                if job is not None and (
                    self.claims is None or job.get("owner") != self.claims.username
                ):
                    job = None
                job_payload = (
                    self._services()._backtest_job_view(job)
                    if job is not None
                    else None
                )
            if job_payload is None:
                persisted = self._services().RESEARCH_LEDGER.get_backtest_job(job_id)
                job_payload = (
                    self._services()._backtest_job_view(persisted)
                    if persisted is not None
                    else None
                )
            if job_payload is None:
                self._send_json(
                    404,
                    {
                        "ok": False,
                        "code": "backtest_job_not_found",
                        "error": "回测任务不存在或已过期",
                    },
                )
            else:
                self._send_json(200, job_payload)
            return
        if path == "/api/workspaces":
            self._send_json(200, self._services()._workspace_catalog())
            return
        if path == "/api/workspace":
            status, payload = self._services()._workspace_response()
            self._send_json(status, payload)
            return
        if path.startswith("/api/"):
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        super().do_GET()

    def do_POST(self) -> None:
        if not self._request_boundary():
            return
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if HttpApiHandler._participant_transport(self, path):
            return
        if path in {"/api/auth/login", "/api/auth/register"} and (
            self.headers.get("X-A-Pi-Request") != "1"
            or self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            != "application/json"
        ):
            self._send_json(403, {"ok": False, "error": "login_csrf_rejected"})
            return
        if (
            path not in {"/api/auth/login", "/api/auth/register"}
            and not self._authorize()
        ):
            return
        if path.startswith(("/api/auth/", "/api/admin/")):
            self._auth_endpoint(path)
            return
        assert self.claims is not None
        light = {
            "/api/backtest/default-display",
            "/api/paper/config",
            "/api/workspaces/select",
            "/api/factor-mining/cancel",
            "/api/mining/external-deep/trials/get",
            "/api/mining/external-deep/trials/status",
        }
        if path.startswith(("/api/mining/arena", "/api/mining/portfolio/")):
            self._dispatch_business_post(path)
            return
        if path in light or (
            path.startswith("/api/backtest/") and path.endswith("/cancel")
        ) or (
            path.startswith("/api/features/jobs/") and path.endswith("/cancel")
        ):
            self._dispatch_business_post(path)
            return
        try:
            limits = get_resource_limits()
            lease = limits.reserve(self.claims.username, path)
            dispatched = False
            try:
                with limits.running_operation(self.claims.username):
                    dispatched = True
                    self._dispatch_business_post(path)
            finally:
                if dispatched:
                    limits.complete(lease)
                else:
                    limits.release(lease)
        except ResourceLimitExceeded as error:
            self._send_json(
                429,
                {"ok": False, "error": error.reason},
                {"Retry-After": str(error.retry_after)},
            )

    def _dispatch_business_post(self, path: str) -> None:
        parsed_url = urlparse(self.path)
        if path == "/api/backtest/default-display":
            try:
                payload = self._read_payload()
                job_id = payload.get("job_id")
                if not isinstance(job_id, str) or not re.fullmatch(r"BT-[\w-]{1,100}", job_id):
                    raise ValueError("请提供已成功完成的回测任务编号")
                display = self._services().RESEARCH_LEDGER.set_default_backtest_display(job_id)
            except (ValueError, TypeError) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            self._send_json(200, {"ok": True, "display": display})
            return
        arena_post = re.fullmatch(
            r"/api/mining/arena(?:/([0-9a-f]{32})/(close|accept|archive))?", path
        )
        if arena_post:
            try:
                payload = self._read_payload()
            except (ValueError, TypeError):
                self._send_json(400, {"ok": False, "code": "invalid_arena_request"})
                return
            self._handle_arena(
                arena_post.group(2) or "create", arena_post.group(1) or "", payload
            )
            return
        if path in {"/api/mining/portfolio/report", "/api/mining/portfolio/policy"}:
            try:
                payload = self._read_payload()
            except (ValueError, TypeError):
                self._send_json(400, {"ok": False, "code": "invalid_portfolio_request"})
                return
            self._handle_portfolio(
                "report" if path.endswith("report") else "set-policy", payload
            )
            return
        trial_route = re.fullmatch(
            r"/api/mining/external-deep/trials/(reserve|start|fail|get|execute|status)",
            path,
        )
        if trial_route:
            try:
                payload = self._read_payload()
            except (TypeError, ValueError, json.JSONDecodeError):
                self._send_json(
                    400, {"ok": False, "code": "invalid_external_deep_request"}
                )
                return
            self._handle_external_deep("trial-" + trial_route.group(1), payload)
            return
        package_route = re.fullmatch(
            r"/api/mining/evolution/([0-9a-f]{32})/package(/accept)?", path
        )
        if package_route:
            try:
                payload = self._read_payload()
            except (TypeError, ValueError, json.JSONDecodeError):
                self._send_json(400, {"ok": False, "code": "invalid_evolution_package"})
                return
            self._handle_evolution_package(
                "accept" if package_route.group(2) else "create",
                package_route.group(1),
                payload,
            )
            return
        if path == "/api/mining/external-deep/import":
            try:
                payload = self._read_payload()
            except (TypeError, ValueError, json.JSONDecodeError):
                self._send_json(
                    400, {"ok": False, "code": "invalid_external_deep_request"}
                )
                return
            self._handle_external_deep("import", payload)
            return
        if path in {
            "/api/paper/research/enable",
            "/api/paper/research/cycle",
            "/api/paper/research/pause",
            "/api/paper/research/upgrade-financial",
            "/api/paper/research/new-version",
        }:
            try:
                payload = self._read_payload()
                action = path.rsplit("/", 1)[-1]
                if payload and action != "new-version":
                    raise ValueError("研究执行不接受临时策略、资金、股票池或门禁覆盖")
                callback = self._services()._research_paper_request
                if callback is None:
                    raise ValueError("research_paper_unavailable")
                self._send_json(200, callback(action, payload) if action == "new-version" else callback(action))
            except (ValueError, RuntimeError) as error:
                self._send_json(409, {"ok": False, "error": str(error)})
            return
        if path == "/api/strategy-recipes":
            try:
                payload = self._read_payload()
                raw = payload.get("recipe")
                if not isinstance(raw, dict):
                    raise InputTypeError("recipe必须是规则对象")
                saved = RecipeStore(
                    self._services().RESEARCH_LEDGER.path.with_name(
                        "strategy_recipes.sqlite3"
                    )
                ).save(raw)
                self._send_json(201, {"ok": True, **saved})
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            return
        if path.startswith("/api/backtest/") and path.endswith("/cancel"):
            self._read_payload()
            job_id = path.split("/")[-2]
            ledger = self._services().RESEARCH_LEDGER
            if ledger.get_backtest_job(job_id) is None:
                self._send_json(404, {"ok": False, "error": "backtest_job_not_found"})
                return
            accepted = ledger.request_backtest_cancel(job_id)
            if accepted:
                with self._services().BACKTEST_JOBS_LOCK:
                    job = self._services().BACKTEST_JOBS.get(job_id)
                    if job and self.claims and job.get("owner") == self.claims.username:
                        job.update(
                            status="cancelling", message="已请求取消，等待安全检查点"
                        )
            self._send_json(
                202 if accepted else 409,
                {
                    "ok": accepted,
                    "status": "cancelling" if accepted else "terminal",
                    "message": "已记账研究不退预算；已完成结果仍保留",
                },
            )
            return
        if path == "/api/paper/config":
            try:
                payload = self._read_payload()
                settings = UserPaperSettings(self._services().PAPER_STORE.path)
                settings.set(
                    str(payload.get("strategy") or ""),
                    str(payload.get("rebalance") or ""),
                )
                self._send_json(200, {"ok": True, "configuration": settings.get()})
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            return
        if path == "/api/features/rows":
            try:
                payload = self._read_payload()
                rows = payload.get("rows")
                if not isinstance(rows, list) or not all(
                    isinstance(row, dict) for row in rows
                ):
                    raise ValueError("rows must be an array of observations")
                assert self.claims is not None
                written = FeatureNamespace(
                    self._services().FEATURE_STORE, self.claims.username
                ).write_rows(rows, expression=str(payload.get("expression") or ""))
                self._send_json(
                    200,
                    {"ok": True, "written": written, "availability_verified": False},
                )
            except LookupError:
                self._send_json(404, {"ok": False, "error": "not_found"})
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            return
        feature_cancel = re.fullmatch(
            r"/api/features/jobs/(FM-[\w-]{1,80})/cancel", path
        )
        if feature_cancel:
            cancel = self._services()._cancel_feature_materialize_job
            if cancel is None:
                self._send_json(501, {"ok": False, "error": "feature_jobs_unavailable"})
                return
            accepted = cancel(feature_cancel.group(1))
            self._send_json(
                200,
                {
                    "ok": bool(accepted),
                    "job_id": feature_cancel.group(1),
                    "status": "cancelling" if accepted else "terminal",
                    "accepted": accepted,
                },
            )
            return
        if path == "/api/features/materialize":
            try:
                payload = self._read_payload()
                query = parse_qs(urlparse(self.path).query)
                sync_flag = str(payload.get("sync") or "").strip().lower() in {
                    "1", "true", "yes",
                } or (query.get("sync", [""])[0] or "").strip().lower() in {
                    "1", "true", "yes",
                }
                submit = self._services()._submit_feature_materialize_job
                if not sync_flag and submit is not None:
                    job = submit(payload)
                    self._send_json(
                        202,
                        {
                            "ok": True,
                            "async": True,
                            "job_id": job["job_id"],
                            "status": job.get("status") or "queued",
                            "status_url": f"/api/features/jobs/{job['job_id']}",
                            "cancel_url": f"/api/features/jobs/{job['job_id']}/cancel",
                            "job": job,
                        },
                    )
                    return
                result = self._services()._materialize_pit_feature_request(payload)
                self._send_json(200, {"ok": True, "async": False, **result})
            except Exception as error:  # noqa: BLE001 - HTTP boundary maps and redacts unexpected errors
                from quant.api.feature_materialization import (
                    classify_materialization_error,
                )

                failure = classify_materialization_error(error)
                logging.getLogger(__name__).warning(
                    "PIT feature materialization failed: code=%s type=%s detail=%s",
                    failure["code"], failure["error_type"], failure["detail"],
                )
                retry_after = getattr(error, "retry_after", None)
                status = int(failure.pop("status"))  # type: ignore[call-overload]
                self._send_json(
                    status,
                    {"ok": False, **failure},
                    {"Retry-After": str(retry_after)} if status == 429 and retry_after else None,
                )
            return
        if path in {
            "/api/factor-mining/run",
            "/api/factor-mining/evaluate",
            "/api/mining/evaluate",
            "/api/factor-mining/accept",
            "/api/factor-mining/promote",
            "/api/factor-mining/cancel",
        }:
            try:
                payload = self._read_payload()
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            status, response = handle_factor_mining_request(
                path,
                payload,
                FactorMiningRouteDependencies(
                    run=self._services()._run_factor_mining_request,
                    evaluate=self._services()._evaluate_factor_expression_request,
                    accept=self._services()._accept_factor_mining_run,
                    promote=self._services()._promote_mined_factor,
                    status=self._services()._factor_mining_status,
                    cancel=self._services()._cancel_factor_mining_job,
                ),
            )
            headers = (
                {"Retry-After": str(response["retry_after"])}
                if status == 429 and "retry_after" in response
                else None
            )
            self._send_json(status, response, headers)
            return
        if path == "/api/portfolio/optimize":
            try:
                payload = self._read_payload()
                result = handle_portfolio_optimize(
                    payload, self._services()._portfolio_api_dependencies()
                )
                self._send_json(200, {"ok": True, "optimization": result})
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "code": "invalid_optimizer_request",
                        "error": str(error),
                    },
                )
            except (OSError, RuntimeError) as error:
                self._send_json(
                    503,
                    {
                        "ok": False,
                        "code": "optimizer_inputs_unavailable",
                        "error": str(error),
                    },
                )
            except Exception as error:
                logging.getLogger(__name__).exception("quant.api.http_handler failed: %s", type(error).__name__, exc_info=None)
                self._send_json(
                    500,
                    {
                        "ok": False,
                        "code": "portfolio_optimization_failed",
                        "error": type(error).__name__,
                    },
                )
            return
        if (
            path == "/api/backtest"
            and parse_qs(parsed_url.query).get("sync", ["0"])[0] != "1"
        ):
            try:
                payload = self._read_payload()
                job_id = self._services()._submit_backtest_job(payload)
                self._send_json(
                    202,
                    {
                        "ok": True,
                        "status": "queued",
                        "job_id": job_id,
                        "status_url": f"/api/backtest/{job_id}",
                        "websocket_url": f"/api/ws/backtest/{job_id}",
                        "message": "回测任务已提交，正在请求 Tushare 实时行情",
                    },
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            except RuntimeError as error:
                full = "队列已满" in str(error)
                self._send_json(
                    429 if full else 503,
                    {
                        "ok": False,
                        "code": "backtest_queue_full"
                        if full
                        else "distributed_queue_unavailable",
                        "error": str(error),
                    },
                )
            return
        if path == "/api/workspaces/select":
            try:
                payload = self._read_payload()
                requested_id = str(payload.get("workspace_id", "")).strip().lower()
                catalog = self._services()._workspace_catalog()
                workspaces = [
                    self._services()._object_mapping(item)
                    for item in self._services()._object_list(catalog.get("workspaces"))
                ]
                selected = next(
                    (item for item in workspaces if item.get("id") == requested_id),
                    None,
                )
                if selected is None:
                    self._send_json(
                        404,
                        {
                            "ok": False,
                            "code": "workspace_not_found",
                            "error": "工作区不存在或未配置",
                        },
                    )
                    return
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "workspace": selected,
                        "current_id": selected["id"],
                        "persisted": False,
                        "message": "已确认当前工作区；服务目前未配置其他工作区",
                    },
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            return
        if path in {
            "/api/factors",
            "/api/paper/order",
            "/api/paper/cycle",
            "/api/paper/reset",
            "/api/research/ledger/archive",
        }:
            try:
                payload = self._read_payload()
                if path == "/api/research/ledger/archive":
                    self._send_json(
                        *archive_research_cycle(
                            payload, self._services()._research_mutation_dependencies()
                        )
                    )
                elif path == "/api/factors":
                    self._send_json(
                        *register_factor(
                            payload, self._services()._research_mutation_dependencies()
                        )
                    )
                elif path == "/api/paper/order":
                    self._handle_paper_order(payload)
                elif path == "/api/paper/cycle":
                    self._handle_paper_cycle(payload)
                else:
                    initial_cash = self._services()._as_float(
                        payload.get("initial_cash", 1000000)
                    )
                    if initial_cash <= 0 or initial_cash > 100000000:
                        raise ValueError("initial_cash is outside the allowed range")
                    self._services().PAPER_STORE.reset(initial_cash)
                    self._send_json(
                        200, {"ok": True, "paper": self._services().PAPER_STORE.state()}
                    )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            except RuntimeError as error:
                self._send_json(
                    502,
                    {
                        "ok": False,
                        "code": "data_provider_unavailable",
                        "error": str(error),
                    },
                )
            except Exception as error:
                logging.getLogger(__name__).exception("quant.api.http_handler failed: %s", type(error).__name__, exc_info=None)
                self._send_json(
                    500, {"ok": False, "error": f"paper_trading_failed: {type(error).__name__}"}
                )
            return
        if path == "/api/validation/sealed":
            try:
                payload = self._read_payload()
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            self._send_json(
                *run_sealed_request(
                    payload, self._services()._sealed_validation_dependencies()
                )
            )
            return
        if path == "/api/validation/sensitivity":
            try:
                payload = self._read_payload()
                self._send_json(
                    200,
                    run_sensitivity_request(
                        payload, self._services()._sensitivity_dependencies()
                    ),
                )
            except ResearchBudgetExceeded as error:
                self._send_json(
                    409,
                    {
                        "ok": False,
                        "code": "research_budget_exceeded",
                        "error": str(error),
                    },
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            except RuntimeError as error:
                self._send_json(
                    502,
                    {
                        "ok": False,
                        "code": "data_provider_unavailable",
                        "error": str(error),
                    },
                )
            except Exception as error:
                logging.getLogger(__name__).exception("quant.api.http_handler failed: %s", type(error).__name__, exc_info=None)
                self._send_json(
                    500, {"ok": False, "error": f"sensitivity_failed: {type(error).__name__}"}
                )
            return
        if path != "/api/backtest":
            self._send_json(404, {"ok": False, "error": "endpoint_not_found"})
            return
        try:
            payload = self._read_payload()
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self._send_json(400, {"ok": False, "error": str(error)})
            return
        self._send_json(
            *run_backtest_request(payload, self._services()._backtest_dependencies())
        )

    def _participant_transport(self, path: str) -> bool:
        authorization = getattr(self, "headers", {}).get("Authorization", "")
        if not isinstance(authorization, str):
            authorization = ""
        if not authorization and not path.startswith("/api/mining/arena/participant/"):
            return False
        # A participant token never upgrades into cookie/owner permissions,
        # even if the caller supplies both credentials or targets health/login.
        route = re.fullmatch(
            r"/api/mining/arena/participant/([0-9a-f]{32})/(brief|history|step)", path
        )
        if not route or (self.command == "POST") != (route.group(2) == "step"):
            self._send_json(403, {"ok": False, "code": "arena_scope_forbidden"})
            return True
        try:
            callback = getattr(self, "_participant_identity_callback", None)
            if callback is None:
                raise PermissionError("Participant authentication unavailable")
            claims = callback(
                authorization.removeprefix("Bearer ")
                if authorization.startswith("Bearer ")
                else ""
            )
            if route.group(1) != claims["session_id"]:
                raise PermissionError("Cross-session access forbidden")
            CURRENT_USERNAME.set(claims["owner"])
            TUSHARE_RESOURCE_CONTEXT.set((get_resource_limits(), claims["owner"]))
            service = self._services()._arena_participant_request
            if service is None:
                raise PermissionError("Arena service unavailable")
            payload = self._read_payload() if self.command == "POST" else {}
            result = service(route.group(2), route.group(1), payload, claims)
            self._send_json(200, {"ok": True, **result})
        except PermissionError:
            self._send_json(403, {"ok": False, "code": "arena_scope_forbidden"})
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "code": "invalid_arena_request"})
        except LookupError:
            self._send_json(404, {"ok": False, "code": "arena_not_found"})
        except ResourceLimitExceeded as error:
            self._send_json(
                429,
                {"ok": False, "code": error.reason},
                {"Retry-After": str(error.retry_after)},
            )
        except Exception as error:
            logging.getLogger(__name__).exception("quant.api.http_handler failed: %s", type(error).__name__, exc_info=None)
            self._send_json(503, {"ok": False, "code": "arena_evaluation_unavailable"})
        return True

    def _handle_arena(self, action: str, arena_id: str, payload: dict) -> None:
        self._handle_research_extension(
            self._services()._arena_request, action, arena_id, payload
        )

    def _handle_portfolio(self, action: str, payload: dict) -> None:
        self._handle_research_extension(
            self._services()._mining_portfolio_request, action, payload
        )

    def _handle_research_extension(
        self, callback: Callable[..., dict[str, Any]] | None, *args: Any
    ) -> None:
        try:
            if callback is None:
                self._send_json(
                    503, {"ok": False, "code": "research_extension_unavailable"}
                )
                return
            self._send_json(200, {"ok": True, **callback(*args)})
        except PermissionError as error:
            self._send_json(
                403,
                {
                    "ok": False,
                    "code": "research_governance_blocked",
                    "error": str(error),
                },
            )
        except (ValueError, TypeError) as error:
            self._send_json(
                400,
                {
                    "ok": False,
                    "code": "invalid_research_extension",
                    "error": str(error),
                },
            )
        except LookupError:
            self._send_json(404, {"ok": False, "code": "research_extension_not_found"})
        except ResourceLimitExceeded as error:
            self._send_json(
                429,
                {"ok": False, "code": error.reason},
                {"Retry-After": str(error.retry_after)},
            )
        except Exception as error:
            logging.getLogger(__name__).exception("quant.api.http_handler failed: %s", type(error).__name__, exc_info=None)
            self._send_json(503, {"ok": False, "code": "research_extension_failed"})

    def _handle_evolution_package(
        self, action: str, session_id: str, payload: dict
    ) -> None:
        callback = self._services()._evolution_package_request
        if callback is None:
            self._send_json(503, {"ok": False, "code": "evolution_package_unavailable"})
            return
        try:
            result = callback(action, session_id, payload)
            self._send_json(201 if action == "create" else 200, {"ok": True, **result})
        except LookupError:
            self._send_json(404, {"ok": False, "code": "evolution_package_not_found"})
        except PermissionError:
            self._send_json(
                409, {"ok": False, "code": "evolution_package_governance_blocked"}
            )
        except (ValueError, TypeError):
            self._send_json(400, {"ok": False, "code": "invalid_evolution_package"})
        except ResearchBudgetExceeded:
            self._send_json(409, {"ok": False, "code": "research_budget_exceeded"})
        except RuntimeError as error:
            code = getattr(error, "api_code", "evolution_package_unavailable")
            self._send_json(
                409 if hasattr(error, "api_code") else 503, {"ok": False, "code": code}
            )
        except (OSError, sqlite3.Error):
            self._send_json(503, {"ok": False, "code": "evolution_package_unavailable"})

    def _handle_external_deep(self, action: str, payload: dict) -> None:
        callback = self._services()._external_deep_request
        if callback is None:
            self._send_json(503, {"ok": False, "code": "external_deep_unavailable"})
            return
        try:
            self._send_json(200, {"ok": True, **callback(action, payload)})
        except LookupError:
            self._send_json(404, {"ok": False, "code": "external_deep_not_found"})
        except PermissionError:
            self._send_json(
                409, {"ok": False, "code": "external_deep_governance_blocked"}
            )
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "code": "invalid_external_deep_request"})
        except ResearchBudgetExceeded:
            self._send_json(409, {"ok": False, "code": "research_budget_exceeded"})
        except (OSError, RuntimeError, sqlite3.Error):
            self._send_json(503, {"ok": False, "code": "external_deep_unavailable"})

    def _read_payload(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > 32000:
            raise ValueError("request too large")
        payload = json.loads(self.rfile.read(size) or b"{}")
        if not isinstance(payload, dict):
            raise InputTypeError("request body must be a JSON object")
        return cast(dict[str, Any], payload)

    def _handle_paper_order(self, payload: dict[str, object]) -> None:
        handle_paper_order(
            self._send_json,
            cast(Mapping[str, str], self.headers),
            payload,
            self._services()._paper_api_dependencies(),
        )

    def _handle_paper_cycle(self, payload: dict[str, object]) -> None:
        handle_paper_cycle(
            self._send_json, payload, self._services()._paper_api_dependencies()
        )

    def log_message(self, format: str, *args: object) -> None:
        logging.getLogger("a_pi.access").info(
            "username=%s ip=%s %s",
            self.claims.username if self.claims else "anonymous",
            self.address_string(),
            format % args,
        )

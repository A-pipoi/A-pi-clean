"""Opt-in, fail-closed research IPO/audit policy shared by research callers."""
from __future__ import annotations

import hashlib
from contextlib import closing
from datetime import datetime, time
from typing import TYPE_CHECKING, Any

from quant.time_utils import SHANGHAI_TZ

from .extension_analytics import latest_rows
from .extension_coverage import ListingCalendar, listing_eligibility
from .extension_financial import financial_status
from .extension_store import ExtensionStore, canonical, default_store_path
from .schema import Bar

if TYPE_CHECKING:
    from quant.api.contracts import ResolvedBacktestUniverse



def preflight_audit_evidence(
    settings: dict[str, Any],
    symbols: list[str],
    *,
    store: ExtensionStore | None = None,
) -> dict[str, Any]:
    """Fail closed before expensive fetches when audit filtering is requested without evidence."""
    if type(settings.get("data_quality_policy", False)) is not bool:
        raise ValueError("data_quality_policy must be boolean")
    if not settings.get("data_quality_policy"):
        return {"required": False, "checked": False}
    exclude_audit = settings.get("exclude_nonstandard_audit", False)
    if type(exclude_audit) is not bool:
        raise ValueError("invalid research eligibility settings")
    if not exclude_audit:
        return {"required": False, "checked": False}
    raw = store or ExtensionStore(default_store_path())
    normalized = [str(item).strip().upper() for item in symbols if str(item).strip()]
    if not normalized:
        raise ValueError("排除非标审计已启用，但股票池为空，无法预检审计证据")
    # Fail closed before Tushare: every requested symbol needs at least one
    # observed fina_audit row. Partial coverage used to pass (any>0) then die
    # mid-backtest with "lacks observed disclosure proof".
    missing: list[str] = []
    with closing(raw.connect()) as db:
        tables = {
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ts_fina_audit'"
            ).fetchall()
        }
        if "ts_fina_audit" not in tables:
            raise ValueError(
                "排除非标审计已启用，但数据扩展库缺少 fina_audit 证据表。"
                "请先同步共享数据扩展，或关闭「排除非标审计」。"
            )
        for symbol in normalized:
            found = db.execute(
                "SELECT 1 FROM ts_fina_audit WHERE ts_code=? LIMIT 1",
                (symbol,),
            ).fetchone()
            if not found:
                missing.append(symbol)
    covered = len(normalized) - len(missing)
    if missing:
        sample = "、".join(missing[:8])
        more = f" 等 {len(missing)} 只" if len(missing) > 8 else ""
        raise ValueError(
            "排除非标审计已启用，但本地 fina_audit 覆盖不足："
            f"{covered}/{len(normalized)} 只有证据；缺证据示例：{sample}{more}。"
            "严格模式不能伪造历史披露时刻——请先同步 fina_audit 覆盖全样本，"
            "或在回测页关闭「排除非标审计」（或关闭「研究数据质量」）后再跑。"
        )
    return {
        "required": True,
        "checked": True,
        "symbols": len(normalized),
        "symbols_with_audit_rows": covered,
        "symbols_missing_audit_rows": 0,
    }


def apply_universe_research_policy(bars: list[Bar], settings: dict[str, Any], universe: ResolvedBacktestUniverse) -> tuple[list[Bar], ResolvedBacktestUniverse]:
    """Bind identical eligibility semantics and provenance across research APIs."""
    filtered, eligibility = apply_research_eligibility(bars, settings)
    if not eligibility['enabled']:
        return filtered, universe
    updated = universe.copy()
    updated['version'] = hashlib.sha256((str(universe['version']) + str(eligibility['version'])).encode()).hexdigest()
    updated['metadata'] = {**universe['metadata'], 'data_quality_policy': eligibility}
    return filtered, updated


def apply_research_eligibility(bars: list[Bar], settings: dict[str, Any], *, store: ExtensionStore | None = None) -> tuple[list[Bar], dict[str, Any]]:
    if type(settings.get('data_quality_policy',False)) is not bool:
        raise ValueError('data_quality_policy must be boolean')
    if not settings.get('data_quality_policy'):
        return bars, {'enabled':False,'reason':'legacy policy preserved; explicit research policy activation required'}
    minimum=settings.get('min_listing_sessions',120)
    exclude_audit=settings.get('exclude_nonstandard_audit',False)
    if type(minimum) is not int or not 0<=minimum<=1000 or type(exclude_audit) is not bool:
        raise ValueError('invalid research eligibility settings')
    raw=store or ExtensionStore(default_store_path())
    listings=latest_rows(raw,'stock_basic')
    sessions=sorted(str(r['cal_date']) for r in latest_rows(raw,'trade_cal') if r.get('exchange')=='SSE' and r.get('is_open')==1)
    calendar=ListingCalendar(tuple(sorted(set(sessions))))
    listing_index: dict[str,list[dict[str,Any]]]={}
    day_symbols: dict[object,set[str]]={}
    for row in listings:
        listing_index.setdefault(str(row.get('ts_code')),[]).append(row)
    for bar in bars:
        day_symbols.setdefault(bar.date,set()).add(bar.symbol)
    reasons: dict[str,int]={}
    result=[]
    proofs=[]
    by_day: dict[object, dict[str, dict[str, Any]]]={}
    for bar in bars:
        status=listing_eligibility(bar.symbol,bar.date.strftime('%Y%m%d'),listing_index.get(bar.symbol,[]),calendar,minimum)
        reason=status['reason']
        if reason in {'insufficient_exchange_calendar','missing_or_ambiguous_listing_metadata','missing_listing_date'}:
            raise ValueError(f'research policy missing evidence: {bar.symbol}: {reason}')
        if status['eligible'] and exclude_audit:
            if bar.date not in by_day:
                cutoff=datetime.combine(bar.date,time(15),SHANGHAI_TZ)
                audit=financial_status(raw,cutoff,sorted(day_symbols[bar.date]))
                by_day[bar.date]={r['symbol']:r for r in audit['audit_eligibility']}
            check=by_day[bar.date][bar.symbol]
            if check['reason']=='missing_audit_evidence':
                raise ValueError('strict historical audit eligibility lacks observed disclosure proof; historical fetch cannot fabricate it')
            if not check['eligible']:
                reason='nonstandard_audit'
                status['eligible']=False
            proofs.append(check['source_hash'])
        if status['eligible']:
            result.append(bar)
        else:
            reasons[reason]=reasons.get(reason,0)+1
    rules={'schema':'observed-research-eligibility-v1','minimum_completed_listing_sessions':minimum,'exclude_nonstandard_audit':exclude_audit,
           'input_bars':len(bars),'output_bars':len(result),'excluded_bar_reasons':reasons,
           'audit_source_hashes':sorted(set(proofs)), 'listing_calendar_hash':hashlib.sha256(canonical({'listings':listings,'sessions':sessions}).encode()).hexdigest(),
           'production_paper_parameters_changed':False,'historical_audit_backfill_enabled':False}
    policy={'schema':rules['schema'],'minimum_completed_listing_sessions':minimum,'exclude_nonstandard_audit':exclude_audit}
    return result, {'enabled':True,**rules,'version':hashlib.sha256(canonical(policy).encode()).hexdigest()}

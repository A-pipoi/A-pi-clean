"""Versioned, bounded daily-bar rules. No user Python, exec or eval."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from quant.errors import InputTypeError

from ..backtest.engine import Order, PortfolioSnapshot
from ..data.schema import Bar
from ..data.trading_lots import rounded_buy_quantity
from ..factors.expression import (
    build_factor_panel,
    compile_expression,
    evaluate_expression,
)

DEFAULT_RECIPE: dict[str,object] = {
    'schema':'daily-rulebook-v1','name':'多信号日线规则',
    'signals':[{'expression':'cs_rank(close / shift(close, 20) - 1)','weight':1.0},
               {'expression':'cs_rank(-ts_std(returns, 20))','weight':.5}],
    'eligibility':'close > ma(close, 20)','top_n':10,'max_weight':.1,
    'risk_off_breadth':.3,'reduced_risk_breadth':.6,'reduced_exposure':.5,
    'stop_loss':.08,'take_profit':.25,'trailing_stop':.1,
}


def validate_recipe(raw: Mapping[str,Any]) -> dict[str,Any]:
    if set(raw)!=set(DEFAULT_RECIPE) or raw.get('schema')!='daily-rulebook-v1':
        raise ValueError('规则字段或 schema 不符合 daily-rulebook-v1')
    recipe=dict(raw)
    if not isinstance(recipe['name'],str) or not 1<=len(recipe['name'].strip())<=80:
        raise ValueError('规则名称必须为1–80个字符')
    recipe['name']=recipe['name'].strip()
    signals=recipe['signals']
    if not isinstance(signals,list) or not 1<=len(signals)<=8:
        raise ValueError('规则必须包含1–8个信号')
    history=0
    for item in signals:
        if not isinstance(item,dict) or set(item)!= {'expression','weight'} or not isinstance(item['expression'],str):
            raise ValueError('每个信号必须具有 expression 和 weight')
        expression=compile_expression(item['expression'])
        history=max(history,expression.min_history)
        weight=item['weight']
        if isinstance(weight,bool) or not isinstance(weight,(int,float)) or not math.isfinite(weight) or not 0<weight<=10:
            raise ValueError('信号权重必须为有限正数，且不超过10')
    if not isinstance(recipe['eligibility'],str):raise InputTypeError('资格表达式必须为字符串')
    history=max(history,compile_expression(recipe['eligibility']).min_history)
    if history>252:raise ValueError('日线规则最多需要252个历史交易日')
    top=recipe['top_n']
    if isinstance(top,bool) or not isinstance(top,int) or not 1<=top<=30:
        raise ValueError('top_n必须是1–30的整数')
    for field in ('max_weight','risk_off_breadth','reduced_risk_breadth','reduced_exposure','stop_loss','take_profit','trailing_stop'):
        value=recipe[field]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<value<1:
            raise ValueError(f'{field}必须是0和1之间的有限数')
    if recipe['risk_off_breadth']>=recipe['reduced_risk_breadth']:
        raise ValueError('空仓广度阈值必须小于减仓阈值')
    # Canonical JSON detaches mutable caller objects and rejects nonfinite numbers.
    return json.loads(json.dumps(recipe,sort_keys=True,allow_nan=False))


class RecipeStore:
    def __init__(self,path: Path) -> None:
        self.path=path
        path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(path,timeout=30)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS recipes(version TEXT PRIMARY KEY,payload TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)')

    def save(self,raw: Mapping[str,Any]) -> dict[str,object]:
        recipe=validate_recipe(raw)
        payload=json.dumps(recipe,sort_keys=True,ensure_ascii=False,allow_nan=False)
        version=hashlib.sha256(payload.encode()).hexdigest()
        with closing(sqlite3.connect(self.path,timeout=30)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM recipes').fetchone()[0]>=256 and not db.execute('SELECT 1 FROM recipes WHERE version=?',(version,)).fetchone():
                raise ValueError('规则库达到256个不可变版本上限')
            db.execute('INSERT OR IGNORE INTO recipes(version,payload) VALUES(?,?)',(version,payload))
        return {'version':version,'recipe':recipe,'research_only':True}

    def get(self,version: str) -> dict[str,Any]:
        with closing(sqlite3.connect(self.path,timeout=30)) as db:
            row=db.execute('SELECT payload FROM recipes WHERE version=?',(version,)).fetchone()
        if not row:raise ValueError('当前账户不存在该规则版本，请先保存')
        return validate_recipe(json.loads(row[0]))

    def list(self) -> list[dict[str,object]]:
        with closing(sqlite3.connect(self.path,timeout=30)) as db:
            rows=db.execute('SELECT version,payload,created_at FROM recipes ORDER BY created_at DESC,version').fetchall()
        return [{'version':row[0],'recipe':json.loads(row[1]),'created_at':row[2]} for row in rows]


class RulebookStrategy:
    def __init__(self,recipe: Mapping[str,Any],rebalance_every: int=5) -> None:
        self.recipe=validate_recipe(recipe)
        if not 1<=rebalance_every<=21:raise ValueError('invalid rulebook rebalance period')
        self.rebalance_every=rebalance_every
        self.days=0
        self.peaks: dict[str,float]={}
        self.last_breadth: float | None=None
        self.history=max(compile_expression(item['expression']).min_history for item in self.recipe['signals'])
        self.history=max(self.history,compile_expression(self.recipe['eligibility']).min_history)

    def factor_metadata(self) -> dict[str,object]:
        return {'rulebook':self.recipe,'research_only':True,'breadth':self.last_breadth,
            'execution':'confirmed prior close -> next-session executable price; no intraday stop fill',
            'pit_fields':'price/volume only; financial and industry expressions fail closed'}

    def orders_for_date(self,trading_date: date,history: dict[str,list[Bar]],portfolio: PortfolioSnapshot) -> list[Order]:
        if any(bar.date>=trading_date for rows in history.values() for bar in rows):
            raise ValueError('rulebook history contains current/future bars')
        self.days+=1
        latest=max((bar.date for rows in history.values() for bar in rows),default=None)
        active={symbol:rows[-self.history-1:] for symbol,rows in history.items()
            if len(rows)>=self.history and rows[-1].date==latest and not rows[-1].paused}
        exits: dict[str,Order]={}
        self.peaks={symbol:peak for symbol,peak in self.peaks.items() if symbol in portfolio.positions}
        for symbol,quantity in portfolio.positions.items():
            rows=history.get(symbol,[]);entry=portfolio.average_entry_prices.get(symbol)
            if not rows or rows[-1].date!=latest or not entry or entry<=0:continue
            close=rows[-1].close
            self.peaks[symbol]=max(self.peaks.get(symbol,entry),close)
            change=close/entry-1
            reason=('rule_close_stop' if change<=-self.recipe['stop_loss'] else
                'rule_close_profit' if change>=self.recipe['take_profit'] else
                'rule_close_trailing_stop' if close/self.peaks[symbol]-1<=-self.recipe['trailing_stop'] else '')
            if reason:exits[symbol]=Order(symbol,'SELL',quantity,tag=reason)
        if not active:return list(exits.values())
        panel=build_factor_panel([bar for rows in active.values() for bar in rows])
        eligibility=evaluate_expression(self.recipe['eligibility'],panel).values[-1]
        signals=[evaluate_expression(item['expression'],panel).values[-1] for item in self.recipe['signals']]
        scores=[];considered=eligible_count=0
        for col,symbol in enumerate(panel.symbols):
            allowed=eligibility[col]
            numbers=[signal[col] for signal in signals]
            if allowed is None or any(value is None or not math.isfinite(value) for value in numbers):continue
            considered+=1
            if allowed>0:
                eligible_count+=1
                if symbol not in exits:
                    score=sum(float(value)*item['weight'] for value,item in zip(numbers,self.recipe['signals']) if value is not None)
                    scores.append((symbol,score))
        if not considered:return list(exits.values())
        self.last_breadth=eligible_count/considered
        scale=(0.0 if self.last_breadth<self.recipe['risk_off_breadth'] else
            self.recipe['reduced_exposure'] if self.last_breadth<self.recipe['reduced_risk_breadth'] else 1.0)
        if scale==0:return [Order(symbol,'SELL',quantity,tag='rule_breadth_risk_off') for symbol,quantity in portfolio.positions.items()]
        if self.days%self.rebalance_every:return list(exits.values())
        selected=[symbol for symbol,_ in sorted(scores,key=lambda item:(-item[1],item[0]))[:self.recipe['top_n']]]
        if not selected:return list(exits.values())
        orders=list(exits.values())
        for symbol,qty in portfolio.positions.items():
            if symbol not in selected and symbol not in exits:orders.append(Order(symbol,'SELL',qty,tag='rule_rebalance_exit'))
        weight=min(1/len(selected),self.recipe['max_weight'])*scale
        for symbol in selected:
            close=active[symbol][-1].close
            delta=portfolio.equity*weight-portfolio.positions.get(symbol,0)*close
            if delta>close:
                quantity=rounded_buy_quantity(symbol,int(delta//close))
                if quantity:orders.append(Order(symbol,'BUY',quantity,tag='rule_rebalance_entry'))
            elif delta<-close:
                orders.append(Order(symbol,'SELL',int(-delta//close),tag='rule_rebalance_reduce'))
        return orders

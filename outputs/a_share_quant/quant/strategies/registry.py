from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..backtest.engine import Strategy
from ..factors.store import FactorDefinition
from ..features.store import FeatureStore
from .audited_baseline import AUDITED_STRATEGIES, AuditedBaseline
from .baseline import (
    CrossSectionalMomentum,
    DualHorizonMomentum,
    LowVolatilityDefensive,
)
from .cost_aware_baseline import CostAwareBaseline
from .lightgbm import WalkForwardLightGBMStrategy
from .ml import WalkForwardLinearStrategy
from .multi_signal import MultiSignalStrategy


class ResearchStrategy(Strategy, Protocol):
    def factor_metadata(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class StrategyPreset:
    key: str
    label: str
    lookback: int
    top_n: int
    description: str


STRATEGY_PRESETS: dict[str, StrategyPreset] = {
    "baseline_cost_aware": StrategyPreset("baseline_cost_aware", "财务与低换手基准 · baseline-cost-financial-v1", 20, 5,
        "严格已观察财务四因子；前8名持仓缓冲、2%仓位偏差门槛、2%费用现金；新研究候选，未证明优于baseline"),
    "rulebook": StrategyPreset("rulebook","复杂日线规则 · 安全 DSL",20,5,
        "不可变规则版本、多信号与资格表达式、广度仓位、收盘止损止盈/移动止损；统一20次研究预算，不自动交易"),
    "multi_signal": StrategyPreset("multi_signal", "多信号与仓位叠加 · research-v1", 20, 5,
        "动量/反转/低波动复合、均线资格、截面广度择时与半仓/空仓条件；收盘触发8%止损/25%止盈，次日可执行价撮合；仅研究"),
    "baseline": StrategyPreset(
        "baseline",
        "横截面因子复合 · baseline-v2",
        20,
        5,
        "20 日动量/波动率因子复合，固定每周调仓，最多持有 5 只",
    ),
    "momentum": StrategyPreset(
        "momentum",
        "双周期因子趋势 · momentum-v3",
        12,
        5,
        "动量优先、波动率抑制的因子复合，固定每周调仓，最多持有 5 只",
    ),
    "defensive": StrategyPreset(
        "defensive",
        "低波动因子防御 · defensive-v2",
        30,
        5,
        "波动率优先、动量辅助的因子复合，固定每周调仓，最多持有 5 只",
    ),
    "inverse_volatility": StrategyPreset(
        "inverse_volatility",
        "逆波动率配置 · portfolio-v1",
        20,
        5,
        "沿用 baseline 信号，以历史日收益率倒数配置权重；单票权重上限 20%，未做行业中性化",
    ),
    "volatility_targeted": StrategyPreset(
        "volatility_targeted",
        "波动率目标配置 · risk-v1",
        20,
        5,
        "沿用 baseline 信号；按历史组合波动率将年化风险控制在15%以内，不加杠杆",
    ),
    "linear_ml": StrategyPreset(
        "linear_ml",
        "滚动线性模型 · ridge-wf-v1",
        20,
        5,
        "每次调仓仅用当时已观测的历史样本训练岭回归，预测下一日收益；未自动晋级或改变纸面策略",
    ),
    "lightgbm": StrategyPreset(
        "lightgbm",
        "滚动 LightGBM · lgbm-wf-v1",
        60,
        5,
        "每次调仓使用点时特征仓库和已成熟标签训练 LightGBM；独立研究策略，不改变纸面默认",
    ),
    "baseline_price_audited": StrategyPreset("baseline_price_audited", "严格价格两因子 · price-audit-v1", 20, 5,
        "动量与低波动同口径；90%完整覆盖；排名8名缓冲、2%调仓门槛、2%现金；含3组训练权重比较，共扣4次研究预算"),
    "baseline_full_audited": StrategyPreset("baseline_full_audited", "严格完整六因子 · full-audit-v1", 20, 5,
        "六因子完整评分；财务仅真实可得记录；90%完整覆盖；低换手控制；含3组训练权重比较，共扣4次研究预算"),
}


def get_strategy_preset(name: str | None) -> StrategyPreset:
    key = str(name or "baseline").strip().lower()
    try:
        return STRATEGY_PRESETS[key]
    except KeyError as error:
        choices = ", ".join(sorted(STRATEGY_PRESETS))
        raise ValueError(
            f"unsupported strategy '{key}', choose one of: {choices}"
        ) from error


class StrategyPresetConflict(ValueError):
    """Fixed catalog strategies reject lookback/top_n overrides."""

    def __init__(self, preset: StrategyPreset, lookback: int, top_n: int) -> None:
        self.code = "strategy_preset_conflict"
        self.expected = {
            "strategy": preset.key,
            "lookback": preset.lookback,
            "top_n": preset.top_n,
        }
        self.got = {"lookback": int(lookback), "top_n": int(top_n)}
        super().__init__(
            f"{preset.key} 使用固定研究预设：lookback={preset.lookback}, top_n={preset.top_n}；"
            f"请求为 lookback={lookback}, top_n={top_n}；不允许通过调参追逐历史最优"
        )


def resolve_strategy_config(
    name: str | None, lookback: int | None = None, top_n: int | None = None
) -> tuple[StrategyPreset, int, int]:
    preset = get_strategy_preset(name)
    actual_lookback = preset.lookback if lookback is None else int(lookback)
    actual_top_n = preset.top_n if top_n is None else int(top_n)
    if actual_lookback != preset.lookback or actual_top_n != preset.top_n:
        raise StrategyPresetConflict(preset, actual_lookback, actual_top_n)
    return preset, actual_lookback, actual_top_n


def build_strategy(
    name: str | None,
    *,
    rebalance_every: int,
    factor_definitions: Sequence[FactorDefinition] | None = None,
    fundamental_values: Mapping[str, Mapping[str, float]] | None = None,
    feature_store: FeatureStore | None = None,
    data_version: str | None = None,
) -> ResearchStrategy:
    preset = get_strategy_preset(name)
    if rebalance_every <= 0:
        raise ValueError("rebalance_every must be positive")
    if preset.key in AUDITED_STRATEGIES:
        return AuditedBaseline(full=preset.key == "baseline_full_audited", rebalance_every=rebalance_every,
                               factor_definitions=factor_definitions)
    if preset.key == "rulebook":
        raise ValueError('复杂规则必须提供已保存的账户规则版本，不允许使用默认策略替代')
    if preset.key == "multi_signal":
        return MultiSignalStrategy(rebalance_every=rebalance_every)
    if preset.key == "baseline_cost_aware":
        return CostAwareBaseline(rebalance_every=rebalance_every, factor_definitions=factor_definitions)
    if preset.key == "baseline":
        return CrossSectionalMomentum(
            preset.lookback,
            preset.top_n,
            rebalance_every,
            max_weight=0.20,
            factor_definitions=factor_definitions,
            fundamental_values=fundamental_values,
        )
    if preset.key == "momentum":
        return DualHorizonMomentum(
            preset.lookback,
            preset.top_n,
            rebalance_every,
            max_weight=0.20,
            factor_definitions=factor_definitions,
            fundamental_values=fundamental_values,
        )
    if preset.key == "defensive":
        return LowVolatilityDefensive(
            preset.lookback,
            preset.top_n,
            rebalance_every,
            max_weight=0.20,
            factor_definitions=factor_definitions,
            fundamental_values=fundamental_values,
        )
    if preset.key == "inverse_volatility":
        return CrossSectionalMomentum(
            preset.lookback,
            preset.top_n,
            rebalance_every,
            max_weight=0.20,
            factor_definitions=factor_definitions,
            fundamental_values=fundamental_values,
            allocation_mode="inverse_volatility",
        )
    if preset.key == "volatility_targeted":
        return CrossSectionalMomentum(
            preset.lookback,
            preset.top_n,
            rebalance_every,
            max_weight=0.20,
            factor_definitions=factor_definitions,
            fundamental_values=fundamental_values,
            allocation_mode="volatility_targeted",
        )
    if preset.key == "lightgbm":
        if feature_store is None or not data_version:
            raise ValueError("LightGBM 研究策略需要点时 FeatureStore 和数据版本")
        return WalkForwardLightGBMStrategy(
            feature_store,
            data_version,
            top_n=preset.top_n,
            rebalance_every=rebalance_every,
            max_weight=0.20,
        )
    return WalkForwardLinearStrategy(
        preset.lookback,
        preset.top_n,
        rebalance_every,
        max_weight=0.20,
    )


def build_research_strategy(
    name: str | None,
    *,
    lookback: int,
    top_n: int,
    rebalance_every: int,
    factor_definitions: Sequence[FactorDefinition] | None = None,
    fundamental_values: Mapping[str, Mapping[str, float]] | None = None,
) -> ResearchStrategy:
    """Build a temporary parameter variant for sensitivity analysis only.

    The production registry remains frozen through ``build_strategy`` and
    ``resolve_strategy_config``.  This explicit escape hatch is used by the
    validation module to test robustness rather than to tune a live strategy.
    """

    key = str(name or "baseline").strip().lower()
    if key not in STRATEGY_PRESETS:
        get_strategy_preset(key)
    if key in {"lightgbm", "multi_signal", "rulebook", "baseline_cost_aware"} | AUDITED_STRATEGIES:
        raise ValueError("该隔离研究策略不开放参数敏感性扫描；模型、特征或规则口径已固定")
    if min(int(lookback), int(top_n), int(rebalance_every)) <= 0:
        raise ValueError("research strategy parameters must be positive")
    if key == "baseline":
        return CrossSectionalMomentum(
            int(lookback),
            int(top_n),
            int(rebalance_every),
            max_weight=0.20,
            factor_definitions=factor_definitions,
            fundamental_values=fundamental_values,
        )
    if key == "momentum":
        return DualHorizonMomentum(
            int(lookback),
            int(top_n),
            int(rebalance_every),
            max_weight=0.20,
            factor_definitions=factor_definitions,
            fundamental_values=fundamental_values,
        )
    if key == "defensive":
        return LowVolatilityDefensive(
            int(lookback),
            int(top_n),
            int(rebalance_every),
            max_weight=0.20,
            factor_definitions=factor_definitions,
            fundamental_values=fundamental_values,
        )
    if key == "inverse_volatility":
        return CrossSectionalMomentum(
            int(lookback),
            int(top_n),
            int(rebalance_every),
            max_weight=0.20,
            factor_definitions=factor_definitions,
            fundamental_values=fundamental_values,
            allocation_mode="inverse_volatility",
        )
    if key == "volatility_targeted":
        return CrossSectionalMomentum(
            int(lookback),
            int(top_n),
            int(rebalance_every),
            max_weight=0.20,
            factor_definitions=factor_definitions,
            fundamental_values=fundamental_values,
            allocation_mode="volatility_targeted",
        )
    return WalkForwardLinearStrategy(
        int(lookback),
        int(top_n),
        int(rebalance_every),
        max_weight=0.20,
    )

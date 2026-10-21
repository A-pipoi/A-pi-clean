from __future__ import annotations

import ast
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TypeGuard

from quant.errors import InputTypeError

from ..data.pit import FeatureAvailabilityChecker
from ..data.schema import Bar, FeatureObservation, PITIndustryObservation

_BASE_FIELDS = {"open", "high", "low", "close", "volume", "returns"}
_PIT_FIELDS = {"pe", "pb", "roe", "netprofit_yoy", "tr_yoy", "debt_to_assets", "market_cap", "float_market_cap"}
from ..data.extension_features import FIELD_NAMES as EXTENSION_FIELDS
from ..data.extension_features import TEMPLATES as EXTENSION_TEMPLATES

_PIT_FIELDS.update(EXTENSION_FIELDS)
_UNARY_TS = {"ts_mean", "ma", "ts_std", "ts_rank", "ts_delta", "ts_max", "ts_min", "ts_argmax", "ts_argmin", "ema", "shift"}
_BINARY_TS = {"ts_corr", "ts_cov"}
_CROSS_SECTIONAL = {"cs_rank", "cs_zscore", "cs_scale"}
_SCALAR_FUNCTIONS = {"abs", "log", "sign"}
_ALL_FUNCTIONS = _UNARY_TS | _BINARY_TS | _CROSS_SECTIONAL | _SCALAR_FUNCTIONS | {"if_else", "cs_industry_neutralize", "cs_size_neutralize"}
_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_CMPOPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)
Panel = list[list[float | None]]
Value = float | Panel
ScalarFunction = Callable[[float], float | bool]
BinaryFunction = Callable[[float, float], float | bool]
TernaryFunction = Callable[[float, float, float], float]


def _is_finite(value: float | None) -> TypeGuard[float]:
    return value is not None and math.isfinite(value)


def _numeric_constant(node: ast.AST, *, label: str = "常数") -> float:
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
        raise InputTypeError(f"{label}必须是数值")
    return float(node.value)

EXPRESSION_TEMPLATES: tuple[dict[str, str], ...] = (
    {"name": "5日动量", "expression": "close / shift(close, 5) - 1", "category": "price_momentum"},
    {"name": "20日均线偏离", "expression": "close / ma(close, 20) - 1", "category": "rolling_stat"},
    {"name": "20日波动率", "expression": "ts_std(returns, 20)", "category": "volatility"},
    {"name": "量能相对强度", "expression": "volume / (ts_mean(volume, 20) + 1)", "category": "volume"},
    {"name": "收盘区间位置", "expression": "(close - low) / (high - low + 0.01)", "category": "candle_shape"},
    {"name": "实体占振幅", "expression": "(close - open) / (high - low + 0.01)", "category": "candle_shape"},
    {"name": "20日价格时序分位", "expression": "ts_rank(close, 20)", "category": "rolling_stat"},
    {"name": "量价收益相关", "expression": "ts_corr(returns, volume, 20)", "category": "volume"},
)
EXPRESSION_TEMPLATES += EXTENSION_TEMPLATES


@dataclass(frozen=True)
class CompiledExpression:
    expression: str
    tree: ast.Expression
    dependencies: tuple[str, ...]
    min_history: int
    tree_json: str


@dataclass(frozen=True)
class FactorPanel:
    dates: tuple[date, ...]
    symbols: tuple[str, ...]
    fields: Mapping[str, Panel]
    industries: tuple[tuple[str | None, ...], ...]
    data_version: str
    point_in_time_fields: tuple[str, ...] = ()
    pit_quality: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExpressionResult:
    dates: tuple[date, ...]
    symbols: tuple[str, ...]
    values: Panel
    metadata: Mapping[str, object]

    def by_date(self) -> dict[date, dict[str, float]]:
        return {
            day: {
                symbol: float(value)
                for symbol, value in zip(self.symbols, row)
                if value is not None and math.isfinite(value)
            }
            for day, row in zip(self.dates, self.values)
        }


def compile_expression(
    expression: str,
    *,
    registered_pit_fields: Sequence[str] = (),
    pit_quality: Mapping[str, str] | None = None,
) -> CompiledExpression:
    """Parse a deliberately small Python-like DSL; arbitrary Python is never evaluated."""

    source = str(expression).strip()
    if not source or len(source) > 500:
        raise ValueError("表达式不能为空且不得超过 500 个字符")
    try:
        parsed = ast.parse(source, mode="eval")
    except SyntaxError as error:
        raise ValueError(f"表达式语法错误：{error.msg}") from error
    pit_fields = set(registered_pit_fields) & _PIT_FIELDS
    dependencies: set[str] = set()
    minimum = _validate_node(parsed.body, dependencies, pit_fields, dict(pit_quality or {}))
    return CompiledExpression(
        expression=source,
        tree=parsed,
        dependencies=tuple(sorted(dependencies)),
        min_history=minimum,
        tree_json=ast.dump(parsed, annotate_fields=True, include_attributes=False),
    )


def build_factor_panel(
    bars: Sequence[Bar],
    *,
    data_version: str = "unknown",
    observations: Sequence[FeatureObservation] = (),
    industry_observations: Sequence[PITIndustryObservation] = (),
    pit_quality: Mapping[str, str] | None = None,
) -> FactorPanel:
    ordered = sorted(bars, key=lambda bar: (bar.date, bar.symbol))
    dates = tuple(sorted({bar.date for bar in ordered}))
    symbols = tuple(sorted({bar.symbol for bar in ordered}))
    date_index = {item: index for index, item in enumerate(dates)}
    symbol_index = {item: index for index, item in enumerate(symbols)}
    fields = {key: _empty_panel(len(dates), len(symbols)) for key in _BASE_FIELDS}
    industries: list[list[str | None]] = [[None for _ in symbols] for _ in dates]
    previous_close: dict[str, tuple[int, float]] = {}
    for bar in ordered:
        row, column = date_index[bar.date], symbol_index[bar.symbol]
        for key in ("open", "high", "low", "close", "volume"):
            signal_value = getattr(bar, f"signal_{key}", None) if key != "volume" else None
            value = float(signal_value if signal_value is not None else getattr(bar, key))
            fields[key][row][column] = value if math.isfinite(value) else None
        signal_close = bar.signal_close if bar.signal_close is not None else bar.close
        prior = previous_close.get(bar.symbol)
        fields["returns"][row][column] = (
            float(signal_close) / prior[1] - 1.0
            if prior and prior[0] == row - 1 and prior[1] > 0 and signal_close > 0
            else None
        )
        previous_close[bar.symbol] = (row, float(signal_close))

    pit_names: set[str] = set()
    checker = FeatureAvailabilityChecker()
    for observation in observations:
        if observation.name not in _PIT_FIELDS:
            raise ValueError(f"未注册的 PIT 财务字段：{observation.name}")
        checker.check(observation)
        observation_row = date_index.get(observation.as_of)
        observation_column = symbol_index.get(observation.symbol)
        if observation_row is None or observation_column is None:
            continue
        if observation.name not in fields:
            fields[observation.name] = _empty_panel(len(dates), len(symbols))
        if observation.value is not None and math.isfinite(float(observation.value)):
            fields[observation.name][observation_row][observation_column] = float(observation.value)
            pit_names.add(observation.name)
    valid_industry_basis = {"provider_available_at", "first_seen_at"}
    verified_industry_rows = 0
    for industry_observation in industry_observations:
        checker.check(
            FeatureObservation(
                industry_observation.symbol,
                industry_observation.as_of,
                industry_observation.available_at,
                "industry",
                1.0,
                industry_observation.source_version,
            )
        )
        if industry_observation.availability_basis not in valid_industry_basis:
            continue
        industry_row = date_index.get(industry_observation.as_of)
        industry_column = symbol_index.get(industry_observation.symbol)
        if industry_row is None or industry_column is None:
            continue
        industries[industry_row][industry_column] = industry_observation.industry
        verified_industry_rows += 1
    quality = {str(key): str(value) for key, value in dict(pit_quality or {}).items()}
    if (
        verified_industry_rows == 0
        or verified_industry_rows != len(industry_observations)
        or quality.get("industry") != "verified"
    ):
        quality["industry"] = "unverified"
    return FactorPanel(
        dates,
        symbols,
        fields,
        tuple(tuple(row) for row in industries),
        str(data_version),
        tuple(sorted(pit_names)),
        quality,
    )


def evaluate_expression(
    expression: str | CompiledExpression,
    panel: FactorPanel,
    *,
    code_version: str = "uncommitted",
) -> ExpressionResult:
    source = expression.expression if isinstance(expression, CompiledExpression) else expression
    compiled = compile_expression(
        source,
        registered_pit_fields=panel.point_in_time_fields,
        pit_quality=panel.pit_quality,
    )
    absent = set(compiled.dependencies) - set(panel.fields) - {"industry"}
    if absent:
        raise ValueError(f"表达式依赖的数据字段未提供：{', '.join(sorted(absent))}")
    raw = _evaluate_node(compiled.tree.body, panel)
    matrix = _as_panel(raw, len(panel.dates), len(panel.symbols))
    computed_at = datetime.now(UTC).isoformat()
    return ExpressionResult(
        panel.dates,
        panel.symbols,
        matrix,
        {
            "expression": compiled.expression,
            "operator_tree": compiled.tree_json,
            "dependencies": list(compiled.dependencies),
            "min_history": compiled.min_history,
            "data_version": panel.data_version,
            "pit_quality": dict(panel.pit_quality),
            "code_version": str(code_version),
            "computed_at": computed_at,
        },
    )


def _validate_node(
    node: ast.AST,
    dependencies: set[str],
    pit_fields: set[str],
    pit_quality: Mapping[str, str],
) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        if not math.isfinite(float(node.value)):
            raise ValueError("常数必须是有限数值")
        return 1
    if isinstance(node, ast.Name):
        name = node.id.lower()
        if name in _BASE_FIELDS:
            dependencies.add(name)
            return 1
        if name in _PIT_FIELDS:
            if name not in pit_fields:
                raise ValueError(f"财务字段 {name} 未绑定公告日对齐的 PIT 观测，已拒绝编译")
            dependencies.add(name)
            return 1
        raise ValueError(f"不允许的数据字段：{node.id}")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
        return _validate_node(node.operand, dependencies, pit_fields, pit_quality)
    if isinstance(node, ast.BinOp) and isinstance(node.op, _BINOPS):
        return max(
            _validate_node(node.left, dependencies, pit_fields, pit_quality),
            _validate_node(node.right, dependencies, pit_fields, pit_quality),
        )
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], _CMPOPS):
        return max(
            _validate_node(node.left, dependencies, pit_fields, pit_quality),
            _validate_node(node.comparators[0], dependencies, pit_fields, pit_quality),
        )
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        return max(_validate_node(item, dependencies, pit_fields, pit_quality) for item in node.values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        function = node.func.id.lower()
        if function not in _ALL_FUNCTIONS:
            raise ValueError(f"不允许的算子：{node.func.id}")
        if function == "cs_industry_neutralize" and pit_quality.get("industry") != "verified":
            raise ValueError("行业中性化需要带可信可得时间的历史 PIT 行业面板；当前来源只有生效区间，已锁定")
        if function == "cs_industry_neutralize":
            dependencies.add("industry")
        if function == "cs_size_neutralize":
            if "market_cap" not in pit_fields or pit_quality.get("size") != "verified":
                raise ValueError("市值中性化需要已绑定且通过覆盖检查的历史 PIT 总市值")
            dependencies.add("market_cap")
        expected = 3 if function in _BINARY_TS | {"if_else"} else 1 if function in _CROSS_SECTIONAL | _SCALAR_FUNCTIONS | {"cs_industry_neutralize", "cs_size_neutralize"} else 2
        if len(node.args) != expected:
            raise ValueError(f"算子 {function} 需要 {expected} 个参数")
        child_windows = [_validate_node(item, dependencies, pit_fields, pit_quality) for item in node.args]
        if function in _UNARY_TS | _BINARY_TS:
            window_arg = node.args[-1]
            if not isinstance(window_arg, ast.Constant) or isinstance(window_arg.value, bool):
                raise ValueError(f"算子 {function} 的窗口必须是常数整数")
            window_value = _numeric_constant(window_arg, label=f"算子 {function} 的窗口")
            window = int(window_value)
            if window_value != window or not 1 <= window <= 252:
                raise ValueError("时序窗口必须是 1 到 252 的正整数")
            extra_history = window if function in {"ts_delta", "shift"} else window - 1
            return max(child_windows[:-1], default=1) + extra_history
        return max(child_windows, default=1)
    raise ValueError(f"表达式包含不支持的语法节点：{type(node).__name__}")


def _evaluate_node(node: ast.AST, panel: FactorPanel) -> Value:
    rows, columns = len(panel.dates), len(panel.symbols)
    if isinstance(node, ast.Constant):
        return _numeric_constant(node)
    if isinstance(node, ast.Name):
        return panel.fields[node.id.lower()]
    if isinstance(node, ast.UnaryOp):
        value = _evaluate_node(node.operand, panel)
        op = (lambda item: not bool(item)) if isinstance(node.op, ast.Not) else (lambda item: -item) if isinstance(node.op, ast.USub) else (lambda item: item)
        return _map_value(value, op, rows, columns)
    if isinstance(node, ast.BinOp):
        left, right = _evaluate_node(node.left, panel), _evaluate_node(node.right, panel)
        function = {
            ast.Add: lambda a, b: a + b,
            ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b,
            ast.Div: lambda a, b: a / b if b != 0 else math.nan,
        }[type(node.op)]
        return _combine(left, right, function, rows, columns)
    if isinstance(node, ast.Compare):
        left, right = _evaluate_node(node.left, panel), _evaluate_node(node.comparators[0], panel)
        compare = {
            ast.Lt: lambda a, b: a < b,
            ast.LtE: lambda a, b: a <= b,
            ast.Gt: lambda a, b: a > b,
            ast.GtE: lambda a, b: a >= b,
            ast.Eq: lambda a, b: a == b,
            ast.NotEq: lambda a, b: a != b,
        }[type(node.ops[0])]
        return _combine(left, right, lambda a, b: float(compare(a, b)), rows, columns)
    if isinstance(node, ast.BoolOp):
        values = [_evaluate_node(item, panel) for item in node.values]
        combine = (lambda a, b: float(bool(a) and bool(b))) if isinstance(node.op, ast.And) else (lambda a, b: float(bool(a) or bool(b)))
        result = values[0]
        for value in values[1:]:
            result = _combine(result, value, combine, rows, columns)
        return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id.lower()
        if name in _UNARY_TS:
            source = _as_panel(_evaluate_node(node.args[0], panel), rows, columns)
            window = int(_numeric_constant(node.args[1], label=f"算子 {name} 的窗口"))
            return _time_series(name, source, window)
        if name in _BINARY_TS:
            left = _as_panel(_evaluate_node(node.args[0], panel), rows, columns)
            right = _as_panel(_evaluate_node(node.args[1], panel), rows, columns)
            return _time_series_binary(name, left, right, int(_numeric_constant(node.args[2], label=f"算子 {name} 的窗口")))
        args = [_evaluate_node(item, panel) for item in node.args]
        if name in _CROSS_SECTIONAL:
            return _cross_section(name, _as_panel(args[0], rows, columns))
        if name == "cs_industry_neutralize":
            return _industry_neutralize(_as_panel(args[0], rows, columns), panel.industries)
        if name == "cs_size_neutralize":
            return _size_neutralize(_as_panel(args[0], rows, columns), panel.fields["market_cap"])
        if name in _SCALAR_FUNCTIONS:
            scalar_functions: dict[str, ScalarFunction] = {
                "abs": lambda item: abs(item),
                "log": lambda item: math.log(item) if item > 0 else math.nan,
                "sign": lambda item: float((item > 0) - (item < 0)),
            }
            scalar_function = scalar_functions[name]
            return _map_value(args[0], scalar_function, rows, columns)
        if name == "if_else":
            condition, yes, no = args
            return _combine3(condition, yes, no, lambda cond, a, b: a if cond != 0 else b, rows, columns)
    raise ValueError("无法执行该表达式")


def _time_series(name: str, values: Panel, window: int) -> Panel:
    rows = len(values)
    columns = len(values[0]) if rows else 0
    out = _empty_panel(rows, columns)
    first_row = window if name in {"ts_delta", "shift"} else window - 1
    for row in range(first_row, rows):
        for column in range(columns):
            start = row - window if name in {"ts_delta", "shift"} else row - window + 1
            if name in {"ts_delta", "shift"}:
                left = values[start][column]
                right = values[row][column]
                if not _is_finite(left) or not _is_finite(right):
                    continue
                out[row][column] = left if name == "shift" else right - left
                continue
            sample = [values[index][column] for index in range(start, row + 1)]
            if not all(_is_finite(value) for value in sample):
                continue
            concrete = [value for value in sample if _is_finite(value)]
            current = concrete[-1]
            if name in {"ts_mean", "ma"}:
                value = sum(concrete) / window
            elif name == "ts_std":
                value = statistics.stdev(concrete) if window > 1 else 0.0
            elif name == "ts_rank":
                ordered = sorted(concrete)
                first = ordered.index(current)
                last = len(ordered) - 1 - ordered[::-1].index(current)
                value = ((first + last) / 2.0) / max(window - 1, 1)
            elif name == "ts_delta":
                value = concrete[-1] - concrete[0]
            elif name == "ts_max":
                value = max(concrete)
            elif name == "ts_min":
                value = min(concrete)
            elif name == "ts_argmax":
                value = float(concrete.index(max(concrete)))
            elif name == "ts_argmin":
                value = float(concrete.index(min(concrete)))
            elif name == "shift":
                out[row][column] = values[row - window][column]
                continue
            else:  # EMA, seeded only after a complete window.
                alpha = 2.0 / (window + 1.0)
                value = concrete[0]
                for observation in concrete[1:]:
                    value = alpha * observation + (1.0 - alpha) * value
            out[row][column] = value if math.isfinite(value) else None
    return out


def _time_series_binary(name: str, left: Panel, right: Panel, window: int) -> Panel:
    rows = len(left)
    columns = len(left[0]) if rows else 0
    out = _empty_panel(rows, columns)
    for row in range(window - 1, rows):
        for column in range(columns):
            x = [left[index][column] for index in range(row - window + 1, row + 1)]
            y = [right[index][column] for index in range(row - window + 1, row + 1)]
            if not all(_is_finite(value) for value in x + y):
                continue
            xs = [value for value in x if _is_finite(value)]
            ys = [value for value in y if _is_finite(value)]
            xm, ym = sum(xs) / window, sum(ys) / window
            covariance = sum((a - xm) * (b - ym) for a, b in zip(xs, ys)) / max(window - 1, 1)
            if name == "ts_cov":
                out[row][column] = covariance
            else:
                denominator = math.sqrt(sum((a - xm) ** 2 for a in xs) * sum((b - ym) ** 2 for b in ys))
                out[row][column] = sum((a - xm) * (b - ym) for a, b in zip(xs, ys)) / denominator if denominator else None
    return out


def _cross_section(name: str, values: Panel) -> Panel:
    rows = len(values)
    columns = len(values[0]) if rows else 0
    out = _empty_panel(rows, columns)
    for row, sample in enumerate(values):
        indices = [index for index, value in enumerate(sample) if _is_finite(value)]
        if not indices:
            continue
        concrete = [value for index in indices if _is_finite(value := sample[index])]
        mean = sum(concrete) / len(concrete)
        if name == "cs_rank":
            ordered = sorted(concrete)
            for index in indices:
                value = sample[index]
                assert value is not None
                first = ordered.index(value)
                last = len(ordered) - 1 - ordered[::-1].index(value)
                out[row][index] = ((first + last) / 2.0) / max(len(ordered) - 1, 1)
        elif name == "cs_zscore":
            deviation = math.sqrt(sum((value - mean) ** 2 for value in concrete) / len(concrete))
            for index in indices:
                value = sample[index]
                assert value is not None
                out[row][index] = (value - mean) / deviation if deviation else 0.0
        else:
            denominator = sum(abs(value) for value in concrete)
            for index in indices:
                value = sample[index]
                assert value is not None
                out[row][index] = value / denominator if denominator else 0.0
    return out


def _industry_neutralize(values: Panel, industries: tuple[tuple[str | None, ...], ...]) -> Panel:
    rows = len(values)
    columns = len(values[0]) if rows else 0
    out = _empty_panel(rows, columns)
    for row in range(rows):
        groups: dict[str, list[int]] = {}
        for column, value in enumerate(values[row]):
            industry = industries[row][column]
            if industry and value is not None and math.isfinite(value):
                groups.setdefault(industry, []).append(column)
        for indices in groups.values():
            concrete = [value for index in indices if _is_finite(value := values[row][index])]
            mean = sum(concrete) / len(concrete)
            for index in indices:
                value = values[row][index]
                assert value is not None
                out[row][index] = value - mean
    return out


def _size_neutralize(values: Panel, market_caps: Panel) -> Panel:
    rows = len(values)
    columns = len(values[0]) if rows else 0
    out = _empty_panel(rows, columns)
    for row in range(rows):
        indices = [
            column for column in range(columns)
            if _is_finite(values[row][column])
            and (cap := market_caps[row][column]) is not None
            and math.isfinite(cap)
            and cap > 0
        ]
        if len(indices) < 3:
            continue
        xs = [math.log(value) for index in indices if _is_finite(value := market_caps[row][index])]
        ys = [value for index in indices if _is_finite(value := values[row][index])]
        mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
        variance = sum((value - mean_x) ** 2 for value in xs)
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / variance if variance > 1e-15 else 0.0
        intercept = mean_y - slope * mean_x
        for index, x, y in zip(indices, xs, ys):
            out[row][index] = y - (intercept + slope * x)
    return out


def _map_value(value: Value, function: ScalarFunction, rows: int, columns: int) -> Value:
    if isinstance(value, (int, float)):
        try:
            result = float(function(float(value)))
            return result if math.isfinite(result) else math.nan
        except (ArithmeticError, ValueError):
            return math.nan
    out = _empty_panel(rows, columns)
    for row in range(rows):
        for column in range(columns):
            current = value[row][column]
            if current is None:
                continue
            try:
                result = float(function(current))
                out[row][column] = result if math.isfinite(result) else None
            except (ArithmeticError, ValueError):
                pass
    return out


def _combine(left: Value, right: Value, function: BinaryFunction, rows: int, columns: int) -> Value:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        try:
            result = float(function(float(left), float(right)))
            return result if math.isfinite(result) else math.nan
        except (ArithmeticError, ValueError):
            return math.nan
    a, b = _as_panel(left, rows, columns), _as_panel(right, rows, columns)
    out = _empty_panel(rows, columns)
    for row in range(rows):
        for column in range(columns):
            left_value, right_value = a[row][column], b[row][column]
            if left_value is None or right_value is None:
                continue
            try:
                value = float(function(left_value, right_value))
                out[row][column] = value if math.isfinite(value) else None
            except (ArithmeticError, ValueError):
                pass
    return out


def _combine3(first: Value, second: Value, third: Value, function: TernaryFunction, rows: int, columns: int) -> Panel:
    a, b, c = (_as_panel(value, rows, columns) for value in (first, second, third))
    out = _empty_panel(rows, columns)
    for row in range(rows):
        for column in range(columns):
            first_value, second_value, third_value = a[row][column], b[row][column], c[row][column]
            if first_value is None or second_value is None or third_value is None:
                continue
            try:
                value = float(function(first_value, second_value, third_value))
                out[row][column] = value if math.isfinite(value) else None
            except (ArithmeticError, ValueError):
                pass
    return out


def _as_panel(value: Value, rows: int, columns: int) -> Panel:
    if isinstance(value, (int, float)):
        scalar = float(value)
        return [[scalar if math.isfinite(scalar) else None for _ in range(columns)] for _ in range(rows)]
    return value


def _empty_panel(rows: int, columns: int) -> Panel:
    return [[None for _ in range(columns)] for _ in range(rows)]

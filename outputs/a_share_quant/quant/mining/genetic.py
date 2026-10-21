from __future__ import annotations

import ast
import hashlib
import math
import random
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from quant.errors import InputTypeError

from ..data.schema import Bar, FeatureObservation, PITIndustryObservation
from ..factors.expression import (
    FactorPanel,
    Panel,
    build_factor_panel,
    compile_expression,
    evaluate_expression,
)
from .portfolio_metrics import daily_spreads, moments


@dataclass(frozen=True)
class FactorMiningResult:
    seed: int
    generations: int
    population_size: int
    n_tested: int
    training_start: str | None
    training_end: str
    data_version: str
    candidates: tuple[dict[str, object], ...]
    generation_log: tuple[dict[str, object], ...]
    protocol: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "generations": self.generations,
            "population_size": self.population_size,
            "n_tested": self.n_tested,
            "training_start": self.training_start,
            "training_end": self.training_end,
            "data_version": self.data_version,
            "candidates": list(self.candidates),
            "generation_log": list(self.generation_log),
            "protocol": dict(self.protocol),
        }


@dataclass(frozen=True)
class _Node:
    name: str
    args: tuple[_Node | float, ...] = ()


def _numeric(value: object, *, default: float = -1.0) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default
    return float(value)


def _node_arg(node: _Node, index: int) -> _Node:
    value = node.args[index]
    if not isinstance(value, _Node):
        raise InputTypeError(f"算子 {node.name} 的第 {index + 1} 个参数必须是表达式")
    return value


def _constant_arg(node: _Node, index: int) -> float:
    value = node.args[index]
    if isinstance(value, _Node):
        raise InputTypeError(f"算子 {node.name} 的第 {index + 1} 个参数必须是常数")
    return value


_TERMINALS = ("open", "high", "low", "close", "volume", "returns")
_UNARY = ("abs", "sign", "log", "ts_mean", "ts_std", "ts_rank", "ts_delta", "ts_max", "ts_min")
_WINDOWS = (5.0, 10.0, 20.0)
_BINARY = ("+", "-", "*", "/")


def run_factor_mining(
    bars: Sequence[Bar],
    *,
    train_end: date,
    seed: int = 20260927,
    generations: int = 10,
    population_size: int = 50,
    top_n: int = 10,
    data_version: str = "unknown",
    observations: Sequence[FeatureObservation] = (),
    industry_observations: Sequence[PITIndustryObservation] = (),
    pit_quality: dict[str, str] | None = None,
    existing_factor_panels: Sequence[Panel] = (),
    fitness_mode: str = "legacy",
    correlation_lambda: float = .5,
    complexity_mu: float = .0005,
    temporal_cv: bool = True,
    cv_min_abs_ic: float = .02,
    field_subset: Sequence[str] | None = None,
    attempt_callback: Callable[[int], None] | None = None,
    operator_subset: Sequence[str] | None = None,
) -> FactorMiningResult:
    """Run deterministic, training-only symbolic evolution over a factor panel."""

    if not 1 <= generations <= 20:
        raise ValueError("generations must be between 1 and 20")
    if not 4 <= population_size <= 100:
        raise ValueError("population_size must be between 4 and 100")
    if not 1 <= top_n <= 20:
        raise ValueError("top_n must be between 1 and 20")
    if fitness_mode not in {"icir", "legacy"}:
        raise ValueError("fitness_mode must be icir or legacy")
    for value in (correlation_lambda, complexity_mu, cv_min_abs_ic):
        if not math.isfinite(value) or value < 0:
            raise ValueError("fitness penalties and CV threshold must be finite and nonnegative")
    ordered = sorted((bar for bar in bars if bar.date <= train_end), key=lambda bar: (bar.date, bar.symbol))
    if not ordered:
        raise ValueError("训练区间没有可用行情")
    panel = build_factor_panel(
        ordered,
        data_version=data_version,
        observations=observations,
        industry_observations=industry_observations,
        pit_quality=pit_quality,
    )
    eligible_rows = _eligible_training_rows(panel, train_end, horizon=5)
    if len(eligible_rows) < 60:
        raise ValueError(f"非密封训练区间仅有 {len(eligible_rows)} 个可评估交易日，至少需要 60 日")

    rng = random.Random(int(seed))
    terminals = tuple(dict.fromkeys((*_TERMINALS, *panel.point_in_time_fields)))
    if field_subset is not None:
        if not field_subset or set(field_subset) - set(terminals):
            raise ValueError("field_subset contains unavailable or non-PIT fields")
        terminals = tuple(dict.fromkeys(field_subset))
    cross_functions = ["cs_rank", "cs_zscore", "cs_scale"]
    if "size" in panel.pit_quality and panel.pit_quality["size"] == "verified":
        cross_functions.append("cs_size_neutralize")
    if "industry" in panel.pit_quality and panel.pit_quality["industry"] == "verified":
        cross_functions.append("cs_industry_neutralize")
    if operator_subset is not None and (not operator_subset or set(operator_subset) - {*_UNARY, *_BINARY, *cross_functions}):
        raise ValueError("operator_subset contains unavailable operators")
    population = [_random_tree(rng, 3, terminals, cross_functions, operator_subset) for _ in range(population_size)]
    n_tested = 0
    generation_log: list[dict[str, object]] = []
    all_candidates: dict[str, dict[str, object]] = {}

    for generation in range(generations):
        scored: list[dict[str, object]] = []
        value_vectors: list[list[float | None] | None] = []
        n_tested += len(population)
        if attempt_callback:
            attempt_callback(n_tested)
        for node in population:
            expression = "<invalid GP AST>"
            try:
                expression = _render(node)
                compiled = compile_expression(
                    expression,
                    registered_pit_fields=panel.point_in_time_fields,
                    pit_quality=panel.pit_quality,
                )
                result = evaluate_expression(compiled, panel)
                daily = daily_spreads(panel, result.values, quantiles=10, end=train_end)
                portfolio_sr = moments([row["return"] for row in daily["rows"]]).get("daily_sharpe")
                ic_values = _rank_ic_series(panel, result.values, eligible_rows, horizon=5)
                if not ic_values:
                    raise ValueError("训练窗口没有足够的有效截面 IC")
                mean_ic = sum(ic_values) / len(ic_values)
                std_ic = statistics.stdev(ic_values) if len(ic_values) > 1 else 0.0
                vector = _training_vector(result.values, eligible_rows, panel)
                fold_means = []
                for fold in range(3) if fitness_mode != "legacy" else ():
                    fold_rows = eligible_rows[len(eligible_rows)*fold//3:len(eligible_rows)*(fold+1)//3]
                    fold_rows = fold_rows[:-5]
                    fold_ic = _rank_ic_series(panel, result.values, fold_rows, horizon=5)
                    fold_means.append(sum(fold_ic)/len(fold_ic) if fold_ic else None)
                positive = sum(value is not None and value >= cv_min_abs_ic for value in fold_means)
                negative = sum(value is not None and value <= -cv_min_abs_ic for value in fold_means)
                scored.append({
                    "expression": expression,
                    "mean_rank_ic": mean_ic,
                    "icir": mean_ic / std_ic * math.sqrt(252) if std_ic > 0 else None,
                    "fitness_raw": mean_ic,
                    "nodes": _node_count(node),
                    "depth": _node_depth(node),
                    "dependencies": list(compiled.dependencies),
                    "min_history": compiled.min_history,
                    "valid": True,
                    "portfolio_daily_sharpe": portfolio_sr,
                    "canonical_hash": canonical_expression_hash(expression),
                    "cv_fold_mean_ic": fold_means,
                    "cv_passed": max(positive, negative) >= 2,
                })
                value_vectors.append(vector)
            except (ValueError, ArithmeticError, IndexError) as error:
                scored.append({
                    "expression": expression,
                    "mean_rank_ic": None,
                    "icir": None,
                    "fitness_raw": -1.0,
                    "nodes": _node_count(node),
                    "depth": _node_depth(node),
                    "dependencies": [],
                    "min_history": None,
                    "valid": False,
                    "error": str(error)[:180],
                })
                value_vectors.append(None)

        for index, item in enumerate(scored):
            if not item["valid"]:
                item["fitness"] = -1.0
                item["max_abs_correlation"] = None
                item["correlation_penalty"] = 0.0
                item["complexity_penalty"] = 0.0
                continue
            candidate_vector: Sequence[float | None] | None = value_vectors[index]
            correlations = [
                abs(correlation)
                for other in existing_factor_panels
                if (correlation := _vector_correlation(candidate_vector, _training_vector(other, eligible_rows, panel), len(panel.symbols))) is not None
            ]
            registry_correlation = max(correlations, default=0.0)
            correlations.extend(
                abs(correlation)
                for peer_index, peer in enumerate(value_vectors)
                if peer_index != index and peer is not None
                if (correlation := _vector_correlation(candidate_vector, peer, len(panel.symbols))) is not None
            )
            maximum_correlation = max(correlations, default=0.0)
            correlation_penalty = max(0.0, maximum_correlation - 0.7) * 0.25 if fitness_mode == "legacy" else correlation_lambda * registry_correlation
            complexity_penalty = (0.0005 * _numeric(item["nodes"]) + 0.002 * _numeric(item["depth"])) if fitness_mode == "legacy" else complexity_mu * (_numeric(item["nodes"]) + 4*_numeric(item["depth"]))
            item["max_abs_correlation"] = maximum_correlation
            item["correlation_penalty"] = correlation_penalty
            item["complexity_penalty"] = complexity_penalty
            item["max_abs_registry_correlation"] = registry_correlation
            item["fitness"] = _numeric(item["fitness_raw"] if fitness_mode == "legacy" else item["icir"]) - correlation_penalty - complexity_penalty
            expression = str(item["expression"])
            if fitness_mode != "legacy" and (registry_correlation > .7 or item["icir"] is None or (temporal_cv and not item["cv_passed"])):
                item["candidate_excluded"] = True
                continue
            canonical_key = str(item.get("canonical_hash") or expression)
            current = all_candidates.get(canonical_key)
            if current is None or _numeric(item["fitness"]) > _numeric(current["fitness"]):
                all_candidates[canonical_key] = dict(item)

        ranked = sorted(scored, key=lambda item: (_numeric(item.get("fitness")), str(item["expression"])), reverse=True)
        generation_log.append({
            "generation": generation + 1,
            "population": [
                {
                    "expression": item["expression"],
                    "valid": item["valid"],
                    "fitness": item.get("fitness"),
                    "mean_rank_ic": item.get("mean_rank_ic"),
                    "max_abs_correlation": item.get("max_abs_correlation"),
                    "nodes": item["nodes"],
                    "depth": item["depth"],
                    "error": item.get("error"),
                    "portfolio_daily_sharpe": item.get("portfolio_daily_sharpe"),
                }
                for item in scored
            ],
            "top": [
                {key: item.get(key) for key in ("expression", "fitness", "mean_rank_ic", "max_abs_correlation", "nodes", "depth")}
                for item in ranked[: min(5, top_n)]
            ],
            "valid_count": sum(bool(item["valid"]) for item in scored),
            "n_tested_cumulative": n_tested,
        })
        elites = [
            _parse_generated_expression(
                str(item["expression"]),
                registered_pit_fields=panel.point_in_time_fields,
                pit_quality=panel.pit_quality,
            )
            for item in ranked[: max(2, population_size // 5)]
            if item["valid"] and not item.get("candidate_excluded")
        ]
        if not elites:
            elites = [_random_tree(rng, 2, terminals, cross_functions, operator_subset) for _ in range(max(2, population_size // 5))]
        next_population = list(elites[:population_size])
        while len(next_population) < population_size:
            parent = rng.choice(elites)
            child = _mutate(parent, rng, terminals, cross_functions, operator_subset) if rng.random() < 0.75 else _crossover(parent, rng.choice(elites), rng)
            next_population.append(child)
        population = next_population

    candidates = sorted(
        all_candidates.values(),
        key=lambda item: (_numeric(item.get("fitness")), str(item["expression"])),
        reverse=True,
    )[:top_n]
    return FactorMiningResult(
        seed=int(seed),
        generations=generations,
        population_size=population_size,
        n_tested=n_tested,
        training_start=min(bar.date for bar in ordered).isoformat(),
        training_end=train_end.isoformat(),
        data_version=str(data_version),
        candidates=tuple(candidates),
        generation_log=tuple(generation_log),
        protocol={
            "fitness": "training_mean_rank_ic_only" if fitness_mode == "legacy" else "training_icir_minus_registry_correlation_and_complexity",
            "fitness_mode": fitness_mode,
            "temporal_cv": temporal_cv and fitness_mode != "legacy",
            "cv_folds": 3,
            "cv_min_abs_ic": cv_min_abs_ic,
            "forward_horizon": 5,
            "sealed_dates_used": False,
            "pit_fields": list(panel.point_in_time_fields),
            "pit_quality": dict(panel.pit_quality),
            "industry_neutralization_enabled": panel.pit_quality.get("industry") == "verified",
            "future_operators": False,
            "correlation_threshold": 0.7,
            "correlation_penalty": .25 if fitness_mode == "legacy" else correlation_lambda,
            "complexity_penalty": "0.0005*nodes + 0.002*depth" if fitness_mode == "legacy" else f"{complexity_mu}*(nodes + 4*depth)",
            "field_subset": list(terminals),
            "operator_subset": list(operator_subset) if operator_subset is not None else None,
            "n_tested_definition": "all generated GP individuals, including invalid and duplicate expressions",
        },
    )


def canonical_expression_hash(expression: str) -> str:
    """AST identity ignores whitespace and harmless commutative ordering."""
    tree = ast.parse(expression.strip(), mode="eval")
    class Canonical(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.AST:
            return ast.Name(id=node.id.lower(), ctx=ast.Load())
        def visit_Constant(self, node: ast.Constant) -> ast.AST:
            if isinstance(node.value, (float, int)) and not isinstance(node.value, bool):
                return ast.Constant(value=float(node.value))
            return node
        def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
            self.generic_visit(node)
            if isinstance(node.op, (ast.Add, ast.Mult)) and ast.dump(node.left) > ast.dump(node.right):
                node.left, node.right = node.right, node.left
            return node
    return hashlib.sha256(ast.dump(Canonical().visit(tree), include_attributes=False).encode()).hexdigest()


def _eligible_training_rows(panel: FactorPanel, train_end: date, *, horizon: int) -> list[int]:
    return [
        index
        for index, day in enumerate(panel.dates)
        if day <= train_end and index + horizon < len(panel.dates) and panel.dates[index + horizon] <= train_end
    ]


def _rank_ic_series(panel: FactorPanel, values: Panel, rows: Sequence[int], *, horizon: int) -> list[float]:
    result: list[float] = []
    for index in rows:
        factor_by_symbol: dict[str, float] = {}
        returns_by_symbol: dict[str, float] = {}
        for column, symbol in enumerate(panel.symbols):
            start, end = panel.fields["close"][index][column], panel.fields["close"][index + horizon][column]
            factor = values[index][column]
            if start is None or end is None or factor is None or start <= 0 or not math.isfinite(factor):
                continue
            factor_by_symbol[symbol] = factor
            returns_by_symbol[symbol] = end / start - 1.0
        ic = _spearman(factor_by_symbol, returns_by_symbol)
        if ic is not None:
            result.append(ic)
    return result


def _training_vector(values: Panel, rows: Sequence[int], panel: FactorPanel) -> list[float | None]:
    return [
        values[index][column]
        for index in rows
        for column in range(len(panel.symbols))
    ]


def _spearman(left: dict[str, float], right: dict[str, float]) -> float | None:
    symbols = [item for item in left if item in right]
    if len(symbols) < 3:
        return None
    return _correlation(_rank([left[item] for item in symbols]), _rank([right[item] for item in symbols]))


def _rank(values: list[float]) -> list[float]:
    order = sorted(enumerate(values), key=lambda pair: pair[1])
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and order[end][1] == order[cursor][1]:
            end += 1
        rank = ((cursor + end - 1) / 2.0) / max(len(order) - 1, 1)
        for offset in range(cursor, end):
            result[order[offset][0]] = rank
        cursor = end
    return result


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    lm, rm = sum(left) / len(left), sum(right) / len(right)
    numerator = sum((a - lm) * (b - rm) for a, b in zip(left, right))
    denominator = math.sqrt(sum((a - lm) ** 2 for a in left) * sum((b - rm) ** 2 for b in right))
    return numerator / denominator if denominator else None


def _vector_correlation(
    left: Sequence[float | None] | None,
    right: Sequence[float | None] | None,
    cross_section_width: int,
) -> float | None:
    if left is None or right is None or len(left) != len(right):
        return None
    correlations: list[float] = []
    width = max(1, int(cross_section_width))
    for start in range(0, len(left), width):
        pairs = [
            (float(a), float(b)) for a, b in zip(left[start:start + width], right[start:start + width])
            if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)
        ]
        if len(pairs) < 3:
            continue
        if len(pairs) > 2048:
            stride = len(pairs) / 2048
            pairs = [pairs[int(index * stride)] for index in range(2048)]
        correlation = _correlation([pair[0] for pair in pairs], [pair[1] for pair in pairs])
        if correlation is not None:
            correlations.append(correlation)
    return max(correlations, key=abs) if correlations else None


def _random_tree(
    rng: random.Random,
    depth: int,
    terminals: Sequence[str] = _TERMINALS,
    cross_functions: Sequence[str] = ("cs_rank", "cs_zscore", "cs_scale"),
    operator_subset: Sequence[str] | None = None,
) -> _Node:
    if depth <= 0 or rng.random() < 0.22:
        return _terminal(rng, terminals)
    if operator_subset is not None:
        name = rng.choice(tuple(operator_subset))
        child = _random_tree(rng, depth - 1, terminals, cross_functions, operator_subset)
        if name in _BINARY:
            return _Node(name, (child, _random_tree(rng, depth - 1, terminals, cross_functions, operator_subset)))
        if name in _UNARY[3:]:
            return _Node(name, (child, _Node("const", (rng.choice(_WINDOWS),))))
        return _Node(name, (child,))
    choice = rng.random()
    if choice < 0.24:
        name = rng.choice(_UNARY[:3])
        return _Node(name, (_random_tree(rng, depth - 1, terminals, cross_functions),))
    if choice < 0.74:
        name = rng.choice(_UNARY[3:])
        return _Node(name, (_random_tree(rng, depth - 1, terminals, cross_functions), _Node("const", (rng.choice(_WINDOWS),))))
    if choice < 0.91:
        name = rng.choice(_BINARY)
        return _Node(name, (_random_tree(rng, depth - 1, terminals, cross_functions), _random_tree(rng, depth - 1, terminals, cross_functions)))
    return _Node(rng.choice(tuple(cross_functions)), (_random_tree(rng, depth - 1, terminals, cross_functions),))


def _terminal(rng: random.Random, terminals: Sequence[str] = _TERMINALS) -> _Node:
    if rng.random() < 0.1:
        return _Node("const", (rng.choice((-1.0, 0.0, 1.0)),))
    return _Node(rng.choice(tuple(terminals)))


def _render(node: _Node) -> str:
    if node.name == "const":
        value = _constant_arg(node, 0)
        return str(int(value)) if value.is_integer() else str(value)
    if node.name in _BINARY:
        return f"({_render(_node_arg(node, 0))} {node.name} {_render(_node_arg(node, 1))})"
    if node.name in _UNARY[3:]:
        window_node = _node_arg(node, 1)
        return f"{node.name}({_render(_node_arg(node, 0))}, {int(_constant_arg(window_node, 0))})"
    return f"{node.name}({', '.join(_render(_node_arg(node, index)) for index in range(len(node.args)))})" if node.args else node.name


def _parse_generated_expression(
    expression: str,
    *,
    registered_pit_fields: Sequence[str] = (),
    pit_quality: Mapping[str, str] | None = None,
) -> _Node:
    from ..factors.expression import compile_expression
    compiled = compile_expression(
        expression,
        registered_pit_fields=registered_pit_fields,
        pit_quality=pit_quality,
    )

    def convert(ast_node: ast.AST) -> _Node:
        if isinstance(ast_node, ast.Name):
            return _Node(ast_node.id.lower())
        if isinstance(ast_node, ast.Constant):
            if isinstance(ast_node.value, bool) or not isinstance(ast_node.value, (int, float)):
                raise InputTypeError("生成表达式常数必须是数值")
            return _Node("const", (float(ast_node.value),))
        if isinstance(ast_node, ast.BinOp):
            name = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}[type(ast_node.op)]
            return _Node(name, (convert(ast_node.left), convert(ast_node.right)))
        if isinstance(ast_node, ast.Call):
            if not isinstance(ast_node.func, ast.Name):
                raise InputTypeError("生成表达式仅允许具名函数")
            return _Node(ast_node.func.id.lower(), tuple(convert(item) for item in ast_node.args))
        if isinstance(ast_node, ast.UnaryOp) and isinstance(ast_node.op, ast.USub):
            operand = convert(ast_node.operand)
            if operand.name == "const":
                return _Node("const", (-_constant_arg(operand, 0),))
            return _Node("*", (_Node("const", (-1.0,)), operand))
        if isinstance(ast_node, ast.UnaryOp) and isinstance(ast_node.op, ast.UAdd):
            return convert(ast_node.operand)
        raise ValueError(f"GP 转换不支持表达式节点 {type(ast_node).__name__}")

    return convert(compiled.tree.body)


def _mutate(
    node: _Node,
    rng: random.Random,
    terminals: Sequence[str] = _TERMINALS,
    cross_functions: Sequence[str] = ("cs_rank", "cs_zscore", "cs_scale"),
    operator_subset: Sequence[str] | None = None,
) -> _Node:
    path = _random_path(node, rng)
    return _replace(node, path, _random_tree(rng, 2, terminals, cross_functions, operator_subset))


def _crossover(left: _Node, right: _Node, rng: random.Random) -> _Node:
    left_path = _random_path(left, rng)
    right_path = _random_path(right, rng)
    return _replace(left, left_path, _get(right, right_path))


def _random_path(node: _Node, rng: random.Random) -> tuple[int, ...]:
    paths: list[tuple[int, ...]] = [()]

    def collect_paths(current: _Node, prefix: tuple[int, ...]) -> None:
        for index, child in enumerate(current.args):
            if current.name in _UNARY[3:] and index == 1:
                # A rolling window is a constant, never a replaceable expression subtree.
                continue
            if isinstance(child, _Node):
                path = (*prefix, index)
                paths.append(path)
                collect_paths(child, path)

    collect_paths(node, ())
    return rng.choice(paths)


def _get(node: _Node, path: tuple[int, ...]) -> _Node:
    if not path:
        return node
    child = node.args[path[0]]
    if not isinstance(child, _Node):
        return node
    return _get(child, path[1:])


def _replace(node: _Node, path: tuple[int, ...], replacement: _Node) -> _Node:
    if not path:
        return replacement
    index, *tail = path
    args = list(node.args)
    child = args[index]
    if not isinstance(child, _Node):
        return node
    args[index] = _replace(child, tuple(tail), replacement)
    return _Node(node.name, tuple(args))


def _node_count(node: _Node) -> int:
    return 1 + sum(_node_count(child) for child in node.args if isinstance(child, _Node))


def _node_depth(node: _Node) -> int:
    children = [child for child in node.args if isinstance(child, _Node)]
    return 1 + max((_node_depth(child) for child in children), default=0)

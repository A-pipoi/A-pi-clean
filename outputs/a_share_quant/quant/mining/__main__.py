from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

from quant.errors import InputTypeError


def _load_flat_yaml(path: Path) -> dict[str, Any]:
    """Read the small scalar/list YAML config used by this dependency-free CLI."""

    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        result: dict[str, Any] = {}
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" not in line:
                raise ValueError(f"配置第 {line_number} 行必须是 key: value")
            key, raw_value = line.split(":", 1)
            key, raw_value = key.strip(), raw_value.strip()
            if not key:
                raise ValueError(f"配置第 {line_number} 行缺少键")
            if not raw_value:
                result[key] = None
            elif raw_value.startswith(("[", "{", "'", '"')):
                try:
                    result[key] = ast.literal_eval(raw_value)
                except (SyntaxError, ValueError) as error:
                    raise ValueError(f"配置第 {line_number} 行不是受支持的 YAML 标量/列表") from error
            elif raw_value.lower() in {"true", "false"}:
                result[key] = raw_value.lower() == "true"
            else:
                try:
                    result[key] = int(raw_value)
                except ValueError:
                    try:
                        result[key] = float(raw_value)
                    except ValueError:
                        result[key] = raw_value.strip("'\"")
        return result
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise InputTypeError("配置文件顶层必须是键值对象")
    return loaded


def main() -> int:
    parser = argparse.ArgumentParser(description="A-pi point-in-time factor mining")
    parser.add_argument("task", choices=("mine", "evaluate", "accept"))
    parser.add_argument("--config", required=True, type=Path, help="flat YAML configuration file")
    args = parser.parse_args()
    if not args.config.is_file():
        parser.error(f"config does not exist: {args.config}")
    config = _load_flat_yaml(args.config)

    # API and CLI deliberately share the same ledger-backed entry points,
    # sealed snapshot scope, and one-time acceptance claim.
    from .. import api_server

    if args.task == "mine":
        payload = api_server._run_factor_mining_request(config)
    elif args.task == "evaluate":
        payload = api_server._evaluate_factor_expression_request(config)
    else:
        run_id = str(config.get("run_id") or "").strip()
        if not run_id:
            parser.error("accept task requires run_id in the config")
        payload = api_server._accept_factor_mining_run(run_id)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

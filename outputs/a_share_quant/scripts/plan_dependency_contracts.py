"""Plan exact callback protocols from the functions used by the composition root."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'quant/api_server.py'


def module_path(module: str) -> Path:
    path = ROOT / module.replace('.', '/')
    return path.with_suffix('.py') if path.with_suffix('.py').exists() else path / '__init__.py'


def absolute_import(node: ast.ImportFrom, module: str) -> str:
    if not node.level:
        return node.module or ''
    parts = module.split('.')[:-1]
    if module_path(module).name == '__init__.py':
        parts = module.split('.')
    parts = parts[:len(parts) - node.level + 1]
    return '.'.join(parts + ([node.module] if node.module else []))


def resolve(module: str, name: str, seen=None):
    seen = set() if seen is None else seen
    if (module, name) in seen:
        return None
    seen.add((module, name))
    path = module_path(module)
    if not path.exists():
        return None
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return module, node, tree
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    return resolve(absolute_import(node, module), alias.name, seen)
    return None


server_tree = ast.parse(SERVER.read_text(encoding='utf-8-sig'))
bindings = {}
for factory in server_tree.body:
    if not isinstance(factory, ast.FunctionDef):
        continue
    for node in ast.walk(factory):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.endswith('Dependencies'):
            for keyword in node.keywords:
                if keyword.arg and isinstance(keyword.value, ast.Name):
                    bindings[(node.func.id, keyword.arg)] = keyword.value.id

protocols = {}
imports = set()
patches = []
unresolved = []
for path in sorted((ROOT / 'quant/api').glob('*.py')):
    source = path.read_text(encoding='utf-8-sig')
    tree = ast.parse(source)
    replacements = []
    names = set()
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef) or not cls.name.endswith('Dependencies'):
            continue
        for field in cls.body:
            if not isinstance(field, ast.AnnAssign) or not isinstance(field.target, ast.Name):
                continue
            if ast.unparse(field.annotation) != 'Callable[..., Any]':
                continue
            bound = bindings.get((cls.name, field.target.id), field.target.id)
            resolved = resolve('quant.api_server', bound)
            if resolved is None:
                unresolved.append((cls.name, field.target.id, bound))
                continue
            module, original, original_tree = resolved
            protocol_name = ''.join(word.title() for word in bound.strip('_').split('_')) + 'Callback'
            if isinstance(original, ast.ClassDef):
                annotation = f'type[{bound}]'
                imports.add(f'from {module} import {original.name}' + (f' as {bound}' if bound != original.name else ''))
                names.add(bound)
            else:
                signature = copy.deepcopy(original)
                signature.name = '__call__'
                signature.args.args.insert(0, ast.arg(arg='self'))
                signature.decorator_list = []
                signature.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
                signature.args.defaults = [ast.Constant(value=Ellipsis) for _ in signature.args.defaults]
                signature.args.kw_defaults = [ast.Constant(value=Ellipsis) if value is not None else None for value in signature.args.kw_defaults]
                signature.returns = signature.returns or ast.Name(id='object')
                ast.fix_missing_locations(signature)
                annotation_names = set()
                for argument in [*signature.args.posonlyargs, *signature.args.args, *signature.args.kwonlyargs]:
                    if argument.annotation:
                        annotation_names.update(node.id for node in ast.walk(argument.annotation) if isinstance(node, ast.Name))
                annotation_names.update(node.id for node in ast.walk(signature.returns) if isinstance(node, ast.Name))
                for import_node in original_tree.body:
                    if not isinstance(import_node, (ast.Import, ast.ImportFrom)):
                        continue
                    for alias in import_node.names:
                        if (alias.asname or alias.name.split('.')[0]) not in annotation_names:
                            continue
                        copied = copy.deepcopy(import_node)
                        copied.names = [copy.deepcopy(alias)]
                        if isinstance(copied, ast.ImportFrom):
                            copied.module = absolute_import(import_node, module)
                            copied.level = 0
                        imports.add(ast.unparse(copied))
                protocols[protocol_name] = f'class {protocol_name}(Protocol):\n' + '\n'.join('    ' + line for line in ast.unparse(signature).splitlines())
                annotation = protocol_name
                names.add(protocol_name)
            old = source.splitlines()[field.lineno - 1]
            replacements.append((old, old.replace('Callable[..., Any]', annotation)))
    if replacements:
        protocol_names = names.intersection(protocols)
        extra = 'from quant.api.contracts import ' + ', '.join(sorted(protocol_names)) + '\n' if protocol_names else ''
        # Class factories use their existing domain imports; all broad class fields already import that class.
        lines = [f'*** Update File: {path.as_posix()}']
        if extra:
            lines += ['@@', ' from __future__ import annotations', '+' + extra.rstrip()]
        for old, new in replacements:
            lines += ['@@', '-' + old, '+' + new]
        patches.extend(lines)

contract_source = '"""Typed callback contracts shared by API services and their composition root."""\nfrom __future__ import annotations\n\nfrom typing import TYPE_CHECKING, Protocol\n\nif TYPE_CHECKING:\n' + '\n'.join('    ' + item for item in sorted(imports)) + '\n\n\n' + '\n\n\n'.join(protocols[name] for name in sorted(protocols)) + '\n'
patch = '*** Begin Patch\n*** Add File: ' + (ROOT / 'quant/api/contracts.py').as_posix() + '\n' + '\n'.join('+' + line for line in contract_source.splitlines()) + '\n' + '\n'.join(patches) + '\n*** End Patch'
print(json.dumps({'patch': patch, 'protocols': len(protocols), 'unresolved': unresolved}, ensure_ascii=True))

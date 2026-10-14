"""Static Python source index. Scanned code is parsed but never imported."""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import AnalysisError, Evidence

EXCLUDED = {".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build", ".mypy_cache", ".pytest_cache"}


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    kind: str
    default: Any = None
    has_default: bool = False


@dataclass(frozen=True, slots=True)
class FunctionInfo:
    name: str
    path: str
    line: int
    parameters: tuple[Parameter, ...]
    signature: str


@dataclass(frozen=True, slots=True)
class FlagInfo:
    flag: str
    path: str
    line: int
    help: str | None = None
    dynamic: bool = False


@dataclass(slots=True)
class CodeIndex:
    root: Path
    files: set[str] = field(default_factory=set)
    functions: dict[str, list[FunctionInfo]] = field(default_factory=dict)
    flags: dict[str, list[FlagInfo]] = field(default_factory=dict)
    environment: dict[str, list[Evidence]] = field(default_factory=dict)
    errors: list[AnalysisError] = field(default_factory=list)

    def evidence(self, path: str, line: int) -> Evidence | None:
        try:
            lines = (self.root / path).read_text(encoding="utf-8").splitlines()
            if 1 <= line <= len(lines):
                return Evidence(path, line, lines[line - 1].strip())
        except (OSError, UnicodeError):
            return None
        return None


def _literal(node: ast.expr) -> tuple[bool, Any]:
    try:
        return True, ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return False, None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[Parameter, ...]:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults: dict[str, tuple[bool, Any]] = {}
    for arg, default in zip(positional[-len(args.defaults):] if args.defaults else [], args.defaults):
        defaults[arg.arg] = _literal(default)
    result: list[Parameter] = []
    for arg in args.posonlyargs:
        has, val = defaults.get(arg.arg, (False, None)); result.append(Parameter(arg.arg, "positional_only", val, has))
    for arg in args.args:
        has, val = defaults.get(arg.arg, (False, None)); result.append(Parameter(arg.arg, "positional_or_keyword", val, has))
    if args.vararg:
        result.append(Parameter(args.vararg.arg, "var_positional"))
    for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults):
        has, val = (False, None) if kw_default is None else _literal(kw_default)
        result.append(Parameter(arg.arg, "keyword_only", val, has))
    if args.kwarg:
        result.append(Parameter(args.kwarg.arg, "var_keyword"))
    return tuple(result)


class _Visitor(ast.NodeVisitor):
    def __init__(self, rel: str, index: CodeIndex) -> None:
        self.rel, self.index = rel, index
        self.scopes: list[str] = []
        self.argparse_parsers: set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        value = node.value
        is_parser = isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "ArgumentParser" and ast.unparse(value.func.value) == "argparse"
        if is_parser:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.argparse_parsers.add(target.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = ".".join([*self.scopes, node.name])
        params = _signature(node)
        signature = f"{node.name}({', '.join(p.name for p in params)})"
        info = FunctionInfo(qualified, self.rel, node.lineno, params, signature)
        self.index.functions.setdefault(node.name, []).append(info)
        self.index.functions.setdefault(qualified, []).append(info) if qualified != node.name else None
        self.scopes.append(node.name)
        for child in node.body:
            self.visit(child)
        self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        receiver = ast.unparse(node.func.value) if isinstance(node.func, ast.Attribute) else ""
        if name == "add_argument" and receiver in self.argparse_parsers:
            literals = [value for arg in node.args if (ok := _literal(arg))[0] for value in [ok[1]]]
            options = [value for value in literals if isinstance(value, str) and value.startswith("-")]
            help_text = None
            dynamic = False
            for kw in node.keywords:
                if kw.arg == "help":
                    is_literal, value = _literal(kw.value)
                    if is_literal and isinstance(value, str): help_text = value
                    else: dynamic = True
            for option in options:
                self.index.flags.setdefault(option, []).append(FlagInfo(option, self.rel, node.lineno, help_text, dynamic))
        if name in {"getenv", "get"} and isinstance(node.func, ast.Attribute):
            owner = ast.unparse(node.func.value)
            if owner in {"os", "os.environ"} and node.args:
                is_literal, value = _literal(node.args[0])
                if is_literal and isinstance(value, str):
                    self.index.environment.setdefault(value, []).append(Evidence(self.rel, node.lineno, ""))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Attribute) and ast.unparse(node.value) == "os.environ":
            is_literal, value = _literal(node.slice)
            if is_literal and isinstance(value, str):
                self.index.environment.setdefault(value, []).append(Evidence(self.rel, node.lineno, ""))
        self.generic_visit(node)


def build_index(root: Path, source_dirs: tuple[str, ...] = (".",)) -> CodeIndex:
    root = root.resolve()
    index = CodeIndex(root=root)
    for configured in source_dirs:
        base = (root / configured).resolve()
        if not base.is_relative_to(root) or not base.exists():
            index.errors.append(AnalysisError(configured, "source scope is missing or escapes repository", kind="configuration_error"))
            continue
        candidates = [base] if base.is_file() else [p for p in base.rglob("*.py") if not any(part in EXCLUDED for part in p.parts)]
        for path in sorted(candidates):
            if path.suffix != ".py":
                continue
            rel = path.relative_to(root).as_posix()
            index.files.add(rel)
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=rel)
                _Visitor(rel, index).visit(tree)
                # Fill evidence snippets after parsing; names and values are never included.
                for name, occurrences in list(index.environment.items()):
                    index.environment[name] = [Evidence(e.path, e.line, source.splitlines()[e.line - 1].strip()) if e.path == rel and e.line <= len(source.splitlines()) else e for e in occurrences]
            except SyntaxError as exc:
                index.errors.append(AnalysisError(rel, exc.msg, exc.lineno, exc.offset, "syntax_error"))
            except (OSError, UnicodeError) as exc:
                index.errors.append(AnalysisError(rel, str(exc), kind="read_error"))
    return index

"""Static Python source index. Scanned code is parsed but never imported."""

from __future__ import annotations

import ast
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
    default_resolved: bool = False


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


@dataclass(frozen=True, slots=True)
class RouteInfo:
    method: str
    path: str
    source_path: str
    line: int
    dynamic: bool = False


@dataclass(slots=True)
class CodeIndex:
    root: Path
    files: set[str] = field(default_factory=set)
    functions: dict[str, list[FunctionInfo]] = field(default_factory=dict)
    flags: dict[str, list[FlagInfo]] = field(default_factory=dict)
    routes: dict[str, list[RouteInfo]] = field(default_factory=dict)
    routes_complete: bool = True
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
    defaults: dict[str, tuple[bool, bool, Any]] = {}
    for arg, default in zip(positional[-len(args.defaults):] if args.defaults else [], args.defaults):
        resolved, value = _literal(default)
        defaults[arg.arg] = (True, resolved, value)
    result: list[Parameter] = []
    for arg in args.posonlyargs:
        has, resolved, val = defaults.get(arg.arg, (False, False, None)); result.append(Parameter(arg.arg, "positional_only", val, has, resolved))
    for arg in args.args:
        has, resolved, val = defaults.get(arg.arg, (False, False, None)); result.append(Parameter(arg.arg, "positional_or_keyword", val, has, resolved))
    if args.vararg:
        result.append(Parameter(args.vararg.arg, "var_positional"))
    for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults):
        has = kw_default is not None
        resolved, val = _literal(kw_default) if has and kw_default is not None else (False, None)
        result.append(Parameter(arg.arg, "keyword_only", val, has, resolved))
    if args.kwarg:
        result.append(Parameter(args.kwarg.arg, "var_keyword"))
    return tuple(result)


class _Visitor(ast.NodeVisitor):
    def __init__(self, rel: str, index: CodeIndex) -> None:
        self.rel, self.index = rel, index
        self.scopes: list[str] = []
        self.argparse_parsers: set[str] = set()
        self.typer_apps: set[str] = set()
        self.click_groups: set[str] = set()
        self.python_apps: dict[str, str] = {}
        self.route_prefixes: dict[str, str | None] = {}

    def visit_Assign(self, node: ast.Assign) -> None:
        value = node.value
        is_parser = isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "ArgumentParser" and ast.unparse(value.func.value) == "argparse"
        is_typer = isinstance(value, ast.Call) and ast.unparse(value.func) == "typer.Typer"
        is_click_group = isinstance(value, ast.Call) and ast.unparse(value.func) in {"click.Group", "click.group"}
        constructor = value.func.id if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) else ""
        is_python_app = constructor in {"FastAPI", "Flask"}
        is_prefixable = constructor in {"APIRouter", "Blueprint"}
        if is_parser:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.argparse_parsers.add(target.id)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if is_typer:
                    self.typer_apps.add(target.id)
                if is_click_group:
                    self.click_groups.add(target.id)
                if is_python_app:
                    self.python_apps[target.id] = constructor
                if is_prefixable and isinstance(value, ast.Call):
                    prefix_kw = next((kw.value for kw in value.keywords if kw.arg in {"prefix", "url_prefix"}), None)
                    if prefix_kw is None:
                        self.route_prefixes[target.id] = ""
                    else:
                        prefix_ok, prefix = _literal(prefix_kw)
                        self.route_prefixes[target.id] = prefix if prefix_ok and isinstance(prefix, str) and (not prefix or prefix.startswith("/")) else None
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
        decorator_names = [ast.unparse(d.func) if isinstance(d, ast.Call) else ast.unparse(d) for d in node.decorator_list]
        click_command = any(
            name in {"click.command", "click.group"}
            or (name.endswith(".command") and name.split(".")[0] in self.click_groups)
            for name in decorator_names
        )
        typer_command = any(name.endswith(".command") and name.split(".")[0] in self.typer_apps for name in decorator_names)
        if click_command:
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if ast.unparse(decorator.func.value) != "click" or decorator.func.attr != "option":
                    continue
                self._index_flag_call(decorator)
        if typer_command:
            positional = [*node.args.posonlyargs, *node.args.args]
            positional_defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
            defaults = [*positional_defaults, *node.args.kw_defaults]
            for default in defaults:
                if not isinstance(default, ast.Call) or ast.unparse(default.func) != "typer.Option":
                    continue
                options: list[str] = []
                for value in default.args[1:]:
                    ok, literal = _literal(value)
                    if ok and isinstance(literal, str) and literal.startswith("-"):
                        options.append(literal)
                for kw in default.keywords:
                    if kw.arg in {"param_decls", "declaration"}:
                        ok, literal = _literal(kw.value)
                        if ok and isinstance(literal, str) and literal.startswith("-"):
                            options.append(literal)
                        elif ok and isinstance(literal, (tuple, list)):
                            options.extend(item for item in literal if isinstance(item, str) and item.startswith("-"))
                for option in options:
                    self.index.flags.setdefault(option, []).append(FlagInfo(option, self.rel, default.lineno))
        self.scopes.append(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for child in node.body:
            self.visit(child)
        self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        receiver = ast.unparse(node.func.value) if isinstance(node.func, ast.Attribute) else ""
        if name == "add_argument" and receiver in self.argparse_parsers:
            self._index_flag_call(node)
        if name == "include_router" and receiver in self.python_apps:
            self.index.routes_complete = False
        if name == "register_blueprint" and receiver in self.python_apps:
            self.index.routes_complete = False
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"get", "post", "put", "patch", "delete", "options", "head", "route", "api_route"}:
            if receiver in self.python_apps or receiver in self.route_prefixes:
                self._index_route(node, self.python_apps.get(receiver), self.route_prefixes.get(receiver))
        if name in {"getenv", "get"} and isinstance(node.func, ast.Attribute):
            owner = ast.unparse(node.func.value)
            if owner in {"os", "os.environ"} and node.args:
                is_literal, value = _literal(node.args[0])
                if is_literal and isinstance(value, str):
                    self.index.environment.setdefault(value, []).append(Evidence(self.rel, node.lineno, ""))
        self.generic_visit(node)

    def _index_flag_call(self, node: ast.Call) -> None:
        options: list[str] = []
        for arg in node.args:
            ok, value = _literal(arg)
            if ok and isinstance(value, str) and value.startswith("-"):
                options.append(value)
        for kw in node.keywords:
            if kw.arg in {"param_decls", "declaration"}:
                ok, value = _literal(kw.value)
                if ok and isinstance(value, str) and value.startswith("-"):
                    options.append(value)
                elif ok and isinstance(value, (tuple, list)):
                    options.extend(item for item in value if isinstance(item, str) and item.startswith("-"))
        for option in options:
            self.index.flags.setdefault(option, []).append(FlagInfo(option, self.rel, node.lineno))

    def _index_route(self, node: ast.Call, framework: str | None, prefix: str | None) -> None:
        method_name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        supported = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
        path_node = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg in {"path", "rule"}), None)
        ok, path = _literal(path_node) if path_node is not None else (False, None)
        methods: list[str] = []
        # A router or blueprint declaration is not an externally reachable endpoint
        # until its mount is resolved; do not treat its local path as a full URL.
        dynamic = framework is None or not ok or not isinstance(path, str) or not path.startswith("/")
        if prefix is not None and isinstance(path, str) and path.startswith("/"):
            path = f"{prefix.rstrip('/')}/{path.lstrip('/')}" if prefix else path
        if method_name in {"route", "api_route"}:
            method_kw = next((kw.value for kw in node.keywords if kw.arg == "methods"), None)
            if method_kw is None:
                methods = ["GET"]
                if framework == "Flask":
                    methods.extend(["HEAD", "OPTIONS"])
            else:
                methods_ok, method_values = _literal(method_kw)
                if methods_ok and isinstance(method_values, (tuple, list, set)) and all(isinstance(x, str) for x in method_values):
                    methods = [x.upper() for x in method_values]
                    if framework == "Flask" and "GET" in methods:
                        methods.extend(method for method in ("HEAD", "OPTIONS") if method not in methods)
                else:
                    dynamic = True
        elif method_name.upper() in supported:
            methods = [method_name.upper()]
        if not methods or any(method not in supported for method in methods):
            dynamic = True
        if dynamic:
            self.index.routes_complete = False
        if ok and isinstance(path, str) and path.startswith("/"):
            for method in methods:
                if method in supported:
                    route = RouteInfo(method, path, self.rel, node.lineno, dynamic)
                    self.index.routes.setdefault(path, []).append(route)

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

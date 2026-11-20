"""Static Python source index. Scanned code is parsed but never imported."""

from __future__ import annotations

import ast
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import AnalysisError, Evidence

EXCLUDED = {"venv", ".venv", "__pycache__", "node_modules", "site-packages", "dist", "build"}


def _excluded_directory(name: str) -> bool:
    return name.startswith(".") or name in EXCLUDED


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
class EnvironmentDefaultInfo:
    name: str
    path: str
    line: int
    has_default: bool
    default_resolved: bool
    default: Any = None


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
    environment_defaults: dict[str, list[EnvironmentDefaultInfo]] = field(default_factory=dict)
    scanned_paths: set[str] = field(default_factory=set)
    source_scopes: tuple[str, ...] = ()
    source_text_files: dict[str, str] = field(default_factory=dict)
    environment_text_files: dict[str, str] = field(default_factory=dict)
    source_text_complete: bool = True
    environment_text_complete: bool = True
    cli_declarations: int = 0
    dynamic_cli_declarations: int = 0
    parser_programs: set[str] = field(default_factory=set)
    project_script_names: set[str] = field(default_factory=set)
    dynamic_environment_access: bool = False
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


def _os_attribute(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and ast.unparse(node) == "os.environ"


def _dynamic_environment_access(tree: ast.Module) -> bool:
    """Return true when common environment access cannot be statically enumerated."""
    dynamic = False
    literal_environment_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import) and any(alias.name == "os" and alias.asname for alias in node.names):
                dynamic = True
            if isinstance(node, ast.ImportFrom) and node.module == "os" and any(
                alias.name in {"environ", "getenv"} for alias in node.names
            ):
                dynamic = True
        if isinstance(node, ast.Subscript) and _os_attribute(node.value):
            if not (isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)):
                dynamic = True
            else:
                literal_environment_nodes.add(id(node.value))
        if isinstance(node, ast.Call):
            function = ast.unparse(node.func)
            if function == "os.getenv":
                key = node.args[0] if node.args else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "key"), None
                )
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    dynamic = True
            elif function == "os.environ.get":
                key = node.args[0] if node.args else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "key"), None
                )
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    dynamic = True
                if isinstance(node.func, ast.Attribute) and _os_attribute(node.func.value):
                    literal_environment_nodes.add(id(node.func.value))
            if any(keyword.arg is None and _os_attribute(keyword.value) for keyword in node.keywords):
                dynamic = True
        if isinstance(node, ast.Attribute) and _os_attribute(node) and id(node) not in literal_environment_nodes:
            dynamic = True
    return dynamic


def _is_environment_text(path: Path) -> bool:
    name = path.name.casefold()
    return (
        path.suffix.casefold() in {".py", ".toml", ".yml", ".yaml", ".json"}
        or name.startswith(".env")
        or name == "dockerfile"
    )


def _readable_source_text(path: Path) -> str | None:
    if path.suffix.casefold() in {".md", ".markdown"}:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


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
            self.index.cli_declarations += 1
            if isinstance(value, ast.Call):
                prog = next((keyword.value for keyword in value.keywords if keyword.arg == "prog"), None)
                prog_ok, prog_value = _literal(prog) if prog is not None else (False, None)
                if prog_ok and isinstance(prog_value, str) and prog_value:
                    self.index.parser_programs.add(prog_value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.argparse_parsers.add(target.id)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if is_typer:
                    self.index.cli_declarations += 1
                    self.typer_apps.add(target.id)
                if is_click_group:
                    self.index.cli_declarations += 1
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
        self._mark_unresolved_route_decorators(node.decorator_list)
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._mark_unresolved_route_decorators(node.decorator_list)
        self._function(node)

    def _mark_unresolved_route_decorators(self, decorators: list[ast.expr]) -> None:
        route_names = {"get", "post", "put", "patch", "delete", "options", "head", "route", "api_route"}
        for decorator in decorators:
            function = decorator.func if isinstance(decorator, ast.Call) else decorator
            if not isinstance(function, ast.Attribute) or function.attr.lower() not in route_names:
                continue
            receiver = ast.unparse(function.value)
            if receiver not in self.python_apps and receiver not in self.route_prefixes:
                self.index.routes_complete = False

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
        if click_command or typer_command:
            self.index.cli_declarations += 1
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
                dynamic_option = False
                for value in default.args[1:]:
                    ok, literal = _literal(value)
                    if ok and isinstance(literal, str) and literal.startswith("-"):
                        options.append(literal)
                    elif not ok:
                        dynamic_option = True
                for kw in default.keywords:
                    if kw.arg in {"param_decls", "declaration"}:
                        ok, literal = _literal(kw.value)
                        if ok and isinstance(literal, str) and literal.startswith("-"):
                            options.append(literal)
                        elif ok and isinstance(literal, (tuple, list)):
                            options.extend(item for item in literal if isinstance(item, str) and item.startswith("-"))
                        elif not ok:
                            dynamic_option = True
                for option in options:
                    self.index.flags.setdefault(option, []).append(FlagInfo(option, self.rel, default.lineno))
                if dynamic_option or not options:
                    self.index.dynamic_cli_declarations += 1
        self.scopes.append(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for child in node.body:
            self.visit(child)
        self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        receiver = ast.unparse(node.func.value) if isinstance(node.func, ast.Attribute) else ""
        if name == "add_argument":
            if receiver in self.argparse_parsers:
                self._index_flag_call(node)
            else:
                # An unknown receiver may be an alias or a parser constructed
                # dynamically. It cannot safely support an absence conclusion.
                self.index.dynamic_cli_declarations += 1
        if name == "include_router" and receiver in self.python_apps:
            self.index.routes_complete = False
        if name == "register_blueprint" and receiver in self.python_apps:
            self.index.routes_complete = False
        if name in {"add_api_route", "add_url_rule"} and receiver in self.python_apps:
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
                    has_default = len(node.args) >= 2
                    resolved, default = _literal(node.args[1]) if has_default else (False, None)
                    self.index.environment_defaults.setdefault(value, []).append(
                        EnvironmentDefaultInfo(value, self.rel, node.lineno,
                                               has_default, resolved, default)
                    )
        self.generic_visit(node)

    def _index_flag_call(self, node: ast.Call) -> None:
        options: list[str] = []
        dynamic = False
        for arg in node.args:
            ok, value = _literal(arg)
            if ok and isinstance(value, str) and value.startswith("-"):
                options.append(value)
            elif not ok:
                dynamic = True
        for kw in node.keywords:
            if kw.arg in {"param_decls", "declaration"}:
                ok, value = _literal(kw.value)
                if ok and isinstance(value, str) and value.startswith("-"):
                    options.append(value)
                elif ok and isinstance(value, (tuple, list)):
                    options.extend(item for item in value if isinstance(item, str) and item.startswith("-"))
                elif not ok:
                    dynamic = True
        if dynamic:
            self.index.dynamic_cli_declarations += 1
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
    index = CodeIndex(root=root, source_scopes=source_dirs)
    candidates_by_path: dict[str, Path] = {}
    for configured in source_dirs:
        base = (root / configured).resolve()
        if not base.is_relative_to(root) or not base.exists():
            index.errors.append(AnalysisError(configured, "source scope is missing or escapes repository", kind="configuration_error"))
            continue
        scoped_paths: list[Path] = []
        if base.is_file():
            candidates = [base] if base.suffix == ".py" else []
            scoped_paths = [base]
            index.scanned_paths.add(base.relative_to(root).as_posix())
        else:
            candidates = []
            for current, directories, files in os.walk(base):
                current_path = Path(current)
                index.scanned_paths.add(current_path.relative_to(root).as_posix())
                directories[:] = sorted(name for name in directories if not _excluded_directory(name))
                for name in files:
                    path = current_path / name
                    scoped_paths.append(path)
                    rel = path.relative_to(root).as_posix()
                    index.scanned_paths.add(rel)
                    if path.suffix == ".py":
                        candidates.append(path)
        for path in candidates:
            candidates_by_path[path.relative_to(root).as_posix()] = path
        for path in scoped_paths:
            rel = path.relative_to(root).as_posix()
            text = _readable_source_text(path)
            if text is None and path.suffix.casefold() not in {".md", ".markdown"}:
                index.source_text_complete = False
                if _is_environment_text(path):
                    index.environment_text_complete = False
            if text is not None:
                index.source_text_files[rel] = text
                if _is_environment_text(path):
                    index.environment_text_files[rel] = text
                if path.name.casefold() == "pyproject.toml":
                    try:
                        metadata = tomllib.loads(text)
                        scripts = metadata.get("project", {}).get("scripts", {})
                        gui_scripts = metadata.get("project", {}).get("gui-scripts", {})
                        if isinstance(scripts, dict):
                            index.project_script_names.update(name for name in scripts if isinstance(name, str))
                        if isinstance(gui_scripts, dict):
                            index.project_script_names.update(name for name in gui_scripts if isinstance(name, str))
                    except (tomllib.TOMLDecodeError, AttributeError):
                        pass
    for rel, path in sorted(candidates_by_path.items()):
        index.files.add(rel)
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
            if _dynamic_environment_access(tree):
                index.dynamic_environment_access = True
            _Visitor(rel, index).visit(tree)
            # Fill evidence snippets after parsing; names and values are never included.
            source_lines = source.splitlines()
            for name, occurrences in list(index.environment.items()):
                index.environment[name] = [
                    Evidence(e.path, e.line, source_lines[e.line - 1].strip())
                    if e.path == rel and e.line <= len(source_lines) else e
                    for e in occurrences
                ]
        except SyntaxError as exc:
            index.errors.append(AnalysisError(rel, exc.msg, exc.lineno, exc.offset, "syntax_error"))
        except (OSError, UnicodeError) as exc:
            index.errors.append(AnalysisError(rel, str(exc), kind="read_error"))
    return index

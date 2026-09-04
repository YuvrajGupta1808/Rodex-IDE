"""Static checks that a fix removed the pattern it was meant to remove.

``py_compile`` proves a patched file still parses; it says nothing about
whether the vulnerability is gone. These AST checks close part of that
gap by counting occurrences of the flagged pattern before and after the
fix: a fix that does not reduce the count did not do its job.

The checks are deliberately conservative. A category with no checker, or
a file that cannot be parsed, yields ``None`` ("no opinion") so the
caller falls back to the compile gate rather than rejecting a good fix.
"""

from __future__ import annotations

import ast


def _is_sql_call(node: ast.AST) -> bool:
    """A call that executes SQL, e.g. cursor.execute(...)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name in {"execute", "executemany", "executescript"}


def _count_sql_injection(tree: ast.AST) -> int:
    """SQL executed with an interpolated (f-string or concatenated) query."""
    count = 0
    for node in ast.walk(tree):
        if not _is_sql_call(node) or not node.args:
            continue
        query = node.args[0]
        if isinstance(query, ast.JoinedStr):  # f-string
            count += 1
        elif isinstance(query, ast.BinOp) and isinstance(query.op, (ast.Add, ast.Mod)):
            count += 1
        elif isinstance(query, ast.Call):
            func = query.func
            if isinstance(func, ast.Attribute) and func.attr == "format":
                count += 1
    return count


def _count_bare_except(tree: ast.AST) -> int:
    """`except:` or `except Exception: pass` swallowing errors silently."""
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body_is_pass = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
        if node.type is None or body_is_pass:
            count += 1
    return count


def _count_unsafe_deserialization(tree: ast.AST) -> int:
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {"loads", "load"}:
            value = func.value
            if isinstance(value, ast.Name) and value.id in {"pickle", "yaml", "marshal"}:
                count += 1
    return count


def _count_command_injection(tree: ast.AST) -> int:
    """os.system, or subprocess called with shell=True."""
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr == "system" and isinstance(func.value, ast.Name):
                if func.value.id == "os":
                    count += 1
                    continue
            for keyword in node.keywords:
                if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant):
                    if keyword.value.value is True:
                        count += 1
    return count


def _count_resource_leak(tree: ast.AST) -> int:
    """open() results bound to a name outside a `with` statement."""
    managed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                managed.add(id(item.context_expr))

    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"open", "connect"} and id(node.value) not in managed:
                count += 1
    return count


# Category name (as emitted by the agents) -> counter.
_CHECKERS = {
    "sql_injection": _count_sql_injection,
    "error_swallowing": _count_bare_except,
    "unsafe_deserialization": _count_unsafe_deserialization,
    "command_injection": _count_command_injection,
    "resource_leak": _count_resource_leak,
}


def pattern_occurrences(source: str, category: str) -> int | None:
    """How many times `category`'s pattern appears, or None if unknown."""
    checker = _CHECKERS.get(category)
    if checker is None:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    return checker(tree)


def pattern_removed(before: str, after: str, category: str) -> bool | None:
    """True if the fix reduced the flagged pattern's occurrences.

    Returns None when the category has no checker or either side fails to
    parse — the caller should then fall back to the compile-only gate.
    """
    before_count = pattern_occurrences(before, category)
    after_count = pattern_occurrences(after, category)
    if before_count is None or after_count is None:
        return None
    if before_count == 0:
        return None  # Nothing detectable to begin with; no opinion.
    return after_count < before_count

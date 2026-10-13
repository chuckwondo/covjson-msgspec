"""Conventions the test suite itself follows (CONTRIBUTING.md, Tests)."""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).parent


def test_no_all_or_any_result_is_asserted_or_stored() -> None:
    offenders = [
        f"{path.relative_to(TESTS_DIR)}:{node.lineno}"
        for path in sorted(TESTS_DIR.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(), str(path)))
        if isinstance(node, ast.stmt) and _asserts_or_stores_all_or_any(node)
    ]

    assert not offenders, (
        "assert a collected set or list, never an all() or any() result "
        "(CONTRIBUTING.md, Tests)"
    )


def _asserts_or_stores_all_or_any(node: ast.stmt) -> bool:
    """Whether ``node`` asserts an ``all()``/``any()`` call or stores its result.

    An assert is flagged for a call at any depth in its condition. An assignment
    is flagged only when its value is the result itself, so an offender list
    filtered with ``any()`` stays allowed.
    """
    match node:
        case ast.Assert(test=condition):
            return any(map(_is_all_or_any_call, ast.walk(condition)))
        case ast.Assign(value=value) | ast.AugAssign(value=value):
            return _is_all_or_any_result(value)
        case ast.AnnAssign(value=ast.expr() as value):
            return _is_all_or_any_result(value)
        case _:
            return False


def _is_all_or_any_result(expr: ast.expr) -> bool:
    """Whether ``expr`` is an ``all()``/``any()`` result, negated or combined."""
    match expr:
        case ast.UnaryOp(op=ast.Not(), operand=operand):
            return _is_all_or_any_result(operand)
        case ast.BoolOp(values=values):
            return any(map(_is_all_or_any_result, values))
        case _:
            return _is_all_or_any_call(expr)


def _is_all_or_any_call(node: ast.AST) -> bool:
    """Whether ``node`` is a call to the builtin ``all`` or ``any``."""
    return isinstance(node, ast.Call) and (
        isinstance(node.func, ast.Name) and node.func.id in {"all", "any"}
    )

"""Exact arithmetic and ISO date maths via an AST allow-list (never `eval`)."""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, Final

from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.tools import ToolOutput

MAX_EXPRESSION_CHARS: Final[int] = 200
MAX_EXPONENT: Final[int] = 1_000
MAX_BASE_FOR_LARGE_EXP: Final[int] = 10**6
LARGE_EXP_THRESHOLD: Final[int] = 100
MAX_INTERMEDIATE: Final[int] = 10**100
DATE_MIN: Final[int] = 1900
DATE_MAX: Final[int] = 2200
SUMMARY_EXPR_CHARS: Final[int] = 60

_PERCENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?|\d[\d_]*\d)(?:%)"
)
_OF_RE: Final[re.Pattern[str]] = re.compile(r"\bof\b", re.IGNORECASE)

_BIN_OPS: Final[dict[type[ast.operator], Callable[[Any, Any], Any]]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: Final[dict[type[ast.unaryop], Callable[[Any], Any]]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _not_allowed(construct: str) -> ToolCallError:
    return ToolCallError(f"not allowed: {construct}")


def _check_magnitude(value: Any) -> Any:
    if isinstance(value, bool):
        raise _not_allowed("bool")
    if isinstance(value, int) and abs(value) > MAX_INTERMEDIATE:
        raise ToolCallError("number too large")
    return value


def _format_number(value: float | int) -> str:
    if isinstance(value, bool):
        raise _not_allowed("bool")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolCallError("number too large")
        text = f"{value:.10g}"
        if text.endswith(".0"):
            text = text[:-2]
        return text
    raise _not_allowed(type(value).__name__)


def _format_date(value: date) -> str:
    return f"{value.isoformat()} ({value.strftime('%A')})"


def _parse_iso_date(raw: str) -> date:
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ToolCallError(f"not allowed: date {raw!r}") from exc
    if parsed.year < DATE_MIN or parsed.year > DATE_MAX:
        raise ToolCallError("date out of range")
    return parsed


def _percent_to_decimal(match: re.Match[str]) -> str:
    raw = match.group(1).replace("_", "")
    value = float(raw) / 100.0
    text = f"{value:.10g}"
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _normalise_expression(expression: str) -> str:
    text = expression.strip()
    text = text.replace("×", "*").replace("÷", "/").replace("^", "**")
    text = _PERCENT_RE.sub(_percent_to_decimal, text)
    text = _OF_RE.sub("*", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _pow(base: Any, exp: Any) -> Any:
    if isinstance(exp, float) and not exp.is_integer():
        result = operator.pow(base, exp)
        return _check_magnitude(result)
    exp_int = int(exp)
    if abs(exp_int) > MAX_EXPONENT:
        raise ToolCallError("number too large")
    if abs(exp_int) > LARGE_EXP_THRESHOLD:
        if isinstance(base, (int, float)) and abs(base) > MAX_BASE_FOR_LARGE_EXP:
            raise ToolCallError("number too large")
    try:
        result = operator.pow(base, exp_int if isinstance(exp, (int, float)) else exp)
    except OverflowError as exc:
        raise ToolCallError("number too large") from exc
    return _check_magnitude(result)


def _call_fn(name: str, args: list[Any], today: date) -> Any:
    if name == "round":
        if len(args) == 1:
            return round(args[0])
        if len(args) == 2:
            return round(args[0], int(args[1]))
        raise _not_allowed("round arity")
    if name == "min":
        if not args:
            raise _not_allowed("min arity")
        return min(args)
    if name == "max":
        if not args:
            raise _not_allowed("max arity")
        return max(args)
    if name == "abs":
        if len(args) != 1:
            raise _not_allowed("abs arity")
        return abs(args[0])
    if name == "sqrt":
        if len(args) != 1:
            raise _not_allowed("sqrt arity")
        return math.sqrt(float(args[0]))
    if name == "floor":
        if len(args) != 1:
            raise _not_allowed("floor arity")
        return math.floor(float(args[0]))
    if name == "ceil":
        if len(args) != 1:
            raise _not_allowed("ceil arity")
        return math.ceil(float(args[0]))
    if name == "sum":
        return sum(args)
    if name == "days_between":
        if len(args) != 2 or not all(isinstance(a, str) for a in args):
            raise _not_allowed("days_between args")
        a = _parse_iso_date(args[0])
        b = _parse_iso_date(args[1])
        return (b - a).days
    if name == "days_until":
        if len(args) != 1 or not isinstance(args[0], str):
            raise _not_allowed("days_until args")
        target = _parse_iso_date(args[0])
        return (target - today).days
    if name == "add_days":
        if len(args) != 2 or not isinstance(args[0], str):
            raise _not_allowed("add_days args")
        base = _parse_iso_date(args[0])
        delta = int(args[1])
        result = base + timedelta(days=delta)
        if result.year < DATE_MIN or result.year > DATE_MAX:
            raise ToolCallError("date out of range")
        return result
    if name == "weekday":
        if len(args) != 1 or not isinstance(args[0], str):
            raise _not_allowed("weekday args")
        return _parse_iso_date(args[0]).strftime("%A")
    if name == "today":
        if args:
            raise _not_allowed("today arity")
        return today
    raise _not_allowed(f"name {name}")


def _eval_node(node: ast.AST, today: date) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, today)

    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return _check_magnitude(value)
        if isinstance(value, str):
            return value
        raise _not_allowed(type(value).__name__)

    if isinstance(node, ast.UnaryOp):
        unary_type = type(node.op)
        if unary_type not in _UNARY_OPS:
            raise _not_allowed(f"unary {unary_type.__name__}")
        return _check_magnitude(
            _UNARY_OPS[unary_type](_eval_node(node.operand, today))
        )

    if isinstance(node, ast.BinOp):
        bin_type = type(node.op)
        left = _eval_node(node.left, today)
        right = _eval_node(node.right, today)
        if bin_type is ast.Pow:
            return _pow(left, right)
        if bin_type not in _BIN_OPS:
            raise _not_allowed(f"binop {bin_type.__name__}")
        try:
            result = _BIN_OPS[bin_type](left, right)
        except ZeroDivisionError as exc:
            raise ToolCallError("division by zero") from exc
        except OverflowError as exc:
            raise ToolCallError("number too large") from exc
        return _check_magnitude(result)

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise _not_allowed("call form")
        args = [_eval_node(arg, today) for arg in node.args]
        return _check_magnitude(_call_fn(node.func.id, args, today))

    if isinstance(node, ast.Name):
        raise _not_allowed(f"name {node.id}")
    if isinstance(node, ast.Attribute):
        raise _not_allowed("attribute")
    if isinstance(node, ast.Subscript):
        raise _not_allowed("subscript")
    if isinstance(node, ast.Lambda):
        raise _not_allowed("lambda")
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        raise _not_allowed("comprehension")
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        raise _not_allowed(type(node).__name__.lower())

    raise _not_allowed(type(node).__name__)


def evaluate(expression: str, *, today: date) -> str:
    """Return ``<normalised> = <result>`` or raise ``ToolCallError``."""
    if not isinstance(expression, str) or "\x00" in expression:
        raise ToolCallError("not allowed: expression")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise ToolCallError(
            f"expression too long (limit {MAX_EXPRESSION_CHARS} characters)"
        )

    normalised = _normalise_expression(expression)
    if not normalised:
        raise ToolCallError("not allowed: empty")

    try:
        tree = ast.parse(normalised, mode="eval")
    except SyntaxError as exc:
        raise ToolCallError("not allowed: syntax") from exc

    result = _eval_node(tree, today)
    if isinstance(result, date):
        result_text = _format_date(result)
    elif isinstance(result, str):
        result_text = result
    else:
        result_text = _format_number(result)

    return f"{normalised} = {result_text}"


def format_row_summary(normalised_eq_result: str) -> str:
    """Build the CLI row summary ``Calculate <expr> = <result>`` (expr ≤ 60)."""
    if " = " not in normalised_eq_result:
        body = normalised_eq_result
    else:
        expr, _, result = normalised_eq_result.partition(" = ")
        if len(expr) > SUMMARY_EXPR_CHARS:
            expr = expr[: SUMMARY_EXPR_CHARS - 1] + "…"
        # Thousands separators for integers in the row only.
        if re.fullmatch(r"-?\d+", result):
            sign = "-" if result.startswith("-") else ""
            digits = result.lstrip("-")
            grouped = f"{int(digits):,}"
            result = f"{sign}{grouped}"
        body = f"{expr} = {result}"
    return f"Calculate {body}"


class CalculateTool(Tool):
    """Evaluate an allow-listed arithmetic or date expression."""

    name = "calculate"
    description = (
        "Evaluate exact arithmetic and ISO date maths. Use for percentages, "
        "sums, and day counts. Never use the shell for simple maths."
    )
    parallel_safe = True
    timeout = 2
    max_output_chars = 500
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": (
                    "Arithmetic or date expression (≤200 chars). Supports %, "
                    "of, ×÷^, and days_between / days_until / add_days / "
                    "weekday / today."
                ),
            },
        },
        "required": ["expression"],
        "additionalProperties": False,
    }

    def __init__(self, *, today_fn: Callable[[], date] | None = None) -> None:
        self._today_fn = today_fn or (lambda: datetime.now().astimezone().date())

    async def forward(self, expression: str) -> ToolOutput:  # type: ignore[override]
        text = evaluate(expression, today=self._today_fn())
        return ToolOutput(
            text,
            summary=format_row_summary(text),
            ok=True,
        )


def build_calculate_tool(
    *,
    today_fn: Callable[[], date] | None = None,
) -> CalculateTool:
    """Construct the `calculate` tool."""
    return CalculateTool(today_fn=today_fn)


__all__ = [
    "CalculateTool",
    "build_calculate_tool",
    "evaluate",
    "format_row_summary",
]

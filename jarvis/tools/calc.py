"""Arithmetic, so the model never has to guess a sum or drive the Calculator app."""
from __future__ import annotations

import ast
import math
import operator

from . import S, tool

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "sin": math.sin, "cos": math.cos, "tan": math.tan,
          "log": math.log, "log10": math.log10, "exp": math.exp, "floor": math.floor, "ceil": math.ceil}
_NAMES = {"pi": math.pi, "e": math.e}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return _NAMES[node.id]
    raise ValueError("only numbers, + - * / % ** ( ) and sqrt, abs, round, sin, cos, tan, log, exp are allowed")


@tool(
    "Work out arithmetic and return the answer. Always use this for any calculation or maths question "
    "(sums, percentages, 'what is 17 times 23'); do not open the Calculator app for that.",
    {"expression": S("Arithmetic in Python syntax, e.g. '17*23', '(1200*15)/100', 'sqrt(2)', '2**10'")},
)
def calculate(expression: str):
    text = expression.replace("^", "**").replace("×", "*").replace("x", "*").replace("÷", "/").replace(",", "")
    if "exp" in expression or "max" in expression:  # keep the letter x in function names
        text = expression.replace("^", "**").replace(",", "")
    value = _eval(ast.parse(text.strip().rstrip("="), mode="eval").body)
    if isinstance(value, float):
        value = round(value, 6)
        if value == int(value):
            value = int(value)
    return f"{expression.strip()} = {value}"

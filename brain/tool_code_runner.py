from __future__ import annotations

import contextlib
import io
from typing import Any, Dict


_SAFE_BUILTINS = {
    "len": len,
    "min": min,
    "max": max,
    "sum": sum,
    "sorted": sorted,
    "range": range,
    "enumerate": enumerate,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "abs": abs,
    "round": round,
    "print": print,
}


def run_tool_code(code: str, *, variables: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if "__import__" in code or "import " in code:
        raise ValueError("Imports are not allowed in tool code runner")
    stdout = io.StringIO()
    local_vars: Dict[str, Any] = dict(variables or {})
    globals_dict = {"__builtins__": _SAFE_BUILTINS}
    with contextlib.redirect_stdout(stdout):
        exec(code, globals_dict, local_vars)
    sanitized_locals = {
        key: value
        for key, value in local_vars.items()
        if not key.startswith("_")
    }
    return {
        "stdout": stdout.getvalue().strip(),
        "locals": sanitized_locals,
    }


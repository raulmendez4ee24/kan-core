import platform
import subprocess
from typing import Any, Dict


_ALLOWED_CLICK_BUTTONS = {"left", "right", "middle"}
_SUPPORTED_PLATFORMS = {"windows", "darwin"}  # darwin = macOS


def _ensure_windows() -> None:
    os_name = platform.system().lower()
    if os_name not in _SUPPORTED_PLATFORMS:
        raise RuntimeError(f"Desktop agent supports Windows and macOS only (got: {os_name})")


def _get_pyautogui():
    try:
        import pyautogui  # type: ignore
    except Exception as exc:
        raise RuntimeError("pyautogui is required for desktop actions") from exc
    return pyautogui



def mover_mouse(x: int, y: int) -> Dict[str, Any]:
    _ensure_windows()
    if not isinstance(x, int) or not isinstance(y, int):
        raise ValueError("x and y must be integers")
    if x < 0 or y < 0:
        raise ValueError("x and y must be >= 0")

    pyautogui = _get_pyautogui()
    pyautogui.moveTo(x, y)
    return {"x": x, "y": y}



def click(
    button: str = "left",
    clicks: int = 1,
    x: int | None = None,
    y: int | None = None,
) -> Dict[str, Any]:
    _ensure_windows()
    normalized_button = str(button or "left").strip().lower()
    if normalized_button not in _ALLOWED_CLICK_BUTTONS:
        raise ValueError("button must be one of left|right|middle")
    if not isinstance(clicks, int) or clicks < 1 or clicks > 5:
        raise ValueError("clicks must be int between 1 and 5")
    if (x is None) != (y is None):
        raise ValueError("x and y must be provided together")
    if x is not None and (not isinstance(x, int) or x < 0):
        raise ValueError("x must be an integer >= 0")
    if y is not None and (not isinstance(y, int) or y < 0):
        raise ValueError("y must be an integer >= 0")

    pyautogui = _get_pyautogui()
    pyautogui.click(x=x, y=y, button=normalized_button, clicks=clicks)
    payload: Dict[str, Any] = {"button": normalized_button, "clicks": clicks}
    if x is not None and y is not None:
        payload["x"] = x
        payload["y"] = y
    return payload



def type_text(text: str, interval: float = 0.0) -> Dict[str, Any]:
    _ensure_windows()
    if not isinstance(text, str) or not text:
        raise ValueError("text must be a non-empty string")
    if not isinstance(interval, (int, float)) or interval < 0 or interval > 1.0:
        raise ValueError("interval must be between 0 and 1")

    pyautogui = _get_pyautogui()
    pyautogui.write(text, interval=float(interval))
    return {"text_length": len(text), "interval": float(interval)}



def press_key(key: str) -> Dict[str, Any]:
    _ensure_windows()
    if not isinstance(key, str) or not key.strip():
        raise ValueError("key must be a non-empty string")
    normalized_key = key.strip().lower()
    parts = [part.strip() for part in normalized_key.split("+") if part.strip()]

    pyautogui = _get_pyautogui()
    if len(parts) <= 1:
        pyautogui.press(parts[0] if parts else normalized_key)
    else:
        pyautogui.hotkey(*parts)
    payload: Dict[str, Any] = {"key": normalized_key}
    if len(parts) > 1:
        payload["parts"] = parts
    return payload



def open_program(
    name: str,
    allowlist: Dict[str, str] | None = None,
    unsafe_allow_any: bool = False,
) -> Dict[str, Any]:
    _ensure_windows()
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")

    resolved_allowlist = allowlist or {}
    raw = name.strip()
    normalized_key = raw.lower()
    executable = resolved_allowlist.get(normalized_key)
    if executable:
        target = executable
    else:
        if not unsafe_allow_any:
            raise ValueError(f"Program '{normalized_key}' is not in allowlist")
        target = raw

    _is_mac = platform.system().lower() == "darwin"

    try:
        if _is_mac:
            process = subprocess.Popen(["open", "-a", target])  # noqa: S603
        else:
            process = subprocess.Popen([target])  # noqa: S603
    except Exception as exc:
        if not unsafe_allow_any:
            raise RuntimeError(f"Failed to open program '{normalized_key}': {exc!s}") from exc
        # Unsafe fallback: let the OS resolve by name/path.
        if any(token in raw for token in ("\n", "\r", "\x00")):
            raise RuntimeError("Program name contains invalid characters") from exc
        try:
            if _is_mac:
                process = subprocess.Popen(["open", "-a", raw])  # noqa: S603
            else:
                process = subprocess.Popen(["cmd", "/c", "start", "", raw])  # noqa: S603
        except Exception as exc2:
            raise RuntimeError(f"Failed to open program '{raw}': {exc2!s}") from exc2

    return {
        "name": normalized_key,
        "executable": target,
        "pid": process.pid,
        "opened_via": ("allowlist" if executable else ("unsafe" if unsafe_allow_any else "unknown")),
    }

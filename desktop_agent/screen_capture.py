import base64
import gc
import io
import os
import platform
import threading
import time
from typing import Any, Dict

try:
    import fcntl  # POSIX (macOS/Linux) for cross-process locking
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

_SUPPORTED_PLATFORMS = {"windows", "darwin"}
_SCREEN_CAPTURE_THREAD_LOCK = threading.RLock()
_SCREEN_CAPTURE_LOCK_PATH = "/tmp/kan_screen_capture.lock"


def _ensure_windows() -> None:
    os_name = platform.system().lower()
    if os_name not in _SUPPORTED_PLATFORMS:
        raise RuntimeError(f"Desktop agent supports Windows and macOS only (got: {os_name})")


def _get_pyautogui():
    try:
        import pyautogui  # type: ignore
    except Exception as exc:
        raise RuntimeError("pyautogui is required for screenshot capture") from exc
    return pyautogui


def _acquire_process_lock(timeout_s: float):
    if fcntl is None:
        return None
    fh = open(_SCREEN_CAPTURE_LOCK_PATH, "a+", encoding="utf-8")
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            if time.monotonic() >= deadline:
                fh.close()
                raise RuntimeError("screen_capture_lock_timeout")
            time.sleep(0.02)


def _release_process_lock(fh) -> None:
    if fh is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def take_screenshot(max_width: int = 1600) -> Dict[str, Any]:
    _ensure_windows()
    if not isinstance(max_width, int) or max_width < 320:
        raise ValueError("max_width must be >= 320")
    lock_timeout = float(os.getenv("SCREEN_CAPTURE_LOCK_TIMEOUT_SEC", "2.0"))

    with _SCREEN_CAPTURE_THREAD_LOCK:
        process_lock = _acquire_process_lock(timeout_s=lock_timeout)
        raw_image = None
        resized_image = None
        image = None
        try:
            pyautogui = _get_pyautogui()
            raw_image = pyautogui.screenshot()
            image = raw_image
            try:
                if getattr(raw_image, "width", 0) and raw_image.width > max_width:
                    ratio = float(max_width) / float(raw_image.width)
                    target_height = int(raw_image.height * ratio)
                    resized_image = raw_image.resize((max_width, target_height))
                    image = resized_image

                with io.BytesIO() as buffer:
                    image.save(buffer, format="PNG")
                    encoded = base64.b64encode(buffer.getvalue()).decode()
                return {
                    "mime_type": "image/png",
                    "width": int(image.width),
                    "height": int(image.height),
                    "image_base64": encoded,
                }
            finally:
                close_resized = getattr(resized_image, "close", None)
                if callable(close_resized):
                    close_resized()
                close_raw = getattr(raw_image, "close", None)
                if callable(close_raw):
                    close_raw()
        finally:
            image = None
            resized_image = None
            raw_image = None
            _release_process_lock(process_lock)
            gc.collect()

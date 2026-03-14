import platform
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Set


def _ensure_windows() -> None:
    if platform.system().lower() != "windows":
        raise RuntimeError("Human recorder supports Windows only in this version")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_pynput():
    try:
        from pynput import keyboard, mouse  # type: ignore
    except Exception as exc:
        raise RuntimeError("pynput is required for human recorder") from exc
    return keyboard, mouse


class HumanRecorder:
    """Best-effort desktop interaction recorder using OS hooks (pynput).

    It emits high-level events compatible with the existing desktop command contract:
    - click {x,y,button,clicks}
    - type_text {text, interval?, redacted?}
    - press_key {key}
    """

    def __init__(
        self,
        *,
        on_event: Callable[[Dict[str, Any]], None],
        redaction: Optional[Dict[str, Any]] = None,
        context_provider: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        _ensure_windows()
        self._on_event = on_event
        self._context_provider = context_provider or (lambda: {})

        resolved = redaction or {}
        self._mask_all_keystrokes = bool(resolved.get("mask_all_keystrokes", False))

        self._lock = threading.Lock()
        self._running = False
        self._keyboard_listener = None
        self._mouse_listener = None

        self._text_buffer: list[str] = []
        self._text_redacted = False
        self._modifiers: Set[str] = set()

    def update_redaction(self, redaction: Dict[str, Any]) -> None:
        with self._lock:
            self._mask_all_keystrokes = bool(redaction.get("mask_all_keystrokes", self._mask_all_keystrokes))

    def start(self) -> None:
        if self._running:
            return
        keyboard, mouse = _get_pynput()
        self._running = True

        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
            suppress=False,
        )
        self._mouse_listener = mouse.Listener(
            on_click=self._on_click,
            suppress=False,
        )
        self._keyboard_listener.start()
        self._mouse_listener.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        try:
            self._flush_text_buffer()
        except Exception:
            pass
        if self._keyboard_listener is not None:
            try:
                self._keyboard_listener.stop()
            except Exception:
                pass
        if self._mouse_listener is not None:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
        self._keyboard_listener = None
        self._mouse_listener = None

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        event = {
            "ts": _utc_iso(),
            "event_type": event_type,
            "payload": payload,
            "context": self._context_provider() or {},
        }
        self._on_event(event)

    def _flush_text_buffer(self) -> None:
        with self._lock:
            if not self._text_buffer:
                return
            text = "".join(self._text_buffer)
            redacted = bool(self._text_redacted)
            self._text_buffer = []
            self._text_redacted = False

        if redacted:
            self._emit(
                "type_text",
                {
                    "text": "[REDACTED]",
                    "interval": 0.0,
                    "redacted": True,
                    "text_length": len(text),
                },
            )
        else:
            self._emit(
                "type_text",
                {
                    "text": text,
                    "interval": 0.0,
                },
            )

    @staticmethod
    def _normalize_modifier(key: Any) -> Optional[str]:
        name = str(key).lower()
        if "ctrl" in name:
            return "ctrl"
        if "alt" in name:
            return "alt"
        if "shift" in name:
            return "shift"
        if "cmd" in name or "win" in name or "meta" in name:
            return "cmd"
        return None

    @staticmethod
    def _normalize_special_key(key: Any) -> Optional[str]:
        token = str(key).lower()
        mapping = {
            "key.enter": "enter",
            "key.tab": "tab",
            "key.esc": "esc",
            "key.space": "space",
            "key.backspace": "backspace",
            "key.delete": "delete",
            "key.up": "up",
            "key.down": "down",
            "key.left": "left",
            "key.right": "right",
            "key.home": "home",
            "key.end": "end",
            "key.page_up": "pageup",
            "key.page_down": "pagedown",
        }
        return mapping.get(token)

    def _hotkey_string(self, base: str) -> str:
        parts = []
        for mod in ("ctrl", "alt", "shift", "cmd"):
            if mod in self._modifiers:
                parts.append(mod)
        parts.append(base)
        return "+".join(parts)

    def _on_key_press(self, key: Any) -> None:
        mod = self._normalize_modifier(key)
        if mod:
            with self._lock:
                self._modifiers.add(mod)
            return

        special = self._normalize_special_key(key)
        if special:
            try:
                self._flush_text_buffer()
            except Exception:
                pass
            # Modifiers + special key => hotkey (best effort).
            if self._modifiers:
                self._emit("press_key", {"key": self._hotkey_string(special)})
            else:
                self._emit("press_key", {"key": special})
            return

        char = getattr(key, "char", None)
        if not isinstance(char, str) or not char:
            return

        # If modifiers are pressed, treat as a hotkey rather than typed text.
        if self._modifiers:
            try:
                self._flush_text_buffer()
            except Exception:
                pass
            self._emit("press_key", {"key": self._hotkey_string(char.lower())})
            return

        with self._lock:
            if self._mask_all_keystrokes:
                self._text_redacted = True
                self._text_buffer.append(char)
            else:
                self._text_buffer.append(char)

    def _on_key_release(self, key: Any) -> None:
        mod = self._normalize_modifier(key)
        if not mod:
            return
        with self._lock:
            self._modifiers.discard(mod)

    def _on_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        # Only record on release to avoid duplicates.
        if pressed:
            return
        try:
            self._flush_text_buffer()
        except Exception:
            pass

        btn = str(button).lower()
        if "right" in btn:
            name = "right"
        elif "middle" in btn:
            name = "middle"
        else:
            name = "left"

        self._emit("click", {"x": int(x), "y": int(y), "button": name, "clicks": 1})


import argparse
import platform
import sys
from typing import Any, Dict, Tuple

from desktop_agent.config import load_config


def _print_section(title: str) -> None:
    print("")
    print(f"== {title} ==")


def _try_import(name: str) -> Tuple[bool, str]:
    try:
        __import__(name)
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _safe_bool(value: Any) -> bool:
    return bool(value) is True


def main() -> None:
    parser = argparse.ArgumentParser(description="Desktop agent doctor / Windows VM smoke checks")
    parser.add_argument("--skip-screenshot", action="store_true", help="Skip screenshot capture test")
    args = parser.parse_args()

    print("kan-core desktop_agent doctor")
    print(f"python={sys.version.split()[0]}")
    print(f"os={platform.system()} {platform.release()}")

    ok = True

    _print_section("Config")
    try:
        config = load_config()
        print("load_config: ok")
        print(f"backend_ws_url={config.backend_ws_url}")
        print(f"client_id={config.client_id}")
        print("client_token=***")
        print(f"agent_name={config.agent_name}")
        print(f"browser_enabled={str(_safe_bool(config.browser_enabled)).lower()}")
        print(f"human_recorder_enabled={str(_safe_bool(config.human_recorder_enabled)).lower()}")
        print(f"open_program_allowlist_items={len(config.open_program_allowlist or {})}")
        print(f"browser_domain_allowlist_items={len(config.browser_domain_allowlist or [])}")
        if config.browser_enabled and not config.browser_domain_allowlist:
            ok = False
            print("WARN: browser is enabled but domain allowlist is empty. browser_goto will be rejected.")
    except Exception as exc:
        ok = False
        print(f"load_config: failed ({exc!s})")
        print("Fix: set DESKTOP_AGENT_WS_URL, DESKTOP_AGENT_CLIENT_ID, DESKTOP_AGENT_CLIENT_TOKEN.")

    _print_section("Dependencies")
    ws_ok, ws_detail = _try_import("websockets")
    print(f"websockets: {ws_detail}")
    ok = ok and ws_ok

    pg_ok, pg_detail = _try_import("pyautogui")
    print(f"pyautogui: {pg_detail}")
    ok = ok and pg_ok

    pil_ok, pil_detail = _try_import("PIL")
    print(f"pillow(PIL): {pil_detail}")
    ok = ok and pil_ok

    if "config" in locals() and getattr(config, "browser_enabled", False):
        pl_ok, pl_detail = _try_import("playwright.sync_api")
        print(f"playwright: {pl_detail}")
        ok = ok and pl_ok
        if pl_ok:
            print("NOTE: if browser launch fails, run `python -m playwright install chromium` on this VM.")

    if "config" in locals() and getattr(config, "human_recorder_enabled", False):
        pn_ok, pn_detail = _try_import("pynput")
        print(f"pynput: {pn_detail}")
        ok = ok and pn_ok

    if not args.skip_screenshot:
        _print_section("Screenshot")
        try:
            from desktop_agent.screen_capture import take_screenshot

            shot = take_screenshot(max_width=640)
            width = int(shot.get("width") or 0)
            height = int(shot.get("height") or 0)
            b64 = str(shot.get("screenshot_base64") or "")
            if width <= 0 or height <= 0 or not b64:
                ok = False
                print("take_screenshot: failed (empty result)")
            else:
                print(f"take_screenshot: ok ({width}x{height}, base64_len={len(b64)})")
        except Exception as exc:
            ok = False
            print(f"take_screenshot: failed ({exc!s})")

    _print_section("Result")
    if ok:
        print("OK: desktop_agent looks healthy for VM run.")
        raise SystemExit(0)
    print("FAIL: desktop_agent has issues. Fix env/deps and retry.")
    raise SystemExit(2)


if __name__ == "__main__":
    main()


#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ycloud_client import send_ycloud_whatsapp_text_message


def _load_env() -> None:
    for name in (".env.secrets", ".env"):
        env_path = ROOT / name
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line or line.startswith("export "):
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


async def main() -> None:
    _load_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    to_number = str(os.getenv("RAUL_WHATSAPP_TO") or "").strip()
    from_number = str(os.getenv("YCLOUD_WHATSAPP_FROM") or "").strip()
    message = "Test KAN Logic ✓"

    print(f"RAUL_WHATSAPP_TO={to_number}")
    print(f"YCLOUD_WHATSAPP_FROM={from_number}")
    print(f"message={message}")

    response = await send_ycloud_whatsapp_text_message(
        from_number=from_number,
        to=to_number,
        text=message,
    )
    print(f"response={response}")


if __name__ == "__main__":
    asyncio.run(main())

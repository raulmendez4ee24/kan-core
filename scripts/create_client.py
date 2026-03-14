import argparse
import asyncio
import secrets
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import select

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from database import AsyncSessionLocal
from models import Client


async def _run(*, name: str, system_prompt: Optional[str], model_name: Optional[str], temperature: Optional[float]) -> int:
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(select(Client).where(Client.name == name).limit(1))).scalars().first()
        if existing:
            token = ""
            try:
                token = existing.get_api_key()
            except Exception:
                # api key decrypt requires MASTER_KEY; keep output minimal.
                token = ""
            print("Client already exists.")
            print(f"CLIENT_ID={existing.id}")
            if token:
                print(f"CLIENT_TOKEN={token}")
                print(f"CLIENT_TOKENS_JSON={{\"{existing.id}\":\"{token}\"}}")
            else:
                print("CLIENT_TOKEN=<unavailable (MASTER_KEY missing or token cannot be decrypted)>")
            return 0

        token = secrets.token_urlsafe(32)
        client = Client(
            name=name,
            api_key_encrypted="",
            persona={},
            llm_preferences={},
            system_prompt=system_prompt,
            is_active=True,
        )
        if model_name:
            client.model_name = model_name
        if temperature is not None:
            client.temperature = float(temperature)

        # Requires MASTER_KEY.
        client.set_api_key(token)

        session.add(client)
        await session.commit()
        await session.refresh(client)

        print("Client created.")
        print(f"CLIENT_ID={client.id}")
        print(f"CLIENT_TOKEN={token}")
        print(f"CLIENT_TOKENS_JSON={{\"{client.id}\":\"{token}\"}}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Client/org in Postgres and print tokens.")
    parser.add_argument("--name", required=True, help="Client/org name (unique).")
    parser.add_argument("--system-prompt", default=None, help="Optional system prompt override.")
    parser.add_argument("--model-name", default=None, help="Optional model name (default from models.py).")
    parser.add_argument("--temperature", type=float, default=None, help="Optional temperature (0..2).")
    args = parser.parse_args()

    try:
        rc = asyncio.run(
            _run(
                name=str(args.name).strip()[:120],
                system_prompt=(str(args.system_prompt) if args.system_prompt is not None else None),
                model_name=(str(args.model_name) if args.model_name is not None else None),
                temperature=args.temperature,
            )
        )
    except Exception as exc:
        print(f"ERROR: {exc!s}")
        print("Hint: set MASTER_KEY and DATABASE_URL, then retry.")
        raise SystemExit(2) from exc
    raise SystemExit(rc)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Chat simple para hablar con Tu Autonomia.

Uso:
  python3 scripts/hablar_con_autonomia.py --personal
  python3 scripts/hablar_con_autonomia.py --client-id "tu-uuid"
"""
import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from brain.autonomous_agent import AutonomousAgent
from database import AsyncSessionLocal
from models import Client


PERSONAL_CLIENT_NAME = "mi_autonomia"


def _check_env() -> None:
    missing = []
    if not os.getenv("MASTER_KEY") and not os.getenv("DATA_ENCRYPTION_KEY"):
        missing.append("MASTER_KEY o DATA_ENCRYPTION_KEY")
    if not os.getenv("OPENAI_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        missing.append("OPENAI_API_KEY o GEMINI_API_KEY")
    if not os.getenv("DATABASE_URL"):
        missing.append("DATABASE_URL")
    if missing:
        print("Faltan variables de entorno:")
        for item in missing:
            print(f" - {item}")
        raise SystemExit(1)


async def _get_or_create_personal_client_id() -> str:
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(select(Client).where(Client.name == PERSONAL_CLIENT_NAME).limit(1))
        ).scalars().first()
        if existing:
            return str(existing.id)

        token = secrets.token_urlsafe(32)
        client = Client(
            name=PERSONAL_CLIENT_NAME,
            api_key_encrypted="",
            persona={},
            llm_preferences={},
            is_active=True,
        )
        client.set_api_key(token)
        session.add(client)
        await session.commit()
        await session.refresh(client)
        return str(client.id)


def _print_help() -> None:
    print(
        """
Comandos:
  /help                 Muestra ayuda
  /salir                Cierra el chat
  /ejecutar <objetivo>  Ejecuta acciones autonomas para ese objetivo

Si escribes texto normal, la autonomia te responde en modo conversacion.
"""
    )


async def _chat(agent: AutonomousAgent) -> None:
    print("\n=== Chat con Tu Autonomia ===")
    print("Escribe /help para ver comandos.\n")

    while True:
        text = input("Tu> ").strip()
        if not text:
            continue

        if text.lower() in {"/salir", "salir", "exit", "quit"}:
            print("Autonomia> Hasta luego.")
            break

        if text.lower() == "/help":
            _print_help()
            continue

        if text.lower().startswith("/ejecutar "):
            goal = text[10:].strip()
            if not goal:
                print("Autonomia> Escribe un objetivo despues de /ejecutar")
                continue
            print("Autonomia> Ejecutando objetivo...")
            result = await agent.run_autonomous_loop(goal, max_iterations=5)
            print(
                "Autonomia> Listo. "
                f"Estado: {result.get('status')} | "
                f"Iteraciones: {result.get('iterations')} | "
                f"APIs descubiertas: {result.get('discovered_apis')}"
            )
            continue

        # Conversacion normal
        answer = await agent.think(text, context={"mode": "chat"})
        raw = str(answer.get("raw_response") or "").strip()
        if not raw:
            raw = "No pude generar respuesta en este momento."
        print(f"Autonomia> {raw}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Hablar con Tu Autonomia desde terminal.")
    parser.add_argument("--client-id", type=str, default=None, help="UUID cliente")
    parser.add_argument("--personal", action="store_true", help="Usa cliente personal automatico")
    args = parser.parse_args()

    _check_env()

    client_id = args.client_id or os.getenv("PERSONAL_CLIENT_ID")
    if args.personal or not client_id:
        client_id = await _get_or_create_personal_client_id()

    agent = AutonomousAgent(
        client_id=str(client_id),
        execution_mode="balanced",
        enable_api_discovery=True,
        enable_browser=True,
        enable_desktop_control=True,
    )

    async with agent:
        await _chat(agent)


if __name__ == "__main__":
    asyncio.run(main())


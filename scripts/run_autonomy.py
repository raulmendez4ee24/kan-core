#!/usr/bin/env python3
"""
Tu Autonomía — Se ejecuta en la terminal.
Es tu agente: controla la computadora, descubre APIs y las encripta.
Sin nombre "Claw"; todo es tuyo.
Para uso personal: no hace falta tener client-id; se crea uno "para mí" solo.
"""
import argparse
import asyncio
import logging
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from brain.autonomous_agent import AutonomousAgent
from database import AsyncSessionLocal
from models import Client
from sqlalchemy import select

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("autonomy")


def print_banner():
    banner = """
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║              TU AUTONOMÍA                                 ║
    ║                                                           ║
    ║  Tu computadora trabajando sola:                          ║
    ║  • Descubre APIs y las guarda encriptadas                  ║
    ║  • Usa inteligencia (GPT) para decidir                     ║
    ║  • Control del navegador y escritorio                     ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)


def check_environment():
    errors = []
    if not os.getenv("MASTER_KEY") and not os.getenv("DATA_ENCRYPTION_KEY"):
        errors.append("Falta MASTER_KEY o DATA_ENCRYPTION_KEY (para encriptar)")
    if not os.getenv("OPENAI_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        errors.append("Falta OPENAI_API_KEY o GEMINI_API_KEY (para la inteligencia)")
    if not os.getenv("DATABASE_URL"):
        errors.append("Falta DATABASE_URL")
    if errors:
        logger.error("Configuración incompleta:")
        for e in errors:
            logger.error(f"  - {e}")
        return False
    logger.info("✓ Configuración correcta")
    return True


# Nombre del cliente "para mí" cuando usas --personal o no pasas client-id
PERSONAL_CLIENT_NAME = "mi_autonomia"


async def get_or_create_personal_client_id() -> str:
    """
    Si usas Tu Autonomía solo para ti, no tienes client-id.
    Esto crea (o reusa) un cliente llamado "mi_autonomia" y devuelve su ID.
    """
    try:
        async with AsyncSessionLocal() as session:
            existing = (
                await session.execute(
                    select(Client).where(Client.name == PERSONAL_CLIENT_NAME).limit(1)
                )
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
            logger.info("Cliente personal creado (solo para ti). Se usará en adelante.")
            return str(client.id)
    except Exception as e:
        error_msg = str(e)
        if "InvalidAuthorizationSpecificationError" in error_msg or "role" in error_msg.lower():
            logger.error("")
            logger.error("❌ Error de conexión a la base de datos PostgreSQL")
            logger.error("")
            logger.error("El usuario/rol de PostgreSQL no existe o las credenciales son incorrectas.")
            logger.error("")
            logger.error("Soluciones:")
            logger.error("1. Verifica que PostgreSQL esté corriendo:")
            logger.error("   brew services list  # macOS")
            logger.error("   sudo systemctl status postgresql  # Linux")
            logger.error("")
            logger.error("2. Crea el usuario/rol si no existe:")
            logger.error("   psql postgres")
            logger.error("   CREATE USER tu_usuario WITH PASSWORD 'tu_password';")
            logger.error("   CREATE DATABASE nombre_db OWNER tu_usuario;")
            logger.error("")
            logger.error("3. Verifica tu DATABASE_URL:")
            logger.error("   export DATABASE_URL='postgresql+asyncpg://usuario:password@localhost:5432/nombre_db'")
            logger.error("")
            logger.error("4. O usa una base de datos remota (Supabase, Neon, etc.)")
            logger.error("")
        elif "Connection refused" in error_msg or "could not connect" in error_msg.lower():
            logger.error("")
            logger.error("❌ No se puede conectar a PostgreSQL")
            logger.error("")
            logger.error("Verifica que PostgreSQL esté corriendo y que DATABASE_URL sea correcta.")
            logger.error("")
        else:
            logger.error(f"Error de base de datos: {e}")
        raise


async def run_interactive_mode(agent: AutonomousAgent):
    print("\nEscribe lo que quieres que haga. Para salir: exit\n")

    while True:
        try:
            goal = input("Autonomía> ").strip()
            if not goal:
                continue
            if goal.lower() in ("exit", "quit", "salir"):
                print("Saliendo...")
                break
            if goal.lower() == "help":
                print("""
Comandos:
  discover <url>     Descubre APIs desde una página
  browser <url>      Abre el navegador y busca APIs
  terminal <comando> Ejecuta un comando y busca APIs
  goal <qué quieres> Le dices un objetivo y lo hace solo
  help               Esta ayuda
  exit               Salir
                """)
                continue
            if goal.startswith("discover "):
                url = goal.replace("discover ", "").strip()
                print(f"Buscando APIs en {url}...")
                result = await agent.discover_apis("url", url=url)
                print(f"Listo. Encontradas: {result.get('total_found', 0)}")
                continue
            if goal.startswith("browser "):
                url = goal.replace("browser ", "").strip()
                print(f"Abriendo navegador en {url}...")
                result = await agent.discover_apis("browser", browser_url=url)
                print(f"Listo. Encontradas: {result.get('total_found', 0)}")
                continue
            if goal.startswith("terminal "):
                cmd = goal.replace("terminal ", "").strip()
                print(f"Ejecutando: {cmd}...")
                result = await agent.discover_apis("terminal", terminal_command=cmd)
                print(f"Listo. Encontradas: {result.get('total_found', 0)}")
                continue
            if goal.startswith("goal "):
                goal_desc = goal.replace("goal ", "").strip()
                print(f"Objetivo: {goal_desc}")
                result = await agent.run_autonomous_loop(goal_desc, max_iterations=5)
                print(f"Estado: {result.get('status')}; APIs descubiertas: {result.get('discovered_apis')}")
                continue
            print(f"Objetivo: {goal}")
            result = await agent.run_autonomous_loop(goal, max_iterations=5)
            print(f"Estado: {result.get('status')}; APIs: {result.get('discovered_apis')}")
        except KeyboardInterrupt:
            print("\nInterrumpido.")
            break
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)


async def run_single_goal(agent: AutonomousAgent, goal: str, max_iterations: int):
    print(f"\nObjetivo: {goal}\n")
    result = await agent.run_autonomous_loop(goal, max_iterations=max_iterations)
    print("\n" + "=" * 50)
    print("RESULTADO")
    print("=" * 50)
    print(f"Estado: {result.get('status')}")
    print(f"Iteraciones: {result.get('iterations')}")
    print(f"APIs descubiertas: {result.get('discovered_apis')}")
    print(f"Acciones: {len(result.get('results', []))}")


async def main():
    parser = argparse.ArgumentParser(
        description="Tu Autonomía — controla tu computadora y descubre APIs"
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="ID de cliente (UUID). Si no lo tienes, no lo pongas: se usa modo 'para mí'.",
    )
    parser.add_argument(
        "--personal",
        action="store_true",
        help="Modo 'para mí': no hace falta client-id. Se crea/usa automáticamente.",
    )
    parser.add_argument("--goal", type=str, help="Objetivo (si no pones nada, modo interactivo)")
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--mode", type=str, choices=["safe", "balanced", "autonomous"], default="autonomous")
    parser.add_argument("--no-browser", action="store_true", help="Sin navegador")
    parser.add_argument("--no-desktop", action="store_true", help="Sin control de escritorio")
    parser.add_argument("--no-api-discovery", action="store_true", help="Sin descubrir APIs")
    args = parser.parse_args()

    print_banner()
    if not check_environment():
        sys.exit(1)

    # Resolver client_id: --client-id, env PERSONAL_CLIENT_ID, o modo --personal
    client_id = args.client_id or os.getenv("PERSONAL_CLIENT_ID")
    if not client_id or args.personal:
        client_id = await get_or_create_personal_client_id()
        if args.personal or not args.client_id:
            logger.info("Usando modo personal (cliente: mi_autonomia)")

    agent = AutonomousAgent(
        client_id=client_id,
        execution_mode=args.mode,
        enable_api_discovery=not args.no_api_discovery,
        enable_browser=not args.no_browser,
        enable_desktop_control=not args.no_desktop,
    )

    try:
        async with agent:
            if args.goal:
                await run_single_goal(agent, args.goal, args.max_iterations)
            else:
                await run_interactive_mode(agent)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

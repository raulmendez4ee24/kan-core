#!/usr/bin/env python3
"""
Script de terminal para ejecutar el Agente Autónomo Claw.
Ejecuta completamente en terminal y controla la computadora como un humano.
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Agregar el directorio raíz al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from brain.autonomous_claw_agent import AutonomousClawAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("claw_agent")


def print_banner():
    """Muestra el banner del agente."""
    banner = """
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║        🤖 AGENTE AUTÓNOMO CLAW 🤖                        ║
    ║                                                           ║
    ║  Control total de tu computadora con inteligencia GPT    ║
    ║  Descubrimiento automático de APIs                       ║
    ║  Encriptación automática de credenciales                  ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)


def check_environment():
    """Verifica que el entorno esté configurado correctamente."""
    errors = []
    
    # Verificar variables de entorno críticas
    if not os.getenv("MASTER_KEY") and not os.getenv("DATA_ENCRYPTION_KEY"):
        errors.append("MASTER_KEY o DATA_ENCRYPTION_KEY no configurado (necesario para encriptación)")
    
    if not os.getenv("OPENAI_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        errors.append("OPENAI_API_KEY o GEMINI_API_KEY no configurado (necesario para GPT)")
    
    if not os.getenv("DATABASE_URL"):
        errors.append("DATABASE_URL no configurado")
    
    if errors:
        logger.error("❌ Errores de configuración:")
        for error in errors:
            logger.error(f"  - {error}")
        return False
    
    logger.info("✓ Entorno configurado correctamente")
    return True


async def run_interactive_mode(agent: AutonomousClawAgent):
    """Modo interactivo donde el usuario puede dar comandos."""
    print("\n🎯 Modo Interactivo - Escribe 'exit' para salir\n")
    
    while True:
        try:
            goal = input("Claw> ").strip()
            
            if not goal:
                continue
            
            if goal.lower() in ("exit", "quit", "salir"):
                print("👋 Saliendo...")
                break
            
            if goal.lower() == "help":
                print("""
Comandos disponibles:
  - discover <url>          - Descubre APIs desde una URL
  - browser <url>           - Abre navegador y descubre APIs
  - terminal <command>      - Ejecuta comando y busca APIs
  - goal <descripción>      - Ejecuta objetivo autónomo
  - help                    - Muestra esta ayuda
  - exit                    - Sale del agente
                """)
                continue
            
            # Procesar comandos especiales
            if goal.startswith("discover "):
                url = goal.replace("discover ", "").strip()
                print(f"🔍 Descubriendo APIs desde {url}...")
                result = await agent.discover_apis("url", url=url)
                print(f"✓ Descubiertas {result.get('total_found', 0)} APIs")
                continue
            
            if goal.startswith("browser "):
                url = goal.replace("browser ", "").strip()
                print(f"🌐 Abriendo navegador en {url}...")
                result = await agent.discover_apis("browser", browser_url=url)
                print(f"✓ Descubiertas {result.get('total_found', 0)} APIs")
                continue
            
            if goal.startswith("terminal "):
                command = goal.replace("terminal ", "").strip()
                print(f"💻 Ejecutando comando: {command}...")
                result = await agent.discover_apis("terminal", terminal_command=command)
                print(f"✓ Descubiertas {result.get('total_found', 0)} APIs")
                continue
            
            if goal.startswith("goal "):
                goal_desc = goal.replace("goal ", "").strip()
                print(f"🚀 Ejecutando objetivo autónomo: {goal_desc}")
                result = await agent.run_autonomous_loop(goal_desc, max_iterations=5)
                print(f"✓ Completado: {result.get('status')}")
                print(f"  Iteraciones: {result.get('iterations')}")
                print(f"  APIs descubiertas: {result.get('discovered_apis')}")
                continue
            
            # Comando por defecto: ejecutar como objetivo autónomo
            print(f"🤖 Ejecutando objetivo: {goal}")
            result = await agent.run_autonomous_loop(goal, max_iterations=5)
            print(f"✓ Completado: {result.get('status')}")
            print(f"  Iteraciones: {result.get('iterations')}")
            print(f"  APIs descubiertas: {result.get('discovered_apis')}")
        
        except KeyboardInterrupt:
            print("\n⚠️  Interrumpido por usuario")
            break
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)


async def run_single_goal(agent: AutonomousClawAgent, goal: str, max_iterations: int):
    """Ejecuta un objetivo único y sale."""
    print(f"\n🎯 Objetivo: {goal}\n")
    
    result = await agent.run_autonomous_loop(goal, max_iterations=max_iterations)
    
    print("\n" + "="*60)
    print("📊 RESULTADOS")
    print("="*60)
    print(f"Estado: {result.get('status')}")
    print(f"Iteraciones: {result.get('iterations')}")
    print(f"APIs descubiertas: {result.get('discovered_apis')}")
    print(f"Acciones ejecutadas: {len(result.get('results', []))}")
    
    if result.get('results'):
        print("\nAcciones realizadas:")
        for i, action_result in enumerate(result['results'], 1):
            action = action_result.get('action', 'unknown')
            if 'error' in action_result:
                print(f"  {i}. ❌ {action}: {action_result['error']}")
            else:
                print(f"  {i}. ✓ {action}")


async def main():
    """Función principal."""
    parser = argparse.ArgumentParser(
        description="Agente Autónomo Claw - Control total de tu computadora"
    )
    parser.add_argument(
        "--client-id",
        type=str,
        required=True,
        help="ID del cliente (UUID)",
    )
    parser.add_argument(
        "--goal",
        type=str,
        help="Objetivo a ejecutar (si no se proporciona, modo interactivo)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Máximo de iteraciones (default: 10)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["safe", "balanced", "autonomous"],
        default="autonomous",
        help="Modo de ejecución (default: autonomous)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Deshabilitar navegador",
    )
    parser.add_argument(
        "--no-desktop",
        action="store_true",
        help="Deshabilitar control de escritorio",
    )
    parser.add_argument(
        "--no-api-discovery",
        action="store_true",
        help="Deshabilitar descubrimiento de APIs",
    )
    
    args = parser.parse_args()
    
    print_banner()
    
    if not check_environment():
        sys.exit(1)
    
    # Crear agente
    agent = AutonomousClawAgent(
        client_id=args.client_id,
        execution_mode=args.mode,
        enable_api_discovery=not args.no_api_discovery,
        enable_browser=not args.no_browser,
        enable_desktop_control=not args.no_desktop,
    )
    
    try:
        async with agent:
            if args.goal:
                # Modo de objetivo único
                await run_single_goal(agent, args.goal, args.max_iterations)
            else:
                # Modo interactivo
                await run_interactive_mode(agent)
    
    except KeyboardInterrupt:
        print("\n⚠️  Interrumpido por usuario")
    except Exception as e:
        logger.error(f"Error fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

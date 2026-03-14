#!/bin/bash
# Ejemplo de uso del Agente Claw Autónomo

# Configurar variables de entorno (ajusta según tu configuración)
export MASTER_KEY="tu-clave-maestra-aqui"
export OPENAI_API_KEY="tu-api-key-openai"
export DATABASE_URL="postgresql://user:pass@localhost/db"

# Obtener client_id (puedes usar uno existente o crear uno nuevo)
CLIENT_ID="tu-client-id-uuid"

echo "🚀 Ejecutando Agente Claw..."

# Ejemplo 1: Modo interactivo
python scripts/run_claw_agent.py --client-id "$CLIENT_ID"

# Ejemplo 2: Descubrir APIs desde una URL específica
# python scripts/run_claw_agent.py \
#   --client-id "$CLIENT_ID" \
#   --goal "Descubrir todas las APIs de https://api.github.com"

# Ejemplo 3: Modo seguro con aprobación humana
# python scripts/run_claw_agent.py \
#   --client-id "$CLIENT_ID" \
#   --goal "Descubrir APIs de Stripe" \
#   --mode safe \
#   --max-iterations 5

# Ejemplo 4: Solo descubrimiento de APIs (sin control de escritorio)
# python scripts/run_claw_agent.py \
#   --client-id "$CLIENT_ID" \
#   --goal "Descubrir APIs desde navegador" \
#   --no-desktop

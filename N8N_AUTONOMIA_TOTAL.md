# N8N Autonomia Total (Web Research + Ejecucion en PC)

Este flujo resuelve el problema de "no busca bien en Google" moviendo la busqueda a proveedores robustos y obligando validacion antes de ejecutar acciones.

## 1) Que se agrego

- Endpoint backend nuevo: `POST /desktop-autonomy/research-run`
- Workflow n8n importable: `n8n/autonomia_total_web_research.json`

## 2) Como funciona

1. Recibe objetivo (goal).
2. Hace busqueda en 3 variantes de query.
3. Usa Tavily y fallback a Brave (si hace falta).
4. Valida minimo de fuentes unicas (`min_sources`).
5. Si no alcanza evidencia y `require_verified_sources=true`, bloquea ejecucion.
6. Si valida, lanza el loop de autonomia de escritorio con el contexto investigado.

Capas nuevas:

- `self-healing`: reintenta automaticamente con estrategia distinta cuando falla un paso.
- `quality gate`: calcula score de calidad y puede bloquear ejecucion si no cumple umbral.
- `dynamic risk`: ajusta aprobacion automatica segun riesgo del objetivo/contexto.

## 3) Variables de entorno (backend)

Minimas:

```bash
export ENABLE_DESKTOP_AUTONOMY_LOOP=true
```

Recomendadas para busqueda confiable:

```bash
export TAVILY_API_KEY="..."
export BRAVE_SEARCH_API_KEY="..."
```

Opcionales (timeouts/reintentos):

```bash
export SEARCH_TIMEOUT_SECONDS=12
export SEARCH_RETRIES=3
export SEARCH_BACKOFF_BASE=0.5
export SEARCH_BACKOFF_MAX=4.0
```

## 4) Variables de entorno (n8n)

```bash
export KAN_BASE_URL="http://localhost:8000"
export KAN_CLIENT_ID="tu-client-id"
export KAN_CLIENT_TOKEN="tu-client-token"
```

## 5) Importar workflow en n8n

1. Ir a n8n -> Workflows -> Import from file.
2. Seleccionar `n8n/autonomia_total_web_research.json`.
3. Activar el workflow.

Webhook path por defecto:

`POST /webhook/autonomia-total-run`

## 6) Prueba rapida

```bash
curl -X POST "http://localhost:5678/webhook/autonomia-total-run" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Investigar como automatizar reportes semanales en mi CRM y ejecutarlo",
    "execution_mode": "autonomous",
    "max_steps": 8,
    "min_sources": 5,
    "enable_self_healing": true,
    "self_heal_max_attempts": 2,
    "quality_min_score": 0.65,
    "enforce_quality_gate": true,
    "dynamic_risk_controls": true,
    "require_verified_sources": true,
    "include_brave_fallback": true
  }'
```

## 7) Notas

- Si no defines `TAVILY_API_KEY` y `BRAVE_SEARCH_API_KEY`, el sistema puede bloquear ejecucion por falta de evidencia (si `require_verified_sources=true`).
- Para pruebas iniciales puedes usar:
  - `execution_mode="safe"`
  - `dry_run=true`
- `tools.n8n_client.send_to_n8n()` fue retirado; usa `tools.n8n_client.send_to_n8n_with_response()` para distinguir dispatch vs ejecucion confirmada.

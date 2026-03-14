# CLAUDE.md

Patrones prohibidos basados en bugs reales ya encontrados en este repo.

## DB / ORM

### No insertar `AuditLog` vía ORM mapper directo

Patrón prohibido:

- crear `AuditLog(...)` y hacer `session.add(...)` en flujos de eval o seeding donde el mapper completo no esté garantizado

Riesgo real:

- puede romper por referencias de FK del mapper (`users`) aunque solo quieras sembrar un registro simple

Patrón correcto:

- usar inserción directa de tabla:

```python
from sqlalchemy import insert

await session.execute(
    insert(AuditLog.__table__).values(...)
)
```

## Activación Anthropic

### No activar Anthropic en todos los casos

Patrón prohibido:

- llamar el path agentic para casos rutinarios o de bajo riesgo

Riesgo real:

- aumenta latencia
- sube costo de tokens
- introduce fallos innecesarios en casos que el fallback heurístico ya resolvía bien

Patrón correcto:

- activar Anthropic solo si:
  - `ENABLE_ANTHROPIC_AGENTIC_RUNTIME=true`
  - y `risk_score >= 0.7`

Todo caso rutinario debe usar fallback heurístico directo.

## CRM

### No asumir que `CrmLead` tiene `external_id`

Patrón prohibido:

- usar `CrmLead.external_id` sin verificar primero que el atributo exista realmente en el modelo actual

Riesgo real:

- rompe `upsert_lead()` y cualquier rollback que dependa de ese path

Patrón correcto:

- verificar el modelo antes de usar atributos opcionales
- si el atributo no existe, usar una ruta alternativa compatible con el esquema real

## Benchmark / Eval Harness

### No correr `eval_harness` sin `N8N_MOCK_MODE=true`

Patrón prohibido:

- ejecutar el benchmark contra el webhook real de `n8n`

Riesgo real:

- produce `404` y ruido de logs
- ensucia la salida del benchmark
- no aporta señal útil para comparar runtimes

Patrón correcto:

- correr siempre:

```bash
N8N_MOCK_MODE=true python3 tests/evals/eval_harness.py
```

## Registro de dominios

### No agregar un dominio nuevo sin registrarlo en el runtime

Patrón prohibido:

- crear fixtures, skills o agentes para un dominio nuevo sin conectarlo en los puntos de routing

Riesgo real:

- el dominio degrada a `auto` o cae en rutas equivocadas
- el benchmark devuelve `runtime_error` aunque el agente ya exista

Patrón correcto:

- registrar cada dominio nuevo al menos en:
  - `brain/autonomous_case_manager.py`
  - `brain/agents/master_brain_router.py`

## Anthropic API

### No usar `betas=["interleaved-thinking"]` en esta cuenta

Patrón prohibido:

- enviar `betas=["interleaved-thinking-..."]` en requests de Anthropic para esta cuenta

Riesgo real:

- la API responde `400 invalid_request_error`
- el path agentic falla y luego parece que “Anthropic no hizo nada”

Patrón correcto:

- usar requests limpias con:
  - `model`
  - `max_tokens`
  - `messages`
  - `tools` (si aplica)

- no enviar `betas=["interleaved-thinking"]` en esta cuenta hasta confirmar soporte real

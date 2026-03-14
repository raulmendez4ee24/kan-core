# API REST — Desktop Autonomy

Ejecuta el mismo flujo de autonomía en tu Mac (objetivo → plan → ejecución en runner) sin Telegram.

## Base URL

- Local: `http://localhost:8000`
- Cloud: `https://tu-app.up.railway.app` (o la URL de tu backend)

## Autenticación

Todos los endpoints requieren:

- **`X-Client-Id`**: UUID de tu organización/cliente (el mismo que usa el runner).
- **`X-Client-Token`**: Token del cliente. Se configura en el servidor vía:
  - Variable de entorno `CLIENT_TOKENS_JSON`: `{"<client-uuid>": "<token>"}`.
  - O en base de datos: cliente con `api_key` (encriptado).

Generar un token para pruebas:

```bash
openssl rand -hex 24
```

En el servidor (env o DB) asocia ese token al `client_id`.

---

## POST /desktop-autonomy/run

Ejecuta un objetivo en la Mac del runner conectado a ese `client_id`.

### Request

**Headers**

| Header           | Obligatorio | Descripción        |
|------------------|------------|--------------------|
| X-Client-Id      | Sí         | UUID del cliente   |
| X-Client-Token   | Sí         | Token del cliente  |
| Content-Type     | Sí         | application/json   |

**Body (JSON)**

| Campo                  | Tipo    | Default      | Descripción |
|------------------------|---------|-------------|-------------|
| goal                   | string  | —           | Objetivo (ej. "Abre Chrome y ve a google.com"). Mín 3, máx 4000 caracteres. |
| max_steps              | int     | 6           | Máximo de pasos del loop (1–30). |
| execution_mode         | string  | "safe"      | "safe" \| "balanced" \| "autonomous". Para autonomía total usa "autonomous". |
| wait_for_results       | bool    | true        | Si true, espera resultado de cada acción en el runner. |
| step_timeout_seconds   | float   | 25.0        | Timeout por paso (5–120). |
| dry_run                | bool    | false       | Si true, solo planifica, no ejecuta. |
| context                | object  | {}          | Contexto opcional (source, run_id, etc.). |

Mínimo para probar: `{"goal": "Abre Chrome"}`.

Para autonomía total sin confirmaciones:  
`{"goal": "Abre Chrome", "execution_mode": "autonomous"}`.

### Response (200)

```json
{
  "run_id": "abc123",
  "goal": "Abre Chrome",
  "status": "completed",
  "summary": "1 actions executed. 1 auto-approved, 1 executed ok...",
  "steps": [...],
  "error": null,
  "created_at": "...",
  "updated_at": "..."
}
```

**Status posibles**

- `completed` — Objetivo ejecutado (al menos un paso ok).
- `failed` — Falló la ejecución o la cola.
- `blocked` — Requiere aprobación humana o algo bloqueó.
- `dry_run` — Solo plan (dry_run: true).
- `runner_offline` — No hay runner conectado para ese client_id. El campo `summary` trae instrucciones para encender el runner.

### Ejemplo cURL

```bash
curl -X POST "https://tu-backend.up.railway.app/desktop-autonomy/run" \
  -H "Content-Type: application/json" \
  -H "X-Client-Id: 11111111-1111-1111-1111-111111111111" \
  -H "X-Client-Token: tu-token-aqui" \
  -d '{"goal": "Abre Chrome", "execution_mode": "autonomous", "max_steps": 8}'
```

### Ejemplo Python (requests)

```python
import requests

url = "https://tu-backend.up.railway.app/desktop-autonomy/run"
headers = {
    "Content-Type": "application/json",
    "X-Client-Id": "11111111-1111-1111-1111-111111111111",
    "X-Client-Token": "tu-token-aqui",
}
payload = {
    "goal": "Abre Chrome y ve a google.com",
    "execution_mode": "autonomous",
    "max_steps": 8,
    "wait_for_results": True,
}
r = requests.post(url, json=payload, headers=headers, timeout=120)
data = r.json()
print("status:", data["status"])
print("summary:", data["summary"])
if data["status"] == "runner_offline":
    print(data["summary"])  # instrucciones para conectar el runner
```

### Ejemplo JavaScript (fetch)

```javascript
const res = await fetch("https://tu-backend.up.railway.app/desktop-autonomy/run", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Client-Id": "11111111-1111-1111-1111-111111111111",
    "X-Client-Token": "tu-token-aqui",
  },
  body: JSON.stringify({
    goal: "Abre Chrome",
    execution_mode: "autonomous",
    max_steps: 8,
  }),
});
const data = await res.json();
console.log(data.status, data.summary);
```

---

## GET /desktop-autonomy/runs/{run_id}

Consulta el resultado de un run ya lanzado (mismo `run_id` que devolvió POST /run).

**Headers:** mismos que arriba (`X-Client-Id`, `X-Client-Token`).

**Response (200):** mismo esquema que el body de POST /run.

**404:** run_id no encontrado o no corresponde a tu cliente.

---

## Errores

- **401 Unauthorized:** Falta o invalidez de `X-Client-Id` / `X-Client-Token`.
- **404:** Feature deshabilitado (`ENABLE_DESKTOP_AUTONOMY_LOOP=false`) o run no encontrado.
- **400:** Body inválido (p. ej. `goal` demasiado corto).

Cuando el runner no está conectado, la API responde **200** con `status: "runner_offline"` y en `summary` las instrucciones para conectar el runner (no es 4xx/5xx).

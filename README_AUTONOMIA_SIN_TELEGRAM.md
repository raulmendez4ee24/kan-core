# Autonomía total sin Telegram

Mismas capacidades (runner + desktop loop + MasterBrain) se pueden usar **sin Telegram** por cualquiera de estas vías.

---

## 1. API REST (recomendado)

**POST `/desktop-autonomy/run`** — Ejecuta el loop de autonomía en tu Mac (mismo flujo que Telegram).

- **Auth:** header `X-Client-Id` + `X-Client-Token` (o Bearer del login de consola).
- **Body:** `{ "goal": "Abre Chrome y ve a google.com", "max_steps": 8, "wait_for_results": true }`
- **Respuesta:** `run_id`, `status`, `summary`, `steps`. Si no hay runner: `status: "runner_offline"` y `summary` con instrucciones.

Documentación detallada y ejemplos (curl, Python, JS): **[docs/API_DESKTOP_AUTONOMY_REST.md](docs/API_DESKTOP_AUTONOMY_REST.md)**

Script para probar desde terminal contra la API:

```bash
export BACKEND_URL="https://tu-backend.up.railway.app"
export CLIENT_ID="<tu-client-uuid>"
export CLIENT_TOKEN="<tu-token>"
python scripts/api_run_goal.py "Abre Chrome" --autonomous
```

Ejemplo con curl (sustituye URL y token):

```bash
curl -X POST "https://tu-backend.up.railway.app/desktop-autonomy/run" \
  -H "Content-Type: application/json" \
  -H "X-Client-Id: <tu-client-uuid>" \
  -H "X-Client-Token: <tu-client-token>" \
  -d '{"goal": "Abre Chrome", "max_steps": 4}'
```

Desde **n8n**, **Zapier**, **cron** o cualquier servicio: llama a esta URL con el mismo body y auth.

---

## 2. Chat API (mismo flujo que la web/Telegram)

**POST `/chat/message`** — Un mensaje de texto puede disparar una acción de escritorio si el contenido lo indica.

- **Auth:** igual que arriba.
- **Body:** `{ "session_id": "cli-1", "message": "Abre Safari y busca kan logic", "channel": "api" }`
- Si el backend decide que es acción de desktop y hay runner, ejecuta en tu Mac. Si no hay runner, responde con las instrucciones de runner offline.

---

## 3. CLI local (sin servidor de chat)

Puedes ejecutar un objetivo desde la terminal usando el mismo motor (AgentBridge + MasterBrain) contra tu backend y runner:

```bash
# Con BACKEND_URL y JARVIS_CLIENT_ID (y opcional X-Client-Token si tu API lo exige)
python scripts/smoke_desktop_telegram.py   # varios objetivos de prueba

# O un solo objetivo (ver script abajo)
python scripts/cli_run_goal.py "Abre Chrome"
```

El runner debe estar conectado en tu Mac; el script usa `AgentBridge` como Telegram pero sin enviar nada por Telegram.

---

## 4. Voz local (Jarvis Listener)

**`jarvis_listener.py`** — En tu Mac, con micrófono y permisos:

- Dices la palabra de activación (p. ej. “K’an”) y luego el comando: “Abre Chrome”, “Busca en Google X”.
- El listener usa el mismo `AgentBridge.run_goal()` que Telegram; la petición va a MasterBrain y, si toca desktop, al runner de esa máquina.

No necesitas Telegram ni móvil; todo es local + tu backend (si está en cloud, el runner en la Mac se conecta por WebSocket como en el flujo con Telegram).

---

## 5. Web / consola

Si tienes una consola web (React, Vue, etc.) o una página interna:

- Añade un formulario “Objetivo” que haga **POST `/desktop-autonomy/run`** (o **POST `/chat/message`**) con el texto del objetivo.
- Misma autenticación que el resto de la consola. El resultado (run_id, status, summary) se muestra en la UI.

---

## 6. Resumen

| Vía           | Cómo disparas              | Runner / backend                    |
|---------------|----------------------------|-------------------------------------|
| **Telegram**  | Mensaje en el bot          | Backend cloud + runner en Mac       |
| **API REST**  | POST desde cron/n8n/script | Mismo                               |
| **Chat API**  | POST /chat/message         | Mismo                               |
| **CLI**       | `python scripts/cli_run_goal.py "objetivo"` | Mismo (runner en Mac)      |
| **Voz**       | Jarvis Listener en Mac     | Mismo (runner en la misma Mac)      |
| **Web/consola** | Formulario → POST        | Mismo                               |

En todos los casos, la **autonomía total** (AUTONOMY_LEVEL=high, ejecución real en la Mac) depende de:

1. Backend con `ENABLE_DESKTOP_AGENT=true` y token de runner configurado.
2. Runner local corriendo en la Mac (`python -m runner.local_runner`).
3. Mismo `client_id` / organización en la llamada y en el runner.

Si no hay runner, la API responde de inmediato con `runner_offline` e instrucciones; no hace falta Telegram para eso.

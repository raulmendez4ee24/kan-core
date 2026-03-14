# Run Jarvis on Mac (autonomía total desde Telegram)

Para que las instrucciones que envías por Telegram ("abre Chrome", "entra a X", "haz click", "escribe", etc.) se ejecuten **realmente** en tu Mac, necesitas:

1. **Permisos macOS**
2. **Backend en la nube** (p. ej. Railway) con `ENABLE_DESKTOP_AGENT=true` y `RUNNER_TOKEN` (o `RUNNER_TOKENS_JSON`) configurado.
3. **Runner local** corriendo en tu Mac, conectado por WebSocket al backend.

---

## 1. Permisos en macOS

- **Accesibilidad (Accessibility):** Sistema prefiere → Seguridad y privacidad → Privacidad → Accesibilidad → añade Terminal (y/o la app que ejecute el runner) y márcala.
- **Grabación de pantalla (Screen Recording):** mismo menú → Grabación de pantalla → añade la misma app.

Sin estos permisos el runner no puede controlar el teclado/ratón ni capturar pantalla.

---

## 2. Variables en el backend (Railway / cloud)

En el proyecto desplegado (Railway, Heroku, etc.) configura al menos:

- `ENABLE_DESKTOP_AGENT=true`
- `JARVIS_CLIENT_ID` o `TELEGRAM_DEFAULT_CLIENT_ID` = UUID de tu organización/cliente.
- `RUNNER_TOKEN` = un secreto que solo conoces tú y el runner (ej. generado con `openssl rand -hex 24`).

O bien varios clientes:

- `RUNNER_TOKENS_JSON='{"<client-uuid-1>":"<token1>","<client-uuid-2>":"<token2>"}'`

Para que en cloud **toda** acción de escritorio exija runner conectado (y no se quede colgada), el backend detecta Railway/Heroku/Kubernetes y exige runner online. Si no hay runner, responderá **runner_offline** al instante con instrucciones.

---

## 3. Runner local en tu Mac

En la Mac donde quieres que se ejecuten las acciones:

```bash
# Clona el repo (si aún no lo tienes) y entra al directorio
cd /ruta/al/chatbotn8n

# Crea un venv y dependencias (incluye websockets, etc.)
python -m venv .venv
source .venv/bin/activate  # En Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Configura (sustituye por tu URL y IDs)
export WS_URL="wss://tu-app.up.railway.app/desktop-agent/ws"
export CLIENT_ID="<tu-jarvis-client-id-uuid>"
export RUNNER_TOKEN="<el-mismo-token-que-en-el-backend>"

# Opcional
export RUNNER_NAME="mi-mac"
export RUNNER_HEARTBEAT_SECONDS=5

# Ejecuta el runner (mantén esta terminal abierta)
python -m runner.local_runner
```

Si el runner se cae, se reconecta solo con backoff. Cuando veas en logs "Local runner connected", ya está listo.

---

## 4. Comandos Telegram útiles

- `/runner` — Estado del runner (conectado, client_id, ws_url) e instrucciones si está offline.
- `/doctor` — Diagnóstico general (OpenAI, n8n, runner, DB).
- `/status` — Enlace y modo; `/status <run_id>` estado de un run.
- `/runs` — Lista de runs recientes.
- `/stop <run_id>` — Marca un run como cancelado.

---

## 5. Autonomía total (AUTONOMY_LEVEL=high)

Con `AUTONOMY_LEVEL=high` en el backend:

- Las acciones de escritorio se ejecutan sin pedir confirmación (incluye riesgo alto/crítico).
- El runner debe estar conectado; si no, recibirás **runner_offline** y el mensaje con los pasos para encenderlo.

---

## 6. Si algo falla

- **403 en WebSocket:** Revisa que `RUNNER_TOKEN` (o `RUNNER_TOKENS_JSON`) en el backend coincida con el `RUNNER_TOKEN` del runner, y que `CLIENT_ID` sea el mismo que `JARVIS_CLIENT_ID` (o el que uses) en el backend.
- **Runner no conecta:** Comprueba que `WS_URL` sea la URL pública del backend (ej. `wss://...`) y que el puerto/firewall permita WebSocket.
- **Acciones no se ejecutan:** Usa `/runner` y `/doctor` para ver si el runner aparece como conectado; si no, sigue las instrucciones que muestra el bot al responder runner_offline.

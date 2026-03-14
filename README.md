# K'an Core

## Tests

Run the test suite from repo root:

```bash
pytest -q
```

## Packaging (editable install)

Install in editable mode so imports like `brain.*` work as a package:

```bash
pip install -e .
```

## Local Runner (Telegram -> Cloud -> Tu Mac)

Cloud (Railway) orquesta y el runner local ejecuta desktop/browser en tu Mac.

```bash
export RUNNER_MODE=remote
export WS_URL="wss://<tu-dominio-railway>/desktop-agent/ws"
export CLIENT_ID="<tu-client-id-uuid>"
export RUNNER_TOKEN="<token-runner>"
python -m runner.local_runner
```

Permisos macOS requeridos para el proceso local:
- `Accessibility`
- `Screen Recording`

## n8n Client

- `tools.n8n_client.send_to_n8n()` fue retirado.
- Usa `tools.n8n_client.send_to_n8n_with_response()`, que expone `dispatched`, `pending`, `confirmed` y `execution_status`.

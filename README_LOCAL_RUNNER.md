# Local Runner (Cloud -> Mac)

El backend en Railway decide/orquesta y el runner local ejecuta acciones reales en tu Mac.

## Requisitos macOS

- `Accessibility` habilitado para tu terminal/proceso Python
- `Screen Recording` habilitado para tu terminal/proceso Python

## Variables (runner local)

```bash
export WS_URL="wss://<tu-dominio-railway>/desktop-agent/ws"
export CLIENT_ID="<uuid-del-cliente>"
export RUNNER_TOKEN="<token-runner>"
export RUNNER_MODE=remote
```

Opcional multi-tenant en cloud:

```bash
export RUNNER_TOKENS_JSON='{"<client-id-1>":"<token-1>","<client-id-2>":"<token-2>"}'
```

## Arranque

```bash
python -m runner.local_runner
```

## Arranque automático en macOS (launchd)

```bash
chmod +x install_local_runner.sh
./install_local_runner.sh install
./install_local_runner.sh status
```

Logs:
- `.run/local_runner/logs/local_runner.out.log`
- `.run/local_runner/logs/local_runner.err.log`

## Ver estado de runners

```bash
curl -H "X-Client-Id: <CLIENT_ID>" -H "X-Client-Token: <CLIENT_TOKEN>" \
  https://<tu-dominio-railway>/console/runners
```

## Comportamiento cuando está offline

Si `RUNNER_MODE=remote` y no hay runner conectado, las acciones desktop retornan `runner_offline` sin quedar colgadas.

# Runtime Telegram Mode

## Variables nuevas

- `TELEGRAM_MODE=polling|webhook|off`
- `TELEGRAM_WEBHOOK_URL=https://.../webhooks/telegram`
- `STRICT_ROUTE_REGISTRY=true|false`

## Ejemplos

### Polling local (sin webhook)

```bash
TELEGRAM_MODE=polling
```

### Webhook

```bash
TELEGRAM_MODE=webhook
TELEGRAM_WEBHOOK_URL=https://my-domain/webhooks/telegram
```

### Desactivado

```bash
TELEGRAM_MODE=off
```

## Notas

- `AUTONOMY_LEVEL=high` no cambia: sigue ejecutando sin confirmaciones.
- `ENABLE_TELEGRAM_POLLING` queda legacy/deprecado; `TELEGRAM_MODE` es la fuente de verdad.
- Si `TELEGRAM_MODE=webhook` y falta `TELEGRAM_WEBHOOK_URL`, el runtime falla de forma explícita.


# DB Migrations (Alembic)

Este proyecto usa Alembic para versionar schema y evitar errores tipo `column does not exist`.

## Local

- Aplicar migraciones:
```bash
alembic upgrade head
```

- Generar nueva migración:
```bash
alembic revision --autogenerate -m "describe change"
```

## Producción (Railway)

- Comando recomendado de arranque (ya aplicado en `Procfile`):
```bash
python -m tools.db_migrate && uvicorn main:app --host 0.0.0.0 --port ${PORT}
```

- Alternativa: ejecutar en startup de app con:
```bash
RUN_DB_MIGRATIONS=true
```
Esto corre `alembic upgrade head` al iniciar FastAPI.

## Script utilitario

También puedes ejecutar:
```bash
python -m tools.db_migrate
```

## Ejemplo puntual del bug actual

```bash
alembic revision --autogenerate -m "add client_id to objective_runs"
alembic upgrade head
```

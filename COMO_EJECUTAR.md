# Cómo Ejecutar `claw` en Terminal

## 1) Instalar el comando

Desde la raíz del proyecto:

```bash
chmod +x scripts/claw scripts/install_claw_command.sh
./scripts/install_claw_command.sh
source ~/.zshrc
```

Después de eso ya puedes usar:

```bash
claw --help
```

## 2) Configurar OpenAI + Base de datos

Este proyecto usa API key (no login interactivo de ChatGPT).
En tu `.env` (raíz del proyecto):

```bash
MASTER_KEY="genera-una-clave-segura"
OPENAI_API_KEY="sk-..."
DATABASE_URL="postgresql+asyncpg://usuario:password@localhost:5432/nombre_db"
```

Puedes generar `MASTER_KEY` así:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 3) Ejecutar

Modo personal (recomendado):

```bash
claw
```

Objetivo único:

```bash
claw --goal "descubre APIs de https://api.github.com" --max-iterations 5
```

Chequeo rápido:

```bash
claw doctor --skip-db
```

## 4) Si algo falla

```bash
echo "$MASTER_KEY"
echo "$OPENAI_API_KEY"
echo "$DATABASE_URL"
```

Si alguno sale vacío, falta configurarlo.

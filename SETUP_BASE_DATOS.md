# Configurar Base de Datos para Tu Autonomía

## Si no tienes PostgreSQL instalado (`psql` no encontrado)

No hace falta instalar nada en tu Mac. Usa una base de datos en la nube gratis.

---

## Opción recomendada: Supabase (gratis, sin instalar nada)

1. Entra a **https://supabase.com** e inicia sesión (o regístrate).
2. Clic en **New project**.
3. Pon nombre al proyecto, contraseña para la base de datos y región; luego **Create new project**.
4. Cuando esté listo, ve a **Project Settings** (icono de engrane) → **Database**.
5. En **Connection string** elige **URI**.
6. Copia la URI. Se verá algo como:
   ```text
   postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
   ```
7. Cámbiala para usar el driver async: **sustituye** `postgresql://` por `postgresql+asyncpg://` al inicio.
8. En tu terminal (o en un archivo `.env`):
   ```bash
   export DATABASE_URL="postgresql+asyncpg://postgres.[PROJECT-REF]:TU_PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres"
   ```
   (Pon tu contraseña y la URI completa que te dio Supabase.)

Luego ejecuta las migraciones (una vez) y Tu Autonomía:

```bash
cd /Users/raulaldairmendezalvarez/Documents/kan-core
alembic upgrade head
python3 scripts/run_autonomy.py --personal
```

---

## Otra opción: Neon (gratis)

1. Entra a **https://neon.tech** y crea una cuenta.
2. Crea un proyecto y copia la **connection string**.
3. Si viene como `postgresql://...`, cámbiala a `postgresql+asyncpg://...`.
4. Configura:
   ```bash
   export DATABASE_URL="postgresql+asyncpg://..."
   ```

---

## Si más adelante quieres PostgreSQL en tu Mac

```bash
brew install postgresql@15
brew services start postgresql@15
# Añade psql al PATH si hace falta:
echo 'export PATH="/opt/homebrew/opt/postgresql@15/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
psql postgres
```

---

## Formato de DATABASE_URL

```text
postgresql+asyncpg://USUARIO:PASSWORD@HOST:PUERTO/NOMBRE_DB
```

La parte **+asyncpg** es necesaria para que el proyecto se conecte bien.

# 🤖 Agente Autónomo Claw

`AutonomousClawAgent` ya no mantiene una implementación separada. Hoy es un alias de compatibilidad hacia `brain.autonomous_agent.AutonomousAgent`, para no romper imports ni scripts existentes.

La ejecución real vive en `brain/autonomous_agent.py`; el nombre "Claw" se conserva solo como capa legacy de compatibilidad.

## 🎯 Características

- ✅ **Control Total de la Computadora**: Controla el escritorio, navegador, y aplicaciones como un humano
- ✅ **Descubrimiento Automático de APIs**: Encuentra APIs desde URLs, navegador, y código fuente
- ✅ **Encriptación Automática**: Encripta y almacena automáticamente todas las credenciales descubiertas
- ✅ **Inteligencia GPT**: Usa GPT para tomar decisiones inteligentes y planificar acciones
- ✅ **Ejecución en Terminal**: Se ejecuta completamente desde la terminal
- ✅ **Gestión de Permisos**: Verifica y solicita permisos del sistema automáticamente

## 📋 Requisitos Previos

1. **Variables de Entorno**:
   ```bash
   export MASTER_KEY="tu-clave-maestra-para-encriptacion"
   export OPENAI_API_KEY="tu-api-key-de-openai"  # o GEMINI_API_KEY
   export DATABASE_URL="postgresql://user:pass@localhost/db"
   ```

2. **Dependencias Python**:
   ```bash
   pip install httpx sqlalchemy pydantic fastapi
   ```

3. **Permisos del Sistema** (macOS):
   - Accesibilidad: Preferencias del Sistema > Seguridad y Privacidad > Accesibilidad
   - Grabación de Pantalla: Preferencias del Sistema > Seguridad y Privacidad > Grabación de Pantalla

## 🚀 Uso

### Modo Interactivo

```bash
python scripts/run_claw_agent.py --client-id "tu-client-id-uuid"
```

Comandos disponibles en modo interactivo:
- `discover <url>` - Descubre APIs desde una URL
- `browser <url>` - Abre navegador y descubre APIs
- `terminal <command>` - Ejecuta comando y busca APIs
- `goal <descripción>` - Ejecuta objetivo autónomo
- `help` - Muestra ayuda
- `exit` - Sale del agente

### Modo de Objetivo Único

```bash
python scripts/run_claw_agent.py \
  --client-id "tu-client-id-uuid" \
  --goal "Descubrir todas las APIs de https://api.ejemplo.com" \
  --max-iterations 10 \
  --mode autonomous
```

### Opciones de Línea de Comandos

- `--client-id` (requerido): UUID del cliente
- `--goal`: Objetivo a ejecutar (si no se proporciona, modo interactivo)
- `--max-iterations`: Máximo de iteraciones (default: 10)
- `--mode`: Modo de ejecución (`safe`, `balanced`, `autonomous`)
- `--no-browser`: Deshabilitar navegador
- `--no-desktop`: Deshabilitar control de escritorio
- `--no-api-discovery`: Deshabilitar descubrimiento de APIs

## 📖 Ejemplos de Uso

### Ejemplo 1: Descubrir APIs desde una URL

```bash
python scripts/run_claw_agent.py --client-id "tu-id"
# En modo interactivo:
Claw> discover https://api.github.com
```

### Ejemplo 2: Descubrir APIs desde Navegador

```bash
Claw> browser https://developers.stripe.com/docs/api
```

### Ejemplo 3: Descubrir APIs desde Código Fuente

```bash
Claw> terminal grep -r "api\.example\.com" /ruta/al/codigo
```

### Ejemplo 4: Objetivo Autónomo Completo

```bash
Claw> goal Descubrir todas las APIs de Stripe, crear integraciones, y probarlas
```

## 🔐 Seguridad

- Todas las credenciales descubiertas se encriptan automáticamente usando `MASTER_KEY`
- Las APIs se almacenan en la base de datos con encriptación
- El agente respeta los permisos del sistema
- Modo `safe` requiere aprobación humana para acciones de alto riesgo

## 🏗️ Arquitectura

```
brain/
├── autonomous_claw_agent.py    # Alias legacy -> AutonomousAgent
├── autonomous_agent.py         # Runtime real de autonomía
├── api_discovery_engine.py     # Motor de descubrimiento de APIs
└── system_permissions.py       # Gestión de permisos del sistema

scripts/
└── run_claw_agent.py           # Script de terminal para ejecutar
```

## 🔧 Configuración Avanzada

### Modos de Ejecución

- **safe**: Requiere aprobación humana para todas las acciones
- **balanced**: Auto-aprueba acciones de bajo riesgo
- **autonomous**: Auto-aprueba todas las acciones permitidas por políticas

### Variables de Entorno Adicionales

```bash
# Configuración de GPT
export OPENAI_API_BASE="https://api.openai.com/v1"
export OPENAI_TIMEOUT=20
export OPENAI_RETRIES=3

# Configuración de base de datos
export DATABASE_URL="postgresql://user:pass@localhost/db"

# Configuración de encriptación
export MASTER_KEY="tu-clave-maestra"
```

## 🐛 Troubleshooting

### Error: "MASTER_KEY no configurado"
Solución: Configura `MASTER_KEY` o `DATA_ENCRYPTION_KEY` en variables de entorno

### Error: "OPENAI_API_KEY no configurado"
Solución: Configura `OPENAI_API_KEY` o `GEMINI_API_KEY`

### Error: "Permisos de Accesibilidad no concedidos" (macOS)
Solución: Ve a Preferencias del Sistema > Seguridad y Privacidad > Accesibilidad y agrega Terminal

### Error: "DISPLAY no configurado" (Linux)
Solución: `export DISPLAY=:0`

## 📝 Notas

- `AutonomousClawAgent` existe por compatibilidad; el runtime real es `AutonomousAgent`
- El agente es completamente autónomo y puede realizar acciones en tu computadora
- Usa el modo `safe` para pruebas iniciales
- Todas las APIs descubiertas se guardan automáticamente en la base de datos
- Las credenciales se encriptan antes de almacenarse

## 🤝 Contribuir

Para mejorar el agente:
1. Mejora el motor de descubrimiento de APIs
2. Agrega más fuentes de descubrimiento
3. Mejora la integración con GPT
4. Agrega más acciones de control de escritorio

## 📄 Licencia

Ver LICENSE en el directorio raíz del proyecto.

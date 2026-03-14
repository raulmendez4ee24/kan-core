# WhatsApp QR Bridge (modo rapido)

Este modo conecta WhatsApp Web por QR y reenvia mensajes a `KAN /chat/message`.

## Requisitos
- Node.js 18+
- Backend KAN corriendo (`http://localhost:8000`)
- Variables en `.env`:
  - `KAN_CLIENT_ID`
  - `KAN_CLIENT_TOKEN`
  - `KAN_BASE_URL` (opcional, default `http://127.0.0.1:8000`)

## Opcionales `.env`
- `WA_ALLOWED_NUMBERS=5215512345678,5215587654321` (lista permitida)
- `WA_PROCESS_FROM_ME=true` (procesar mensajes enviados por ti mismo desde linked device)
- `WA_HEADLESS=true`
- `WA_DATA_PATH=.run/wa_qr_runtime`
- `WA_CLIENT_ID=kan-whatsapp-qr-v2`
- `WA_CHROME_EXECUTABLE_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`

## Arranque
```bash
cd ~/Documents/chatbotn8n
kan wa up
```

Si WhatsApp bloquea QR temporalmente, usa vinculación por número:
```bash
kan wa pair 5215512345678
```
Ese comando corre en foreground y muestra `CODIGO DE VINCULACION` en la terminal.
Luego en tu celular: `Dispositivos vinculados -> Vincular con número de teléfono` y capturas ese código.

Ver QR/logs:
```bash
kan wa logs
```

Detener:
```bash
kan wa down
```

Estado:
```bash
kan wa status
```

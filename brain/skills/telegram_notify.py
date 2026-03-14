SKILL = {
    "name": "telegram_notify",
    "trigger_keywords": ["telegram", "notificar", "notificacion", "mensaje", "bot", "aviso"],
    "integrations": ["telegram"],
    "description": "Envía mensajes y notificaciones a chats o grupos de Telegram.",
    "instructions": (
        "Para enviar un mensaje a Telegram usa action_type=integration, integration=telegram. "
        "Acción: send_message. "
        "arguments debe incluir: chat_id (ID numérico del chat o grupo), text (mensaje). "
        "Opcional: parse_mode=HTML o Markdown para formato."
    ),
    "example_action": {
        "contract_version": "1.0",
        "action_type": "integration",
        "action": "send_message",
        "integration": "telegram",
        "arguments": {"chat_id": "-1001234567890", "text": "Alerta: inventario bajo en producto SKU-001."},
        "rationale": "Notificar al grupo de operaciones sobre inventario bajo.",
    },
}

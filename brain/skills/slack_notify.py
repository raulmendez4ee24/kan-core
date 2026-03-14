SKILL = {
    "name": "slack_notify",
    "trigger_keywords": ["slack", "notificar", "notificacion", "alerta", "alert", "notifcar", "avisar"],
    "integrations": ["slack"],
    "description": "Envía notificaciones y alertas a canales de Slack.",
    "instructions": (
        "Para enviar una notificación a Slack usa action_type=integration, integration=slack. "
        "Acción: send_message. "
        "arguments debe incluir: channel (ID o nombre del canal, ej: C1234 o #general), message (texto). "
        "Opcional: thread_ts para responder en hilo."
    ),
    "example_action": {
        "contract_version": "1.0",
        "action_type": "integration",
        "action": "send_message",
        "integration": "slack",
        "arguments": {"channel": "#ventas", "message": "Nueva venta de $5,000 MXN cerrada."},
        "rationale": "Notificar al equipo de ventas sobre el nuevo cierre.",
    },
}

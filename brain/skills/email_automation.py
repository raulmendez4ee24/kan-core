SKILL = {
    "name": "email_automation",
    "trigger_keywords": ["email", "correo", "enviar", "newsletter", "campaña", "campaign", "mailchimp", "sendgrid"],
    "integrations": ["mailchimp", "sendgrid", "smtp"],
    "description": "Envía emails transaccionales o de campaña vía SendGrid, Mailchimp o SMTP.",
    "instructions": (
        "Para enviar un email usa action_type=integration, integration=sendgrid|mailchimp|smtp. "
        "Acciones: send_email, create_campaign, add_subscriber. "
        "arguments debe incluir: to (email destino), subject, body (texto plano o HTML). "
        "Para campañas masivas usa mailchimp con create_campaign."
    ),
    "example_action": {
        "contract_version": "1.0",
        "action_type": "integration",
        "action": "send_email",
        "integration": "sendgrid",
        "arguments": {
            "to": "cliente@ejemplo.com",
            "subject": "Confirmación de pedido",
            "body": "Tu pedido #1234 ha sido confirmado.",
        },
        "rationale": "Enviar confirmación de pedido al cliente.",
    },
}

SKILL = {
    "name": "invoice_generate",
    "trigger_keywords": ["factura", "invoice", "facturar", "cobro", "pago", "payment", "billing", "stripe", "conekta", "cfdi"],
    "integrations": ["stripe", "conekta", "facturapi"],
    "description": "Genera y envía facturas o cobros vía Stripe, Conekta o FacturAPI (CFDI México).",
    "instructions": (
        "Para generar una factura usa action_type=integration, integration=stripe|conekta|facturapi. "
        "Acciones: create_invoice, create_payment_link, send_invoice. "
        "arguments para create_invoice: customer_email, amount (en centavos para Stripe/Conekta), "
        "currency (mxn|usd), description, due_date (ISO 8601, opcional). "
        "Para CFDI mexicano usa facturapi con: rfc_receptor, uso_cfdi, conceptos (lista)."
    ),
    "example_action": {
        "contract_version": "1.0",
        "action_type": "integration",
        "action": "create_invoice",
        "integration": "stripe",
        "arguments": {
            "customer_email": "cliente@empresa.com",
            "amount": 500000,
            "currency": "mxn",
            "description": "Servicio mensual de automatización",
        },
        "rationale": "Generar factura de $5,000 MXN para el cliente.",
    },
}

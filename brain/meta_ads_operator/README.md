# Meta Ads Operator V1

Operador de Meta Ads orientado a cuentas de:

- lead generation
- mensajes a WhatsApp
- negocios locales
- servicios

No esta optimizado para ecommerce complejo en esta fase.

## Capas

- `client.py`: cliente API y adapters de normalizacion
- `diagnostics.py`: deteccion de hallazgos
- `playbooks.py`: reglas y acciones dry-run
- `engine.py`: analisis end-to-end
- `reporting.py`: resumen ejecutivo y reporte final

## Ejemplo rapido

```python
from brain.meta_ads_operator import analyze_context_snapshot

report = analyze_context_snapshot(
    {
        "campaign_name": "WA Clinica Dental",
        "ctr_link_pct": 3.8,
        "reply_rate": 0.12,
        "left_on_read_pct": 74,
        "daily_spend_usd": 45,
        "link_clicks": 63,
        "impressions": 4200,
        "spend": 28,
    },
    risk_score=0.4,
)
```

El operador prefiere `hold_steady` si la data es debil y evita castigar anuncios con buen CTR pero mal seguimiento downstream.

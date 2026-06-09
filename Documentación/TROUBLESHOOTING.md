# Troubleshooting

## 1) "La referencia ya ha sido usada" en Wompi

Causa:
- Se reutiliza la misma referencia.

Estado en este proyecto:
- Resuelto con referencia única por intento: `Sxxxxx-<attempt>`.
- Webhook convierte `ref_raw` a `ref_odoo` para buscar en Odoo.

## 2) Webhook no llega al servicio

Síntoma:
- No aparecen `POST /webhooks/wompi` en logs.

Causas comunes:
- URL de webhook en Wompi mal configurada.
- Nginx enruta `/webhooks/wompi` a otro backend.

Acción:
- Usar URL actual validada: `https://webhooks-odoo.wondertech.com.co/wompi/webhooks/wompi`.
- Verificar evento `transaction.updated` habilitado.

## 3) `No se pudo autenticar con Odoo`

Causa:
- combinación inválida de `ODOO_DB`, `ODOO_USER` o `ODOO_API_KEY`.

Checklist:
- `ODOO_USER` sea login real (no UID numérico si no aplica en authenticate).
- API key creada para ese mismo usuario en esa base.
- `pm2 env` no esté pisando `.env` con valores viejos.

Prueba directa:

```python
common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
```

Debe devolver `uid` (entero) y no `False`.

## 4) Error de permisos `account.payment` / `account.move.line`

Síntomas:
- `No puede crear registros 'Payments' (account.payment)`
- o reglas de registro sobre `account.move.line`.

Causa:
- Usuario API sin grupo/ACL contable suficiente.

Acciones:
- Dar permisos `Accounting/Invoicing` al usuario API.
- Revisar reglas de registro por compañía/diario.

Comportamiento actual:
- Si falla `action_post` por reglas de `account.move.line`, el webhook continúa.
- Si falla `account.payment.create`, el webhook devuelve `500`.

## 5) Firma inválida (`Checksum inválido`)

Causa:
- `WOMPI_EVENT_SECRET` no coincide con Wompi.
- payload firmado distinto al esperado.

Acciones:
- Validar secret de eventos en Wompi y `.env`.
- Durante pruebas controladas puede usarse `SKIP_SIGNATURE_VALIDATION=true`.
- En producción dejar `false`.

## 6) `No se encontró payment.provider ... en Odoo`

Síntoma:
- El pago se crea y la orden se confirma, pero el webhook responde 500 con
  `RuntimeError: No se encontró payment.provider`.

Causa:
- El `payment.provider` fue renombrado en Odoo (ej. de "Wompi" a "PSE") y el
  webhook lo busca por `ODOO_PAYMENT_PROVIDER_NAME` (default `wompi`).

Acción:
- Ajustar `ODOO_PAYMENT_PROVIDER_NAME` en el `.env` al nombre actual del
  provider y reiniciar con `pm2 restart wompi-webhook --update-env`.
- Luego reenviar los eventos fallidos desde el dashboard de Wompi: la
  idempotencia evita duplicar pagos y completa la `payment.transaction`
  que faltó.

## 7) Pago no refleja en portal

Verificar:
- `payment.transaction` se cree en `done` para la orden.
- referencia base Odoo (`Sxxxxx`) exista y sea encontrada.
- el evento sea `APPROVED`.

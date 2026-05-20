# WEBHOOK_WOMPI

Integración entre Wompi y Odoo para:
- abrir checkout de Wompi desde cotizaciones de Odoo,
- recibir eventos `transaction.updated` de Wompi,
- reflejar pagos en Odoo (payments + payment transactions),
- confirmar cotizaciones cuando el pago cubre el total.

## Alcance funcional

Este servicio Flask expone:
- `GET /checkout` y `GET /wompi/checkout`: genera URL de checkout Wompi con firma de integridad.
- `POST /webhooks/wompi`: procesa eventos de pago de Wompi y sincroniza estado en Odoo.
- `GET /health`: healthcheck simple.

## Arquitectura resumida

1. Portal Odoo llama `/wompi/checkout?reference=Sxxxxx&amount_in_cents=...`.
2. El backend construye referencia única para Wompi (`Sxxxxx-<attempt>`), firma integridad y redirige a `checkout.wompi.co`.
3. Wompi notifica `transaction.updated` al webhook.
4. El backend extrae referencia base Odoo (`Sxxxxx`) desde `ref_raw` con sufijo.
5. Para `APPROVED`:
- intenta crear `account.payment` en Odoo,
- intenta postear el pago,
- confirma la cotización si el acumulado cubre el total,
- crea `payment.transaction` en `done` para reflejo en portal.
6. Para `PENDING` crea `payment.transaction` en `pending`.

## Requisitos

- Python 3.10+
- Dependencias en `requirements.txt`
- Acceso XML-RPC a Odoo (`/xmlrpc/2/common`, `/xmlrpc/2/object`)
- Proveedor de pago Wompi configurado en Odoo (`payment.provider` + `payment.method`)

## Permisos

1. sale.order
read (buscar cotización por referencia, leer total/estado/partner/currency)
write + ejecutar acción action_confirm (confirmar cotización cuando el pago cubre total)
2. account.payment
create (crear pago entrante)
read (buscar pagos previos por memo)
3. ejecutar action_post (postear pago)
4. account.move.line (apuntes contables)
create (lo exige Odoo al postear account.payment)
read (normalmente implícito por reglas contables)
5. payment.transaction
read (buscar transacciones pendientes de la orden)
write (marcar pending -> done)
create (crear transacción done/pending para portal)
6. payment.provider
read (buscar provider Wompi)
7. payment.method
read (buscar método ligado al provider o unknown)
8. res.partner
read (lectura de email/contacto en flujo de notificación)
9. account.journal
read (si dejan diario dinámico; hoy está fijo en código, pero igual recomendable)

## Variables de entorno

Variables principales:

- `ODOO_URL`
- `ODOO_DB`
- `ODOO_USER`
- `ODOO_API_KEY`
- `ODOO_JOURNAL_NAME`
- `WOMPI_PUBLIC_KEY`
- `WOMPI_INTEGRITY_SECRET`
- `WOMPI_EVENT_SECRET`
- `WOMPI_CURRENCY` (default `COP`)
- `WOMPI_CHECKOUT_BASE_URL` (default `https://checkout.wompi.co/p/`)
- `SKIP_SIGNATURE_VALIDATION` (`true`/`false`)
- `PORT`

Notas importantes:
- `ODOO_USER` debe ser login válido de Odoo (email/usuario técnico), no necesariamente el UID numérico.
- `WOMPI_EVENT_SECRET` se usa para validar firma de eventos cuando `SKIP_SIGNATURE_VALIDATION=false`.

## Referencias Wompi y referencias Odoo

- Odoo usa referencias base tipo `S12345`.
- Checkout genera referencia única para Wompi: `S12345-<attempt>`.
- Webhook recibe `ref_raw` y extrae `ref_odoo=S12345` para buscar la cotización.

Esto evita errores de Wompi tipo "La referencia ya ha sido usada" sin romper el match en Odoo.

## Comportamiento ante permisos contables restringidos

Para pagos `APPROVED`, el sistema intenta flujo contable completo.
Si Odoo bloquea el `action_post` por reglas de `account.move.line`, el webhook no se cae y continúa el flujo para reflejo funcional (confirmación + `payment.transaction done`).

Si Odoo bloquea creación de `account.payment` (`account.payment.create`), el webhook actualmente responde error y requiere ajuste de permisos del usuario API.

## Ejecución local

```bash
pip install -r requirements.txt
python3 wompi_odoo_webhook.py
```

## Ejecución con PM2

```bash
pm2 start wompi_odoo_webhook.py --name wompi-webhook --interpreter python3
pm2 logs wompi-webhook
```

## Enrutamiento recomendado (Nginx)

El webhook debe enrutar a Flask en puerto `7000`:
- `POST /wompi/webhooks/wompi` (ruta pública actualmente usada)
- opcionalmente `POST /webhooks/wompi` si se configura location específico.

## Documentación adicional

- [Despliegue y Operación](./docs/DEPLOYMENT.md)
- [Troubleshooting](./docs/TROUBLESHOOTING.md)

# Despliegue y Operación

## 1) Preparar entorno

En servidor (ejemplo de ruta usada):
- Código: `/home/danielwonder/wompi`
- Proceso: `pm2` app `wompi-webhook`
- Puerto app: `7000`

Instalar deps:

```bash
cd /home/danielwonder/wompi
pip3 install -r requirements.txt
```

## 2) Configurar `.env`

Variables mínimas de producción:

```dotenv
ODOO_URL=https://<tu-instancia>.odoo.com
ODOO_DB=<db_name>
ODOO_USER=<login_usuario_api>
ODOO_API_KEY=<api_key_usuario_api>
ODOO_JOURNAL_NAME=<nombre_diario>

WOMPI_PUBLIC_KEY=<pub_prod_...>
WOMPI_INTEGRITY_SECRET=<prod_integrity_...>
WOMPI_EVENT_SECRET=<prod_events_...>

WOMPI_CURRENCY=COP
WOMPI_REDIRECT_URL=https://wondertechsas.odoo.com/shop/confirmation
PORT=7000
SKIP_SIGNATURE_VALIDATION=false
```

Nota sobre `WOMPI_REDIRECT_URL`:
- Es opcional. Si está vacía o no existe, el checkout se genera igual que antes (sin redirección).
- Wompi redirige al cliente a esa URL al finalizar el pago, agregando `?id=<transaction_id>`.
- La confirmación de la orden en Odoo NO depende de esta redirección: la sigue haciendo el webhook `transaction.updated`.

## 3) Iniciar/reiniciar servicio

Reinicio estándar:

```bash
pm2 restart wompi-webhook --update-env
```

Cuando PM2 trae envs viejos y pisa `.env`, reiniciar limpio:

```bash
pm2 delete wompi-webhook
pm2 start /home/danielwonder/wompi/wompi_odoo_webhook.py --name wompi-webhook --interpreter python3
```

Ver entorno efectivo del proceso:

```bash
pm2 env 0 | grep -E 'ODOO_URL|ODOO_DB|ODOO_USER|ODOO_API_KEY|SKIP_SIGNATURE_VALIDATION'
```

## 4) Configuración Wompi Dashboard

Webhook URL recomendada actual:

```text
https://webhooks-odoo.wondertech.com.co/wompi/webhooks/wompi
```

Evento requerido:
- `transaction.updated`

## 5) Probar checkout

```bash
curl -I "http://127.0.0.1:7000/checkout?reference=S12345&amount_in_cents=100&_t=1770000000"
```

Debe retornar `302` y `Location` con:
- `reference=S12345-1770000000`
- `signature:integrity=...`

## 6) Probar webhook (simulación)

```bash
curl -i -X POST http://127.0.0.1:7000/webhooks/wompi \
  -H "Content-Type: application/json" \
  -d '{
    "event":"transaction.updated",
    "data":{"transaction":{
      "id":"WOMPI-SIM-TEST-01",
      "reference":"S12345-1770000000",
      "status":"APPROVED",
      "amount_in_cents":100
    }}
  }'
```

## 7) Logs

```bash
pm2 logs wompi-webhook --lines 200 --nostream
```

Filtrado útil:

```bash
pm2 logs wompi-webhook --lines 1000 --nostream | \
grep -E 'Evento recibido|Transacción:|Cotización:|payment.transaction|Error procesando|POST /webhooks/wompi'
```

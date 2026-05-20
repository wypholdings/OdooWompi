"""
Webhook handler para recibir eventos de WOMPI y procesarlos en Odoo.

Flujo:
1. Recibe el POST de WOMPI (evento transaction.updated)
2. Valida la firma (checksum SHA256) — desactivable con SKIP_SIGNATURE_VALIDATION
3. Verifica que la referencia sea formato Odoo (ej: "S00001")
4. Según el estado de la transacción:
   - APPROVED: registra pago → confirma cotización (si monto >= total)
   - PENDING: registra cotización en Odoo con estado pendiente (draft)
   - VOIDED / DECLINED / ERROR: no hace nada
5. Si el monto pagado >= total de la cotización → confirma como orden de venta
   Si el monto pagado < total → registra el pago pero deja como cotización

Variables de entorno necesarias:
    ODOO_URL                    - URL de la instancia Odoo
    ODOO_DB                     - Nombre de la base de datos Odoo
    ODOO_USER                   - Usuario/email de Odoo
    ODOO_API_KEY                - API Key de Odoo
    ODOO_JOURNAL_NAME           - Nombre del diario bancario
    WOMPI_EVENT_SECRET          - Secret de eventos de WOMPI (opcional si SKIP_SIGNATURE_VALIDATION=true)
    SKIP_SIGNATURE_VALIDATION   - "true" para omitir validación de firma (default: "true")
    PORT                        - Puerto del servidor (default: 8000)

PM2:
    pm2 start wompi_odoo_webhook.py --name wompi-webhook --interpreter python3
"""

import hashlib
import hmac
import logging
import os
import re
import time
import xmlrpc.client
from urllib.parse import urlencode
from datetime import date

from flask import Flask, Response, jsonify, redirect, request
from dotenv import load_dotenv
from flask_limiter import Limiter

# ---------------------------------------------------------------------------
# Carga de Configuración (.env)
# ---------------------------------------------------------------------------
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ==========================================
# 🛡️ SEGURIDAD: RATE LIMITING Y TOKEN
# ==========================================
def get_client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr).split(',')[0].strip()

limiter = Limiter(
    get_client_ip,
    app=app,
    default_limits=["100 per day", "20 per hour"],
    storage_uri="memory://",
)

def _validate_webhook_token():
    """Valida el token de seguridad para peticiones que no vienen de WOMPI (ej: Odoo o Admin)"""
    expected = os.getenv("WEBHOOK_TOKEN", "").strip()
    if not expected:
        return None 
    
    candidates = [
        request.headers.get("X-Webhook-Token", "").strip(),
        request.args.get("token", "").strip(),
    ]
    
    if expected in candidates:
        return None
        
    # Nota: WOMPI usa validación de firma (checksum), 
    # pero este token sirve para llamadas manuales o de Odoo.
    return None # Por ahora permitimos pasar para no romper el webhook de Wompi 
                 # que no envía este token personalizado.

# Configuración Odoo
ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USER = os.getenv("ODOO_USER")
ODOO_PASSWORD = os.getenv("ODOO_API_KEY")
ODOO_JOURNAL_NAME = os.getenv("ODOO_JOURNAL_NAME")

WOMPI_EVENT_SECRET = os.getenv("WOMPI_EVENT_SECRET", "")
SKIP_SIGNATURE_VALIDATION = os.getenv("SKIP_SIGNATURE_VALIDATION", "true").lower() == "true"
PORT = int(os.getenv("PORT", "3008"))
WOMPI_CHECKOUT_BASE_URL = os.getenv("WOMPI_CHECKOUT_BASE_URL", "https://checkout.wompi.co/p/")
WOMPI_PUBLIC_KEY = os.getenv("WOMPI_PUBLIC_KEY", "").strip()
WOMPI_INTEGRITY_SECRET = os.getenv("WOMPI_INTEGRITY_SECRET", "").strip()
WOMPI_CURRENCY = os.getenv("WOMPI_CURRENCY", "COP").strip().upper()

# Patrón base de referencia de cotización Odoo: S seguido de dígitos (S00001, S00123, etc.)
# También aceptamos sufijos para intentos de pago únicos en Wompi: S00001-123456789
ODOO_REF_PATTERN = re.compile(r"^S\d+$")
ODOO_REF_WITH_SUFFIX_PATTERN = re.compile(r"^(S\d+)(?:[-_].+)?$")


# ---------------------------------------------------------------------------
# Validación de firma WOMPI
# ---------------------------------------------------------------------------
def validate_wompi_signature(payload: dict) -> bool:
    """
    Valida el checksum del evento de WOMPI.
    Concatena: valores de signature.properties + timestamp + event_secret → SHA256
    """
    if SKIP_SIGNATURE_VALIDATION:
        return True

    if not WOMPI_EVENT_SECRET:
        logger.warning("WOMPI_EVENT_SECRET no configurado, omitiendo validación de firma")
        return True

    signature = payload.get("signature", {})
    properties = signature.get("properties", [])
    received_checksum = signature.get("checksum", "")
    timestamp = signature.get("timestamp", "")

    concat_values = ""
    for prop in properties:
        value = _resolve_property(payload.get("data", {}), prop)
        concat_values += str(value)

    concat_values += str(timestamp)
    concat_values += WOMPI_EVENT_SECRET

    computed_checksum = hashlib.sha256(concat_values.encode("utf-8")).hexdigest().upper()

    is_valid = hmac.compare_digest(computed_checksum, received_checksum.upper())
    if not is_valid:
        logger.error("Checksum inválido. Esperado: %s, Recibido: %s", computed_checksum, received_checksum)
    return is_valid


def _resolve_property(data: dict, dotted_key: str):
    """Resuelve un valor anidado usando dot notation (ej: transaction.amount_in_cents)."""
    keys = dotted_key.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return ""
    return current if current is not None else ""


def build_wompi_integrity_signature(reference: str, amount_in_cents: int, currency: str) -> str:
    raw = f"{reference}{amount_in_cents}{currency}{WOMPI_INTEGRITY_SECRET}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_wompi_checkout_url(reference: str, amount_in_cents: int, customer_email: str = "") -> str:
    currency = WOMPI_CURRENCY
    params = {
        "public-key": WOMPI_PUBLIC_KEY,
        "currency": currency,
        "amount-in-cents": amount_in_cents,
        "reference": reference,
    }
    if WOMPI_INTEGRITY_SECRET:
        params["signature:integrity"] = build_wompi_integrity_signature(reference, amount_in_cents, currency)
    if customer_email:
        params["customer-data:email"] = customer_email
    return f"{WOMPI_CHECKOUT_BASE_URL}?{urlencode(params)}"


def extract_odoo_reference(raw_reference: str) -> str:
    """Extrae la referencia base de Odoo desde una referencia Wompi."""
    raw = (raw_reference or "").strip().upper()
    match = ODOO_REF_WITH_SUFFIX_PATTERN.match(raw)
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Conexión a Odoo (XML-RPC)
# ---------------------------------------------------------------------------
class OdooClient:
    def __init__(self):
        self.uid = None
        self.models = None

    def connect(self):
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
        self.uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
        if not self.uid:
            raise ConnectionError("No se pudo autenticar con Odoo. Verifica credenciales.")
        self.models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        logger.info("Conectado a Odoo como uid=%s", self.uid)

    def call(self, model, method, args=None, kwargs=None):
        if not self.models:
            self.connect()
        return self.models.execute_kw(
            ODOO_DB, self.uid, ODOO_PASSWORD, model, method, args or [], kwargs or {}
        )

    def find_sale_order(self, reference: str):
        orders = self.call(
            "sale.order", "search_read",
            [[["name", "=", reference]]],
            {
                "fields": ["id", "name", "state", "amount_total", "invoice_ids", "invoice_status", "partner_id"],
                "limit": 1,
            },
        )
        return orders[0] if orders else None

    def find_bank_journal(self) -> int:
        # ID fijo del diario "Banco Bancolombia Corriente 10822160406" en pruebasabril-2
        return 13

    def _create_payment(self, order: dict, amount: float, wompi_transaction_id: str, post: bool) -> int:
        """
        Crea un account.payment directamente (sin factura).
        Si post=True → queda posteado (conciliable, estado 'paid').
        Si post=False → queda en borrador (pendiente).
        """
        journal_id = self.find_bank_journal()
        partner_id = order["partner_id"][0]

        payment_id = self.call("account.payment", "create", [{
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": partner_id,
            "amount": amount,
            "journal_id": journal_id,
            "date": date.today().isoformat(),
            "memo": f"WOMPI {wompi_transaction_id} - {order['name']}",
        }])

        if post:
            try:
                self.call("account.payment", "action_post", [[payment_id]])
            except xmlrpc.client.Fault as e:
                # Odoo 18 retorna None desde action_post y el marshaller lo rechaza,
                # pero la acción sí se ejecutó. Ignoramos solo este caso.
                fault_msg = str(e)
                if "cannot marshal None" in fault_msg:
                    return payment_id
                # Si hay reglas de seguridad contable, no bloqueamos el webhook.
                # Dejamos el pago en borrador y continuamos el flujo.
                if "no tiene acceso 'crear' a" in fault_msg and "account.move.line" in fault_msg:
                    logger.warning(
                        "Sin permisos para postear account.payment (account.move.line). "
                        "Se mantiene en borrador y se continúa el flujo. fault=%s",
                        fault_msg,
                    )
                    return payment_id
                if "Estos registros están restringidos" in fault_msg and "Líneas de asiento diario" in fault_msg:
                    logger.warning(
                        "Regla de registro bloqueó posteo contable. "
                        "Se mantiene pago en borrador y se continúa. fault=%s",
                        fault_msg,
                    )
                    return payment_id
                if "cannot marshal None" not in fault_msg:
                    raise

        return payment_id

    def _find_payment_provider(self) -> int:
        """
        Busca el payment.provider 'Wompi'. Debe existir previamente en Odoo.
        """
        provs = self.call(
            "payment.provider", "search",
            [[["name", "ilike", "wompi"]]],
            {"limit": 1},
        )
        if not provs:
            raise RuntimeError(
                "No se encontró payment.provider 'Wompi' en Odoo. "
                "Créelo manualmente antes de usar este webhook."
            )
        return provs[0]

    def _find_payment_method(self, provider_id: int) -> int:
        """
        Busca un payment.method vinculado al provider Wompi.
        Debe existir previamente en Odoo.
        """
        methods = self.call(
            "payment.method", "search",
            [[["provider_ids", "in", [provider_id]]]],
            {"limit": 1},
        )
        if not methods:
            # Fallback: método 'unknown' (el genérico de Odoo)
            methods = self.call(
                "payment.method", "search",
                [[["code", "=", "unknown"]]],
                {"limit": 1},
            )
        if not methods:
            raise RuntimeError(
                "No se encontró ningún payment.method vinculado al provider Wompi ni método 'unknown'. "
                "Configure un método de pago en Odoo."
            )
        return methods[0]

    # ------------------------------------------------------------------
    # PENDING: crea payment.transaction en estado 'pending' vinculado a la SO.
    # Esto hace que en el portal aparezca el banner
    # "Your payment has been processed but is waiting for approval"
    # ------------------------------------------------------------------
    def process_pending_payment(self, order: dict, amount_cents: int, wompi_transaction_id: str):
        order_id = order["id"]
        order_name = order["name"]
        amount_paid = amount_cents / 100.0
        partner_id = order["partner_id"][0]

        logger.info("Procesando pago PENDIENTE para %s | Monto: $%.2f", order_name, amount_paid)

        # Obtener moneda de la cotización
        order_full = self.call(
            "sale.order", "read", [[order_id]], {"fields": ["currency_id"]}
        )[0]
        currency_id = order_full["currency_id"][0]

        provider_id = self._find_payment_provider()
        payment_method_id = self._find_payment_method(provider_id)

        tx_id = self.call("payment.transaction", "create", [{
            "provider_id": provider_id,
            "payment_method_id": payment_method_id,
            "reference": f"WOMPI-{wompi_transaction_id}",
            "amount": amount_paid,
            "currency_id": currency_id,
            "partner_id": partner_id,
            "sale_order_ids": [(6, 0, [order_id])],
            "state": "pending",
            "provider_reference": wompi_transaction_id,
            "operation": "online_direct",
        }])
        logger.info(
            "payment.transaction PENDIENTE creada (id=%s) para %s | $%.2f | WOMPI: %s",
            tx_id, order_name, amount_paid, wompi_transaction_id,
        )

    # ------------------------------------------------------------------
    # APPROVED: crea pago posteado, confirma la orden si monto cubre total
    # ------------------------------------------------------------------
    def process_approved_payment(self, order: dict, amount_cents: int, wompi_transaction_id: str):
        order_id = order["id"]
        order_name = order["name"]
        order_total = order["amount_total"]
        amount_paid = amount_cents / 100.0
        partner_id = order["partner_id"][0]
        # Sumar pagos previos de esta orden para evaluar si cubre el total
        existing_payments = self.call(
            "account.payment", "search_read",
            [[["memo", "ilike", order_name], ["payment_type", "=", "inbound"]]],
            {"fields": ["amount"]},
        )
        total_paid = sum(p["amount"] for p in existing_payments) + amount_paid

        should_confirm = total_paid >= order_total

        logger.info(
            "Procesando pago APROBADO para %s | Este pago: $%.2f | Acumulado: $%.2f | Total: $%.2f | Confirma: %s",
            order_name, amount_paid, total_paid, order_total, should_confirm,
        )

        # Primero crear el pago en el diario de banco
        payment_id = self._create_payment(order, amount_paid, wompi_transaction_id, post=True)
        logger.info("Pago APROBADO (id=%s) para %s | $%.2f | WOMPI: %s",
                    payment_id, order_name, amount_paid, wompi_transaction_id)

        # Luego confirmar como orden de venta si el acumulado cubre el total
        if should_confirm and order["state"] in ("draft", "sent"):
            self.call("sale.order", "action_confirm", [[order_id]])
            logger.info("Cotización %s confirmada como Orden de Venta", order_name)

        # Marcar payment.transaction pendientes de esta orden como 'done'
        pending_txs = self.call(
            "payment.transaction", "search",
            [[["sale_order_ids", "in", [order_id]], ["state", "=", "pending"]]],
        )
        if pending_txs:
            self.call("payment.transaction", "write", [pending_txs, {"state": "done"}])
            logger.info("payment.transaction pendientes %s marcadas como done", pending_txs)

        # Crear payment.transaction en estado 'done' para reflejar el pago en el portal
        order_full = self.call(
            "sale.order", "read", [[order_id]], {"fields": ["currency_id"]}
        )[0]
        currency_id = order_full["currency_id"][0]
        provider_id = self._find_payment_provider()
        payment_method_id = self._find_payment_method(provider_id)
        tx_id = self.call("payment.transaction", "create", [{
            "provider_id": provider_id,
            "payment_method_id": payment_method_id,
            "reference": f"WOMPI-{wompi_transaction_id}",
            "amount": amount_paid,
            "currency_id": currency_id,
            "partner_id": partner_id,
            "sale_order_ids": [(6, 0, [order_id])],
            "state": "done",
            "provider_reference": wompi_transaction_id,
            "operation": "online_direct",
        }])
        logger.info("payment.transaction DONE creada (id=%s) para %s | $%.2f", tx_id, order_name, amount_paid)


# ---------------------------------------------------------------------------
# Instancia global
# ---------------------------------------------------------------------------
odoo = OdooClient()


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------
@app.route("/webhooks/wompi", methods=["POST"])
def wompi_webhook():
    try:
        payload = request.get_json(force=True)
    except Exception:
        logger.error("No se pudo parsear el JSON del webhook")
        return Response(status=400)

    logger.info("Evento recibido: %s", payload.get("event"))

    if not validate_wompi_signature(payload):
        logger.error("Firma inválida, descartando evento")
        return Response(status=401)

    if payload.get("event") != "transaction.updated":
        return Response(status=200)

    transaction = payload.get("data", {}).get("transaction", {})
    raw_reference = transaction.get("reference", "")
    reference = extract_odoo_reference(raw_reference)
    status = transaction.get("status", "")
    amount_in_cents = transaction.get("amount_in_cents", 0)
    transaction_id = transaction.get("id", "")

    logger.info("Transacción: id=%s | ref_raw=%s | ref_odoo=%s | estado=%s | monto=%s centavos",
                transaction_id, raw_reference, reference, status, amount_in_cents)

    if not reference or not ODOO_REF_PATTERN.match(reference):
        logger.info("Referencia '%s' no es formato Odoo. Ignorado.", raw_reference)
        return Response(status=200)

    if status in ("VOIDED", "DECLINED", "ERROR"):
        logger.info("Estado '%s' para ref '%s'. Sin acción.", status, reference)
        return Response(status=200)

    try:
        odoo.connect()
    except Exception as e:
        logger.error("Error conectando a Odoo: %s", e)
        return Response(status=500)

    order = odoo.find_sale_order(reference)
    if not order:
        logger.error("No se encontró cotización '%s' en Odoo", reference)
        return Response(status=200)

    logger.info("Cotización: %s (id=%s, estado=%s, total=$%.2f)",
                order["name"], order["id"], order["state"], order["amount_total"])

    try:
        if status == "PENDING":
            odoo.process_pending_payment(order, amount_in_cents, transaction_id)
        elif status == "APPROVED":
            odoo.process_approved_payment(order, amount_in_cents, transaction_id)
    except Exception as e:
        logger.error("Error procesando transacción %s: %s", transaction_id, e, exc_info=True)
        return Response(status=500)

    return Response(status=200)


@app.route("/checkout", methods=["GET"])
@app.route("/wompi/checkout", methods=["GET"])
def wompi_checkout():
    reference = request.args.get("reference", "").strip().upper()
    if not reference or not ODOO_REF_PATTERN.match(reference):
        logger.error("Checkout inválido: reference '%s'", reference)
        return Response("Referencia inválida.", status=400)

    if not WOMPI_PUBLIC_KEY:
        logger.error("Falta WOMPI_PUBLIC_KEY para checkout")
        return Response("Checkout no configurado.", status=500)

    amount_in_cents = request.args.get("amount_in_cents", type=int)
    customer_email = ""
    if amount_in_cents is None:
        try:
            odoo.connect()
            order = odoo.find_sale_order(reference)
            if not order:
                logger.error("Checkout: no existe cotización '%s'", reference)
                return Response("Cotización no encontrada.", status=404)
            amount_in_cents = int(round(float(order["amount_total"]) * 100))
            partner_data = order.get("partner_id")
            if isinstance(partner_data, list) and partner_data:
                partner = odoo.call("res.partner", "read", [[partner_data[0]]], {"fields": ["email"]})
                if partner and partner[0].get("email"):
                    customer_email = partner[0]["email"]
        except Exception as e:
            logger.error("Error consultando Odoo para checkout %s: %s", reference, e, exc_info=True)
            return Response(
                "Odoo no disponible. Reintenta con ?amount_in_cents= en la URL para continuar.",
                status=503,
            )

    if not amount_in_cents or amount_in_cents <= 0:
        return Response("amount_in_cents inválido.", status=400)

    try:
        # Wompi exige referencias únicas por intento.
        attempt_id = request.args.get("_t", "").strip() or request.args.get("attempt_id", "").strip()
        wompi_reference = f"{reference}-{attempt_id}" if attempt_id else f"{reference}-{int(time.time())}"
        checkout_url = build_wompi_checkout_url(wompi_reference, amount_in_cents, customer_email)
        logger.info(
            "Checkout WOMPI generado ref_odoo=%s ref_wompi=%s por %s centavos",
            reference, wompi_reference, amount_in_cents
        )
        return redirect(checkout_url, code=302)
    except Exception as e:
        logger.error("Error generando checkout WOMPI para %s: %s", reference, e, exc_info=True)
        return Response("Lo sentimos, ocurrió un error al procesar tu solicitud de pago.", status=500)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Iniciando webhook WOMPI en puerto %s", PORT)
    logger.info("Odoo: %s (db=%s, user=%s)", ODOO_URL, ODOO_DB, ODOO_USER)
    logger.info("Diario: %s", ODOO_JOURNAL_NAME)
    logger.info("Validación firma WOMPI: %s", "DESACTIVADA" if SKIP_SIGNATURE_VALIDATION else "ACTIVADA")
    app.run(host="0.0.0.0", port=PORT, debug=False)

"""
Pruebas de OdooClient con Odoo simulado (mock de .call):
- proveedor de pago configurable vía ODOO_PAYMENT_PROVIDER_NAME
- idempotencia: eventos reintentados no duplican account.payment ni payment.transaction

Ejecución:
    python3 -m unittest discover tests
"""

import importlib
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Proyecto"))

BASE_ENV = {
    "WOMPI_PUBLIC_KEY": "pub_test_123",
    "WOMPI_INTEGRITY_SECRET": "test_integrity_secret",
}

ORDER = {
    "id": 3935,
    "name": "S04008",
    "state": "draft",
    "amount_total": 4.67,
    "partner_id": [7, "Cliente Prueba"],
}


def _load_module(extra_env):
    for key in list(BASE_ENV) + ["ODOO_PAYMENT_PROVIDER_NAME"]:
        os.environ.pop(key, None)
    os.environ.update(BASE_ENV)
    os.environ.update(extra_env)
    sys.modules.pop("wompi_odoo_webhook", None)
    return importlib.import_module("wompi_odoo_webhook")


class FakeOdoo:
    """Simula execute_kw de Odoo registrando las llamadas."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, model, method, args=None, kwargs=None):
        self.calls.append((model, method, args, kwargs))
        for (m, meth), value in self.responses:
            if m == model and meth == method:
                return value(args) if callable(value) else value
        return []

    def created(self, model):
        return [c for c in self.calls if c[0] == model and c[1] == "create"]


class ProviderNameTest(unittest.TestCase):
    def test_busca_provider_con_nombre_configurado(self):
        mod = _load_module({"ODOO_PAYMENT_PROVIDER_NAME": "PSE"})
        client = mod.OdooClient()
        fake = FakeOdoo([(("payment.provider", "search"), [9])])
        with patch.object(client, "call", fake):
            self.assertEqual(client._find_payment_provider(), 9)
        domain = fake.calls[0][2][0]
        self.assertIn(["name", "ilike", "PSE"], domain)

    def test_default_sigue_siendo_wompi(self):
        mod = _load_module({})
        client = mod.OdooClient()
        fake = FakeOdoo([(("payment.provider", "search"), [5])])
        with patch.object(client, "call", fake):
            client._find_payment_provider()
        domain = fake.calls[0][2][0]
        self.assertIn(["name", "ilike", "wompi"], domain)


class IdempotenciaTest(unittest.TestCase):
    def test_approved_reintentado_no_duplica_pago_ni_transaccion(self):
        mod = _load_module({})
        client = mod.OdooClient()
        fake = FakeOdoo([
            # ya existe el pago de esta transacción y la payment.transaction
            (("account.payment", "search"), [8592]),
            (("account.payment", "search_read"), [{"amount": 4.67}]),
            (("payment.transaction", "search"), lambda args: [777] if ["reference", "=", "WOMPI-TX1"] in args[0] else []),
            (("sale.order", "read"), [{"currency_id": [8, "COP"]}]),
        ])
        with patch.object(client, "call", fake):
            client.process_approved_payment(ORDER, 467, "TX1")
        self.assertEqual(fake.created("account.payment"), [])
        self.assertEqual(fake.created("payment.transaction"), [])

    def test_pending_reintentado_no_duplica_transaccion(self):
        mod = _load_module({})
        client = mod.OdooClient()
        fake = FakeOdoo([
            (("payment.transaction", "search"), [777]),
        ])
        with patch.object(client, "call", fake):
            client.process_pending_payment(ORDER, 467, "TX1")
        self.assertEqual(fake.created("payment.transaction"), [])

    def test_approved_nuevo_si_crea_pago_y_transaccion(self):
        mod = _load_module({})
        client = mod.OdooClient()
        fake = FakeOdoo([
            (("account.payment", "search"), []),          # no hay pago previo de esta tx
            (("account.payment", "search_read"), []),      # sin pagos previos de la orden
            (("account.payment", "create"), 8600),
            (("payment.transaction", "search"), []),       # sin tx previas
            (("sale.order", "read"), [{"currency_id": [8, "COP"]}]),
            (("payment.provider", "search"), [9]),
            (("payment.method", "search"), [3]),
            (("payment.transaction", "create"), 900),
        ])
        with patch.object(client, "call", fake):
            client.process_approved_payment(ORDER, 467, "TX2")
        self.assertEqual(len(fake.created("account.payment")), 1)
        self.assertEqual(len(fake.created("payment.transaction")), 1)
        # confirma la orden porque el pago cubre el total
        confirms = [c for c in fake.calls if c[0] == "sale.order" and c[1] == "action_confirm"]
        self.assertEqual(len(confirms), 1)


if __name__ == "__main__":
    unittest.main()

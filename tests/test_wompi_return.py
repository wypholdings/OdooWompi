"""
Pruebas del endpoint /wompi/return: regreso del cliente desde Wompi hacia
la página de SU orden en el portal de Odoo, con fallback a /my/orders.

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
    "ODOO_URL": "https://wondertechsas.odoo.com",
    "WOMPI_PUBLIC_KEY": "pub_test_123",
    "WOMPI_INTEGRITY_SECRET": "test_integrity_secret",
}


def _load_module():
    os.environ.update(BASE_ENV)
    sys.modules.pop("wompi_odoo_webhook", None)
    return importlib.import_module("wompi_odoo_webhook")


class WompiReturnTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.client = self.mod.app.test_client()

    def test_sin_id_redirige_a_my_orders(self):
        resp = self.client.get("/wompi/return")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "https://wondertechsas.odoo.com/my/orders")

    def test_con_id_redirige_a_la_orden_con_token(self):
        with patch.object(self.mod, "fetch_wompi_transaction",
                          return_value={"reference": "S04006-1781039674", "status": "APPROVED"}), \
             patch.object(self.mod.odoo, "connect"), \
             patch.object(self.mod.odoo, "find_sale_order",
                          return_value={"id": 3933, "name": "S04006"}), \
             patch.object(self.mod.odoo, "call",
                          return_value=[{"access_token": "abc-123"}]):
            resp = self.client.get("/wompi/return?id=11283-1781039693-72647&env=prod")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.headers["Location"],
            "https://wondertechsas.odoo.com/my/orders/3933?access_token=abc-123",
        )

    def test_orden_sin_token_redirige_sin_token(self):
        with patch.object(self.mod, "fetch_wompi_transaction",
                          return_value={"reference": "S04006-1781039674"}), \
             patch.object(self.mod.odoo, "connect"), \
             patch.object(self.mod.odoo, "find_sale_order",
                          return_value={"id": 3933, "name": "S04006"}), \
             patch.object(self.mod.odoo, "call",
                          return_value=[{"access_token": False}]):
            resp = self.client.get("/return?id=TX1")
        self.assertEqual(resp.headers["Location"], "https://wondertechsas.odoo.com/my/orders/3933")

    def test_falla_de_wompi_cae_en_fallback(self):
        with patch.object(self.mod, "fetch_wompi_transaction",
                          side_effect=RuntimeError("Wompi caído")):
            resp = self.client.get("/wompi/return?id=TX1")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "https://wondertechsas.odoo.com/my/orders")

    def test_referencia_no_odoo_cae_en_fallback(self):
        with patch.object(self.mod, "fetch_wompi_transaction",
                          return_value={"reference": "OTRA-COSA-99"}):
            resp = self.client.get("/wompi/return?id=TX1")
        self.assertEqual(resp.headers["Location"], "https://wondertechsas.odoo.com/my/orders")


if __name__ == "__main__":
    unittest.main()

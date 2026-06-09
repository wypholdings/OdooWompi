"""
Pruebas de build_wompi_checkout_url: verifica que redirect-url solo se
agregue cuando WOMPI_REDIRECT_URL está configurada y que el resto de la
URL (incluida la firma de integridad) no cambie.

Ejecución:
    python3 -m unittest discover tests
"""

import hashlib
import importlib
import os
import sys
import unittest
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Proyecto"))

BASE_ENV = {
    "WOMPI_PUBLIC_KEY": "pub_test_123",
    "WOMPI_INTEGRITY_SECRET": "test_integrity_secret",
    "WOMPI_CURRENCY": "COP",
}


def _load_module(extra_env):
    for key in list(BASE_ENV) + ["WOMPI_REDIRECT_URL"]:
        os.environ.pop(key, None)
    os.environ.update(BASE_ENV)
    os.environ.update(extra_env)
    sys.modules.pop("wompi_odoo_webhook", None)
    return importlib.import_module("wompi_odoo_webhook")


class CheckoutUrlTest(unittest.TestCase):
    def test_sin_redirect_url_no_agrega_parametro(self):
        mod = _load_module({})
        url = mod.build_wompi_checkout_url("S12345-1770000000", 100)
        params = parse_qs(urlparse(url).query)
        self.assertNotIn("redirect-url", params)
        self.assertEqual(params["reference"], ["S12345-1770000000"])
        self.assertEqual(params["amount-in-cents"], ["100"])

    def test_con_redirect_url_agrega_parametro(self):
        redirect = "https://wondertechsas.odoo.com/shop/confirmation"
        mod = _load_module({"WOMPI_REDIRECT_URL": redirect})
        url = mod.build_wompi_checkout_url("S12345-1770000000", 100)
        params = parse_qs(urlparse(url).query)
        self.assertEqual(params["redirect-url"], [redirect])

    def test_firma_integridad_no_cambia_con_redirect(self):
        redirect = "https://wondertechsas.odoo.com/shop/confirmation"
        mod = _load_module({"WOMPI_REDIRECT_URL": redirect})
        url = mod.build_wompi_checkout_url("S12345-1770000000", 100)
        params = parse_qs(urlparse(url).query)
        expected = hashlib.sha256(
            "S12345-1770000000100COPtest_integrity_secret".encode("utf-8")
        ).hexdigest()
        self.assertEqual(params["signature:integrity"], [expected])


if __name__ == "__main__":
    unittest.main()

"""Sovos (Foriba / fitbulut) ClientEInvoiceServices (SOAP, HTTP Basic auth).

e-Fatura is sent with ``sendUBL`` as a base64 zip of the UBL XML; the returned envelope
UUID is queried with ``getEnvelopeStatus``. Sovos' e-Arşiv runs on a separate service
that this adapter does not implement yet.
"""

import base64

from . import soap
from .base import (DEBUG_FIELD, ENV_FIELD, ConfigField, Integrator, IntegratorError, SendResult, StatusResult,
                   XmlOptions, map_status)
from .logo import zip_bytes

NS = "http://fitcons.com/eInvoice/"


class SovosIntegrator(Integrator):
    key = "sovos"
    label = "Sovos (Foriba)"
    description = "Sovos / Foriba ClientEInvoiceServices (SOAP): e-Fatura via sendUBL. e-Arşiv not yet supported."
    live = True
    verified = False
    capabilities = {"efatura", "status"}
    xml_defaults = XmlOptions(embed_xslt=True, earsiv_sending_type=True)
    URLS = {
        "test": {"default": "https://efaturawstest.fitbulut.com/ClientEInvoiceServices/ClientEInvoiceServicesPort.svc"},
        "production": {"default": "https://efaturaws.fitbulut.com/ClientEInvoiceServices/ClientEInvoiceServicesPort.svc"},
    }
    fields = [
        ENV_FIELD,
        ConfigField("username", "Web service username"),
        ConfigField("password", "Web service password", kind="password"),
        ConfigField("sender_alias", "Sender alias (GB)", help="Your gönderici birim etiketi, e.g. urn:mail:defaultgb@…"),
        ConfigField("url_default", "Service URL (optional)"),
        DEBUG_FIELD,
    ]

    def _call(self, method, *children):
        body = soap.el(f"{method}Request", ns=NS, children=children)
        return self.soap_call(self.url(), f"{NS}{method}", body,
                              auth=(self.config.get("username", ""), self.config.get("password", "")))

    def _vkn(self):
        from ...models import Setting

        return Setting.get("company.tax_id")

    def test_connection(self):
        self._call("getEnvelopeStatus", soap.el("Identifier", self.config.get("sender_alias", "")),
                   soap.el("VKN_TCKN", self._vkn()), soap.el("UUID", "00000000-0000-0000-0000-000000000000"))
        return f"Connected to Sovos ({self.config.get('environment')})."

    def send(self, invoice, xml, receiver_alias=""):
        self.ensure_supported(invoice)
        if not receiver_alias:
            raise IntegratorError("Receiver e-Fatura alias is missing. Check the customer's e-Fatura status.")
        data = zip_bytes(f"{invoice.uuid}.xml", xml)
        resp = self._call("sendUBL",
                          soap.el("VKN_TCKN", self._vkn()),
                          soap.el("SenderIdentifier", self.config.get("sender_alias", "")),
                          soap.el("ReceiverIdentifier", receiver_alias),
                          soap.el("DocType", "INVOICE"),
                          soap.el("DocData", base64.b64encode(data).decode()))
        env = soap.text(resp, "EnvUUID") or invoice.uuid
        return SendResult(ok=True, status="sent", reference=env, message="Accepted by Sovos.")

    def get_status(self, invoice):
        resp = self._call("getEnvelopeStatus", soap.el("Identifier", self.config.get("sender_alias", "")),
                          soap.el("VKN_TCKN", self._vkn()), soap.el("UUID", invoice.integrator_ref or invoice.uuid))
        code = soap.text(resp, "STATUS") or soap.text(resp, "ResponseCode")
        detail = soap.text(resp, "STATUS_DESCRIPTION") or soap.text(resp, "Description") or code
        return StatusResult(map_status(f"{code} {detail}"), detail)

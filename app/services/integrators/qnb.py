"""QNB eSolutions (eFinans) connector services (SOAP, WS-Security UsernameToken).

e-Fatura goes through ``connectorService`` (belgeGonder / efaturaKullanicisi /
gidenBelgeDurumSorgula), e-Arşiv through ``EarsivWebService`` (faturaOlustur /
faturaIptalEt) whose parameters are passed as a JSON string.
"""

import base64
import hashlib
import json
from datetime import date

from . import soap
from .base import (DEBUG_FIELD, ENV_FIELD, Alias, ConfigField, Integrator, IntegratorError, SendResult,
                   StatusResult, XmlOptions, map_status)

NS_EFATURA = "http://service.connector.uut.cs.com.tr/"
NS_EARSIV = "http://service.earsiv.uut.cs.com.tr/"


class QnbEfinansIntegrator(Integrator):
    key = "qnb"
    label = "QNB eSolutions (eFinans)"
    description = "QNB eFinans connector: e-Fatura connectorService and e-Arşiv EarsivWebService (SOAP)."
    live = True
    verified = False
    capabilities = {"efatura", "earsiv", "lookup", "status", "cancel"}
    xml_defaults = XmlOptions(embed_xslt=True, earsiv_sending_type=True)
    URLS = {
        "test": {"efatura": "https://erpefaturatest.cs.com.tr:8443/efatura/ws/connectorService",
                 "earsiv": "https://earsivtest.efinans.com.tr/earsiv/ws/EarsivWebService"},
        "production": {"efatura": "https://efaturaconnector.efinans.com.tr/connector/ws/connectorService",
                       "earsiv": "https://earsivconnector.efinans.com.tr/earsiv/ws/EarsivWebService"},
    }
    fields = [
        ENV_FIELD,
        ConfigField("username", "Connector username"),
        ConfigField("password", "Connector password", kind="password"),
        ConfigField("sube", "e-Arşiv branch (şube)", default="DFLT", help="As defined in the QNB portal"),
        ConfigField("kasa", "e-Arşiv till (kasa)", default="DFLT"),
        ConfigField("url_efatura", "e-Fatura service URL (optional)"),
        ConfigField("url_earsiv", "e-Arşiv service URL (optional)"),
        DEBUG_FIELD,
    ]

    def _vkn(self):
        from ...models import Setting

        return Setting.get("company.tax_id")

    def _call(self, service, ns, method, params):
        body = soap.el(method, ns=ns, children=[soap.el(k, v) for k, v in params])
        header = soap.wsse_header(self.config.get("username", ""), self.config.get("password", ""))
        return self.soap_call(self.url(service), "", body, headers=[header])

    def test_connection(self):
        self.check_user(self._vkn())
        return f"Connected to QNB eFinans ({self.config.get('environment')})."

    def check_user(self, tax_id):
        resp = self._call("efatura", NS_EFATURA, "efaturaKullanicisi", [("vergiTcKimlikNo", tax_id)])
        ret = soap.text(resp, "return").lower()
        if ret != "true":
            return []
        # registered; the connector routes to the receiver's default mailbox, so no alias is needed
        return [Alias(alias="", title="")]

    def send(self, invoice, xml, receiver_alias=""):
        self.ensure_supported(invoice)
        if invoice.is_earsiv:
            return self._send_earsiv(invoice, xml)
        resp = self._call("efatura", NS_EFATURA, "belgeGonder", [
            ("vergiTcKimlikNo", self._vkn()),
            ("belgeTuru", "FATURA_UBL"),
            ("belgeNo", invoice.number),
            ("veri", base64.b64encode(xml).decode()),
            ("belgeHash", hashlib.md5(xml).hexdigest()),
            ("mimeType", "application/xml"),
            ("belgeVersiyon", "3.0"),
        ])
        oid = soap.text(resp, "return") or soap.text(resp, "belgeOid")
        if not oid:
            raise IntegratorError("QNB did not return a document id.")
        return SendResult(ok=True, status="sent", reference=oid, message="Accepted by QNB eFinans.")

    def _send_earsiv(self, invoice, xml):
        params = {"islemId": invoice.uuid, "vkn": self._vkn(), "sube": self.config.get("sube") or "DFLT",
                  "kasa": self.config.get("kasa") or "DFLT", "numaraVerilsinMi": 0, "donenBelgeFormati": 9}
        if invoice.contact.email:
            params["eposta"] = invoice.contact.email
        fatura = soap.el("fatura", children=[soap.el("belgeFormati", "UBL"),
                                             soap.el("belgeIcerigi", base64.b64encode(xml).decode())])
        body = soap.el("faturaOlustur", ns=NS_EARSIV, children=[soap.el("input", json.dumps(params)), fatura])
        header = soap.wsse_header(self.config.get("username", ""), self.config.get("password", ""))
        resp = self.soap_call(self.url("earsiv"), "", body, headers=[header])
        code = soap.text(resp, "resultCode")
        if code and code != "AE00000":
            raise IntegratorError(f"{code}: {soap.text(resp, 'resultText')}")
        return SendResult(ok=True, status="accepted", reference=invoice.uuid, message="Accepted by QNB eFinans.")

    def get_status(self, invoice):
        if invoice.is_earsiv:
            return StatusResult(invoice.status, "e-Arşiv invoices are final once accepted.")
        resp = self._call("efatura", NS_EFATURA, "gidenBelgeDurumSorgula",
                          [("vergiTcKimlikNo", self._vkn()), ("belgeOid", invoice.integrator_ref)])
        answer = soap.text(resp, "yanitDurumu")
        detail = soap.text(resp, "aciklama") or soap.text(resp, "durum")
        if answer in ("1", "KABUL"):
            return StatusResult("accepted", detail)
        if answer in ("0", "RED"):
            return StatusResult("rejected", detail)
        return StatusResult(map_status(soap.text(resp, "gonderimDurumu") + " " + detail), detail)

    def cancel(self, invoice):
        if not invoice.is_earsiv:
            raise IntegratorError("Only e-Arşiv invoices can be cancelled; e-Fatura needs a return invoice.")
        params = {"vkn": self._vkn(), "faturaUuid": invoice.uuid, "faturaNo": invoice.number,
                  "iptalTarihi": date.today().strftime("%Y%m%d")}
        body = soap.el("faturaIptalEt", ns=NS_EARSIV, children=[soap.el("input", json.dumps(params))])
        header = soap.wsse_header(self.config.get("username", ""), self.config.get("password", ""))
        resp = self.soap_call(self.url("earsiv"), "", body, headers=[header])
        code = soap.text(resp, "resultCode")
        if code and code != "AE00000":
            raise IntegratorError(f"{code}: {soap.text(resp, 'resultText')}")
        return StatusResult("cancelled", "Cancelled at the integrator.")

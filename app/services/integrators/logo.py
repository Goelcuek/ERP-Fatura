"""Logo eLogo PostBoxService (SOAP). Session based: Login → SendDocument → Logout.

Documents are sent as a base64 zip containing the UBL XML, with an MD5 hash of the zip.
The document type (e-Fatura / e-Arşiv) is given in ``paramList``.
"""

import base64
import hashlib
import io
import zipfile

from . import soap
from .base import (DEBUG_FIELD, ENV_FIELD, Alias, ConfigField, Integrator, IntegratorError, SendResult,
                   StatusResult, XmlOptions, map_status)

NS = "http://tempuri.org/"
NS_DC = "http://schemas.datacontract.org/2004/07/eFinans.PostBox"
NS_ARR = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"


def zip_bytes(name, data):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, data)
    return buf.getvalue()


class LogoIntegrator(Integrator):
    key = "logo"
    label = "Logo eLogo"
    description = "Logo eLogo PostBoxService (SOAP, session login). Sends zipped UBL for e-Fatura and e-Arşiv."
    live = True
    verified = False
    capabilities = {"efatura", "earsiv", "lookup", "status"}
    xml_defaults = XmlOptions(embed_xslt=True, earsiv_sending_type=True)
    URLS = {
        "test": {"default": "https://pb-demo.elogo.com.tr/PostBoxService.svc"},
        "production": {"default": "https://pb.elogo.com.tr/PostBoxService.svc"},
    }
    fields = [
        ENV_FIELD,
        ConfigField("username", "eLogo username"),
        ConfigField("password", "eLogo password", kind="password"),
        ConfigField("url_default", "Service URL (optional)"),
        DEBUG_FIELD,
    ]

    def _call(self, method, *children):
        body = soap.el(method, ns=NS, children=children)
        return self.soap_call(self.url(), f"{NS}IPostBoxService/{method}", body)

    def _login(self):
        login = soap.el("login", ns=NS, children=[
            soap.el("appStr", "AtolyeERP", NS_DC),
            soap.el("passWord", self.config.get("password", ""), NS_DC),
            soap.el("source", "", NS_DC),
            soap.el("userName", self.config.get("username", ""), NS_DC),
            soap.el("version", "1.0", NS_DC),
        ])
        resp = self._call("Login", login)
        sid = soap.text(resp, "sessionID")
        if soap.text(resp, "LoginResult").lower() == "false" or not sid:
            raise IntegratorError("eLogo login failed. Check username and password.")
        return sid

    def _logout(self, sid):
        try:
            self._call("Logout", soap.el("sessionID", sid, NS))
        except IntegratorError:
            pass

    @staticmethod
    def _params(*items):
        return soap.el("paramList", ns=NS, children=[soap.el("string", i, NS_ARR) for i in items])

    @staticmethod
    def _check(resp, method):
        result = soap.find(resp, f"{method}Result")
        code = soap.text(result, "resultCode") if result is not None else ""
        if code and code not in ("1", "0000", "200"):
            raise IntegratorError(f"{code}: {soap.text(result, 'resultMsg') or method + ' failed'}")
        return result

    def test_connection(self):
        self._logout(self._login())
        return f"Connected to eLogo ({self.config.get('environment')})."

    def check_user(self, tax_id):
        sid = self._login()
        try:
            resp = self._call("CheckGibUser", soap.el("sessionID", sid, NS),
                              soap.el("vknTcknList", ns=NS, children=[soap.el("string", tax_id, NS_ARR)]))
        finally:
            self._logout(sid)
        out = []
        for node in soap.find_all(resp, "Alias") + soap.find_all(resp, "string"):
            val = (node.text or "").strip()
            if val.startswith("urn:mail:") and "pk" in val.lower():
                out.append(Alias(alias=val))
        return out

    def send(self, invoice, xml, receiver_alias=""):
        self.ensure_supported(invoice)
        doc_type = "EARCHIVE" if invoice.is_earsiv else "EINVOICE"
        data = zip_bytes(f"{invoice.uuid}.xml", xml)
        params = [f"DOCUMENTTYPE={doc_type}", "SIGNED=0"]
        if not invoice.is_earsiv:
            if not receiver_alias:
                raise IntegratorError("Receiver e-Fatura alias is missing. Check the customer's e-Fatura status.")
            params.append(f"ALIAS={receiver_alias}")
        document = soap.el("document", ns=NS, children=[
            soap.el("binaryData", ns=NS_DC, children=[soap.el("Value", base64.b64encode(data).decode(), NS_DC)]),
            soap.el("fileName", f"{invoice.uuid}.zip", NS_DC),
            soap.el("hash", hashlib.md5(data).hexdigest(), NS_DC),
        ])
        sid = self._login()
        try:
            resp = self._call("SendDocument", soap.el("sessionID", sid, NS), self._params(*params), document)
            self._check(resp, "SendDocument")
        finally:
            self._logout(sid)
        return SendResult(ok=True, status="sent", reference=invoice.uuid, message="Accepted by eLogo.")

    def get_status(self, invoice):
        sid = self._login()
        try:
            doc_type = "EARCHIVE" if invoice.is_earsiv else "EINVOICE"
            resp = self._call("GetDocumentStatus", soap.el("sessionID", sid, NS),
                              self._params(f"DOCUMENTTYPE={doc_type}"), soap.el("uuid", invoice.uuid, NS))
        finally:
            self._logout(sid)
        result = soap.find(resp, "GetDocumentStatusResult")
        code = soap.text(result, "Status") or soap.text(result, "resultCode")
        detail = soap.text(result, "Description") or soap.text(result, "resultMsg") or code
        return StatusResult(map_status(f"{code} {detail}"), detail)

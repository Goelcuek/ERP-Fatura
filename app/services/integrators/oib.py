"""İzibiz and EDM Bilişim share a SOAP design: Login returns a SESSION_ID that goes into a
REQUEST_HEADER on every call; invoices are sent with SENDER / RECEIVER / INVOICE/CONTENT
(base64 UBL). They differ in namespaces, endpoints and the e-Arşiv method.
"""

import base64
import uuid
from datetime import datetime

from . import soap
from .base import (DEBUG_FIELD, ENV_FIELD, Alias, ConfigField, Integrator, IntegratorError, SendResult,
                   StatusResult, XmlOptions, map_status)


class OibStyleIntegrator(Integrator):
    NS = ""
    ACTION_PREFIX = ""
    live = True
    verified = False
    capabilities = {"efatura", "earsiv", "lookup", "status"}

    def service_for(self, method):
        return "default"

    def header(self, sid):
        return soap.el("REQUEST_HEADER", children=[
            soap.el("SESSION_ID", sid),
            soap.el("CLIENT_TXN_ID", str(uuid.uuid4())),
            soap.el("ACTION_DATE", datetime.now().strftime("%Y-%m-%dT%H:%M:%S")),
            soap.el("APPLICATION_NAME", "AtolyeERP"),
            soap.el("CHANNEL_NAME", "ERP"),
            soap.el("COMPRESSED", "N"),
        ])

    def _call(self, method, request_name, *children):
        body = soap.el(request_name, ns=self.NS, children=children)
        resp = self.soap_call(self.url(self.service_for(method)), f"{self.ACTION_PREFIX}{method}", body)
        err = soap.find(resp, "ERROR_TYPE")
        if err is None:
            err = soap.find(resp, "REQUEST_ERROR")
        if err is not None and soap.text(err, "ERROR_CODE") not in ("", "0"):
            raise IntegratorError(f"{soap.text(err, 'ERROR_CODE')}: {soap.text(err, 'ERROR_SHORT_DES') or soap.text(err, 'ERROR_LONG_DES')}")
        ret = soap.find(resp, "REQUEST_RETURN")
        if ret is not None and soap.text(ret, "RETURN_CODE") not in ("", "0"):
            raise IntegratorError(f"{method} failed (code {soap.text(ret, 'RETURN_CODE')}).")
        return resp

    def login(self):
        resp = self._call("Login", "LoginRequest", self.header("-1"),
                          soap.el("USER_NAME", self.config.get("username", "")),
                          soap.el("PASSWORD", self.config.get("password", "")))
        sid = soap.text(resp, "SESSION_ID")
        if not sid:
            raise IntegratorError("Login failed. Check username and password.")
        return sid

    def logout(self, sid):
        try:
            self._call("Logout", "LogoutRequest", self.header(sid))
        except IntegratorError:
            pass

    def test_connection(self):
        self.logout(self.login())
        return f"Connected to {self.label} ({self.config.get('environment')})."

    def check_user(self, tax_id):
        sid = self.login()
        try:
            resp = self._call("CheckUser", "CheckUserRequest", self.header(sid),
                              soap.el("USER", children=[soap.el("IDENTIFIER", tax_id)]),
                              soap.el("DOCUMENT_TYPE", "INVOICE"))
        finally:
            self.logout(sid)
        out = []
        for user in soap.find_all(resp, "USER"):
            alias = soap.text(user, "ALIAS")
            if alias and (soap.text(user, "UNIT", "PK") or "PK").upper() == "PK" and "gb" not in alias.lower():
                out.append(Alias(alias=alias, title=soap.text(user, "TITLE")))
        return out

    def party(self, tag, vkn, alias):
        attrib = {"vkn": vkn}
        if alias:
            attrib["alias"] = alias
        return soap.el(tag, attrib=attrib)

    def send(self, invoice, xml, receiver_alias=""):
        self.ensure_supported(invoice)
        from ...models import Setting

        sid = self.login()
        try:
            if invoice.is_earsiv:
                self.send_earsiv(sid, invoice, xml)
            else:
                if not receiver_alias:
                    raise IntegratorError("Receiver e-Fatura alias is missing. Check the customer's e-Fatura status.")
                self._call("SendInvoice", "SendInvoiceRequest", self.header(sid),
                           self.party("SENDER", Setting.get("company.tax_id"), self.config.get("sender_alias", "")),
                           self.party("RECEIVER", invoice.contact.tax_id, receiver_alias),
                           soap.el("INVOICE", children=[soap.el("CONTENT", base64.b64encode(xml).decode())]))
        finally:
            self.logout(sid)
        return SendResult(ok=True, status="sent" if not invoice.is_earsiv else "accepted", reference=invoice.uuid,
                          message=f"Accepted by {self.label}.")

    def send_earsiv(self, sid, invoice, xml):
        raise NotImplementedError

    def get_status(self, invoice):
        sid = self.login()
        try:
            resp = self._call("GetInvoiceStatus", "GetInvoiceStatusRequest", self.header(sid),
                              soap.el("INVOICE", attrib={"UUID": invoice.uuid}))
        finally:
            self.logout(sid)
        st = soap.find(resp, "INVOICE_STATUS")
        code = soap.text(st, "STATUS") or soap.text(st, "GIB_STATUS_CODE")
        detail = soap.text(st, "STATUS_DESCRIPTION") or code
        return StatusResult(map_status(f"{code} {detail}"), detail)


OIB_FIELDS = [
    ENV_FIELD,
    ConfigField("username", "Web service username"),
    ConfigField("password", "Web service password", kind="password"),
    ConfigField("sender_alias", "Sender alias (GB)", help="Optional. Your gönderici birim etiketi"),
]


class IzibizIntegrator(OibStyleIntegrator):
    key = "izibiz"
    label = "İzibiz"
    description = "İzibiz SOAP services: AuthenticationWS, EInvoiceWS (e-Fatura) and EIArchiveWS (e-Arşiv)."
    NS = "http://schemas.i2i.com/ei/wsdl"
    capabilities = {"efatura", "earsiv", "lookup", "status", "cancel"}
    xml_defaults = XmlOptions(embed_xslt=True, earsiv_sending_type=False)
    URLS = {
        "test": {"auth": "https://efaturatest.izibiz.com.tr/AuthenticationWS",
                 "efatura": "https://efaturatest.izibiz.com.tr/EInvoiceWS",
                 "earsiv": "https://efaturatest.izibiz.com.tr/EIArchiveWS/EFaturaArchive"},
        "production": {"auth": "https://efatura.izibiz.com.tr/AuthenticationWS",
                       "efatura": "https://efatura.izibiz.com.tr/EInvoiceWS",
                       "earsiv": "https://efatura.izibiz.com.tr/EIArchiveWS/EFaturaArchive"},
    }
    fields = OIB_FIELDS + [
        ConfigField("url_auth", "AuthenticationWS URL (optional)"),
        ConfigField("url_efatura", "EInvoiceWS URL (optional)"),
        ConfigField("url_earsiv", "EIArchiveWS URL (optional)"),
        DEBUG_FIELD,
    ]

    def service_for(self, method):
        if method in ("Login", "Logout", "CheckUser"):
            return "auth"
        if method in ("WriteToArchiveExtended", "CancelEArchiveInvoice"):
            return "earsiv"
        return "efatura"

    def send_earsiv(self, sid, invoice, xml):
        props = [soap.el("EARSIV_TYPE", "NORMAL"), soap.el("SUB_STATUS", "NEW")]
        if invoice.contact.email:
            props.append(soap.el("EARSIV_EMAIL", invoice.contact.email))
        content = soap.el("ArchiveInvoiceExtendedContent", children=[soap.el("INVOICE_PROPERTIES", children=[
            soap.el("EARSIV_FLAG", "Y"),
            soap.el("EARSIV_PROPERTIES", children=props),
            soap.el("ARCHIVE_INVOICE_CONTENT", base64.b64encode(xml).decode()),
        ])])
        self._call("WriteToArchiveExtended", "ArchiveInvoiceExtendedRequest", self.header(sid), content)

    def cancel(self, invoice):
        if not invoice.is_earsiv:
            raise IntegratorError("Only e-Arşiv invoices can be cancelled; e-Fatura needs a return invoice.")
        sid = self.login()
        try:
            self._call("CancelEArchiveInvoice", "CancelEArchiveInvoiceRequest", self.header(sid),
                       soap.el("CancelEArsivInvoiceContent", children=[
                           soap.el("FATURA_UUID", invoice.uuid),
                           soap.el("IPTAL_TARIHI", datetime.now().strftime("%Y-%m-%d"))]))
        finally:
            self.logout(sid)
        return StatusResult("cancelled", "Cancelled at the integrator.")


class EdmIntegrator(OibStyleIntegrator):
    key = "edm"
    label = "EDM Bilişim"
    description = "EDM Bilişim EFaturaEDM service (SOAP). e-Arşiv invoices go through the same SendInvoice call."
    NS = "http://tempuri.org/"
    ACTION_PREFIX = "http://tempuri.org/IEFaturaEDM/"
    xml_defaults = XmlOptions(embed_xslt=True, earsiv_sending_type=True)
    URLS = {
        "test": {"default": "https://test.edmbilisim.com.tr/EFaturaEDM21ea/EFaturaEDM.svc"},
        "production": {"default": "https://portal2.edmbilisim.com.tr/EFaturaEDM/EFaturaEDM.svc"},
    }
    fields = OIB_FIELDS + [ConfigField("url_default", "Service URL (optional)"), DEBUG_FIELD]

    def send_earsiv(self, sid, invoice, xml):
        from ...models import Setting

        self._call("SendInvoice", "SendInvoiceRequest", self.header(sid),
                   self.party("SENDER", Setting.get("company.tax_id"), self.config.get("sender_alias", "")),
                   self.party("RECEIVER", invoice.contact.tax_id or "11111111111", ""),
                   soap.el("INVOICE", children=[soap.el("CONTENT", base64.b64encode(xml).decode())]))

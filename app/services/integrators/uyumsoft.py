"""Uyumsoft e-Fatura / e-Arşiv integration web service (SOAP, WS-Security UsernameToken).

Public test service: https://efatura-test.uyumsoft.com.tr/Services/Integration
(user "Uyumsoft", password "Uyumsoft"). One service handles both e-Fatura and e-Arşiv;
the UBL document is embedded as XML (not base64) inside ``InvoiceInfo``.
"""

from lxml import etree

from . import soap
from .base import (DEBUG_FIELD, ENV_FIELD, Alias, ConfigField, Integrator, IntegratorError, SendResult,
                   StatusResult, XmlOptions, map_status)

NS = "http://tempuri.org/"


class UyumsoftIntegrator(Integrator):
    key = "uyumsoft"
    label = "Uyumsoft"
    description = "Uyumsoft integration web service (SOAP). One service for e-Fatura and e-Arşiv."
    live = True
    verified = False
    capabilities = {"efatura", "earsiv", "lookup", "status", "cancel"}
    # e-Arşiv delivery type travels in EArchiveInvoiceInfo, not in the XML
    xml_defaults = XmlOptions(embed_xslt=True, earsiv_sending_type=False)
    URLS = {
        "test": {"default": "https://efatura-test.uyumsoft.com.tr/Services/Integration"},
        "production": {"default": "https://efatura.uyumsoft.com.tr/Services/Integration"},
    }
    fields = [
        ENV_FIELD,
        ConfigField("username", "Web service username", default="Uyumsoft", help="Test account: Uyumsoft / Uyumsoft"),
        ConfigField("password", "Web service password", kind="password", default="Uyumsoft"),
        ConfigField("url_default", "Service URL (optional)", help="Leave empty for the standard address"),
        DEBUG_FIELD,
    ]

    def _call(self, method, *children):
        body = soap.el(method, ns=NS, children=children)
        header = soap.wsse_header(self.config.get("username", ""), self.config.get("password", ""))
        resp = self.soap_call(self.url(), f"{NS}IIntegration/{method}", body, headers=[header])
        result = soap.find(resp, f"{method}Result")
        if result is None:
            raise IntegratorError(f"Unexpected response to {method}.")
        if (result.get("IsSucceded") or result.get("IsSucceeded") or "true").lower() != "true":
            raise IntegratorError(result.get("Message") or soap.text(result, "Message") or f"{method} failed")
        return result

    def test_connection(self):
        from ...models import Setting

        self._call("IsEInvoiceUser", soap.el("vknTckn", Setting.get("company.tax_id"), NS), soap.el("alias", "", NS))
        return f"Connected to Uyumsoft ({self.config.get('environment')})."

    def check_user(self, tax_id):
        result = self._call("GetUserAliasses", soap.el("vknTckn", tax_id, NS))
        aliases = []
        for node in result.iter():
            if not isinstance(node.tag, str):
                continue
            for val in [node.text or "", *node.attrib.values()]:
                val = val.strip()
                if val.startswith("urn:mail:") and "pk" in val.lower() and val not in [a.alias for a in aliases]:
                    aliases.append(Alias(alias=val, title=node.get("Title", "")))
        return aliases

    def send(self, invoice, xml, receiver_alias=""):
        self.ensure_supported(invoice)
        c = invoice.contact
        info = soap.el("InvoiceInfo", ns=NS, attrib={"LocalDocumentId": invoice.uuid})
        info.append(etree.fromstring(xml))  # the UBL <Invoice> element itself
        target = {"VknTckn": c.tax_id or "11111111111", "Title": c.name}
        if not invoice.is_earsiv:
            if not receiver_alias:
                raise IntegratorError("Receiver e-Fatura alias is missing. Check the customer's e-Fatura status.")
            target["Alias"] = receiver_alias
        info.append(soap.el("TargetCustomer", ns=NS, attrib=target))
        if invoice.is_earsiv:
            ea = {"DeliveryType": "Electronic"}
            info.append(soap.el("EArchiveInvoiceInfo", ns=NS, attrib=ea))
            if c.email:
                info.append(soap.el("Notification", ns=NS, children=[
                    soap.el("Mailing", ns=NS, attrib={"EnableNotification": "true", "To": c.email,
                                                      "Attachment": "Pdf"})]))
        info.append(soap.el("Scenario", "eArchive" if invoice.is_earsiv else "eInvoice", NS))
        result = self._call("SendInvoice", soap.el("invoices", ns=NS, children=[info]))
        value = soap.find(result, "Value")
        ref = (value.get("Id") if value is not None else "") or invoice.uuid
        return SendResult(ok=True, status="sent", reference=ref, message="Accepted by Uyumsoft.")

    def get_status(self, invoice):
        ids = soap.el("invoiceIds", ns=NS, children=[soap.el("string", invoice.uuid, "http://schemas.microsoft.com/2003/10/Serialization/Arrays")])
        result = self._call("QueryOutboxInvoiceStatus", ids)
        value = soap.find(result, "Value")
        if value is None:
            return StatusResult(invoice.status)
        code = value.get("Status") or soap.text(value, "Status") or value.get("StatusCode", "")
        detail = value.get("Message") or value.get("StatusDescription") or code
        return StatusResult(map_status(code + " " + detail), detail)

    def cancel(self, invoice):
        if not invoice.is_earsiv:
            raise IntegratorError("Only e-Arşiv invoices can be cancelled; e-Fatura needs a return invoice.")
        self._call("CancelEArchiveInvoice", soap.el("invoiceId", invoice.uuid, NS))
        return StatusResult("cancelled", "Cancelled at the integrator.")

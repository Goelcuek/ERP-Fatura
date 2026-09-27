"""Special-integrator (özel entegratör) adapter interface.

Every Turkish e-Fatura/e-Arşiv invoice must be submitted to GİB through a licensed
integrator. Each integrator exposes its own API and has its own expectations about
the UBL-TR XML, so the app talks to them through this interface:

* ``check_user`` / ``send`` / ``get_status`` / ``cancel`` — the transport.
* ``xml_defaults`` + ``customize_xml`` — how the canonical invoice XML is adapted
  for this integrator before sending (see ``services/ubl_profile.py``). Admins can
  override the XML options per integrator in Settings.

Adding an integrator = one subclass + registering it in ``integrators/__init__.py``.
"""

import os
import re
from dataclasses import dataclass, field, fields
from datetime import datetime

import requests
from lxml import etree

from . import soap


class IntegratorError(Exception):
    pass


@dataclass
class Alias:
    alias: str  # posta kutusu etiketi, e.g. urn:mail:defaultpk@firma.com.tr
    title: str = ""


@dataclass
class SendResult:
    ok: bool
    status: str  # sent | accepted | exported | error
    reference: str = ""
    message: str = ""


@dataclass
class StatusResult:
    status: str  # sent | accepted | rejected | error | cancelled
    message: str = ""


@dataclass
class ConfigField:
    name: str
    label: str
    kind: str = "text"  # text | password | url | select | checkbox
    default: str = ""
    help: str = ""
    options: list = field(default_factory=list)


@dataclass
class XmlOptions:
    """Integrator-dependent adjustments applied to the canonical UBL-TR XML at send time."""

    embed_xslt: bool = True  # embed the invoice display template (AdditionalDocumentReference, DocumentType XSLT)
    earsiv_sending_type: bool = True  # e-Arşiv delivery type inside the XML (else passed as API metadata)
    uppercase_uuid: bool = True  # ETTN in upper case
    pretty_print: bool = True  # indented XML (some parsers dislike whitespace-only text nodes)

    LABELS = {
        "embed_xslt": "Embed invoice display template (XSLT)",
        "earsiv_sending_type": "Put e-Arşiv delivery type in the XML",
        "uppercase_uuid": "Write ETTN (UUID) in upper case",
        "pretty_print": "Indent the XML",
    }


# Capability keys shown in the UI
CAPABILITIES = {
    "efatura": "e-Fatura",
    "earsiv": "e-Arşiv",
    "lookup": "GİB user lookup",
    "status": "Status query",
    "cancel": "e-Arşiv cancel",
}

ENV_FIELD = ConfigField("environment", "Environment", kind="select", default="test", options=["test", "production"])
DEBUG_FIELD = ConfigField("debug_log", "Log requests and responses (for troubleshooting)", kind="checkbox",
                          help="Written to data/logs with passwords removed. Turn off when everything works.")


class Integrator:
    key = "base"
    label = "Base"
    description = ""
    fields: list = []
    capabilities: set = set()
    live = False  # actually delivers invoices to GİB
    verified = True  # False: implemented from public information, not yet tested against the live test service
    xml_defaults = XmlOptions()
    # environment -> service name -> URL; adapters with several services use several names
    URLS: dict = {}

    def __init__(self, config=None, data_dir=None, session=None):
        self.config = {f.name: f.default for f in self.fields}
        self.config.update(config or {})
        self.data_dir = data_dir
        self.http = session or requests.Session()

    # -- XML dialect ----------------------------------------------------------

    def xml_options(self):
        opts = XmlOptions(**{f.name: getattr(self.xml_defaults, f.name) for f in fields(XmlOptions)})
        for f in fields(XmlOptions):
            v = self.config.get("xml_" + f.name)
            if v in ("1", "0"):
                setattr(opts, f.name, v == "1")
        return opts

    def customize_xml(self, root, invoice):
        """Hook for integrator-specific changes to the parsed UBL tree (in place)."""

    def prepare_xml(self, xml, invoice):
        from ..ubl_profile import apply_profile

        return apply_profile(xml, invoice, self.xml_options(), self.customize_xml)

    # -- capabilities ---------------------------------------------------------

    def test_connection(self) -> str:
        """Return a human readable success message or raise IntegratorError."""
        return "OK"

    def check_user(self, tax_id: str) -> list:
        """Return the e-Fatura aliases for a VKN/TCKN; an empty list means not an e-Fatura user."""
        raise IntegratorError("This integrator cannot query GİB users. Set the e-Fatura flag on the customer by hand.")

    def send(self, invoice, xml: bytes, receiver_alias: str = "") -> SendResult:
        raise NotImplementedError

    def get_status(self, invoice) -> StatusResult:
        return StatusResult(status=invoice.status, message="")

    def cancel(self, invoice) -> StatusResult:
        raise IntegratorError("Cancellation is not supported by this integrator.")

    def ensure_supported(self, invoice):
        need = "earsiv" if invoice.profile == "EARSIVFATURA" else "efatura"
        if need not in self.capabilities:
            from ...i18n import _

            raise IntegratorError(_(
                "The {integ} adapter does not support {what} yet. Use “Manual XML export” for these invoices.",
                integ=_(self.label), what=CAPABILITIES[need]))

    # -- transport helpers ----------------------------------------------------

    def url(self, service="default"):
        custom = (self.config.get("url_" + service) or "").strip()
        if custom:
            return custom
        env = self.config.get("environment") or "test"
        return self.URLS.get(env, self.URLS.get("test", {})).get(service, "")

    def secrets(self):
        return [v for k, v in self.config.items() if v and any(s in k for s in ("password", "api_key", "secret"))]

    def log(self, title, payload):
        if self.config.get("debug_log") != "1" or not self.data_dir:
            return
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", "replace")
        for s in self.secrets():
            payload = payload.replace(s, "***")
        if len(payload) > 20000:
            payload = payload[:20000] + "\n… (truncated)"
        d = os.path.join(self.data_dir, "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"integrator-{datetime.now():%Y-%m}.log"), "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} {self.key} {title}\n{payload}\n")

    def soap_call(self, url, action, body, headers=(), auth=None, timeout=60):
        """POST a SOAP 1.1 request and return the parsed response Body child."""
        data = soap.envelope(body, headers)
        self.log(f"→ {action} {url}", data)
        try:
            resp = self.http.post(url, data=data, auth=auth, timeout=timeout,
                                  headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{action}"'})
        except requests.RequestException as e:
            raise IntegratorError(f"Cannot reach integrator: {e}")
        self.log(f"← {resp.status_code}", resp.content or b"")
        if resp.status_code in (401, 403):
            raise IntegratorError(f"Integrator rejected the credentials ({resp.status_code}).")
        try:
            root = etree.fromstring(resp.content)
        except (etree.XMLSyntaxError, ValueError):
            raise IntegratorError(f"Unexpected response from integrator (HTTP {resp.status_code}).")
        fault = soap.fault_message(root)
        if fault:
            raise IntegratorError(fault)
        if resp.status_code >= 400:
            raise IntegratorError(f"Integrator error HTTP {resp.status_code}.")
        body_el = soap.find(root, "Body")
        return body_el[0] if body_el is not None and len(body_el) else root


def map_status(code_text):
    """Heuristic mapping of an integrator's status text/code to our status names."""
    c = (code_text or "").lower().replace("i\u0307", "i")  # "İ".lower() leaves a combining dot

    def has(*words):  # match at the start of a word only ("red" must not match "delivered")
        return any(re.search(r"(?<![a-zçğıöşü])" + re.escape(w), c) for w in words)

    if has("reject", "red", "reddedil"):
        return "rejected"
    if has("cancel", "iptal"):
        return "cancelled"
    if has("error", "hata", "fail", "başarısız", "basarisiz"):
        return "error"
    if has("accept", "kabul", "onay", "approved", "succe", "başarı", "basari", "1300", "tamam", "deliver", "iletildi"):
        return "accepted"
    return "sent"

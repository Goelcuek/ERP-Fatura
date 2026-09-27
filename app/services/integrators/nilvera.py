"""Nilvera REST adapter (https://developer.nilvera.com).

Endpoint paths are kept in one place below. Verify them against the integrator's
current documentation with a test account (apitest.nilvera.com) before going live.
"""

import requests

from .base import Alias, ConfigField, Integrator, IntegratorError, SendResult, StatusResult


class NilveraIntegrator(Integrator):
    key = "nilvera"
    label = "Nilvera (REST API)"
    description = "Sends e-Fatura and e-Arşiv invoices through the Nilvera REST API using an API key."
    live = True
    fields = [
        ConfigField("environment", "Environment", kind="select", default="test", options=["test", "production"]),
        ConfigField("api_key", "API key", kind="password", help="Nilvera portal → Settings → API"),
        ConfigField("sender_alias", "Sender alias (GB)", default="", help="Optional. Your gönderici birim etiketi"),
    ]

    BASE_URLS = {"test": "https://apitest.nilvera.com", "production": "https://api.nilvera.com"}
    PATH_CHECK_USER = "/general/GlobalCompany/Check/TaxNumber/{tax_id}"
    PATH_COMPANY = "/general/Company"
    PATH_SEND_EFATURA = "/einvoice/Send/Xml"
    PATH_SEND_EARSIV = "/earchive/Send/Xml"
    PATH_STATUS_EFATURA = "/einvoice/Sale/{uuid}/Status"
    PATH_STATUS_EARSIV = "/earchive/Invoices/{uuid}/Status"
    PATH_CANCEL_EARSIV = "/earchive/Invoices/Cancel"
    TIMEOUT = 30

    def __init__(self, config=None, data_dir=None, session=None):
        super().__init__(config, data_dir)
        self.http = session or requests.Session()

    # -- helpers ------------------------------------------------------------

    @property
    def base_url(self):
        return self.BASE_URLS.get(self.config.get("environment"), self.BASE_URLS["test"])

    def _headers(self):
        key = (self.config.get("api_key") or "").strip()
        if not key:
            raise IntegratorError("API key is not configured.")
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    def _request(self, method, path, **kw):
        try:
            resp = self.http.request(method, self.base_url + path, headers=self._headers(), timeout=self.TIMEOUT, **kw)
        except requests.RequestException as e:
            raise IntegratorError(f"Cannot reach integrator: {e}")
        if resp.status_code == 401:
            raise IntegratorError("Integrator rejected the API key (401).")
        if resp.status_code >= 400:
            raise IntegratorError(f"Integrator error {resp.status_code}: {_error_text(resp)}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -- capabilities -------------------------------------------------------

    def test_connection(self):
        data = self._request("GET", self.PATH_COMPANY)
        name = ""
        if isinstance(data, dict):
            name = data.get("Name") or data.get("Title") or ""
        return f"Connected to Nilvera ({self.config.get('environment')}). {name}".strip()

    def check_user(self, tax_id):
        data = self._request("GET", self.PATH_CHECK_USER.format(tax_id=tax_id), params={"globalUserType": "Invoice"})
        items = data if isinstance(data, list) else (data or {}).get("Content") or []
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            alias = it.get("Name") or it.get("Alias") or ""
            if alias and it.get("Type", "PK") in ("PK", "Invoice", None, "") and "urn:mail" in alias:
                out.append(Alias(alias=alias, title=it.get("Title") or ""))
        return out

    def send(self, invoice, xml, receiver_alias=""):
        earsiv = invoice.profile == "EARSIVFATURA"
        path = self.PATH_SEND_EARSIV if earsiv else self.PATH_SEND_EFATURA
        params = {}
        if not earsiv:
            if not receiver_alias:
                raise IntegratorError("Receiver e-Fatura alias is missing. Check the customer's e-Fatura status.")
            params["Alias"] = receiver_alias
        files = {"file": (f"{invoice.uuid}.xml", xml, "application/xml")}
        data = self._request("POST", path, params=params, files=files)
        ref = ""
        if isinstance(data, dict):
            ref = data.get("UUID") or data.get("InvoiceNumber") or ""
        return SendResult(ok=True, status="sent", reference=ref or invoice.uuid, message="Accepted by Nilvera.")

    def get_status(self, invoice):
        path = self.PATH_STATUS_EARSIV if invoice.is_earsiv else self.PATH_STATUS_EFATURA
        data = self._request("GET", path.format(uuid=invoice.uuid))
        if not isinstance(data, dict):
            return StatusResult(status=invoice.status, message=str(data or ""))
        code = str(data.get("StatusCode") or data.get("Status") or "").lower()
        detail = data.get("StatusDetail") or data.get("Message") or code
        if any(k in code for k in ("reject", "red")):
            return StatusResult("rejected", detail)
        if any(k in code for k in ("error", "hata", "fail")):
            return StatusResult("error", detail)
        if any(k in code for k in ("cancel", "iptal")):
            return StatusResult("cancelled", detail)
        if any(k in code for k in ("accept", "kabul", "succeed", "success", "1300", "approved")):
            return StatusResult("accepted", detail)
        return StatusResult("sent", detail)

    def cancel(self, invoice):
        if not invoice.is_earsiv:
            raise IntegratorError("Only e-Arşiv invoices can be cancelled; e-Fatura needs a return invoice.")
        self._request("POST", self.PATH_CANCEL_EARSIV, json=[invoice.uuid])
        return StatusResult("cancelled", "Cancelled at Nilvera.")


def _error_text(resp):
    try:
        data = resp.json()
        if isinstance(data, dict):
            errs = data.get("Errors") or data.get("errors")
            if errs:
                return "; ".join(str(e.get("Description") if isinstance(e, dict) else e) for e in errs)
            return data.get("Message") or data.get("message") or resp.text[:300]
    except ValueError:
        pass
    return resp.text[:300]


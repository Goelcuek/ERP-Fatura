"""Sandbox integrator: never contacts GİB. Useful for training and demos."""

import hashlib

from .base import Alias, Integrator, IntegratorError, SendResult, StatusResult


class MockIntegrator(Integrator):
    key = "mock"
    label = "Sandbox (test mode)"
    description = (
        "Simulates an integrator without sending anything to GİB. Every 10-digit VKN is treated as an "
        "e-Fatura user; TCKNs are not. Use this for training, then switch to your real integrator."
    )
    live = False
    capabilities = {"efatura", "earsiv", "lookup", "status", "cancel"}

    def test_connection(self):
        return "Sandbox is always reachable."

    def check_user(self, tax_id):
        tax_id = (tax_id or "").strip()
        if len(tax_id) == 10 and tax_id.isdigit():
            return [Alias(alias=f"urn:mail:defaultpk@{tax_id}.test", title="Test Mükellefi")]
        return []

    def send(self, invoice, xml, receiver_alias=""):
        if b"<cbc:ID>" not in xml:
            raise IntegratorError("Invalid XML")
        ref = hashlib.sha1(xml).hexdigest()[:12].upper()
        # Basic profiles and e-Arşiv are final as soon as they are delivered.
        status = "sent" if invoice.profile == "TICARIFATURA" else "accepted"
        return SendResult(ok=True, status=status, reference="SBX-" + ref, message="Delivered to sandbox.")

    def get_status(self, invoice):
        # Commercial invoices are "accepted" by the receiver in the sandbox on the next status query.
        if invoice.status == "sent":
            return StatusResult(status="accepted", message="Receiver accepted the invoice (sandbox).")
        return StatusResult(status=invoice.status)

    def cancel(self, invoice):
        if invoice.profile != "EARSIVFATURA":
            raise IntegratorError("Only e-Arşiv invoices can be cancelled; e-Fatura needs a return invoice.")
        return StatusResult(status="cancelled", message="Cancelled in sandbox.")

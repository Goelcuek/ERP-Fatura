"""Manual mode: write UBL-TR XML files to a folder for upload in the integrator's web portal.

Works with *any* integrator (including the one the business already uses) because every
integrator portal accepts UBL-TR XML uploads. Status is then updated by hand.
"""

import os

from .base import ConfigField, Integrator, IntegratorError, SendResult, StatusResult


class FileExportIntegrator(Integrator):
    key = "file"
    label = "Manual XML export"
    description = (
        "Writes each issued invoice as a UBL-TR XML file into a folder. Upload the files in your "
        "integrator's web portal, then mark the invoice as sent. Works with any integrator."
    )
    fields = [
        ConfigField("folder", "Export folder", default="", help="Leave empty to use data/outbox"),
    ]
    live = False
    capabilities = {"efatura", "earsiv", "cancel"}

    def _folder(self):
        folder = self.config.get("folder") or os.path.join(self.data_dir or ".", "outbox")
        os.makedirs(folder, exist_ok=True)
        return folder

    def test_connection(self):
        folder = self._folder()
        probe = os.path.join(folder, ".write-test")
        try:
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
        except OSError as e:
            raise IntegratorError(f"Folder is not writable: {e}")
        return f"Folder is writable: {folder}"

    def check_user(self, tax_id):
        raise IntegratorError("Manual mode cannot query GİB. Set the e-Fatura flag on the customer by hand.")

    def send(self, invoice, xml, receiver_alias=""):
        path = os.path.join(self._folder(), f"{invoice.number}.xml")
        with open(path, "wb") as fh:
            fh.write(xml)
        return SendResult(ok=True, status="exported", reference=path, message=f"XML written to {path}")

    def get_status(self, invoice):
        return StatusResult(status=invoice.status, message="Update the status manually in manual mode.")

    def cancel(self, invoice):
        return StatusResult(status="cancelled", message="Marked as cancelled. Also cancel it in the integrator portal.")

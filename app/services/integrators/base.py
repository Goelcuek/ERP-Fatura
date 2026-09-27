"""Special-integrator (özel entegratör) adapter interface.

Every Turkish e-Fatura/e-Arşiv invoice must be submitted to GİB through a licensed
integrator. Each integrator exposes its own API, so the app talks to them through
this small interface. Adding a new integrator = one subclass + registering it in
``integrators/__init__.py``.
"""

from dataclasses import dataclass, field


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


class Integrator:
    key = "base"
    label = "Base"
    description = ""
    fields: list = []
    # Whether this integrator actually delivers invoices to GİB
    live = False

    def __init__(self, config=None, data_dir=None):
        self.config = {f.name: f.default for f in self.fields}
        self.config.update(config or {})
        self.data_dir = data_dir

    # -- capabilities -------------------------------------------------------

    def test_connection(self) -> str:
        """Return a human readable success message or raise IntegratorError."""
        return "OK"

    def check_user(self, tax_id: str) -> list:
        """Return the e-Fatura aliases for a VKN/TCKN; an empty list means not an e-Fatura user."""
        raise NotImplementedError

    def send(self, invoice, xml: bytes, receiver_alias: str = "") -> SendResult:
        raise NotImplementedError

    def get_status(self, invoice) -> StatusResult:
        return StatusResult(status=invoice.status, message="")

    def cancel(self, invoice) -> StatusResult:
        raise IntegratorError("Cancellation is not supported by this integrator.")

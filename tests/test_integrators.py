from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.services.integrators import IntegratorError
from app.services.integrators.file_export import FileExportIntegrator
from app.services.integrators.mock import MockIntegrator
from app.services.integrators.nilvera import NilveraIntegrator


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data
        self.content = b"x" if data is not None else b""
        self.text = str(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


def inv(profile="TICARIFATURA"):
    return SimpleNamespace(profile=profile, uuid="abc-123", number="EFT2026000000001", status="issued",
                           is_earsiv=profile == "EARSIVFATURA", issue_date=date.today(), payable=D("1"))


def test_mock_integrator():
    m = MockIntegrator()
    assert m.check_user("1234567890")[0].alias.startswith("urn:mail:")
    assert m.check_user("10000000146") == []
    r = m.send(inv(), b"<cbc:ID>1</cbc:ID>")
    assert r.ok and r.status == "sent"
    assert m.send(inv("EARSIVFATURA"), b"<cbc:ID>1</cbc:ID>").status == "accepted"
    with pytest.raises(IntegratorError):
        m.cancel(inv())


def test_file_export(tmp_path):
    f = FileExportIntegrator(config={"folder": str(tmp_path)})
    assert "writable" in f.test_connection()
    r = f.send(inv(), b"<xml/>")
    assert r.status == "exported"
    assert (tmp_path / "EFT2026000000001.xml").read_bytes() == b"<xml/>"


def test_nilvera_requires_key():
    n = NilveraIntegrator(config={"api_key": ""}, session=FakeSession([]))
    with pytest.raises(IntegratorError, match="API key"):
        n.check_user("1234567890")


def test_nilvera_check_user_and_send():
    s = FakeSession([
        FakeResp(200, [{"Name": "urn:mail:defaultpk@firma.com", "Type": "PK", "Title": "Firma"},
                       {"Name": "urn:mail:defaultgb@firma.com", "Type": "GB"}]),
        FakeResp(200, {"UUID": "abc-123", "InvoiceNumber": "EFT2026000000001"}),
    ])
    n = NilveraIntegrator(config={"api_key": "k", "environment": "test"}, session=s)
    aliases = n.check_user("1234567890")
    assert [a.alias for a in aliases] == ["urn:mail:defaultpk@firma.com"]
    method, url, kw = s.calls[0]
    assert url == "https://apitest.nilvera.com/general/GlobalCompany/Check/TaxNumber/1234567890"
    assert kw["headers"]["Authorization"] == "Bearer k"

    r = n.send(inv(), b"<xml/>", receiver_alias="urn:mail:defaultpk@firma.com")
    assert r.ok and r.reference == "abc-123"
    method, url, kw = s.calls[1]
    assert method == "POST" and url.endswith("/einvoice/Send/Xml")
    assert kw["params"] == {"Alias": "urn:mail:defaultpk@firma.com"}


def test_nilvera_errors_are_readable():
    s = FakeSession([FakeResp(400, {"Errors": [{"Description": "Fatura numarası daha önce kullanılmış"}]})])
    n = NilveraIntegrator(config={"api_key": "k"}, session=s)
    with pytest.raises(IntegratorError, match="daha önce"):
        n.send(inv("EARSIVFATURA"), b"<xml/>")


def test_nilvera_status_mapping():
    s = FakeSession([FakeResp(200, {"StatusCode": "Rejected", "StatusDetail": "Alıcı reddetti"})])
    n = NilveraIntegrator(config={"api_key": "k"}, session=s)
    assert n.get_status(inv()).status == "rejected"

"""Adapters are exercised against a fake HTTP session: we check the request each one builds
(method, auth, key fields) and that canned integrator responses are parsed correctly."""

import base64
import io
import json
import zipfile
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from lxml import etree

from app.services.integrators import REGISTRY, IntegratorError
from app.services.integrators import soap
from app.services.integrators.base import map_status

SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
UBL = b'<?xml version="1.0"?><Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"><x>1</x></Invoice>'


def envelope(inner):
    return (f'<s:Envelope xmlns:s="{SOAP}"><s:Body>{inner}</s:Body></s:Envelope>').encode()


class Resp:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status
        self.text = content.decode() if isinstance(content, bytes) else str(content)


class FakeSoap:
    """Returns queued responses; records (url, headers, parsed body, auth)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, data=None, headers=None, auth=None, timeout=None):
        body = etree.fromstring(data)
        self.calls.append(SimpleNamespace(url=url, headers=headers, auth=auth, root=body,
                                          op=soap.local(soap.find(body, "Body")[0])))
        return Resp(envelope(self.responses.pop(0)) if not isinstance(self.responses[0], Resp) else self.responses.pop(0).content)


def invoice(profile="TICARIFATURA", email="a@b.c"):
    contact = SimpleNamespace(tax_id="9870011223", name="Yıldız A.Ş.", email=email, efatura_alias="urn:mail:defaultpk@y")
    return SimpleNamespace(profile=profile, is_earsiv=profile == "EARSIVFATURA", uuid="11111111-2222-3333-4444-555555555555",
                           number="EFT2026000000001", status="sent", contact=contact, integrator_ref="REF1",
                           issue_date=date(2026, 9, 1), payable=D("100"))


def make(key, session, **cfg):
    return REGISTRY[key](config={"username": "u", "password": "p", **cfg}, data_dir=None, session=session)


def test_every_adapter_declares_metadata():
    for key, cls in REGISTRY.items():
        assert cls.label and cls.description and cls.capabilities, key
        if cls.live:
            assert cls.URLS["test"] and cls.URLS["production"], key
            inst = cls(config={})
            assert inst.url(next(iter(cls.URLS["test"]))).startswith("https://"), key


def test_custom_url_overrides_default():
    u = make("uyumsoft", None, url_default="https://example.test/svc")
    assert u.url() == "https://example.test/svc"
    assert make("uyumsoft", None, environment="production").url() == "https://efatura.uyumsoft.com.tr/Services/Integration"


# ---------------------------------------------------------------- Uyumsoft

def test_uyumsoft_send_embeds_ubl_and_uses_wsse(app, ctx):
    s = FakeSoap('<SendInvoiceResponse xmlns="http://tempuri.org/"><SendInvoiceResult IsSucceded="true" Message="">'
                 '<Value Id="ABC" Number="EFT2026000000001"/></SendInvoiceResult></SendInvoiceResponse>')
    r = make("uyumsoft", s).send(invoice(), UBL, receiver_alias="urn:mail:defaultpk@y")
    assert r.reference == "ABC"
    call = s.calls[0]
    assert call.op == "SendInvoice"
    assert call.headers["SOAPAction"] == '"http://tempuri.org/IIntegration/SendInvoice"'
    assert soap.text(call.root, "Username") == "u" and soap.text(call.root, "Password") == "p"
    inv_el = soap.find(call.root, "InvoiceInfo")
    assert soap.find(inv_el, "Invoice").tag == "{urn:oasis:names:specification:ubl:schema:xsd:Invoice-2}Invoice"
    assert soap.find(inv_el, "TargetCustomer").get("Alias") == "urn:mail:defaultpk@y"
    assert soap.text(inv_el, "Scenario") == "eInvoice"


def test_uyumsoft_earsiv_and_error(app, ctx):
    s = FakeSoap('<SendInvoiceResponse><SendInvoiceResult IsSucceded="true"><Value Id="X"/></SendInvoiceResult></SendInvoiceResponse>',
                 '<SendInvoiceResponse><SendInvoiceResult IsSucceded="false" Message="Fatura numarası mükerrer"/></SendInvoiceResponse>')
    u = make("uyumsoft", s)
    u.send(invoice("EARSIVFATURA"), UBL)
    info = soap.find(s.calls[0].root, "InvoiceInfo")
    assert soap.find(info, "EArchiveInvoiceInfo").get("DeliveryType") == "Electronic"
    assert soap.find(info, "Mailing").get("To") == "a@b.c"
    with pytest.raises(IntegratorError, match="mükerrer"):
        u.send(invoice("EARSIVFATURA"), UBL)


def test_uyumsoft_lookup_parses_aliases(app, ctx):
    s = FakeSoap('<GetUserAliassesResponse><GetUserAliassesResult IsSucceded="true"><Value>'
                 '<Receiver Alias="urn:mail:defaultpk@firma.com" Title="Firma"/><Sender Alias="urn:mail:defaultgb@firma.com"/>'
                 '</Value></GetUserAliassesResult></GetUserAliassesResponse>')
    aliases = make("uyumsoft", s).check_user("1234567890")
    assert [a.alias for a in aliases] == ["urn:mail:defaultpk@firma.com"]


def test_soap_fault_becomes_readable_error(app, ctx):
    fault = Resp(envelope('<s:Fault><faultcode>s:Client</faultcode><faultstring>Kullanıcı adı veya şifre hatalı</faultstring></s:Fault>'), 500)
    s = FakeSoap(fault)
    with pytest.raises(IntegratorError, match="şifre hatalı"):
        make("uyumsoft", s).check_user("1")


# ---------------------------------------------------------------- QNB

def test_qnb_efatura_and_earsiv(app, ctx):
    s = FakeSoap('<ns2:belgeGonderResponse xmlns:ns2="http://service.connector.uut.cs.com.tr/"><return>OID-9</return></ns2:belgeGonderResponse>',
                 '<ns2:faturaOlusturResponse xmlns:ns2="x"><return><resultCode>AE00000</resultCode></return></ns2:faturaOlusturResponse>',
                 '<ns2:faturaOlusturResponse xmlns:ns2="x"><return><resultCode>AE00042</resultCode><resultText>Hatalı VKN</resultText></return></ns2:faturaOlusturResponse>')
    q = make("qnb", s, sube="MRKZ")
    assert q.send(invoice(), UBL).reference == "OID-9"
    call = s.calls[0]
    assert call.op == "belgeGonder" and "connectorService" in call.url
    assert base64.b64decode(soap.text(call.root, "veri")) == UBL
    assert soap.text(call.root, "belgeTuru") == "FATURA_UBL"
    assert soap.text(call.root, "vergiTcKimlikNo") == "1234567890"  # company VKN from settings

    assert q.send(invoice("EARSIVFATURA"), UBL).status == "accepted"
    call = s.calls[1]
    assert call.op == "faturaOlustur" and "EarsivWebService" in call.url
    params = json.loads(soap.text(call.root, "input"))
    assert params["sube"] == "MRKZ" and params["islemId"] == invoice().uuid
    with pytest.raises(IntegratorError, match="AE00042"):
        q.send(invoice("EARSIVFATURA"), UBL)


def test_qnb_status(app, ctx):
    s = FakeSoap('<r><return><yanitDurumu>KABUL</yanitDurumu><aciklama>ok</aciklama></return></r>')
    assert make("qnb", s).get_status(invoice()).status == "accepted"


# ---------------------------------------------------------------- Logo

def test_logo_session_zip_and_logout(app, ctx):
    s = FakeSoap('<LoginResponse><LoginResult>true</LoginResult><sessionID>S1</sessionID></LoginResponse>',
                 '<SendDocumentResponse><SendDocumentResult><resultCode>1</resultCode></SendDocumentResult></SendDocumentResponse>',
                 '<LogoutResponse/>')
    make("logo", s).send(invoice(), UBL, receiver_alias="urn:mail:defaultpk@y")
    assert [c.op for c in s.calls] == ["Login", "SendDocument", "Logout"]
    send = s.calls[1].root
    assert soap.text(send, "sessionID") == "S1"
    params = [n.text for n in soap.find_all(soap.find(send, "paramList"), "string")]
    assert "DOCUMENTTYPE=EINVOICE" in params and "ALIAS=urn:mail:defaultpk@y" in params
    data = base64.b64decode(soap.text(send, "Value"))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.read(zf.namelist()[0]) == UBL


def test_logo_bad_login(app, ctx):
    s = FakeSoap('<LoginResponse><LoginResult>false</LoginResult></LoginResponse>')
    with pytest.raises(IntegratorError, match="login"):
        make("logo", s).test_connection()


def test_logo_send_error_still_logs_out(app, ctx):
    s = FakeSoap('<LoginResponse><LoginResult>true</LoginResult><sessionID>S1</sessionID></LoginResponse>',
                 '<SendDocumentResponse><SendDocumentResult><resultCode>-5</resultCode><resultMsg>Şema hatası</resultMsg></SendDocumentResult></SendDocumentResponse>',
                 '<LogoutResponse/>')
    with pytest.raises(IntegratorError, match="Şema hatası"):
        make("logo", s).send(invoice("EARSIVFATURA"), UBL)
    assert s.calls[-1].op == "Logout"


# ---------------------------------------------------------------- İzibiz / EDM

LOGIN_OK = '<LoginResponse><SESSION_ID>SID-7</SESSION_ID></LoginResponse>'


def test_izibiz_efatura_uses_services_and_session(app, ctx):
    s = FakeSoap(LOGIN_OK, '<SendInvoiceResponse><REQUEST_RETURN><RETURN_CODE>0</RETURN_CODE></REQUEST_RETURN></SendInvoiceResponse>',
                 '<LogoutResponse/>')
    make("izibiz", s, sender_alias="urn:mail:defaultgb@me").send(invoice(), UBL, receiver_alias="urn:mail:defaultpk@y")
    login, send, logout = s.calls
    assert "AuthenticationWS" in login.url and "EInvoiceWS" in send.url
    assert soap.text(send.root, "SESSION_ID") == "SID-7"
    assert soap.find(send.root, "RECEIVER").get("alias") == "urn:mail:defaultpk@y"
    assert soap.find(send.root, "SENDER").get("alias") == "urn:mail:defaultgb@me"
    assert base64.b64decode(soap.text(send.root, "CONTENT")) == UBL


def test_izibiz_earsiv_and_error(app, ctx):
    s = FakeSoap(LOGIN_OK, '<ArchiveInvoiceExtendedResponse><REQUEST_RETURN><RETURN_CODE>0</RETURN_CODE></REQUEST_RETURN></ArchiveInvoiceExtendedResponse>',
                 '<LogoutResponse/>', LOGIN_OK,
                 '<SendInvoiceResponse><ERROR_TYPE><ERROR_CODE>10005</ERROR_CODE><ERROR_SHORT_DES>Alıcı etiketi bulunamadı</ERROR_SHORT_DES></ERROR_TYPE></SendInvoiceResponse>',
                 '<LogoutResponse/>')
    iz = make("izibiz", s)
    assert iz.send(invoice("EARSIVFATURA"), UBL).status == "accepted"
    ear = s.calls[1]
    assert "EIArchiveWS" in ear.url and soap.text(ear.root, "EARSIV_EMAIL") == "a@b.c"
    with pytest.raises(IntegratorError, match="10005"):
        iz.send(invoice(), UBL, receiver_alias="urn:mail:x")


def test_edm_lookup(app, ctx):
    s = FakeSoap(LOGIN_OK, '<CheckUserResponse><USER><IDENTIFIER>1234567890</IDENTIFIER><ALIAS>urn:mail:defaultpk@edm.com</ALIAS>'
                           '<TITLE>Firma</TITLE></USER><USER><ALIAS>urn:mail:defaultgb@edm.com</ALIAS></USER></CheckUserResponse>',
                 '<LogoutResponse/>')
    aliases = make("edm", s).check_user("1234567890")
    assert [a.alias for a in aliases] == ["urn:mail:defaultpk@edm.com"]
    assert s.calls[1].headers["SOAPAction"] == '"http://tempuri.org/IEFaturaEDM/CheckUser"'


# ---------------------------------------------------------------- Sovos

def test_sovos_basic_auth_zip_and_earsiv_unsupported(app, ctx):
    s = FakeSoap('<sendUBLResponse><Response><EnvUUID>ENV-1</EnvUUID></Response></sendUBLResponse>')
    sv = make("sovos", s, sender_alias="urn:mail:defaultgb@me")
    assert sv.send(invoice(), UBL, receiver_alias="urn:mail:defaultpk@y").reference == "ENV-1"
    call = s.calls[0]
    assert call.auth == ("u", "p")
    assert soap.text(call.root, "ReceiverIdentifier") == "urn:mail:defaultpk@y"
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(soap.text(call.root, "DocData")))) as zf:
        assert zf.read(zf.namelist()[0]) == UBL
    with pytest.raises(IntegratorError, match="Manual XML export|Elle XML"):
        sv.send(invoice("EARSIVFATURA"), UBL)
    with pytest.raises(IntegratorError):
        sv.check_user("1")


def test_status_mapping():
    assert map_status("Delivered") == "accepted"
    assert map_status("Reddedildi") == "rejected"
    assert map_status("İptal edildi") == "cancelled"
    assert map_status("KUYRUKTA") == "sent"


def test_debug_log_redacts_password(app, tmp_path):
    s = FakeSoap('<IsEInvoiceUserResponse><IsEInvoiceUserResult IsSucceded="true"/></IsEInvoiceUserResponse>')
    u = REGISTRY["uyumsoft"](config={"username": "u", "password": "Sup3rSecret", "debug_log": "1"},
                             data_dir=str(tmp_path), session=s)
    with app.test_request_context():
        u.test_connection()
    log = next((tmp_path / "logs").iterdir()).read_text()
    assert "IsEInvoiceUser" in log and "Sup3rSecret" not in log and "***" in log

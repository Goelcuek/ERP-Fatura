import json
from datetime import date
from decimal import Decimal as D

import pytest

from app.extensions import db
from app.models import (Account, AssistantConversation, Contact, Invoice, Product, ServiceOrder, ServiceOrderLine,
                        Setting, User)
from app.services.assistant import agent, commands
from app.services.assistant.llm import LLMError, OpenAICompatibleBackend, ToolCall, Turn, parse_message


# ---------------------------------------------------------------- command parser

@pytest.mark.parametrize("text,intent,ref,extra", [
    ("Bu işi bitirdim", "ready", ("this",), ""),
    ("bu işi bitirdim kömürleri ve şalteri değiştirdim", "ready", ("this",), "Kömürleri ve şalteri değiştirdim"),
    ("SRV-2026-00240 hazır, rotor değişti ve test edildi.", "ready", ("number", "SRV-2026-00240"), "Rotor değişti ve test edildi"),
    ("srv 2026 240 tamamlandı", "ready", ("number", "SRV-2026-00240"), ""),
    ("240 numaralı iş parça bekliyor", "awaiting_parts", ("seq", 240), ""),
    ("onarıma başladım", "in_repair", None, ""),
    ("Müşteri onayladı", "approved", None, ""),
    ("teslim ettim", "delivered", None, ""),
    ("teklif verdim", "awaiting_approval", None, ""),
    ("kayıt 12 iptal edildi", "cancelled", ("seq", 12), ""),
    ("not ekle: müşteri cuma günü alacak", "note", None, "müşteri cuma günü alacak"),
    ("I finished this job, replaced the brushes", "ready", ("this",), "Replaced the brushes"),
])
def test_parse_commands(text, intent, ref, extra):
    c = commands.parse(text)
    assert (c.intent, c.ref, c.extra) == (intent, ref, extra)


@pytest.mark.parametrize("text", ["240 hazır mı?", "hazır değil", "Yıldız İnşaatın borcu ne kadar",
                                  "Makita avuç taşlama geldi", "", "bugün hangi işler var"])
def test_parse_leaves_other_text_to_the_model(text):
    assert commands.parse(text) is None


def test_normalize_keeps_length():
    s = "İŞİ BİTİRDİM, Iğdır!"
    assert len(commands.normalize(s)) == len(s)
    assert commands.normalize(s).startswith("işi bitirdim")


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def shop(app):
    with app.app_context():
        tech = User.query.filter_by(username="staff").one()
        tech.is_technician = True
        c = Contact(name="Anadolu Metal", tax_id="0680045512", district="A", city="B", efatura_user=True,
                    efatura_alias="urn:mail:defaultpk@x")
        db.session.add(c)
        db.session.add(Account(name="Kasa", kind="cash"))
        db.session.add(Product(code="RT", name="Rotor 18V", kind="part", price=D("1850"), stock_qty=D("5")))
        orders = []
        for i, st in enumerate(["in_repair", "received", "ready"], start=1):
            o = ServiceOrder(number=f"SRV-{date.today().year}-{i:05d}", contact=c, status=st, device_type="Matkap",
                             brand="Bosch", model=f"GSB {i}", technician=tech if i == 1 else None)
            db.session.add(o)
            orders.append(o)
        db.session.commit()
        return {"tech_id": tech.id, "orders": [o.id for o in orders], "contact_id": c.id}


class FakeLLM:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls = []

    def chat(self, messages, tools):
        self.calls.append(json.loads(json.dumps(messages)))
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return t


def session(app, user="staff", page=None, llm=None, conv=None):
    u = User.query.filter_by(username=user).one()
    if conv is None:
        conv = AssistantConversation(user_id=u.id)
        db.session.add(conv)
        db.session.flush()
    return agent.Session(conv, u, page=page or {}, llm=llm)


def texts(res):
    return [i["text"] for i in res["items"]]


# ---------------------------------------------------------------- fast path

def test_this_job_on_order_page_with_work_done_and_undo(app, shop):
    oid = shop["orders"][1]
    with app.test_request_context():
        s = session(app, page={"order_id": oid})
        res = s.message("bu işi bitirdim, kömürleri değiştirdim")
        o = db.session.get(ServiceOrder, oid)
        assert o.status == "ready" and o.work_done == "Kömürleri değiştirdim"
        assert o.events[0].source == "assistant"
        reply = res["items"][-1]
        assert "Teslime hazır" in reply["text"] and res["changed"]
        assert reply["undo"][0]["status"] == "received"
        res = s.undo(reply["id"], 0)
        assert db.session.get(ServiceOrder, oid).status == "received"
        assert res["undo_update"] and "Geri alındı" in texts(res)[-1]


def test_technician_single_active_job_is_picked(app, shop):
    with app.test_request_context():
        s = session(app)
        s.message("parça bekliyor")
        assert db.session.get(ServiceOrder, shop["orders"][0]).status == "awaiting_parts"


def test_single_candidate_is_applied_directly(app, shop):
    with app.test_request_context():
        s = session(app, user="admin")  # admin has no assigned jobs: all open jobs are candidates
        res = s.message("onarıma başladım")  # only one job (received) can move to in_repair
        assert res["pending"] is None
        assert db.session.get(ServiceOrder, shop["orders"][1]).status == "in_repair"


def test_choice_with_several_candidates(app, shop):
    with app.test_request_context():
        s = session(app, user="admin")
        res = s.message("bu işi bitirdim")  # in_repair + received are both candidates
        assert res["pending"]["type"] == "choice"
        options = res["pending"]["options"]
        assert [o["id"] for o in options] == [shop["orders"][0], shop["orders"][1]]  # in_repair first
        res = s.choose(options[1]["id"])
        assert db.session.get(ServiceOrder, shop["orders"][1]).status == "ready"
        assert res["pending"] is None and res["items"][-1]["undo"]


def test_sequence_reference_and_not_found(app, shop):
    with app.test_request_context():
        s = session(app, user="admin")
        s.message("2 numaralı iş hazır")
        assert db.session.get(ServiceOrder, shop["orders"][1]).status == "ready"
        res = s.message("SRV-2019-00099 hazır")
        assert "bulamadım" in texts(res)[-1]


def test_cancel_needs_confirmation(app, shop):
    oid = shop["orders"][1]
    with app.test_request_context():
        s = session(app, page={"order_id": oid})
        res = s.message("bu işi iptal et")
        assert res["pending"]["type"] == "confirm"
        assert db.session.get(ServiceOrder, oid).status == "received"
        s.confirm({"command": True})
        assert db.session.get(ServiceOrder, oid).status == "cancelled"


def test_note_and_already_status(app, shop):
    oid = shop["orders"][2]
    with app.test_request_context():
        s = session(app, page={"order_id": oid})
        s.message("not ekle: müşteri aradı, cuma alacak")
        assert db.session.get(ServiceOrder, oid).events[0].message == "müşteri aradı, cuma alacak"
        res = s.message("hazır")
        assert "zaten" in texts(res)[-1]


def test_without_model_unknown_text_gets_examples(app, shop):
    with app.test_request_context():
        Setting.set("assistant.use_llm", False)
        res = session(app).message("bugün hava nasıl")
        assert "anlayamadım" in texts(res)[-1]


# ---------------------------------------------------------------- language model path

def call(name, args, cid="c1"):
    return ToolCall(id=cid, name=name, arguments=args, raw_arguments=json.dumps(args))


def test_model_lookup_then_answer(app, shop):
    llm = FakeLLM(Turn(tool_calls=[call("find_orders", {"status": "open"})]),
                  Turn(text="Açık 3 iş var."))
    with app.test_request_context():
        s = session(app, llm=llm, page={"order_id": shop["orders"][0]})
        res = s.message("kaç açık iş var")
        assert texts(res)[-1] == "Açık 3 iş var."
        first = llm.calls[0]
        assert first[0]["role"] == "system" and "Turkish" in first[0]["content"]
        assert "screen: service order SRV-" in first[1]["content"]  # page context reaches the model
        tool_msg = llm.calls[1][-1]
        assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["count"] == 3  # ready-for-pickup is open


def test_model_write_tool_runs_immediately(app, shop):
    oid = shop["orders"][0]
    llm = FakeLLM(Turn(tool_calls=[call("add_order_parts", {"order": str(oid), "part_id": 1, "quantity": 2})]),
                  Turn(text="Eklendi."))
    with app.test_request_context():
        res = session(app, llm=llm).message("rotordan iki tane kullandım bu işe")  # not a status command
        o = db.session.get(ServiceOrder, oid)
        assert len(o.lines) == 1 and o.lines[0].unit_price == D("1850.00") and o.lines[0].qty == D("2")
        assert res["changed"] and res["items"][-1]["links"]


def test_model_confirm_flow(app, shop):
    oid = shop["orders"][0]
    with app.app_context():
        o = db.session.get(ServiceOrder, oid)
        o.lines.append(ServiceOrderLine(description="İşçilik", qty=D("1"), unit_price=D("750"), vat_rate=20))
        db.session.commit()
    llm = FakeLLM(Turn(tool_calls=[call("create_draft_invoice", {"order": str(oid)}, "a")]),
                  Turn(tool_calls=[call("issue_invoice", {"invoice_id": 1}, "b")]),
                  Turn(text="Fatura gönderildi."))
    with app.test_request_context():
        s = session(app, llm=llm)
        res = s.message("bu işin faturasını kes")
        assert res["pending"]["type"] == "confirm"
        assert "Düzenle ve GİB'e gönder: Anadolu Metal · 900,00 ₺" in res["pending"]["calls"][0]["text"]
        assert db.session.get(Invoice, 1).status == "draft"
        res = s.confirm({"b": True})
        assert db.session.get(Invoice, 1).status == "sent"
        assert texts(res)[-1] == "Fatura gönderildi."


def test_model_decline_and_moving_on(app, shop):
    llm = FakeLLM(Turn(tool_calls=[call("adjust_stock", {"part_id": 1, "change": -2}, "x")]),
                  Turn(text="Tamam, değiştirmedim."),
                  Turn(tool_calls=[call("adjust_stock", {"part_id": 1, "change": 5}, "y")]),
                  Turn(text="Başka?"))
    with app.test_request_context():
        s = session(app, llm=llm)
        s.message("rotor stoğunu 2 azalt")
        s.confirm({"x": False})
        assert db.session.get(Product, 1).stock_qty == D("5")
        assert json.loads(llm.calls[1][-1]["content"])["declined"] is True
        s.message("rotor stoğunu 5 artır")
        s.message("boşver")  # new message while a confirmation is pending declines it
        assert db.session.get(Product, 1).stock_qty == D("5")
        tool_msgs = [m for m in llm.calls[-1] if m["role"] == "tool"]
        assert json.loads(tool_msgs[-1]["content"])["declined"] is True


def test_bad_tool_calls_become_errors_for_the_model(app, shop):
    llm = FakeLLM(Turn(tool_calls=[ToolCall(id="1", name="drop_database", arguments={}),
                                   ToolCall(id="2", name="get_order", arguments=None, raw_arguments="{oops"),
                                   call("get_order", {}, "3"),
                                   call("get_order", {"order": "99999"}, "4")]),
                  Turn(text="Bulamadım."))
    with app.test_request_context():
        session(app, llm=llm).message("kayıt hakkında bilgi ver")
    results = {m["tool_call_id"]: json.loads(m["content"]) for m in llm.calls[1] if m["role"] == "tool"}
    assert "Unknown tool" in results["1"]["error"]
    assert "not valid JSON" in results["2"]["error"]
    assert "Missing required" in results["3"]["error"]
    assert "not found" in results["4"]["error"]


def test_model_server_down_is_reported(app, shop):
    llm = FakeLLM(LLMError("The local AI model is not running. Start Ollama (or your model server) and try again."))
    with app.test_request_context():
        res = session(app, llm=llm).message("bugünün özeti")
        assert res["items"][-1]["role"] == "error" and "Ollama" in res["items"][-1]["text"]


def test_cancel_via_model_is_refused(app, shop):
    llm = FakeLLM(Turn(tool_calls=[call("set_order_status", {"order": "1", "status": "cancelled"})]),
                  Turn(text="İptal sayfadan yapılmalı."))
    with app.test_request_context():
        session(app, llm=llm).message("birinci işi iptal edebilir misin")
        assert db.session.get(ServiceOrder, 1).status == "in_repair"


# ---------------------------------------------------------------- model server client

def test_parse_message_variants():
    t = parse_message({"content": "<think>hmm</think>Merhaba", "tool_calls": [
        {"function": {"name": "find_orders", "arguments": '{"mine": true}'}}]})
    assert t.text == "Merhaba" and t.tool_calls[0].arguments == {"mine": True} and t.tool_calls[0].id.startswith("call_")
    t = parse_message({"content": 'Bakıyorum <tool_call>{"name": "find_parts", "arguments": {"query": "rotor"}}</tool_call>'})
    assert t.text == "Bakıyorum" and t.tool_calls[0].name == "find_parts" and t.tool_calls[0].arguments == {"query": "rotor"}
    t = parse_message({"content": '{"name": "business_summary", "arguments": {}}'})
    assert t.tool_calls[0].name == "business_summary" and t.text == ""
    t = parse_message({"content": "", "tool_calls": [{"id": "x", "function": {"name": "get_order", "arguments": {"order": 3}}}]})
    assert t.tool_calls[0].arguments == {"order": 3}
    assert t.history_message()["tool_calls"][0]["function"]["arguments"] == '{"order": 3}'


class Resp:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


class FakeHTTP:
    def __init__(self, *responses):
        self.responses, self.posts = list(responses), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json))
        return self.responses.pop(0)

    def get(self, url, headers=None, timeout=None):
        return self.responses.pop(0)


def test_backend_request_and_errors():
    http = FakeHTTP(Resp(200, {"choices": [{"message": {"content": "Tamam"}}], "usage": {"total_tokens": 5}}),
                    Resp(404, {"error": "model not found"}))
    b = OpenAICompatibleBackend("http://127.0.0.1:11434/v1/", "qwen3.5:2b", session=http)
    turn = b.chat([{"role": "user", "content": "selam"}], [{"name": "t", "description": "d", "parameters": {}}])
    url, body = http.posts[0]
    assert url == "http://127.0.0.1:11434/v1/chat/completions"
    assert body["model"] == "qwen3.5:2b" and body["tools"][0]["type"] == "function" and body["stream"] is False
    assert turn.text == "Tamam"
    with pytest.raises(LLMError, match="not found"):
        b.chat([], [])


def test_thinking_switch():
    ok = Resp(200, {"choices": [{"message": {"content": "Tamam"}}]})
    http = FakeHTTP(ok)
    OpenAICompatibleBackend("http://think-off/v1", "qwen3.5:2b", thinking=False, session=http).chat([], [])
    assert http.posts[0][1]["reasoning_effort"] == "none"  # Ollama: thinking off
    http = FakeHTTP(ok)
    OpenAICompatibleBackend("http://think-on/v1", "qwen3.5:2b", thinking=True, session=http).chat([], [])
    assert "reasoning_effort" not in http.posts[0][1]  # model decides (thinks)


def test_thinking_switch_falls_back_on_servers_that_reject_it():
    reject = Resp(400, {"error": 'invalid reasoning value: "none"'})
    ok = Resp(200, {"choices": [{"message": {"content": "Tamam"}}]})
    http = FakeHTTP(reject, ok, ok)
    b = OpenAICompatibleBackend("http://old-ollama/v1", "qwen3.5:2b", thinking=False, session=http)
    assert b.chat([], []).text == "Tamam"  # retried without the switch
    assert "reasoning_effort" not in http.posts[1][1]
    b.chat([], [])
    assert len(http.posts) == 3 and "reasoning_effort" not in http.posts[2][1]  # remembered: no second failure


def test_backend_health():
    b = OpenAICompatibleBackend("http://x/v1", "qwen3.5:2b", session=FakeHTTP(Resp(200, {"data": [{"id": "llama3"}]})))
    ok, msg = b.health()
    assert not ok and "llama3" in msg
    b = OpenAICompatibleBackend("http://x/v1", "qwen3.5:2b", session=FakeHTTP(Resp(200, {"data": [{"id": "qwen3.5:2b"}]})))
    assert b.health()[0]


def test_every_tool_has_a_valid_schema():
    from app.services.assistant.tools import TOOLS

    for t in TOOLS.values():
        spec = t.spec()
        assert spec["description"] and spec["parameters"]["type"] == "object"
        assert set(spec["parameters"]["required"]) <= set(spec["parameters"]["properties"])
        assert t.risk in ("read", "write", "confirm")
        if t.risk == "confirm":
            assert t.confirm_text is not None


# ---------------------------------------------------------------- HTTP

def test_http_endpoints(app, client, shop):
    assert client.get("/assistant/").status_code == 200
    assert "data-assistant" in client.get("/").get_data(as_text=True)
    r = client.get("/assistant/api/state")
    assert r.json["config"]["voice_engine"] in ("browser", "local")
    r = client.post("/assistant/api/message", json={"text": "2 numaralı iş hazır",
                                                    "page": {"endpoint": "dashboard.index", "args": {}}})
    assert r.status_code == 200 and r.json["changed"]
    cid = r.json["conversation_id"]
    with app.app_context():
        assert db.session.get(ServiceOrder, shop["orders"][1]).status == "ready"
    r = client.get(f"/assistant/api/state?conversation_id={cid}")
    assert [i["role"] for i in r.json["items"]] == ["user", "assistant"]
    r = client.post("/assistant/api/message", json={"conversation_id": cid, "text": "bu işi teslim ettim",
                                                    "page": {"endpoint": "orders.view", "args": {"oid": shop["orders"][1]}}})
    with app.app_context():
        assert db.session.get(ServiceOrder, shop["orders"][1]).status == "delivered"
    assert client.post("/assistant/api/new").json["conversation_id"] != cid
    assert client.post("/assistant/api/transcribe").status_code == 400
    assert client.get("/settings/assistant").status_code == 200


def test_assistant_can_be_disabled(app, client):
    client.post("/settings/assistant", data={"base_url": "http://127.0.0.1:11434/v1", "model": "qwen3.5:2b",
                                             "timeout": "60", "temperature": "0.2", "voice_engine": "auto"})
    assert client.get("/assistant/").status_code == 404
    assert "data-assistant" not in client.get("/").get_data(as_text=True)


def test_other_users_conversations_are_private(app, client, shop):
    with app.app_context():
        other = User.query.filter_by(username="staff").one()
        conv = AssistantConversation(user_id=other.id)
        conv.put("display", [{"id": "x", "role": "user", "text": "gizli"}])
        db.session.add(conv)
        db.session.commit()
        cid = conv.id
    r = client.get(f"/assistant/api/state?conversation_id={cid}")
    assert "gizli" not in json.dumps(r.json)


def test_https_certificate(tmp_path):
    from app.tls import ensure_cert

    cert, key = ensure_cert(str(tmp_path))
    assert open(cert).read().startswith("-----BEGIN CERTIFICATE")
    assert ensure_cert(str(tmp_path)) == (cert, key)

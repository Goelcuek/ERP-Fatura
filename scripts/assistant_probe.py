"""Put real workshop questions to the assistant with a local model and log exactly what goes in and out.

    python scripts/assistant_probe.py http://127.0.0.1:11435/v1 gemma4:e2b,qwen3.5:2b

Uses a throw-away data folder with the demo data. For every model and thinking setting it asks each
question in a fresh conversation and prints the request (without the tool list), the model server's
raw answer, and what the assistant finally showed. Used by the "Assistant probe" GitHub workflow.
"""

import json
import os
import sys
import tempfile
import time

os.environ["ERP_DATA_DIR"] = tempfile.mkdtemp(prefix="probe-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

QUESTIONS = [
    "Anadolu Metal Sanayi'nin borcu ne kadar?",
    "Bugün teslim edilecek işler hangileri?",
    "Açık servis kayıtlarını listele",
    "Bu ay satışlar ne durumda?",
    "Anadolu Metal'in ödenmemiş faturaları hangileri?",
]


class LoggingHTTP(requests.Session):
    def post(self, url, json=None, **kw):  # noqa: A002 (same signature as requests)
        body = dict(json or {})
        tools = body.pop("tools", None) or []
        messages = body.pop("messages", [])
        extra = {k: v for k, v in body.items() if k != "model"}
        print(f"  >>> {len(messages)} messages, {len(tools)} tools, {extra}")
        print("      last:", _short(messages[-1] if messages else {}, 400))
        started = time.time()
        resp = super().post(url, json=json, **kw)
        print(f"  <<< HTTP {resp.status_code} in {time.time() - started:.1f}s")
        try:
            data = resp.json()
            msg = data["choices"][0]["message"]
            print("      content:   ", _short(msg.get("content"), 600))
            print("      tool_calls:", _short(msg.get("tool_calls"), 600))
            if msg.get("reasoning"):
                print("      reasoning: ", _short(msg.get("reasoning"), 300))
            print("      finish:    ", data["choices"][0].get("finish_reason"), "| usage:", data.get("usage"))
        except (ValueError, KeyError, IndexError):
            print("      raw:", resp.text[:800])
        return resp


def _short(value, n):
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    s = (s or "").replace("\n", "⏎ ")
    return s if len(s) <= n else s[:n] + f"… (+{len(s) - n} chars)"


def model_info(base_url, model):
    root = base_url.rsplit("/v1", 1)[0]
    try:
        info = requests.post(f"{root}/api/show", json={"model": model}, timeout=60).json()
    except (requests.RequestException, ValueError) as e:
        return f"(api/show failed: {e})"
    details = info.get("details", {})
    return (f"capabilities={info.get('capabilities')} family={details.get('family')} "
            f"params={details.get('parameter_size')} quant={details.get('quantization_level')} "
            f"ctx={info.get('model_info', {}).get(next((k for k in info.get('model_info', {}) if k.endswith('context_length')), ''), '?')}")


def main():
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:11435/v1"
    models = [m.strip() for m in (sys.argv[2] if len(sys.argv) > 2 else "gemma4:e2b").split(",") if m.strip()]

    from app import create_app
    from app.demo import seed
    from app.extensions import db
    from app.models import AssistantConversation, User
    from app.services.assistant import agent, tools
    from app.services.assistant.llm import OpenAICompatibleBackend

    app = create_app()
    with app.test_request_context():  # like a real request (tools build links to pages)
        seed()
        user = User.query.filter_by(username="demo").one()
        spec_chars = len(json.dumps(tools.specs(), ensure_ascii=False))
        print(f"system prompt {len(agent.SYSTEM_PROMPT)} chars, {len(tools.specs())} tools ({spec_chars} chars)\n")
        for model in models:
            print("=" * 100)
            print(f"MODEL {model}: {model_info(base_url, model)}")
            for thinking in (False, True):
                print("-" * 100)
                print(f"{model}  thinking={'on' if thinking else 'off'}")
                for q in QUESTIONS:
                    print(f"\n  ? {q}")
                    backend = OpenAICompatibleBackend(base_url, model, timeout=600, temperature=0.2,
                                                      thinking=thinking, session=LoggingHTTP())
                    conv = AssistantConversation(user_id=user.id)
                    db.session.add(conv)
                    db.session.flush()
                    started = time.time()
                    try:
                        res = agent.Session(conv, user, llm=backend).message(q)
                    except Exception as e:  # keep probing the other questions
                        print(f"  !!! {type(e).__name__}: {e}")
                        db.session.rollback()
                        continue
                    for item in res["items"]:
                        if item["role"] != "user":
                            print(f"  = [{item['role']}] {_short(item['text'], 500)}")
                    if res.get("pending"):
                        print("  = pending:", _short(res["pending"], 300))
                    print(f"  ({time.time() - started:.1f}s)")
                    db.session.rollback()


if __name__ == "__main__":
    main()

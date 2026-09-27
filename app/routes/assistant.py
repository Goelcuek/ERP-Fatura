from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request

from ..extensions import db
from ..i18n import _
from ..models import AssistantConversation, Setting
from ..services.assistant import agent, speech

bp = Blueprint("assistant", __name__, url_prefix="/assistant")

PAGE_KEYS = {
    "orders.view": ("order_id", "oid"), "orders.edit": ("order_id", "oid"), "orders.receipt": ("order_id", "oid"),
    "contacts.view": ("customer_id", "cid"), "contacts.edit": ("customer_id", "cid"),
    "invoices.view": ("invoice_id", "iid"), "invoices.edit": ("invoice_id", "iid"),
}


@bp.before_request
def _enabled():
    if not Setting.get("assistant.enabled"):
        abort(404)


def _page(data):
    page = data.get("page") or {}
    key = PAGE_KEYS.get(page.get("endpoint"))
    if not key:
        return {}
    try:
        return {key[0]: int((page.get("args") or {}).get(key[1]))}
    except (TypeError, ValueError):
        return {}


def _conversation(cid=None, create=True):
    q = AssistantConversation.query.filter_by(user_id=g.user.id)
    if cid:
        conv = q.filter_by(id=int(cid)).first()
        if conv is not None:
            return conv
    conv = q.filter(AssistantConversation.updated_at >= datetime.now() - timedelta(hours=12)).order_by(
        AssistantConversation.updated_at.desc()).first()
    if conv is None and create:
        conv = AssistantConversation(user_id=g.user.id)
        db.session.add(conv)
        db.session.flush()
    return conv


def _session(data):
    conv = _conversation(data.get("conversation_id"))
    return agent.Session(conv, g.user, page=_page(data))


def client_config():
    s = agent.settings()
    engine = s["voice_engine"]
    if engine == "auto":
        engine = "local" if speech.available() else "browser"
    return {"voice_engine": engine, "voice_lang": s["voice_lang"], "speak_replies": bool(s["voice_speak_replies"]),
            "auto_send": bool(s["voice_auto_send"])}


@bp.route("/")
def page():
    return render_template("assistant/page.html")


@bp.route("/api/state")
def state():
    conv = _conversation(request.args.get("conversation_id"), create=False)
    if conv is None:
        return jsonify({"conversation_id": None, "items": [], "pending": None, "config": client_config()})
    return jsonify({"conversation_id": conv.id, "items": conv.get("display"),
                    "pending": agent.public_pending(conv.get("pending")), "config": client_config()})


@bp.route("/api/new", methods=["POST"])
def new():
    conv = AssistantConversation(user_id=g.user.id)
    db.session.add(conv)
    db.session.commit()
    return jsonify({"conversation_id": conv.id, "items": [], "pending": None})


@bp.route("/api/message", methods=["POST"])
def message():
    data = request.get_json(silent=True) or {}
    return jsonify(_session(data).message(data.get("text", "")))


@bp.route("/api/confirm", methods=["POST"])
def confirm():
    data = request.get_json(silent=True) or {}
    decisions = {str(k): bool(v) for k, v in (data.get("decisions") or {}).items()}
    return jsonify(_session(data).confirm(decisions))


@bp.route("/api/choose", methods=["POST"])
def choose():
    data = request.get_json(silent=True) or {}
    return jsonify(_session(data).choose(data.get("order_id")))


@bp.route("/api/undo", methods=["POST"])
def undo():
    data = request.get_json(silent=True) or {}
    return jsonify(_session(data).undo(data.get("item"), data.get("index", 0)))


@bp.route("/api/transcribe", methods=["POST"])
def transcribe():
    audio = request.files.get("audio")
    if audio is None:
        return jsonify({"error": _("No audio received.")}), 400
    s = agent.settings()
    try:
        text = speech.transcribe(audio.read(), language=(s["voice_lang"] or "tr-TR")[:2], size=s["voice_whisper_model"],
                                 download_root=current_app.config["DATA_DIR"] + "/models")
    except speech.SpeechError as e:
        return jsonify({"error": _(str(e))}), 400
    except Exception:  # model download or decoding problems
        current_app.logger.exception("transcription failed")
        return jsonify({"error": _("Speech recognition failed. Please try again or type your message.")}), 500
    return jsonify({"text": text})

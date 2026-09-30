"""Assistant orchestration: command fast path → local model with tools → confirmations.

Conversation state lives in AssistantConversation:
  messages – model-facing history (OpenAI chat format, without the system prompt)
  display  – what the chat panel shows (user/assistant bubbles, links, undo buttons)
  pending  – a question waiting for the user: {"type": "confirm" | "choice", ...}
"""

import json
import uuid
from datetime import date, datetime

from flask import current_app, url_for

from ...extensions import db
from ...i18n import _, current_lang
from ...models import ServiceOrder, Setting
from ...web import STATUS_LABELS
from .. import workshop
from ..branding import company_name
from . import commands, ollama, tools
from .llm import LLMError, OpenAICompatibleBackend

NOT_UNDERSTOOD = ("I did not understand that. Try for example: “SRV-2026-00012 is ready”, “I finished this job”, "
                  "“waiting for parts”, “add note: customer will pick up on Friday”.")
STILL_DOWNLOADING = "The AI model is still downloading ({percent}%). Workshop commands such as “I finished this job” already work."
MAX_STEPS = 6
HISTORY_LIMIT = 16  # messages kept for the model; small models do better with short context

SYSTEM_PROMPT = """You are the assistant inside the workshop software of {company}, a power-tool repair shop.
You help the staff by calling tools.
Rules:
- Always answer in {language}, in one or two short sentences. Replies may be read aloud: no tables, no markdown.
- Call the tools yourself, right away. Never say you will look something up or ask the user to wait.
- Use tools for facts. Never invent order numbers, amounts or ids. Pass customer names to tools, not guessed ids.
- "This job" means the order on the user's screen (see Context). If there is none, use find_orders with mine=true.
  Jobs due today or late: find_orders with due=today. How much a customer owes: get_customer with the name.
- If several records match, ask which one, naming at most three by number.
- Status words: finished, done, fixed, ready -> ready. Customer took it -> delivered. Started -> in_repair.
  Waiting for parts -> awaiting_parts. Sent a price quote -> awaiting_approval.
- issue_invoice, record_payment, record_expense, adjust_stock and create_customer are confirmed by the user in the
  app: call the tool, do not ask for confirmation in text.
- After a change, say briefly what was done and the order or invoice number."""

FINAL_NUDGE = "Now give your short answer to my question, using the tool results above."

# statuses a job can be in before each command, most likely first (used to pick "the" job)
FROM_STATUSES = {
    "ready": ["in_repair", "awaiting_parts", "diagnosing", "received", "awaiting_approval"],
    "delivered": ["ready"],
    "in_repair": ["received", "diagnosing", "awaiting_approval", "awaiting_parts"],
    "awaiting_parts": ["in_repair", "diagnosing", "received"],
    "awaiting_approval": ["diagnosing", "received"],
    "diagnosing": ["received"],
    "approved": ["awaiting_approval"],
    "note": list(ServiceOrder.OPEN),
    "cancelled": list(ServiceOrder.OPEN),
}


def settings():
    s = Setting.group("assistant")
    s.update({f"voice_{k}": v for k, v in Setting.group("voice").items()})
    mgr = ollama.manager()
    s["bundled"] = mgr.bundled
    s["effective_base_url"] = mgr.bundled_url() if (s["server"] == "auto" and mgr.bundled) else s["base_url"]
    return s


def model_status(cfg=None):
    cfg = cfg or settings()
    return ollama.manager().status(ollama.api_root(cfg["effective_base_url"]), cfg["model"])


def backend(cfg=None):
    cfg = cfg or settings()
    return OpenAICompatibleBackend(cfg["effective_base_url"], cfg["model"], api_key=cfg.get("api_key", ""),
                                   timeout=int(cfg.get("timeout") or 120),
                                   temperature=float(cfg.get("temperature") or 0.2),
                                   thinking=bool(cfg.get("thinking")))


def _now():
    return datetime.now().strftime("%H:%M")


class Session:
    """Wraps one conversation for one request."""

    def __init__(self, conv, user, page=None, llm=None):
        self.conv = conv
        self.user = user
        self.messages = conv.get("messages")
        self.display = conv.get("display")
        self.pending = conv.get("pending")
        self.page = page or {}
        self.ctx = tools.Context(user, page_order_id=self.page.get("order_id"),
                                 page_customer_id=self.page.get("customer_id"),
                                 page_invoice_id=self.page.get("invoice_id"))
        self._llm = llm
        self.new_items = []

    # -- state ---------------------------------------------------------------

    def say(self, text, role="assistant", **extra):
        item = {"id": uuid.uuid4().hex[:10], "role": role, "text": text, "at": _now(), **extra}
        if role == "assistant" and self.ctx.links:
            item["links"] = list(self.ctx.links)
            self.ctx.links.clear()
        if role == "assistant" and self.ctx.undo:
            item["undo"] = list(self.ctx.undo)
            self.ctx.undo.clear()
        self.display.append(item)
        self.new_items.append(item)
        return item

    def save(self):
        self.conv.put("messages", self.messages[-60:])
        self.conv.put("display", self.display[-80:])
        self.conv.put("pending", self.pending)
        self.conv.updated_at = datetime.now().replace(microsecond=0)
        db.session.commit()

    def result(self):
        return {"conversation_id": self.conv.id, "items": self.new_items, "pending": public_pending(self.pending),
                "changed": self.ctx.changed}

    # -- entry points ----------------------------------------------------------

    def message(self, text):
        text = (text or "").strip()[:1000]
        if not text:
            return self.result()
        self.say(text, role="user")
        self._drop_pending()
        cmd = commands.parse(text)
        if cmd is not None:
            reply = self._run_command(cmd)
            # keep the model informed so follow-up questions have context
            self.messages += [{"role": "user", "content": text}, {"role": "assistant", "content": reply or ""}]
        elif settings().get("use_llm"):
            self.messages.append({"role": "user", "content": self._context_line() + "\n" + text})
            self._run_llm()
        else:
            self.say(_(NOT_UNDERSTOOD))
        self.save()
        return self.result()

    def confirm(self, decisions):
        p = self.pending
        if not p or p.get("type") != "confirm":
            return self.result()
        self.pending = None
        if p.get("command"):  # confirmation of a parsed command (e.g. cancel)
            if decisions.get("command"):
                self._apply_command(commands.Command(**p["command"]), db.session.get(ServiceOrder, p["order_id"]))
            else:
                self.say(_("OK, nothing was changed."))
            self.save()
            return self.result()
        results = list(p.get("results", []))
        for call in p["calls"]:
            if decisions.get(call["id"]):
                res, err = tools.run(call["name"], call["args"], self.ctx)
                results.append(self._tool_message(call["id"], res))
                self.say(("✗ " if err else "✓ ") + call["text"], role="action", error=err)
            else:
                results.append(self._tool_message(call["id"], {"declined": True,
                                                               "note": "The user declined. Do not retry."}))
                self.say("— " + call["text"], role="action", declined=True)
        self.messages += results
        self._run_llm()
        self.save()
        return self.result()

    def choose(self, order_id):
        p = self.pending
        if not p or p.get("type") != "choice":
            return self.result()
        self.pending = None
        order = db.session.get(ServiceOrder, int(order_id))
        if order is not None:
            cmd = commands.Command(**p["command"])
            reply = self._apply_command(cmd, order)
            self.messages.append({"role": "assistant", "content": reply or ""})
        self.save()
        return self.result()

    def undo(self, item_id, undo_index=0):
        item = next((i for i in self.display if i.get("id") == item_id), None)
        try:
            u = item["undo"][int(undo_index)]
        except (IndexError, KeyError, ValueError, TypeError):
            return self.result()
        if u.get("done"):
            return self.result()
        if u["type"] == "status":
            order = db.session.get(ServiceOrder, u["order_id"])
            if order is not None:
                workshop.change_status(order, u["status"], self.user, note=_("undone"), source="assistant")
                u["done"] = True
                self.ctx.changed = True
                self.say(_("Undone: {number} is back to “{status}”.", number=order.number,
                           status=_(STATUS_LABELS[u["status"]])))
                self.messages.append({"role": "assistant", "content": self.new_items[-1]["text"]})
        self.save()
        res = self.result()
        res["undo_update"] = True
        res["all_items"] = [i for i in self.display if i not in self.new_items]
        return res

    # -- helpers -----------------------------------------------------------------

    def _drop_pending(self):
        """A new message answers nothing that was pending: treat it as declined."""
        p = self.pending
        if p and p.get("type") == "confirm" and p.get("calls"):
            self.messages += list(p.get("results", [])) + [
                self._tool_message(c["id"], {"declined": True, "note": "The user moved on without confirming."})
                for c in p["calls"]]
        self.pending = None

    @staticmethod
    def _tool_message(call_id, result):
        return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False, default=str)}

    def _context_line(self):
        u = self.user
        who = f"{u.full_name or u.username}" + (" (technician)" if u.is_technician else "")
        screen = "none"
        if self.ctx.page_order_id:
            o = db.session.get(ServiceOrder, self.ctx.page_order_id)
            if o:
                screen = f"service order {o.number} (id {o.id}, {o.device_label}, status {o.status})"
        elif self.ctx.page_invoice_id:
            screen = f"invoice id {self.ctx.page_invoice_id}"
        elif self.ctx.page_customer_id:
            screen = f"customer id {self.ctx.page_customer_id}"
        return f"Context: date {date.today().isoformat()}; user {who}; screen: {screen}."

    # -- command fast path -------------------------------------------------------

    def _find_order_for(self, cmd):
        """Returns (order, candidates). One of them is set, or both empty when nothing matches."""
        ref = cmd.ref
        if ref and ref[0] == "number":
            o = ServiceOrder.query.filter(ServiceOrder.number == ref[1]).first()
            return o, []
        if ref and ref[0] == "seq":
            o = (ServiceOrder.query.filter(ServiceOrder.number.like(f"%-{date.today().year}-{ref[1]:05d}")).first()
                 or ServiceOrder.query.filter(ServiceOrder.number.like(f"%-{ref[1]:05d}"))
                 .order_by(ServiceOrder.id.desc()).first())
            return o, []
        if self.ctx.page_order_id:
            return db.session.get(ServiceOrder, self.ctx.page_order_id), []
        states = FROM_STATUSES.get(cmd.intent, ServiceOrder.OPEN)
        q = ServiceOrder.query.filter(ServiceOrder.status.in_(states))
        mine = q.filter(ServiceOrder.technician_id == self.user.id).all()
        pool = mine or q.all()
        pool.sort(key=lambda o: (states.index(o.status), o.promised_date or date.max))
        if len(pool) == 1:
            return pool[0], []
        return None, pool[:8]

    def _run_command(self, cmd):
        order, candidates = self._find_order_for(cmd)
        if order is None and not candidates:
            if cmd.ref and cmd.ref[0] in ("number", "seq"):
                return self.say(_("I could not find that service order. Please check the number."))["text"]
            return self.say(_("I could not find a matching open job. Please say the job number, e.g. “SRV-2026-00012 is ready”."))["text"]
        if order is None:
            self.pending = {"type": "choice", "command": cmd.__dict__,
                            "options": [{"id": o.id, "label": o.number, "sub": f"{o.device_label} · {o.contact.name}",
                                         "status": _(STATUS_LABELS[o.status])} for o in candidates]}
            return self.say(_("Which job do you mean?"))["text"]
        if cmd.intent == "cancelled":
            text = _("Cancel service order {number} ({device})?", number=order.number, device=order.device_label)
            self.pending = {"type": "confirm", "command": cmd.__dict__, "order_id": order.id,
                            "calls": [{"id": "command", "text": text}]}
            return self.say(_("Please confirm."))["text"]
        return self._apply_command(cmd, order)

    def _apply_command(self, cmd, order):
        ctx = self.ctx
        ctx.link(order.number, url_for("orders.view", oid=order.id))
        label = f"{order.number} · {order.device_label}"
        if cmd.intent == "note":
            workshop.log_order(order, self.user, cmd.extra, source="assistant")
            ctx.changed = True
            db.session.commit()
            return self.say(_("Note added to {label}.", label=label))["text"]
        if cmd.intent == "approved":
            order.customer_approved = True
            workshop.log_order(order, self.user, _("Customer approved the estimate."), source="assistant")
            new = "in_repair" if order.status in ("awaiting_approval", "received", "diagnosing") else order.status
        else:
            new = cmd.intent
        if new == order.status and cmd.intent != "approved":
            return self.say(_("{label} is already “{status}”.", label=label, status=_(STATUS_LABELS[new])))["text"]
        old = workshop.change_status(order, new, self.user, note=cmd.extra if new != "ready" else "", source="assistant")
        if new == "ready" and cmd.extra:
            workshop.append_work_done(order, cmd.extra)
        if old != new:
            ctx.undo.append({"type": "status", "order_id": order.id, "status": old,
                             "label": _(STATUS_LABELS[old])})
        ctx.changed = True
        db.session.commit()
        text = _("{label} marked as “{status}”.", label=label, status=_(STATUS_LABELS[new]))
        if cmd.intent == "approved":
            text = _("Approval saved. {label} is now “{status}”.", label=label, status=_(STATUS_LABELS[new]))
        if cmd.extra:
            text += " " + (_("Work done saved.") if new == "ready" else _("Note saved."))
        if new == "ready" and not order.lines:
            text += " " + _("No parts or labour recorded yet.")
        return self.say(text)["text"]

    # -- language model ----------------------------------------------------------

    @property
    def llm(self):
        if self._llm is None:
            self._llm = backend()
        return self._llm

    def _system(self):
        lang = "Turkish" if current_lang() == "tr" else "English"
        return SYSTEM_PROMPT.format(company=company_name(), language=lang)

    def _trimmed(self):
        msgs = self.messages[-HISTORY_LIMIT:]
        # never start in the middle of a tool exchange
        while msgs and msgs[0]["role"] != "user":
            msgs = msgs[1:]
        return [{"role": "system", "content": self._system()}] + msgs

    def _final_answer(self):
        try:
            turn = self.llm.chat(self._trimmed() + [{"role": "user", "content": FINAL_NUDGE}], [])
        except LLMError:
            return ""
        if turn.text:
            self.messages[-1] = turn.history_message()  # keep the words, not the empty reply
        return turn.text

    def _run_llm(self):
        dl = ollama.manager().download.snapshot()
        if dl["state"] == "downloading":
            self.say(_(STILL_DOWNLOADING, percent=dl["percent"]))
            return
        cfg = settings()
        mgr = ollama.manager()
        if cfg["bundled"] and cfg["server"] == "auto" and not mgr.running(ollama.api_root(cfg["effective_base_url"])):
            mgr.start(wait=20)  # the bundled server stopped (e.g. after sleep): bring it back
        specs = tools.specs()
        for _step in range(MAX_STEPS):
            try:
                turn = self.llm.chat(self._trimmed(), specs)
            except LLMError as e:
                current_app.logger.warning("assistant model error: %s", e)
                self.say(_(str(e)), role="error")
                return
            self.messages.append(turn.history_message())
            if not turn.tool_calls:
                # some models end with thinking only and no words: ask once for the short answer
                self.say(turn.text or self._final_answer() or _("Done."))
                return
            if turn.text:
                self.say(turn.text)
            results, confirms = [], []
            for call in turn.tool_calls:
                t = tools.TOOLS.get(call.name)
                if t is not None and t.risk == "confirm" and call.arguments is not None:
                    confirms.append({"id": call.id, "name": call.name, "args": call.arguments,
                                     "text": t.confirm_text(call.arguments) if t.confirm_text else call.name})
                else:
                    res, _err = tools.run(call.name, call.arguments, self.ctx)
                    results.append(self._tool_message(call.id, res))
            if confirms:
                self.pending = {"type": "confirm", "calls": confirms, "results": results}
                self.say(_("Please confirm."))
                return
            self.messages += results
        self.say(_("I could not finish this. Please try again with a simpler request."), role="error")


def public_pending(p):
    if not p:
        return None
    if p["type"] == "confirm":
        return {"type": "confirm", "calls": [{"id": c["id"], "text": c["text"]} for c in p["calls"]]}
    return {"type": "choice", "options": p["options"]}


def health(cfg=None):
    return backend(cfg).health()

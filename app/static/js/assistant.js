/* In-app assistant: chat panel, voice input (browser speech recognition or local Whisper),
   spoken replies, confirmations, "which job?" choices and undo. */
(function () {
  "use strict";
  const root = document.querySelector("[data-assistant]");
  if (!root) return;
  const $ = (s) => root.querySelector(s);
  const T = JSON.parse($("[data-asst-strings]").innerHTML);
  const api = root.dataset.api;
  const pageMode = root.dataset.mode === "page";
  const panel = $("[data-asst-panel]");
  const log = $("[data-asst-log]");
  const pendingBox = $("[data-asst-pending]");
  const input = $("[data-asst-text]");
  const statusEl = $("[data-asst-status]");
  const micBtn = $("[data-asst-mic]");
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  let conversationId = null, config = null, busy = false, loaded = false;

  const store = {
    get(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch (e) {} },
    local(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } },
  };

  function pageContext() {
    let args = {};
    try { args = JSON.parse(document.body.dataset.viewArgs || "{}"); } catch (e) {}
    return { endpoint: document.body.dataset.endpoint, args };
  }

  function setStatus(text) { statusEl.textContent = text || T.ready; }

  async function call(path, body, isForm) {
    const opts = { method: body ? "POST" : "GET", headers: { "X-CSRF-Token": csrf } };
    if (body && !isForm) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    if (isForm) opts.body = body;
    const resp = await fetch(api + path, opts);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || T.error);
    return data;
  }

  // ---------------------------------------------------------------- rendering
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function renderItem(item) {
    $("[data-asst-hello]")?.classList.add("hidden");
    const row = el("div", "asst-msg asst-" + item.role);
    const bubble = el("div", "asst-bubble", item.text);
    row.appendChild(bubble);
    if (item.links && item.links.length) {
      const links = el("div", "asst-links");
      item.links.forEach((l) => { const a = el("a", "asst-chip", l.label); a.href = l.url; links.appendChild(a); });
      row.appendChild(links);
    }
    (item.undo || []).forEach((u, ui) => {
      const b = el("button", "asst-undo", u.done ? T.undone : `${T.undo} (${u.label})`);
      b.type = "button";
      b.disabled = !!u.done;
      b.addEventListener("click", () => act("/undo", { item: item.id, index: ui }));
      row.appendChild(b);
    });
    if (item.at) row.appendChild(el("div", "asst-time", item.at));
    log.appendChild(row);
  }

  function renderPending(p) {
    pendingBox.innerHTML = "";
    if (!p) return;
    const card = el("div", "asst-card");
    if (p.type === "confirm") {
      p.calls.forEach((c) => {
        const line = el("label", "asst-confirm-line");
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.checked = true; cb.dataset.id = c.id;
        line.append(cb, el("span", null, c.text));
        card.appendChild(line);
      });
      const btns = el("div", "asst-card-actions");
      const ok = el("button", "btn btn-primary btn-sm", T.confirm);
      const no = el("button", "btn btn-sm", T.decline);
      ok.type = no.type = "button";
      ok.addEventListener("click", () => {
        const decisions = {};
        card.querySelectorAll("input[type=checkbox]").forEach((cb) => (decisions[cb.dataset.id] = cb.checked));
        act("/confirm", { decisions });
      });
      no.addEventListener("click", () => {
        const decisions = {};
        p.calls.forEach((c) => (decisions[c.id] = false));
        act("/confirm", { decisions });
      });
      btns.append(ok, no);
      card.appendChild(btns);
    } else if (p.type === "choice") {
      p.options.forEach((o) => {
        const b = el("button", "asst-option");
        b.type = "button";
        b.append(el("b", null, o.label), el("span", null, o.sub), el("span", "tag", o.status));
        b.addEventListener("click", () => act("/choose", { order_id: o.id }));
        card.appendChild(b);
      });
    }
    pendingBox.appendChild(card);
  }

  function scrollDown() { log.scrollTop = log.scrollHeight; }

  function speak(items) {
    if (store.local("asst-speak") !== "1" || !("speechSynthesis" in window)) return;
    const text = items.filter((i) => i.role === "assistant" || i.role === "error").map((i) => i.text).join(" ");
    if (!text) return;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = (config && config.voice_lang) || "tr-TR";
    const voice = speechSynthesis.getVoices().find((v) => v.lang && v.lang.startsWith(u.lang.slice(0, 2)));
    if (voice) u.voice = voice;
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  }

  function apply(data) {
    if (data.conversation_id) { conversationId = data.conversation_id; store.set("asst-conv", conversationId); }
    log.querySelectorAll("[data-optimistic]").forEach((n) => n.remove());
    if (data.undo_update) {  // an undo marks the original item's button as done
      log.querySelectorAll(".asst-msg").forEach((m) => m.remove());
      (data.all_items || []).forEach(renderItem);
    }
    (data.items || []).forEach(renderItem);
    renderPending(data.pending);
    scrollDown();
    speak(data.items || []);
    if (data.changed && !pageMode) {
      store.set("asst-open", "1");
      setTimeout(() => location.reload(), (data.items || []).length ? 1400 : 300);
    }
  }

  async function load() {
    if (loaded) return;
    loaded = true;
    try {
      const data = await call("/state" + (store.get("asst-conv") ? "?conversation_id=" + store.get("asst-conv") : ""));
      config = data.config;
      conversationId = data.conversation_id;
      log.querySelectorAll(".asst-msg").forEach((m) => m.remove());
      (data.items || []).forEach(renderItem);
      renderPending(data.pending);
      scrollDown();
    } catch (e) { setStatus(e.message); }
  }

  async function act(path, body) {
    if (busy) return;
    busy = true;
    root.classList.add("is-busy");
    setStatus(T.thinking);
    try {
      apply(await call(path, { conversation_id: conversationId, page: pageContext(), ...body }));
      setStatus();
    } catch (e) {
      setStatus(e.message);
    } finally {
      busy = false;
      root.classList.remove("is-busy");
    }
  }

  function send(text) {
    text = (text || input.value).trim();
    if (!text) return;
    input.value = "";
    autosize();
    renderItem({ role: "user", text });
    log.lastChild.dataset.optimistic = "1";
    scrollDown();
    act("/message", { text });
  }

  // ---------------------------------------------------------------- voice
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognizer = null, recorder = null, chunks = [], stopTimer = null;

  function micState(on) { micBtn?.classList.toggle("is-on", on); root.classList.toggle("is-listening", on); }

  function startBrowser() {
    if (!SR) { setStatus(T.no_mic); return; }
    recognizer = new SR();
    recognizer.lang = (config && config.voice_lang) || "tr-TR";
    recognizer.interimResults = true;
    recognizer.continuous = false;
    let finalText = "";
    recognizer.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += e.results[i][0].transcript;
        else interim += e.results[i][0].transcript;
      }
      input.value = (finalText + interim).trim();
      autosize();
    };
    recognizer.onerror = (e) => setStatus(e.error === "not-allowed" ? T.mic_denied : T.error);
    recognizer.onend = () => {
      micState(false);
      recognizer = null;
      setStatus();
      if (config && config.auto_send && input.value.trim()) send();
    };
    recognizer.start();
    micState(true);
    setStatus(T.listening);
  }

  async function startLocal() {
    if (!navigator.mediaDevices || !window.MediaRecorder) { setStatus(T.no_mic); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (e) { setStatus(T.mic_denied); return; }
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      micState(false);
      clearTimeout(stopTimer);
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      recorder = null;
      if (!blob.size) return setStatus();
      setStatus(T.transcribing);
      const form = new FormData();
      form.append("audio", blob, "speech.webm");
      try {
        const data = await call("/transcribe", form, true);
        input.value = data.text || "";
        autosize();
        setStatus();
        if (config && config.auto_send && input.value.trim()) send();
      } catch (e) { setStatus(e.message); }
    };
    recorder.start();
    stopTimer = setTimeout(() => recorder && recorder.stop(), 20000);
    micState(true);
    setStatus(T.listening);
  }

  micBtn?.addEventListener("click", async () => {
    await load();
    if (!window.isSecureContext) { setStatus(T.need_https); return; }
    if (recognizer) { recognizer.stop(); return; }
    if (recorder) { recorder.stop(); return; }
    if (config && config.voice_engine === "off") { setStatus(T.no_mic); return; }
    if (config && config.voice_engine === "local") startLocal(); else startBrowser();
  });

  // ---------------------------------------------------------------- wiring
  function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 140) + "px"; }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  $("[data-asst-form]").addEventListener("submit", (e) => { e.preventDefault(); send(); });
  root.querySelectorAll("[data-asst-example]").forEach((b) => b.addEventListener("click", () => send(b.textContent)));

  const speakBtn = $("[data-asst-speak]");
  function syncSpeak() { speakBtn.classList.toggle("is-on", store.local("asst-speak") === "1"); }
  speakBtn.addEventListener("click", () => {
    store.local("asst-speak", store.local("asst-speak") === "1" ? "0" : "1");
    if (store.local("asst-speak") !== "1" && "speechSynthesis" in window) speechSynthesis.cancel();
    syncSpeak();
  });

  $("[data-asst-new]").addEventListener("click", async () => {
    const data = await call("/new", {});
    conversationId = data.conversation_id;
    store.set("asst-conv", conversationId);
    log.querySelectorAll(".asst-msg").forEach((m) => m.remove());
    $("[data-asst-hello]")?.classList.remove("hidden");
    renderPending(null);
  });

  function open() {
    panel.classList.remove("hidden");
    root.classList.add("is-open");
    store.set("asst-open", "1");
    load().then(() => { if (!("ontouchstart" in window)) input.focus(); });
  }
  function close() { panel.classList.add("hidden"); root.classList.remove("is-open"); store.set("asst-open", null); }
  $("[data-asst-open]")?.addEventListener("click", () => (panel.classList.contains("hidden") ? open() : close()));
  $("[data-asst-close]")?.addEventListener("click", close);

  // first-time defaults from settings; afterwards the viewer's own toggle wins
  if (store.local("asst-speak") === null) {
    call("/state").then((d) => { config = d.config; if (config.speak_replies) store.local("asst-speak", "1"); syncSpeak(); }).catch(() => {});
  }
  syncSpeak();
  if (pageMode || store.get("asst-open") === "1") open();
})();

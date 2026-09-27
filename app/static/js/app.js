/* Atölye ERP — small progressive enhancements. Every page works without JS except the live totals. */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  // ---------------------------------------------------------------- theme & nav
  const root = document.documentElement;
  function currentTheme() {
    return root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  $$("[data-theme-toggle]").forEach((b) =>
    b.addEventListener("click", () => {
      const next = currentTheme() === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (e) {}
    })
  );
  $$("[data-nav-toggle]").forEach((b) => b.addEventListener("click", () => document.body.classList.toggle("nav-open")));
  document.addEventListener("click", (e) => {
    if (document.body.classList.contains("nav-open") && !e.target.closest(".sidebar, [data-nav-toggle]")) {
      document.body.classList.remove("nav-open");
    }
  });

  // ---------------------------------------------------------------- toasts
  $$(".toast").forEach((t, i) => {
    const close = () => t.remove();
    t.querySelector("[data-dismiss]")?.addEventListener("click", close);
    if (!t.classList.contains("error")) setTimeout(close, 5000 + i * 800);
  });

  // ---------------------------------------------------------------- confirmations & row links
  document.addEventListener("submit", (e) => {
    const msg = e.target.getAttribute("data-confirm");
    if (msg && !confirm(msg)) e.preventDefault();
  });
  document.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-href]");
    if (tr && !e.target.closest("a, button, input, select, form")) {
      if (e.metaKey || e.ctrlKey) window.open(tr.dataset.href, "_blank");
      else location.href = tr.dataset.href;
    }
    // close <details> menus when clicking outside
    $$("details.menu[open]").forEach((d) => { if (!d.contains(e.target)) d.removeAttribute("open"); });
  });

  // "/" focuses search
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      const s = $("#global-search");
      if (s) { e.preventDefault(); s.focus(); }
    }
  });

  // ---------------------------------------------------------------- number helpers (TR format)
  function parseNum(v) {
    if (v == null) return 0;
    let s = String(v).trim().replace(/\s|₺/g, "");
    if (!s) return 0;
    if (s.includes(",") && s.includes(".")) {
      s = s.lastIndexOf(",") > s.lastIndexOf(".") ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
    } else if (s.includes(",")) s = s.replace(",", ".");
    const n = parseFloat(s);
    return isNaN(n) ? 0 : n;
  }
  const r2 = (n) => Math.round((n + Number.EPSILON) * 100) / 100;
  const fmt = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const money = (n) => (n < 0 ? "−" : "") + fmt.format(Math.abs(n)) + " ₺";

  // ---------------------------------------------------------------- line editor
  $$("[data-line-editor]").forEach((table) => {
    const body = $("[data-lines]", table);
    const tpl = document.querySelector("[data-line-template]");
    const list = $("#product-list");
    const totalsBox = $("[data-totals]");
    const form = table.closest("form");
    const whInput = form ? form.querySelector("[name=withholding_rate]") : null;
    const typeInput = form ? form.querySelector("[name=type_code]") : null;

    function withholdingRate() {
      if (typeInput && whInput) {
        const type = typeInput.tagName === "SELECT" ? typeInput.value : (form.querySelector("[name=type_code]:checked") || {}).value;
        return type === "TEVKIFAT" ? parseNum(whInput.value) : 0;
      }
      return parseNum(table.dataset.withholding);
    }

    function recalc() {
      const t = { gross: 0, discount: 0, net: 0, vat: 0, withholding: 0 };
      const wh = withholdingRate();
      $$("[data-line]", body).forEach((row) => {
        const qty = parseNum($("[data-qty]", row).value);
        const price = parseNum($("[data-price]", row).value);
        const disc = Math.min(Math.max(parseNum($("[data-disc]", row).value), 0), 100);
        const vatRate = parseNum($("[data-vat]", row).value);
        const gross = r2(qty * price);
        const discount = r2((gross * disc) / 100);
        const net = gross - discount;
        const vat = r2((net * vatRate) / 100);
        const w = r2((vat * wh) / 100);
        t.gross += gross; t.discount += discount; t.net += net; t.vat += vat; t.withholding += w;
        $("[data-total]", row).textContent = $("[data-desc]", row).value || price ? money(net + vat) : "—";
      });
      t.payable = t.net + t.vat - t.withholding;
      if (totalsBox) {
        for (const k of Object.keys(t)) {
          const el = totalsBox.querySelector(`[data-t="${k}"]`);
          if (el) el.textContent = (k === "discount" || k === "withholding" ? "−" : "") + money(t[k]);
        }
        totalsBox.querySelector('[data-row="discount"]')?.classList.toggle("hidden", !t.discount);
        totalsBox.querySelector('[data-row="withholding"]')?.classList.toggle("hidden", !t.withholding);
      }
    }

    function bindRow(row) {
      const desc = $("[data-desc]", row);
      desc.addEventListener("change", () => {
        const opt = list && $$("option", list).find((o) => o.value === desc.value);
        const pid = row.querySelector("[name=line_product_id]");
        if (opt) {
          pid.value = opt.dataset.id;
          $("[data-price]", row).value = opt.dataset.price;
          $("[data-unit]", row).value = opt.dataset.unit;
          $("[data-vat]", row).value = opt.dataset.vat;
        } else {
          pid.value = "";
        }
        recalc();
      });
      $("[data-del-line]", row).addEventListener("click", () => {
        if ($$("[data-line]", body).length > 1) row.remove();
        else $$("input", row).forEach((i) => { i.value = i.hasAttribute("data-qty") ? "1" : ""; });
        recalc();
      });
    }

    function addLine(focus) {
      const frag = tpl.content.cloneNode(true);
      const row = frag.querySelector("[data-line]");
      body.appendChild(frag);
      bindRow(row);
      if (focus) $("[data-desc]", row).focus();
      recalc();
    }

    $$("[data-line]", body).forEach(bindRow);
    if (!$$("[data-line]", body).length) addLine(false);
    table.addEventListener("input", recalc);
    table.addEventListener("change", recalc);
    form?.addEventListener("change", recalc);
    document.querySelector("[data-add-line]")?.addEventListener("click", () => addLine(true));
    // Enter in the last row adds a new line instead of submitting
    body.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.tagName === "INPUT") {
        e.preventDefault();
        const rows = $$("[data-line]", body);
        if (e.target.closest("[data-line]") === rows[rows.length - 1]) addLine(true);
      }
    });
    recalc();
  });

  // ---------------------------------------------------------------- contact picker (autocomplete)
  $$("[data-contact-picker]").forEach((box) => {
    const hidden = box.querySelector("input[type=hidden]");
    const input = $("[data-ac-input]", box);
    const list = $("[data-ac-list]", box);
    const picked = $("[data-picked]", box);
    const inputWrap = $("[data-picker-input]", box);
    let timer = null, items = [], active = -1;

    function render() {
      list.innerHTML = "";
      if (!items.length) {
        list.innerHTML = `<div class="ac-item muted">${box.dataset.emptyText || "—"}</div>`;
      }
      items.forEach((c, i) => {
        const el = document.createElement("div");
        el.className = "ac-item" + (i === active ? " active" : "");
        el.innerHTML = `<div><div class="strong"></div><div class="sub"></div></div><span></span>`;
        el.querySelector(".strong").textContent = c.name;
        el.querySelector(".sub").textContent = [c.tax_id, c.phone, c.city].filter(Boolean).join(" · ");
        if (c.efatura) el.querySelector("span").innerHTML = '<span class="tag blue">e-Fatura</span>';
        el.addEventListener("mousedown", (e) => { e.preventDefault(); choose(c); });
        list.appendChild(el);
      });
      list.classList.remove("hidden");
    }
    function choose(c) {
      hidden.value = c.id;
      $("[data-picked-name]", box).textContent = c.name;
      $("[data-picked-sub]", box).textContent = [c.tax_id || "—", c.phone].filter(Boolean).join(" · ");
      picked.classList.remove("hidden");
      inputWrap.classList.add("hidden");
      list.classList.add("hidden");
      box.dispatchEvent(new CustomEvent("contact-picked", { detail: c, bubbles: true }));
    }
    let seq = 0, latest = Promise.resolve();
    function search() {
      const mine = ++seq;
      timer = null;
      latest = fetch(box.dataset.endpoint + "?q=" + encodeURIComponent(input.value))
        .then((r) => r.json()).then((data) => {
          if (mine !== seq) return; // a newer search is in flight
          items = data; active = -1; render();
        });
      return latest;
    }
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(search, 150); });
    input.addEventListener("focus", search);
    input.addEventListener("blur", () => setTimeout(() => list.classList.add("hidden"), 150));
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); render(); e.preventDefault(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        // results may still be loading for what was just typed
        const ready = timer ? (clearTimeout(timer), search()) : latest;
        ready.then(() => { const c = items[active] || items[0]; if (c) choose(c); });
      }
    });
    $("[data-picked-clear]", box)?.addEventListener("click", () => {
      hidden.value = "";
      picked.classList.add("hidden");
      inputWrap.classList.remove("hidden");
      input.value = "";
      input.focus();
    });
  });

  // ---------------------------------------------------------------- invoice form: profile/type dependent fields
  const invForm = $("[data-invoice-form]");
  if (invForm) {
    const sync = () => {
      const type = (invForm.querySelector("[name=type_code]:checked") || {}).value;
      $$("[data-show-type]", invForm).forEach((el) => el.classList.toggle("hidden", el.dataset.showType !== type));
    };
    invForm.addEventListener("change", sync);
    sync();
    const whCode = invForm.querySelector("[name=withholding_code]");
    whCode?.addEventListener("change", () => {
      const opt = whCode.selectedOptions[0];
      if (opt && opt.dataset.rate) invForm.querySelector("[name=withholding_rate]").value = opt.dataset.rate;
      invForm.dispatchEvent(new Event("change"));
    });
    invForm.addEventListener("contact-picked", (e) => {
      const c = e.detail;
      const profile = c.efatura ? invForm.dataset.defaultProfile : "EARSIVFATURA";
      const radio = invForm.querySelector(`[name=profile][value=${profile}]`);
      if (radio) radio.checked = true;
      if (c.withholding) {
        const t = invForm.querySelector("[name=type_code][value=TEVKIFAT]");
        if (t) t.checked = true;
      }
      $$("[data-efatura-note]", invForm).forEach((n) => n.classList.toggle("hidden", c.efatura));
      invForm.dispatchEvent(new Event("change"));
    });
  }

  // ---------------------------------------------------------------- chart tooltip
  $$("[data-chart]").forEach((svg) => {
    const tip = document.createElement("div");
    tip.className = "chart-tip hidden";
    document.body.appendChild(tip);
    const la = svg.closest(".card")?.dataset.labelA || "Income";
    const lb = svg.closest(".card")?.dataset.labelB || "Expenses";
    svg.querySelectorAll(".hit").forEach((h) => {
      h.addEventListener("mousemove", (e) => {
        tip.innerHTML = `<b></b><div><span><i style="background:var(--series-1)"></i>${la}</span><span data-a></span></div>` +
          `<div><span><i style="background:var(--series-2)"></i>${lb}</span><span data-b></span></div>`;
        tip.querySelector("b").textContent = h.dataset.tipTitle;
        tip.querySelector("[data-a]").textContent = h.dataset.tipA;
        tip.querySelector("[data-b]").textContent = h.dataset.tipB;
        tip.classList.remove("hidden");
        const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
        tip.style.left = x + "px";
        tip.style.top = e.clientY - tip.offsetHeight - 10 + "px";
      });
      h.addEventListener("mouseleave", () => tip.classList.add("hidden"));
    });
  });

  // auto print (receipt / invoice print pages)
  if (document.body.dataset.autoPrint === "1") setTimeout(() => window.print(), 300);
})();

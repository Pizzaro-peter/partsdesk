/* Search-as-you-type pickers: <div class="picker" data-picker="part|customer" data-url="..."> */
(function () {
  document.querySelectorAll("[data-password-toggle]").forEach(function (btn) {
    var input = document.getElementById(btn.getAttribute("aria-controls"));
    if (!input) return;
    btn.addEventListener("click", function () {
      var visible = input.type === "password";
      input.type = visible ? "text" : "password";
      btn.setAttribute("aria-label", visible ? "Hide password" : "Show password");
      btn.setAttribute("title", visible ? "Hide password" : "Show password");
      btn.setAttribute("aria-pressed", String(visible));
    });
  });
})();

(function () {
  document.querySelectorAll(".picker").forEach(function (box) {
    var text = box.querySelector("input[type=text]");
    var hidden = box.querySelector("input[type=hidden]");
    var list = box.querySelector(".picker-list");
    var url = box.dataset.url, timer = null;

    function render(items) {
      list.replaceChildren();
      items.forEach(function (it) {
        var li = document.createElement("li");
        li.textContent = it.label;
        li.addEventListener("mousedown", function (e) { e.preventDefault(); choose(it); });
        list.appendChild(li);
      });
      list.hidden = !items.length;
    }
    function choose(it) {
      text.value = it.label;
      if (hidden) hidden.value = it.id;
      list.hidden = true;
      var target = box.dataset.vehicleSelect && document.getElementById(box.dataset.vehicleSelect);
      if (target && it.vehicles) {
        target.replaceChildren(new Option("Select a vehicle…", ""));
        it.vehicles.forEach(function (v) { target.appendChild(new Option(v.label, v.id)); });
        if (!it.vehicles.length) target.replaceChildren(new Option("No vehicles yet — add one on the customer page", ""));
      }
      box.dispatchEvent(new CustomEvent("picker:select", { detail: it, bubbles: true }));
    }
    text.addEventListener("input", function () {
      if (hidden) hidden.value = "";
      clearTimeout(timer);
      if (!text.value.trim()) { render([]); box.dispatchEvent(new CustomEvent("picker:clear", { bubbles: true })); return; }
      timer = setTimeout(function () {
        fetch(url + "?q=" + encodeURIComponent(text.value.trim()))
          .then(function (r) { return r.json(); }).then(function (d) { render(d.results); });
      }, 200);
    });
    text.addEventListener("blur", function () { setTimeout(function () { list.hidden = true; }, 150); });
    var form = box.closest("form");
    if (form && box.dataset.required !== "0") {
      form.addEventListener("submit", function (e) {
        if (hidden && !hidden.value) { e.preventDefault(); text.focus(); alert("Pick an item from the search results first."); }
      });
    }
  });
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
  });
  // A print-preview request is observable; completed physical printing is not.
  if (!document.body.dataset.autoprint && document.querySelector('button[onclick*="window.print"]')) {
    window.addEventListener("beforeprint", function () {
      var token = document.querySelector('input[name="csrfmiddlewaretoken"]');
      if (!token) return;
      var data = new FormData();
      data.append("csrfmiddlewaretoken", token.value);
      data.append("path", window.location.pathname);
      navigator.sendBeacon(document.body.dataset.printLogUrl, data);
    });
  }
  if (document.body.dataset.autoprint) window.print();
})();

/* Forms open as pop-up modals: links to create/edit pages load the form in place; the standalone page also shows as a modal. */
(function () {
  function initWizard(root) {
    var f = root.querySelector("form.wizard"); if (!f || f.dataset.ready) return; f.dataset.ready = "1";
    var steps = [].slice.call(f.querySelectorAll(".wiz-step")), n = steps.length, i = 0;
    var dots = [].slice.call(f.querySelectorAll(".wiz-progress li"));
    var back = f.querySelector("[data-wiz=back]"), next = f.querySelector("[data-wiz=next]"), sub = f.querySelector("[data-wiz=submit]");
    function show(k) {
      i = k;
      steps.forEach(function (s, j) { s.hidden = n > 1 && j !== k; });
      dots.forEach(function (d, j) { d.classList.toggle("on", j === k); d.classList.toggle("done", j < k); });
      back.hidden = k === 0 || n < 2; next.hidden = k >= n - 1; sub.hidden = n > 1 && k < n - 1;
      var first = steps[k].querySelector("input:not([type=hidden]),select,textarea"); if (first) first.focus();
    }
    function valid(k) {
      var ok = true;
      [].slice.call(steps[k].querySelectorAll("input,select,textarea")).forEach(function (e) { if (ok && !e.checkValidity()) { e.reportValidity(); ok = false; } });
      return ok;
    }
    next.addEventListener("click", function () { if (valid(i)) show(i + 1); });
    back.addEventListener("click", function () { show(i - 1); });
    f.addEventListener("submit", function (e) { for (var k = 0; k < n; k++) if (!valid(k)) { e.preventDefault(); e.stopImmediatePropagation(); show(k); return; } });
    f.addEventListener("keydown", function (e) { if (e.key === "Enter" && n > 1 && i < n - 1 && e.target.tagName !== "TEXTAREA") { e.preventDefault(); next.click(); } });
    var err = steps.findIndex(function (s) { return s.dataset.error; });
    show(err >= 0 ? err : 0);
  }
  window.initWizard = initWizard;
  var standalone = document.querySelector(".form-modal");
  if (standalone) {
    initWizard(standalone);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") { var c = standalone.querySelector("[data-modal-close]"); if (c) window.location = c.href; } });
  }

  var LINK = /\/(new|add|edit)\/?(\?.*)?$/;
  var overlay = null;
  function close() { if (overlay) { overlay.remove(); overlay = null; } }
  function mount(html) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var box = doc.querySelector(".modal-form");
    if (!box) return false;
    close();
    overlay = document.createElement("div"); overlay.className = "modal-wrap";
    box.querySelector("[data-modal-close]").addEventListener("click", function (e) { e.preventDefault(); close(); });
    overlay.appendChild(document.importNode(box, true));
    overlay.addEventListener("mousedown", function (e) { if (e.target === overlay) close(); });
    document.body.appendChild(overlay);
    var closer = overlay.querySelector("[data-modal-close]"); closer.addEventListener("click", function (e) { e.preventDefault(); close(); });
    var cancel = overlay.querySelector(".wiz-nav a.btn"); if (cancel) cancel.addEventListener("click", function (e) { e.preventDefault(); close(); });
    var form = overlay.querySelector("form");
    initWizard(overlay);
    form.addEventListener("submit", function (e) {
      if (e.defaultPrevented) return;
      e.preventDefault();
      fetch(form.action || window.location.href, { method: "POST", body: new FormData(form), credentials: "same-origin" })
        .then(function (r) { if (r.redirected) { window.location = r.url; return null; } return r.text(); })
        .then(function (t) { if (t !== null && !mount(t)) window.location.reload(); });
    });
    return true;
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || a.target || a.closest(".form-modal")) return;
    var u = new URL(a.href, window.location.href);
    if (u.origin !== window.location.origin || !LINK.test(u.pathname + u.search)) return;
    e.preventDefault();
    fetch(a.href, { credentials: "same-origin" }).then(function (r) { return r.text(); })
      .then(function (t) { if (!mount(t)) window.location = a.href; })
      .catch(function () { window.location = a.href; });
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
})();

/* Collapsible navigation pane (remembered per browser) */
(function () {
  var btn = document.getElementById("nav-toggle"); if (!btn) return;
  var KEY = "partsdesk.nav.closed", de = document.documentElement;
  var side = document.getElementById("side-nav");
  var mobile = window.matchMedia("(max-width: 900px)");
  function updateExpanded() {
    btn.setAttribute("aria-expanded", mobile.matches
      ? String(de.classList.contains("nav-mobile-open"))
      : String(!de.classList.contains("nav-closed")));
  }
  function set(c) {
    de.classList.toggle("nav-closed", c);
    updateExpanded();
    try { localStorage.setItem(KEY, c ? "1" : "0"); } catch (x) {}
  }
  function closeMobile() {
    de.classList.remove("nav-mobile-open");
    updateExpanded();
  }
  btn.addEventListener("click", function () {
    if (mobile.matches) {
      de.classList.toggle("nav-mobile-open");
      updateExpanded();
    } else {
      set(!de.classList.contains("nav-closed"));
    }
  });
  mobile.addEventListener("change", function () {
    closeMobile();
    updateExpanded();
  });
  document.addEventListener("click", function (e) {
    if (!mobile.matches || !de.classList.contains("nav-mobile-open")) return;
    if (side && !side.contains(e.target) && !btn.contains(e.target)) closeMobile();
    if (side && side.contains(e.target) && e.target.closest("a")) closeMobile();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && de.classList.contains("nav-mobile-open")) closeMobile();
  });
  updateExpanded();
})();

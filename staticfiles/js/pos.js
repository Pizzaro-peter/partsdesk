/* Point of sale: search parts, build a ticket, take payment. The server re-checks every price, discount and stock level. */
(function () {
  var root = document.getElementById("pos");
  if (!root) return;
  var d = root.dataset, TAX = parseFloat(d.taxRate || 0), INCL = d.taxInclusive === "1", CUR = d.currency;
  var MAXD = d.maxDiscount === "" ? 100 : parseFloat(d.maxDiscount), OVERRIDE = d.canOverride === "1";
  var csrf = document.querySelector("[name=csrfmiddlewaretoken]").value;
  var $ = function (id) { return document.getElementById(id); };
  var q = $("q"), results = $("results"), body = $("cart-body"), err = $("err");
  var cart = [], customer = null, timer = null;
  var HAS_UI = !!$("pay-modal"), store = (typeof localStorage !== "undefined") ? localStorage : null;
  var DKEY = "partsdesk.pos.drafts." + (d.draftKey || "");

  var fmt = function (n) { return (n < 0 ? "-" : "") + CUR + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
  var isTrade = function () { return customer && customer.type === "trade"; };
  var priceFor = function (p) { return isTrade() && p.trade_price ? p.trade_price : p.price; };
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

  function search(v) {
    return fetch(d.searchUrl + "?q=" + encodeURIComponent(v) + (v ? "" : "&browse=1")).then(function (r) { return r.json(); }).then(function (data) { show(data.results); return data.results; });
  }
  function show(items) {
    results.replaceChildren();
    if ($("result-note")) $("result-note").textContent = items.length + (items.length === 1 ? " match" : " matches");
    if (!items.length) { results.appendChild(el("div", "empty", "No parts match. Try fewer words, a part number, or a vehicle.")); return; }
    items.forEach(function (p) {
      var b = el("button", "hit"); b.type = "button";
      b.appendChild(el("span", "nm", p.name)); b.appendChild(el("span", "pr", fmt(priceFor(p))));
      var meta = el("span", "meta");
      meta.appendChild(document.createTextNode(p.sku + " · " + [p.brand, p.condition, p.part_type].filter(Boolean).join(" · ") + (p.bin ? " · bin " + p.bin : "") + " · "));
      var s = el("span", p.qty <= 0 ? "stockout" : "stockok", p.qty <= 0 ? "out of stock" : p.qty + " " + p.unit + " in stock");
      meta.appendChild(s); b.appendChild(meta);
      b.addEventListener("click", function () { add(p); q.focus(); });
      results.appendChild(b);
    });
  }
  function add(p) {
    var line = cart.find(function (l) { return l.id === p.id; });
    if (line) line.qty += 1; else cart.push({ id: p.id, name: p.name, sku: p.sku, unit: p.unit, stock: p.qty, p: p, price: priceFor(p), qty: 1, disc: 0 });
    draw();
  }
  function totals() {
    var gross = 0, net = 0;
    cart.forEach(function (l) { var g = l.qty * l.price; gross += g; net += g * (1 - l.disc / 100); });
    var tax = INCL ? net * TAX / (100 + TAX) : net * TAX / 100;
    return { gross: gross, disc: gross - net, tax: tax, total: INCL ? net : net + tax };
  }
  function draw() {
    body.replaceChildren();
    cart.forEach(function (l, i) {
      var tr = el("tr");
      var nm = el("td"); nm.appendChild(el("strong", "", l.name)); nm.appendChild(el("br")); nm.appendChild(el("small", "mono", l.sku));
      if (l.qty > l.stock) nm.appendChild(el("small", "stockout", "  only " + l.stock + " in stock"));
      tr.appendChild(nm);
      function cell(cls, val, min, max, step, onchange, ro) {
        var td = el("td"), inp = el("input", cls); inp.type = "number"; inp.value = val; inp.min = min; inp.step = step; if (max !== null) inp.max = max; if (ro) inp.readOnly = true;
        inp.addEventListener("change", function () { onchange(parseFloat(inp.value) || 0); draw(); }); td.appendChild(inp); return td;
      }
      tr.appendChild(cell("prc", l.price, 0, null, "0.01", function (v) { l.price = v; }, !OVERRIDE));
      tr.appendChild(cell("qty", l.qty, 0.01, null, "0.01", function (v) { l.qty = v > 0 ? v : 1; }));
      tr.appendChild(cell("dsc", l.disc, 0, MAXD, "0.5", function (v) { l.disc = Math.min(Math.max(v, 0), MAXD); }));
      var tot = el("td", "num", fmt(l.qty * l.price * (1 - l.disc / 100))); tr.appendChild(tot);
      var x = el("td"), rm = el("button", "btn sm danger", "×"); rm.type = "button"; rm.title = "Remove";
      rm.addEventListener("click", function () { cart.splice(i, 1); draw(); }); x.appendChild(rm); tr.appendChild(x);
      body.appendChild(tr);
    });
    $("empty-cart").hidden = cart.length > 0;
    if (root.classList) root.classList.toggle("has-cart", cart.length > 0);
    var t = totals();
    $("t-gross").textContent = fmt(t.gross); $("t-disc").textContent = "-" + fmt(t.disc).replace("-", "");
    $("t-tax").textContent = fmt(t.tax); $("t-total").textContent = fmt(t.total);
    $("t-tax-label").textContent = (d.taxName || "Tax") + (INCL ? " (included)" : " (added)");
    $("row-tax").hidden = TAX === 0; $("row-disc").hidden = t.disc === 0;
    var paid = $("paid"); if (!paid.dataset.touched) paid.value = $("method").value === "credit" ? "" : t.total.toFixed(2);
    var change = parseFloat(paid.value || 0) - t.total;
    var chg = $("change"), short = parseFloat(paid.value || 0) < t.total - 0.005 && cart.length;
    chg.textContent = $("method").value !== "credit" && change > 0 ? "Change due: " + fmt(change) : (short ? "On account: " + fmt(t.total - (parseFloat(paid.value) || 0)) : "");
    chg.className = "co-result" + (chg.textContent.indexOf("Change") === 0 ? " ok" : short ? " warn" : "");
    $("checkout").disabled = !cart.length;
    if (HAS_UI) { $("hold-btn").disabled = !cart.length; drawPay(t); }
  }

  q.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { var v = q.value.trim(); search(v); }, 180); });
  q.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return; e.preventDefault();
    var v = q.value.trim(); if (!v) return;
    search(v).then(function (items) {   // barcode scanners "type" the code then press Enter
      var exact = items.find(function (p) { return p.sku.toLowerCase() === v.toLowerCase() || (p.barcode || "").toLowerCase() === v.toLowerCase(); });
      var pick = exact || (items.length === 1 ? items[0] : null);
      if (pick) { add(pick); q.value = ""; results.replaceChildren(); }
    });
  });
  root.addEventListener("picker:select", function (e) { customer = e.detail; cart.forEach(function (l) { l.price = priceFor(l.p); }); showCustomer(); draw(); });
  root.addEventListener("picker:clear", function () { customer = null; cart.forEach(function (l) { l.price = priceFor(l.p); }); showCustomer(); draw(); });
  function showCustomer() {
    var n = $("cust-note"); n.textContent = "";
    if (!customer) return;
    var bits = [customer.type === "trade" ? "Trade prices" : "Retail"];
    if (customer.credit_limit > 0) bits.push("credit limit " + fmt(customer.credit_limit) + ", owes " + fmt(customer.balance));
    n.textContent = bits.join(" · ");
  }
  $("method").addEventListener("change", function () { delete $("paid").dataset.touched; draw(); });
  $("paid").addEventListener("input", function () { $("paid").dataset.touched = "1"; draw(); });
  function submit() {
    err.textContent = ""; var btn = $("confirm-pay") || $("checkout"); btn.disabled = true;
    var payload = {
      request_id: d.requestId,
      customer_id: customer ? customer.id : null, payment_method: $("method").value, notes: $("notes").value,
      amount_paid: $("paid").value === "" ? ($("method").value === "credit" ? 0 : null) : parseFloat($("paid").value),
      lines: cart.map(function (l) { return { part_id: l.id, quantity: l.qty, unit_price: OVERRIDE ? l.price : null, discount_pct: l.disc }; })
    };
    fetch(d.checkoutUrl, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(payload) })
      .then(function (r) { return r.json(); })
      .then(function (res) { if (res.ok) window.location = res.url; else { err.textContent = res.error; btn.disabled = false; } })
      .catch(function () { err.textContent = "Couldn't reach the server. Check the connection and try again."; btn.disabled = false; });
  }
  $("checkout").addEventListener("click", function () { if (HAS_UI) openPay(); else submit(); });
  if (HAS_UI) initUI();
  q.focus(); draw();
  if (HAS_UI) search("");

  /* ---- Professional POS extras: checkout dialog, held sales, calculator, shortcuts ---- */
  function initUI() {
    var modal = $("pay-modal"), drafts = $("drafts"), calc = $("calc");
    var total = function () { return totals().total; };

    function drawPay(t) {
      $("pay-due").textContent = fmt(t.total);
      $("pay-summary").textContent = cart.length + " line" + (cart.length === 1 ? "" : "s") + " · " + (customer ? customer.name || customer.label : "Walk-in customer");
      var pl = $("pay-lines"); pl.replaceChildren();
      cart.forEach(function (l) {
        var r = el("div", "co-line"), n = el("div", "n"); n.appendChild(el("strong", "", l.name));
        n.appendChild(el("small", "", l.qty + " × " + fmt(l.price) + (l.disc ? " · " + l.disc + "% off" : "")));
        r.appendChild(n); r.appendChild(el("span", "", fmt(l.qty * l.price * (1 - l.disc / 100)))); pl.appendChild(r);
      });
      $("pay-cust").textContent = customer ? "Customer: " + (customer.name || customer.label) + (isTrade() ? " (trade)" : "") : "Customer: Walk-in";
      var tt = $("pay-totals"); tt.replaceChildren();
      function row(k, v, c) { var r = el("div", c || ""); r.appendChild(el("span", "", k)); r.appendChild(el("strong", "", v)); tt.appendChild(r); }
      row("Subtotal", fmt(t.gross)); if (t.disc) row("Discount", "-" + fmt(t.disc));
      if (TAX) row((d.taxName || "Tax") + (INCL ? " (included)" : ""), fmt(t.tax)); row("Total", fmt(t.total), "grand");
      document.querySelectorAll("#pay-methods .method").forEach(function (b) { b.classList.toggle("on", b.dataset.method === $("method").value); });
      var qc = $("quick-cash"); qc.replaceChildren();
      if ($("method").value === "credit" || t.total <= 0) return;
      var seen = {};
      [t.total, Math.ceil(t.total / 10) * 10, Math.ceil(t.total / 50) * 50, Math.ceil(t.total / 100) * 100, Math.ceil(t.total / 500) * 500].forEach(function (v, i) {
        v = Math.round(v * 100) / 100; if (seen[v]) return; seen[v] = 1;
        var b = el("button", "", i === 0 ? "Exact " + fmt(v) : fmt(v)); b.type = "button";
        b.addEventListener("click", function () { $("paid").value = v.toFixed(2); $("paid").dataset.touched = "1"; draw(); });
        qc.appendChild(b);
      });
    }
    window.drawPay = drawPay;
    var keys = $("co-keys");
    ["7", "8", "9", "4", "5", "6", "1", "2", "3", "0", ".", "⌫"].forEach(function (k) {
      var b = el("button", "", k); b.type = "button";
      b.addEventListener("click", function () {
        var p = $("paid"), v = p.dataset.touched ? p.value : "";
        v = k === "⌫" ? v.slice(0, -1) : (k === "." && v.indexOf(".") >= 0 ? v : v + k);
        p.value = v; p.dataset.touched = "1"; draw();
      });
      keys.appendChild(b);
    });
    function openPay() { if (!cart.length) return; err.textContent = ""; $("confirm-pay").disabled = false; modal.hidden = false; delete $("paid").dataset.touched; draw(); $("paid").focus(); $("paid").select(); }
    function closePay() { modal.hidden = true; q.focus(); }
    $("pay-close").addEventListener("click", closePay); $("pay-cancel").addEventListener("click", closePay);
    $("confirm-pay").addEventListener("click", submit);
    $("pay-methods").addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest(".method") : null; if (!b) return;
      $("method").value = b.dataset.method; delete $("paid").dataset.touched; draw();
    });
    window.openPay = openPay;

    // Held sales (kept in this browser)
    function load() { try { return JSON.parse(store.getItem(DKEY) || "[]"); } catch (x) { return []; } }
    function save(list) { if (store) store.setItem(DKEY, JSON.stringify(list)); counts(); }
    function counts() { $("draft-count").textContent = store ? load().length : 0; }
    function custLabel() { var t = root.querySelector(".picker input[type=text]"); return t ? t.value : ""; }
    function reset() {
      cart = []; customer = null; var t = root.querySelector(".picker input[type=text]"); if (t) t.value = "";
      $("notes").value = ""; delete $("paid").dataset.touched; showCustomer(); draw();
    }
    function hold() {
      if (!cart.length) return;
      if (!store) { err.textContent = "This browser cannot store held sales."; return; }
      var list = load();
      list.unshift({ id: Date.now(), at: new Date().toISOString(), cart: cart, customer: customer, label: custLabel(), notes: $("notes").value });
      save(list.slice(0, 20)); reset(); q.focus();
    }
    function resume(item) {
      if (cart.length && !confirm("Replace the current sale with this held sale? The current sale will be discarded.")) return;
      cart = item.cart; customer = item.customer; $("notes").value = item.notes || "";
      var t = root.querySelector(".picker input[type=text]"); if (t) t.value = item.label || "";
      showCustomer(); save(load().filter(function (x) { return x.id !== item.id; })); drafts.hidden = true; draw();
    }
    function listDrafts() {
      var box = $("drafts-list"); box.replaceChildren(); var list = load();
      if (!list.length) { box.appendChild(el("p", "empty", "No held sales.")); return; }
      list.forEach(function (it) {
        var n = it.cart.reduce(function (a, l) { return a + l.qty; }, 0);
        var amt = it.cart.reduce(function (a, l) { return a + l.qty * l.price * (1 - l.disc / 100); }, 0);
        var card = el("div", "draft"), row = el("div", "row");
        var info = el("div"); info.appendChild(el("strong", "", it.label || "Walk-in customer"));
        info.appendChild(el("br")); info.appendChild(el("small", "muted", new Date(it.at).toLocaleString() + " · " + n + " items"));
        row.appendChild(info); row.appendChild(el("strong", "", fmt(amt))); card.appendChild(row);
        var acts = el("div", "row"); acts.style.marginTop = "8px";
        var r = el("button", "btn sm primary", "Resume"); r.type = "button"; r.addEventListener("click", function () { resume(it); });
        var x = el("button", "btn sm danger", "Delete"); x.type = "button";
        x.addEventListener("click", function () { if (confirm("Delete this held sale?")) { save(load().filter(function (y) { return y.id !== it.id; })); listDrafts(); } });
        acts.appendChild(r); acts.appendChild(x); card.appendChild(acts); box.appendChild(card);
      });
    }
    function openDrafts() { listDrafts(); drafts.hidden = false; }
    $("hold-btn").addEventListener("click", hold);
    $("draft-btn").addEventListener("click", openDrafts);
    $("drafts-close").addEventListener("click", function () { drafts.hidden = true; });
    $("clear-btn").addEventListener("click", function () { if (!cart.length || confirm("Clear the current sale?")) reset(); });
    counts();

    // Calculator
    var cur = "0", prev = null, op = null, fresh = true;
    var keys = [["C", "fn"], ["⌫", "fn"], ["%", "op"], ["÷", "op"], ["7"], ["8"], ["9"], ["×", "op"], ["4"], ["5"], ["6"], ["−", "op"], ["1"], ["2"], ["3"], ["+", "op"], ["0"], ["."], ["±", "op"], ["=", "eq"]];
    function show() { $("calc-display").textContent = cur; $("calc-expr").textContent = prev !== null && op ? prev + " " + op : ""; }
    function run(a, b, o) { return o === "+" ? a + b : o === "−" ? a - b : o === "×" ? a * b : o === "÷" ? (b === 0 ? NaN : a / b) : b; }
    function fix(n) { return isFinite(n) ? String(parseFloat(n.toFixed(8))) : "Error"; }
    function press(k) {
      if (cur === "Error" && k !== "C") return;
      if (/^[0-9]$/.test(k)) { cur = fresh || cur === "0" ? k : cur + k; fresh = false; }
      else if (k === ".") { if (fresh) { cur = "0."; fresh = false; } else if (cur.indexOf(".") < 0) cur += "."; }
      else if (k === "C") { cur = "0"; prev = null; op = null; fresh = true; }
      else if (k === "⌫") { cur = cur.length > 1 && !fresh ? cur.slice(0, -1) : "0"; }
      else if (k === "±") { if (cur !== "0") cur = cur[0] === "-" ? cur.slice(1) : "-" + cur; }
      else if (k === "%") { cur = fix(parseFloat(cur) / 100); fresh = true; }
      else if (k === "=") { if (op !== null) { cur = fix(run(prev, parseFloat(cur), op)); prev = null; op = null; fresh = true; } }
      else { if (op !== null && !fresh) { cur = fix(run(prev, parseFloat(cur), op)); } prev = parseFloat(cur); op = k; fresh = true; }
      show();
    }
    keys.forEach(function (k) { var b = el("button", k[1] || "", k[0]); b.type = "button"; b.addEventListener("click", function () { press(k[0]); }); $("calc-keys").appendChild(b); });
    function toggleCalc() { calc.hidden = !calc.hidden; }
    $("calc-btn").addEventListener("click", toggleCalc); $("calc-close").addEventListener("click", function () { calc.hidden = true; });

    document.addEventListener("keydown", function (e) {
      if (e.key === "F2") { e.preventDefault(); q.focus(); q.select(); }
      else if (e.key === "F3") { e.preventDefault(); toggleCalc(); }
      else if (e.key === "F4") { e.preventDefault(); openPay(); }
      else if (e.key === "F7") { e.preventDefault(); hold(); }
      else if (e.key === "Escape") { if (!modal.hidden) closePay(); else if (!drafts.hidden) drafts.hidden = true; }
      else if (e.key === "Enter" && !modal.hidden && e.target && e.target.id === "paid") { e.preventDefault(); submit(); }
    });
  }
})();

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

  var fmt = function (n) { return (n < 0 ? "-" : "") + CUR + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
  var isTrade = function () { return customer && customer.type === "trade"; };
  var priceFor = function (p) { return isTrade() && p.trade_price ? p.trade_price : p.price; };
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

  function search(v) {
    return fetch(d.searchUrl + "?q=" + encodeURIComponent(v)).then(function (r) { return r.json(); }).then(function (data) { show(data.results); return data.results; });
  }
  function show(items) {
    results.replaceChildren();
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
    var t = totals();
    $("t-gross").textContent = fmt(t.gross); $("t-disc").textContent = "-" + fmt(t.disc).replace("-", "");
    $("t-tax").textContent = fmt(t.tax); $("t-total").textContent = fmt(t.total);
    $("t-tax-label").textContent = (d.taxName || "Tax") + (INCL ? " (included)" : " (added)");
    $("row-tax").hidden = TAX === 0; $("row-disc").hidden = t.disc === 0;
    var paid = $("paid"); if (!paid.dataset.touched) paid.value = $("method").value === "credit" ? "" : t.total.toFixed(2);
    var change = parseFloat(paid.value || 0) - t.total;
    $("change").textContent = $("method").value !== "credit" && change > 0 ? "Change due: " + fmt(change) : (parseFloat(paid.value || 0) < t.total - 0.005 && cart.length ? "On account: " + fmt(t.total - (parseFloat(paid.value) || 0)) : "");
    $("checkout").disabled = !cart.length;
  }

  q.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { var v = q.value.trim(); if (v) search(v); else results.replaceChildren(); }, 180); });
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
  $("checkout").addEventListener("click", function () {
    err.textContent = ""; var btn = $("checkout"); btn.disabled = true;
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
  });
  q.focus(); draw();
})();

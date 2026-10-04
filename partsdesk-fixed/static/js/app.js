/* Search-as-you-type pickers: <div class="picker" data-picker="part|customer" data-url="..."> */
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

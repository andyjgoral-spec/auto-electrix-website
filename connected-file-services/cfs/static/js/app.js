// Small progressive enhancements. Every page works without JavaScript.
(function () {
  document.querySelectorAll("[data-dropzone]").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]");
    var label = zone.querySelector("[data-filename]");
    function show() {
      if (!input.files.length) return;
      var f = input.files[0];
      label.textContent = f.name + " (" + (f.size / 1024).toFixed(1) + " KB)";
    }
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("drag"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.remove("drag"); });
    });
    zone.addEventListener("drop", function (e) {
      if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); }
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  document.querySelectorAll("tr[data-href]").forEach(function (row) {
    row.classList.add("link-row");
    row.addEventListener("click", function (e) {
      if (e.target.closest("a, button, input, form")) return;
      window.location = row.getAttribute("data-href");
    });
  });

  // Prevent double submission of uploads on slow mobile connections.
  document.querySelectorAll("form[data-once]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("button[type=submit]");
      if (btn) { setTimeout(function () { btn.disabled = true; btn.textContent = "Uploading…"; }, 0); }
    });
  });
})();

document.querySelectorAll("[data-print]").forEach(function (b) {
  b.addEventListener("click", function () { window.print(); });
});

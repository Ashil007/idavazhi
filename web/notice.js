/* Pop-up messages in plain words and large type: why a trip could not be
 * planned ("Nearest bus stop is 10 km away"), and what to know about one
 * that was ("1.4 km walk to the bus stop"). Riders skip small print, so
 * these sit in the middle of the screen until tapped away.
 *
 *   NOTICE.show({
 *     tone: "error" | "warn" | "info",
 *     title: "Nearest bus stop is 10 km away",
 *     text: "There is no bus stop within 2 km of your start.",   (optional)
 *     items: [{ title, text }],          (optional: several points in one pop-up)
 *     actions: [{ label, run }]          (optional: the first is the main button)
 *   });
 *
 * "OK" always closes it; so do Escape and a tap outside the card.
 */
var NOTICE = (function () {
  "use strict";

  var ICON = { error: "!", warn: "!", info: "i" };
  var box = null, lastFocus = null;

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function build() {
    box = document.createElement("div");
    box.className = "notice";
    box.hidden = true;
    box.setAttribute("role", "alertdialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-labelledby", "notice-title");
    box.innerHTML =
      '<div class="notice__card">' +
      '  <div class="notice__icon" aria-hidden="true"></div>' +
      '  <h2 class="notice__title" id="notice-title"></h2>' +
      '  <p class="notice__text"></p>' +
      '  <ul class="notice__items"></ul>' +
      '  <div class="notice__actions"></div>' +
      "</div>";
    document.body.appendChild(box);
    box.addEventListener("click", function (e) { if (e.target === box) hide(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !box.hidden) hide();
    });
  }

  function show(o) {
    if (!box) build();
    lastFocus = document.activeElement;
    var tone = o.tone || "info";
    box.className = "notice notice--" + tone;
    box.querySelector(".notice__icon").textContent = ICON[tone] || "i";
    box.querySelector(".notice__title").textContent = o.title || "";

    var text = box.querySelector(".notice__text");
    text.textContent = o.text || "";
    text.hidden = !o.text;

    var items = box.querySelector(".notice__items");
    items.innerHTML = (o.items || []).map(function (it) {
      return "<li><b>" + esc(it.title) + "</b>" + (it.text ? "<span>" + esc(it.text) + "</span>" : "") + "</li>";
    }).join("");
    items.hidden = !(o.items && o.items.length);

    var acts = box.querySelector(".notice__actions");
    acts.innerHTML = "";
    (o.actions || []).forEach(function (a, i) {
      acts.appendChild(button(a.label, i === 0 ? "is-main" : "", function () { hide(); a.run(); }));
    });
    acts.appendChild(button(o.okLabel || "OK", (o.actions || []).length ? "" : "is-main", hide));

    box.hidden = false;
    document.documentElement.classList.add("notice-open");
    acts.firstChild.focus();
  }

  function button(label, cls, run) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "notice__btn " + cls;
    b.textContent = label;
    b.addEventListener("click", run);
    return b;
  }

  function hide() {
    if (!box || box.hidden) return;
    box.hidden = true;
    document.documentElement.classList.remove("notice-open");
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus({ preventScroll: true }); } catch (e) { /* gone */ } }
  }

  return { show: show, hide: hide, isOpen: function () { return !!box && !box.hidden; } };
})();

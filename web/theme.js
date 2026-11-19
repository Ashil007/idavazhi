/* Idavazhi website and app: light and dark mode.
 *
 * Loaded in <head>, before the page is drawn, so the right colours are there
 * from the first frame. The visitor's own choice (the sun/moon button) is
 * remembered on this device, for the website and the app alike; until they
 * choose, the page follows the device's setting.
 *
 * The app's map follows too: a "vazhi:theme" event says when the choice
 * changes, and VAZHI_THEME.toggle() lets the map's own button change it. */
(function () {
  "use strict";
  var KEY = "vazhi-site-theme", root = document.documentElement, saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) { /* private mode: follow the device */ }
  var dark = saved ? saved === "dark"
                   : !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);

  function apply() {
    root.setAttribute("data-theme", dark ? "dark" : "light");
    // The browser's own bar: each page names its light colour in the tag
    // itself and its dark one in data-dark.
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      if (!meta.getAttribute("data-light")) meta.setAttribute("data-light", meta.getAttribute("content"));
      meta.setAttribute("content", dark ? (meta.getAttribute("data-dark") || "#12110d") : meta.getAttribute("data-light"));
    }
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.setAttribute("aria-pressed", String(dark));
      btn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
      btn.title = dark ? "Light mode" : "Dark mode";
    }
  }

  function toggle() {
    dark = !dark;
    try { localStorage.setItem(KEY, dark ? "dark" : "light"); } catch (e) { /* still switches for this visit */ }
    root.classList.add("theme-switching");          // a short cross-fade, only when the visitor switches
    apply();
    window.setTimeout(function () { root.classList.remove("theme-switching"); }, 320);
    try { document.dispatchEvent(new CustomEvent("vazhi:theme", { detail: { dark: dark } })); } catch (e) { /* old browser */ }
  }
  window.VAZHI_THEME = { isDark: function () { return dark; }, toggle: toggle };

  apply();
  document.addEventListener("DOMContentLoaded", function () {
    apply();
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", toggle);
  });
})();

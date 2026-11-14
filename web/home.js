/* Vazhi website: the "Download the app" buttons, the install steps for each
 * device, and small touches (nav border on scroll, sections fading in).
 *
 * "Download" installs Vazhi as an app (a Progressive Web App): Android and
 * computers with Chrome or Edge get the browser's own Install prompt; iPhones
 * have none, so the button shows Safari's Share > Add to Home Screen steps.
 * The installed app opens at /app (manifest.webmanifest). */
(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }
  var ua = navigator.userAgent;
  var isIOS = /iphone|ipad|ipod/i.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var isAndroid = /android/i.test(ua);
  var isMobile = isIOS || isAndroid || /Mobi/i.test(ua);
  var installed = (window.matchMedia && matchMedia("(display-mode: standalone)").matches) || navigator.standalone === true;
  var prompt = null;

  // The service worker makes Vazhi installable (and lets it open offline).
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(function () { /* the page works without */ });
  }

  /* ---------------------------------------------------- device tabs */
  var tabs = ["android", "iphone", "computer"];
  function pick(name, focus) {
    tabs.forEach(function (t) {
      var on = t === name, tab = $("tab-" + t);
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
      $("pane-" + t).hidden = !on;
      if (on && focus) tab.focus();
    });
  }
  tabs.forEach(function (t, i) {
    var tab = $("tab-" + t);
    tab.addEventListener("click", function () { pick(t); });
    tab.addEventListener("keydown", function (e) {
      var step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (step) { e.preventDefault(); pick(tabs[(i + step + tabs.length) % tabs.length], true); }
    });
  });
  var mine = isIOS ? "iphone" : isAndroid ? "android" : isMobile ? "android" : "computer";
  pick(mine);

  /* ---------------------------------------------------- install */
  var buttons = Array.prototype.slice.call(document.querySelectorAll(".js-install"));
  function label(text) {
    document.querySelectorAll(".js-install-label").forEach(function (el) { el.textContent = text; });
  }
  function status(text) {
    var el = $("install-status");
    el.textContent = text; el.hidden = !text;
  }

  if (installed) label("Open the app");
  else if (isIOS) label("Add to Home Screen");
  else label(isMobile ? "Download the app" : "Install on this computer");

  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();          // our own buttons ask, not the browser's bar
    prompt = e;
  });
  window.addEventListener("appinstalled", function () {
    prompt = null;
    label("Installed ✓ Open Vazhi");
    status("Vazhi is installed. Find it on your " + (isMobile ? "home screen" : "desktop or Start menu") + ".");
    buttons.forEach(function (b) { b.dataset.open = "1"; });
  });

  var sheet = $("install-sheet");
  function showSheet(html) {
    $("sheet-body").innerHTML = html;
    sheet.hidden = false;
    $("sheet-ok").focus();
  }
  function hideSheet() { sheet.hidden = true; }
  $("sheet-ok").addEventListener("click", hideSheet);
  sheet.addEventListener("click", function (e) { if (e.target === sheet) hideSheet(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !sheet.hidden) hideSheet(); });

  function steps() {
    if (isIOS) {
      var safari = /safari/i.test(ua) && !/crios|fxios|edgios/i.test(ua);
      return (safari ? "" : "<p style='margin-bottom:12px'>First open this page in <b>Safari</b> &mdash; only Safari can add apps on iPhone.</p>") +
        "<ol><li>Tap <b>Share</b> <span class='key'>⬆</span> at the bottom of the screen.</li>" +
        "<li>Scroll down and tap <b>Add to Home Screen</b>.</li>" +
        "<li>Tap <b>Add</b>. Vazhi is now on your home screen.</li></ol>";
    }
    if (isMobile) {
      return "<ol><li>Tap the <b>⋮</b> menu at the top right of the browser.</li>" +
        "<li>Tap <b>Install app</b> or <b>Add to Home screen</b>.</li>" +
        "<li>Tap <b>Install</b>. Vazhi is now on your home screen.</li></ol>" +
        "<p style='margin-top:12px'>Don't see it? Open this page in <b>Chrome</b>.</p>";
    }
    return "<ol><li>Open this page in <b>Chrome</b> or <b>Microsoft Edge</b>.</li>" +
      "<li>Click the install icon <span class='key'>⊕</span> at the right end of the address bar, or the <b>⋮</b> menu &rarr; <b>Install Vazhi</b>.</li>" +
      "<li>Vazhi opens in its own window.</li></ol>" +
      "<p style='margin-top:12px'>Or skip installing and <a href='/app'>use the web version</a>.</p>";
  }

  buttons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (installed || btn.dataset.open) { window.location.href = "/app"; return; }
      if (prompt) {
        prompt.prompt();
        prompt.userChoice.then(function (choice) {
          prompt = null;
          if (choice.outcome !== "accepted") status("No problem — you can install it any time, or use the web version.");
        });
        return;
      }
      showSheet(steps());
    });
  });

  /* ---------------------------------------------------- nav + reveal */
  var nav = $("nav");
  function onScroll() { nav.classList.toggle("is-scrolled", window.scrollY > 8); }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  if ("IntersectionObserver" in window) {
    var items = document.querySelectorAll(".pain, .feature, .step, .getcard, .laptop, .faq details, .numbers__in > div");
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("is-in"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    items.forEach(function (el) { el.classList.add("reveal"); io.observe(el); });
  }

  // The demo video: plays (muted, looping) while it is on screen, unless the
  // visitor asked their device for less motion; the button pauses and plays.
  var video = $("demo-video"), toggle = $("demo-toggle");
  if (video && toggle) {
    var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var wanted = !still;                       // what the visitor wants, apart from scrolling
    var showState = function () {
      var playing = !video.paused;
      toggle.classList.toggle("is-paused", !playing);
      $("demo-toggle-label").textContent = playing ? "Pause" : "Play";
      toggle.setAttribute("aria-label", playing ? "Pause the demo video" : "Play the demo video");
    };
    video.addEventListener("play", showState);
    video.addEventListener("pause", showState);
    toggle.addEventListener("click", function () {
      wanted = video.paused;
      if (wanted) video.play().catch(function () {}); else video.pause();
    });
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        var seen = entries[entries.length - 1].isIntersecting;   // the newest, if several came at once
        if (seen && wanted) video.play().catch(function () {});
        if (!seen && !video.paused) video.pause();
      }, { threshold: 0.35 }).observe(video);
    } else if (wanted) {
      video.play().catch(function () {});
    }
    showState();
  }

  // The full guide: light up the topic being read in the topic list.
  var toc = document.querySelectorAll(".guide__toc a");
  var guideBody = document.querySelector(".guide__body");
  if (toc.length && "IntersectionObserver" in window) {
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        toc.forEach(function (a) {
          a.classList.toggle("is-here", a.getAttribute("href") === "#" + en.target.id);
        });
      });
    }, { root: guideBody, rootMargin: "-10% 0px -70% 0px" });
    document.querySelectorAll(".chapter").forEach(function (c) { spy.observe(c); });
  }

  // Smooth scroll within the guide body when clicking TOC links
  if (guideBody && toc.length) {
    toc.forEach(function (a) {
      a.addEventListener("click", function (e) {
        var href = a.getAttribute("href");
        if (href && href.charAt(0) === "#") {
          var target = document.querySelector(href);
          if (target && guideBody.contains(target)) {
            e.preventDefault();
            target.scrollIntoView({ behavior: "smooth", block: "start" });
            if (history.pushState) history.pushState(null, null, href);
          }
        }
      });
    });
  }

  // The three-bar menu on phones: open and close it; a tap on a section,
  // outside it, or Escape closes it.
  var menuBtn = $("nav-menu"), drop = $("nav-drop");
  if (menuBtn && drop) {
    var setMenu = function (open) {
      drop.hidden = !open;
      menuBtn.setAttribute("aria-expanded", String(open));
      menuBtn.setAttribute("aria-label", open ? "Close menu" : "Menu");
    };
    menuBtn.addEventListener("click", function () { setMenu(drop.hidden); });
    drop.addEventListener("click", function (e) { if (e.target.closest("a")) setMenu(false); });
    document.addEventListener("click", function (e) {
      if (!drop.hidden && !e.target.closest("#nav")) setMenu(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !drop.hidden) { setMenu(false); menuBtn.focus(); }
    });
  }

  // "You pick how early it rings": the chips move the bell along the line to
  // where the alarm would ring, and the sentence under it says it in words.
  var ring = $("ring");
  if (ring) {
    var chips = Array.prototype.slice.call(ring.querySelectorAll("[role=radio]"));
    var share = { "0.5": 10, "1": 18, "2": 30, "3": 42, "5": 60, "10": 86 };   // % of the line, not to scale
    var choose = function (chip, focus) {
      chips.forEach(function (c) {
        var on = c === chip;
        c.setAttribute("aria-checked", String(on));
        c.tabIndex = on ? 0 : -1;
      });
      var w = share[chip.dataset.km] || 30;
      $("ring-zone").style.width = w + "%";
      $("ring-bus").style.left = (100 - w) + "%";
      $("ring-km").textContent = chip.dataset.say;
      $("ring-min").textContent = chip.dataset.min + " minutes";
      if (focus) chip.focus();
    };
    chips.forEach(function (chip, i) {
      chip.addEventListener("click", function () { choose(chip); });
      chip.addEventListener("keydown", function (e) {
        var step = (e.key === "ArrowRight" || e.key === "ArrowDown") ? 1 : (e.key === "ArrowLeft" || e.key === "ArrowUp") ? -1 : 0;
        if (step) { e.preventDefault(); choose(chips[(i + step + chips.length) % chips.length], true); }
      });
    });
    choose(chips[0]);
  }
})();

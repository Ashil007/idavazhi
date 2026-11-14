# 1. Update web/about.js
with open('web/about.js', 'r', encoding='utf-8') as f:
    about_js = f.read()

target_ask = """  function askRating(demo, where) {
    if (!demo) {
      try {
        var last = Number(window.localStorage.getItem(ASKED_KEY) || 0);
        if (Date.now() - last < ASK_EVERY_MS) return;
        window.localStorage.setItem(ASKED_KEY, String(Date.now()));
      } catch (e) { /* no storage: ask anyway */ }
    }
    rateDemo = !!demo;
    rate.show();
    setStars(0);
    el("rate-form").reset();
    el("rate-msg").textContent = "";
    el("rate-where").textContent = (demo ? "Demo ride done" : "Trip done") +
      (where && where !== "your stop" ? " · " + where : "");
  }"""

# In case there's an encoding artifact with the middle dot in about.js, let's find the function
if "function askRating(demo, where)" in about_js:
    start_idx = about_js.find("function askRating(demo, where)")
    end_idx = about_js.find("/* ---------------------------------------------------------- start */", start_idx)
    old_func = about_js[start_idx:end_idx].strip()
    new_func = """function askRating(demo, where) {
    if (demo) return; // Never show feedback/rating prompt for demo mode
    try {
      var last = Number(window.localStorage.getItem(ASKED_KEY) || 0);
      if (Date.now() - last < ASK_EVERY_MS) return;
      window.localStorage.setItem(ASKED_KEY, String(Date.now()));
    } catch (e) { /* no storage: ask anyway */ }
    rateDemo = false;
    rate.show();
    setStars(0);
    el("rate-form").reset();
    el("rate-msg").textContent = "";
    el("rate-where").textContent = "Trip done" + (where && where !== "your stop" ? " · " + where : "");
  }"""
    about_js = about_js[:start_idx] + new_func + "\n\n  " + about_js[end_idx:]
    with open('web/about.js', 'w', encoding='utf-8') as f:
        f.write(about_js)
    print("about.js updated successfully")
else:
    print("askRating not found in about.js")

# 2. Update web/alerts.js
with open('web/alerts.js', 'r', encoding='utf-8') as f:
    alerts_js = f.read()

# Replace attach() button text setting with updateStartButton()
target_attach = 'el("trip-start").textContent = "Wake me before " + t[t.length - 1].name;'
if target_attach in alerts_js:
    alerts_js = alerts_js.replace(target_attach, 'updateStartButton();')
    print("alerts.js attach() updated")
else:
    print("target_attach not found in alerts.js")

# Add updateStartButton definition right above attach
target_above_attach = '  // Called whenever a plan (or another route option) is shown.\n  function attach(p) {'
repl_above_attach = """  function updateStartButton() {
    var btn = el("trip-start");
    if (!btn) return;
    if (running) {
      btn.textContent = "Stop trip alert";
      btn.classList.add("is-running");
      btn.setAttribute("aria-label", "Stop trip alert");
    } else {
      var t = (plan && targetsOf(plan)) || [];
      var stopName = t.length ? t[t.length - 1].name : "my stop";
      btn.textContent = "Wake me before " + stopName;
      btn.classList.remove("is-running");
      btn.setAttribute("aria-label", "Wake me before " + stopName);
    }
  }

  // Called whenever a plan (or another route option) is shown.
  function attach(p) {"""

if target_above_attach in alerts_js:
    alerts_js = alerts_js.replace(target_above_attach, repl_above_attach)
    print("alerts.js updateStartButton added")
else:
    print("target_above_attach not found in alerts.js")

# In silence(): do not dispatch vazhi:arrived if demo
target_silence = """    if (targets.length && fired[(targets.length - 1) + ":arrive"]) {
      var demo = simulating, where = targets[targets.length - 1].name;
      stop("You have arrived. Trip alerts are off.");
      // The trip is over: about.js asks how it went (and offers a bus ticket).
      document.dispatchEvent(new CustomEvent("vazhi:arrived", { detail: { demo: demo, stop: where } }));
    }"""

repl_silence = """    if (targets.length && fired[(targets.length - 1) + ":arrive"]) {
      var demo = simulating, where = targets[targets.length - 1].name;
      stop("You have arrived. Trip alerts are off.");
      // The trip is over: about.js asks how it went (and offers a bus ticket).
      if (!demo) {
        document.dispatchEvent(new CustomEvent("vazhi:arrived", { detail: { demo: false, stop: where } }));
      }
    }"""

if target_silence in alerts_js:
    alerts_js = alerts_js.replace(target_silence, repl_silence)
    print("alerts.js silence() updated")
else:
    print("target_silence not found in alerts.js")

# Update begin() and stop()
target_begin = """  function begin() {
    targets = targetsOf(plan);
    fired = {};
    awake = false;
    last = null;
    speed = null;
    running = true;
    el("trip-start").hidden = true;
    el("trip-sim").hidden = true;
    el("trip-stop").hidden = false;
    el("trip-status").hidden = false;
    el("trip-status").textContent = "Starting…";
  }"""

repl_begin = """  function begin() {
    targets = targetsOf(plan);
    fired = {};
    awake = false;
    last = null;
    speed = null;
    running = true;
    updateStartButton();
    el("trip-start").hidden = false;
    el("trip-sim").hidden = true;
    el("trip-stop").hidden = true;
    el("trip-status").hidden = false;
    el("trip-status").textContent = "Starting…";
  }"""

if target_begin in alerts_js:
    alerts_js = alerts_js.replace(target_begin, repl_begin)
    print("alerts.js begin() updated")
else:
    # check with three dots or unicode
    print("Looking for begin() with loose matching...")
    b_idx = alerts_js.find("function begin()")
    b_end = alerts_js.find("function start()", b_idx)
    alerts_js = alerts_js[:b_idx] + repl_begin + "\n\n  " + alerts_js[b_end:]
    print("alerts.js begin() updated via offset")

target_stop = """  function stop(message) {
    running = false;
    document.dispatchEvent(new CustomEvent("vazhi:tripend"));
    simulating = false;
    if (watchId != null) navigator.geolocation.clearWatch(watchId);
    watchId = null;
    clearInterval(simTimer);
    simTimer = null;
    stopRinging();
    if (wakeLock) { wakeLock.release(); wakeLock = null; }
    if (meLayer) meLayer.clearLayers();
    el("trip-start").hidden = false;
    el("trip-sim").hidden = false;
    el("trip-stop").hidden = true;
    el("trip-status").hidden = !message;
    el("trip-status").textContent = message || "";
  }"""

repl_stop = """  function stop(message) {
    running = false;
    document.dispatchEvent(new CustomEvent("vazhi:tripend"));
    simulating = false;
    if (watchId != null) navigator.geolocation.clearWatch(watchId);
    watchId = null;
    clearInterval(simTimer);
    simTimer = null;
    stopRinging();
    if (wakeLock) { wakeLock.release(); wakeLock = null; }
    if (meLayer) meLayer.clearLayers();
    updateStartButton();
    el("trip-start").hidden = false;
    el("trip-sim").hidden = false;
    el("trip-stop").hidden = true;
    el("trip-status").hidden = !message;
    el("trip-status").textContent = message || "";
  }"""

if target_stop in alerts_js:
    alerts_js = alerts_js.replace(target_stop, repl_stop)
    print("alerts.js stop() updated")
else:
    print("target_stop not found directly, using loose matching")
    s_idx = alerts_js.find("function stop(message)")
    s_end = alerts_js.find("function init()", s_idx)
    alerts_js = alerts_js[:s_idx] + repl_stop + "\n\n  " + alerts_js[s_end:]
    print("alerts.js stop() updated via offset")

# In init(): make trip-start toggle stop/start
target_init_click = 'el("trip-start").addEventListener("click", start);'
repl_init_click = """el("trip-start").addEventListener("click", function () {
      if (running) {
        stop("Trip alerts are off.");
      } else {
        start();
      }
    });"""

if target_init_click in alerts_js:
    alerts_js = alerts_js.replace(target_init_click, repl_init_click)
    print("alerts.js init() trip-start click handler updated")
else:
    print("target_init_click not found in alerts.js")

with open('web/alerts.js', 'w', encoding='utf-8') as f:
    f.write(alerts_js)

# 3. Update web/style.css
with open('web/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

# Add --i-stop to :root
target_css_icon = '--i-check: url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'black\' stroke-width=\'2\' stroke-linecap=\'round\' stroke-linejoin=\'round\'%3E%3Cpath d=\'m5 12 5 5L20 7\'/%3E%3C/svg%3E");'
repl_css_icon = """--i-check: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m5 12 5 5L20 7\'/%3E%3C/svg%3E");
  --i-stop: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='currentColor'%3E%3Crect x='5' y='5' width='14' height='14' rx='2.5'/%3E%3C/svg%3E");"""

if target_css_icon in css:
    css = css.replace(target_css_icon, repl_css_icon)
    print("style.css --i-stop added")
else:
    print("target_css_icon not found in style.css")

# Add .trip__start.is-running
target_trip_start = """.trip__start {
  width: 100%; min-height: 56px; padding: 12px 20px; border: 0; border-radius: 14px; cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 12px; text-align: left;
  background: var(--amber); color: var(--amber-ink); font: inherit; font-size: 18px; font-weight: 700;
  line-height: 24px; box-shadow: var(--sh-1);
}
.trip__start::before {
  content: ""; flex: 0 0 22px; height: 22px; background: currentColor;
  -webkit-mask: var(--i-bell) center / contain no-repeat; mask: var(--i-bell) center / contain no-repeat;
}
.trip__start:hover { background: #f8c25e; }
.trip__start:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }"""

repl_trip_start = """.trip__start {
  width: 100%; min-height: 56px; padding: 12px 20px; border: 0; border-radius: 14px; cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 12px; text-align: left;
  background: var(--amber); color: var(--amber-ink); font: inherit; font-size: 18px; font-weight: 700;
  line-height: 24px; box-shadow: var(--sh-1);
  transition: background .2s, color .2s;
}
.trip__start::before {
  content: ""; flex: 0 0 22px; height: 22px; background: currentColor;
  -webkit-mask: var(--i-bell) center / contain no-repeat; mask: var(--i-bell) center / contain no-repeat;
}
.trip__start:hover { background: #f8c25e; }
.trip__start:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
.trip__start.is-running {
  background: #dc2626; color: #ffffff;
}
.trip__start.is-running:hover {
  background: #b91c1c;
}
.trip__start.is-running::before {
  -webkit-mask: var(--i-stop) center / contain no-repeat;
  mask: var(--i-stop) center / contain no-repeat;
}
#trip-stop {
  display: none !important;
}
.trip__sim[hidden] {
  display: none !important;
}"""

if target_trip_start in css:
    css = css.replace(target_trip_start, repl_trip_start)
    print("style.css .trip__start.is-running added")
else:
    print("target_trip_start not found in style.css")

with open('web/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

# 4. Bump sw.js
with open('web/sw.js', 'r', encoding='utf-8') as f:
    sw = f.read()

if 'vazhi-shell-v50' in sw:
    sw = sw.replace('vazhi-shell-v50', 'vazhi-shell-v51')
    with open('web/sw.js', 'w', encoding='utf-8') as f:
        f.write(sw)
    print("sw.js bumped to v51")

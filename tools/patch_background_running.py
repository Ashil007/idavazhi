# 1. Update web/alerts.js with background keepalive
with open('web/alerts.js', 'r', encoding='utf-8') as f:
    alerts_js = f.read()

bg_code = """  var bgAudio = null;
  var bgLockRelease = null;
  var silentWavUrl = null;

  function getSilentWavUrl() {
    if (silentWavUrl) return silentWavUrl;
    var rate = 8000, secs = 1.0, n = Math.floor(rate * secs);
    var buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    function str(o, t) { for (var i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); }
    str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
    str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, "data"); v.setUint32(40, n * 2, true);
    for (var i = 0; i < n; i++) {
      v.setInt16(44 + i * 2, 0, true);
    }
    silentWavUrl = URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
    return silentWavUrl;
  }

  function startBackgroundKeepAlive() {
    try {
      if (!bgAudio) {
        bgAudio = new Audio(getSilentWavUrl());
        bgAudio.loop = true;
        bgAudio.volume = 0.05;
      }
      bgAudio.play().catch(function () {});
    } catch (e) {}

    var dest = (targets && targets.length) ? targets[targets.length - 1].name : "your stop";
    if ("mediaSession" in navigator) {
      try {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: "Vazhi • Trip Alert Active",
          artist: "Wake me before " + dest,
          album: "Following your trip in background",
          artwork: [
            { src: "icons/icon-192.png", sizes: "192x192", type: "image/png" },
            { src: "icons/icon-512.png", sizes: "512x512", type: "image/png" }
          ]
        });
        navigator.mediaSession.setActionHandler("stop", function () {
          stop("Trip alerts are off.");
        });
      } catch (e) {}
    }

    if (navigator.locks && navigator.locks.request) {
      try {
        navigator.locks.request("vazhi-tracking", function () {
          return new Promise(function (resolve) {
            bgLockRelease = resolve;
          });
        }).catch(function () {});
      } catch (e) {}
    }

    if (swReg && swReg.showNotification && Notification.permission === "granted") {
      try {
        swReg.showNotification("Vazhi is following your ride", {
          body: "Alarm set for " + dest + ". Active in background.",
          icon: "icons/icon-192.png",
          badge: "icons/favicon-32.png",
          tag: "vazhi-trip-status",
          silent: true,
          renotify: false
        });
      } catch (e) {}
    }
  }

  function stopBackgroundKeepAlive() {
    if (bgAudio) {
      try { bgAudio.pause(); } catch (e) {}
      bgAudio = null;
    }
    if ("mediaSession" in navigator) {
      try { navigator.mediaSession.metadata = null; } catch (e) {}
    }
    if (bgLockRelease) {
      try { bgLockRelease(); } catch (e) {}
      bgLockRelease = null;
    }
    if (swReg && swReg.getNotifications) {
      try {
        swReg.getNotifications({ tag: "vazhi-trip-status" }).then(function (notes) {
          notes.forEach(function (n) { n.close(); });
        }).catch(function () {});
      } catch (e) {}
    }
  }
"""

# Place helper functions right above begin()
target_begin_marker = "  function begin() {"
if target_begin_marker in alerts_js:
    alerts_js = alerts_js.replace(target_begin_marker, bg_code + "\n  function begin() {")
    print("Added bg_code to alerts.js")
else:
    print("target_begin_marker not found in alerts.js")

# In begin(): call startBackgroundKeepAlive()
target_inside_begin = """    running = true;
    updateStartButton();"""
repl_inside_begin = """    running = true;
    updateStartButton();
    startBackgroundKeepAlive();"""
if target_inside_begin in alerts_js:
    alerts_js = alerts_js.replace(target_inside_begin, repl_inside_begin)
    print("Updated begin() with startBackgroundKeepAlive")
else:
    print("target_inside_begin not found in alerts.js")

# In stop(message): call stopBackgroundKeepAlive()
target_inside_stop = """    stopRinging();
    if (wakeLock) { wakeLock.release(); wakeLock = null; }"""
repl_inside_stop = """    stopRinging();
    stopBackgroundKeepAlive();
    if (wakeLock) { wakeLock.release(); wakeLock = null; }"""
if target_inside_stop in alerts_js:
    alerts_js = alerts_js.replace(target_inside_stop, repl_inside_stop)
    print("Updated stop() with stopBackgroundKeepAlive")
else:
    print("target_inside_stop not found in alerts.js")

# In ring(): pause bgAudio so loud siren plays clearly
target_ring = "    var s = silent() ? null : ensureSiren();"
repl_ring = """    if (bgAudio) { try { bgAudio.pause(); } catch (e) {} }
    var s = silent() ? null : ensureSiren();"""
if target_ring in alerts_js:
    alerts_js = alerts_js.replace(target_ring, repl_ring)
    print("Updated ring() to pause bgAudio")
else:
    print("target_ring not found in alerts.js")

with open('web/alerts.js', 'w', encoding='utf-8') as f:
    f.write(alerts_js)

# 2. Update web/index.html note
with open('web/index.html', 'r', encoding='utf-8') as f:
    index_html = f.read()

old_note = '<p class="trip__note">Keep Vazhi open during the trip. The screen stays on.</p>'
new_note = '<p class="trip__note">Vazhi stays active in the background. The screen stays on while open.</p>'
if old_note in index_html:
    index_html = index_html.replace(old_note, new_note)
    with open('web/index.html', 'w', encoding='utf-8') as f:
        f.write(index_html)
    print("Updated trip__note in index.html")
else:
    print("old_note not found in index.html")

# 3. Bump sw.js cache version
with open('web/sw.js', 'r', encoding='utf-8') as f:
    sw_js = f.read()

if 'vazhi-shell-v51' in sw_js:
    sw_js = sw_js.replace('vazhi-shell-v51', 'vazhi-shell-v52')
    with open('web/sw.js', 'w', encoding='utf-8') as f:
        f.write(sw_js)
    print("Bumped sw.js to v52")
else:
    print("vazhi-shell-v51 not found in sw.js")

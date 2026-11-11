/* The message form on /contact: sends to the same inbox as "Tell me what
 * you think" in the app (POST /api/feedback, saved in data/feedback.db). */
(function () {
  "use strict";
  var form = document.getElementById("contact-form");
  if (!form) return;
  var text = document.getElementById("contact-text");
  var who = document.getElementById("contact-who");
  var count = document.getElementById("contact-count");
  var msg = document.getElementById("contact-msg");
  var btn = form.querySelector("button");

  function say(t, kind) { msg.textContent = t; msg.className = "contact__msg" + (kind ? " is-" + kind : ""); }

  text.addEventListener("input", function () { count.textContent = text.value.length + " / 1000"; });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var body = text.value.trim();
    if (body.length < 3) { say("Write a few words first.", "bad"); text.focus(); return; }
    btn.disabled = true;
    say("Sending...");
    fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: body, contact: who.value.trim(), page: "contact page" })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || "Couldn't send it. Please try again.");
      });
    }).then(function () {
      form.reset();
      count.textContent = "0 / 1000";
      say("Thank you! Your message reached me.", "ok");
    }).catch(function (err) {
      say(err.message && err.message.length < 120 ? err.message : "Couldn't send it. Please check your connection and try again.", "bad");
    }).then(function () { btn.disabled = false; });
  });
})();

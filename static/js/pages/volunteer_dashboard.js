// Page: volunteer_dashboard
// Depends on: hc-core.js (optional) for window.hc / telemetry
// Expects DOM ids: matchControls, matchModal, hc-confirm-overlay, hc-help-success, hc-detail-toast
// Expects JSON payloads: #hc-volDash-i18n, #hc-volDash-cfg
(function () {
  "use strict";

  function readJson(id, fallback) {
    var el = document.getElementById(id);
    if (!el) return fallback;
    try {
      return JSON.parse(el.textContent || el.innerText || "");
    } catch (_) {
      return fallback;
    }
  }

  var I18N = readJson("hc-volDash-i18n", {});
  var CFG = readJson("hc-volDash-cfg", {});

  function track(name, props) {
    try {
      if (typeof window.hcTrack === "function") {
        window.hcTrack(name, props);
        return;
      }
      if (window.hc && typeof window.hc.post === "function") {
        window.hc.post("/events", { event: name, props: props || {} }).catch(function () {});
      }
    } catch (_) {}
  }

  async function postJson(url, payload) {
    try {
      if (typeof window.hcPostJson === "function") {
        return await window.hcPostJson(url, payload || {});
      }
      if (window.hc && typeof window.hc.post === "function") {
        var r = await window.hc.post(url, payload || {});
        return !!(r && r.ok);
      }
    } catch (_) {}
    return false;
  }

  function escHtml(s) {
    return String(s).replace(/[&<>"']/g, function (ch) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#39;"
      }[ch];
    });
  }

  function toast(msg) {
    var t = document.querySelector(".hc-toast");
    if (!t) {
      t = document.createElement("div");
      t.className = "hc-toast";
      t.setAttribute("role", "status");
      t.setAttribute("aria-live", "polite");
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add("is-show");
    clearTimeout(window.__hcToastTimer);
    window.__hcToastTimer = setTimeout(function () {
      t.classList.remove("is-show");
    }, 2800);
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (window.__hcVolDashBound) return;
    window.__hcVolDashBound = true;

    bindMatchControls();
    bindSeenAndDismiss();
    bindMatchModal();
    bindMyReqTabs();
    bindHelpDelegation();
  });

  function bindMatchControls() {
    var controls = document.getElementById("matchControls");
    if (!controls) return;

    var minEl = document.getElementById("min_match");
    var prioEl = document.getElementById("prio");
    var nearEl = document.getElementById("matchNear");
    if (!minEl || !prioEl || !nearEl) return;

    function buildUrl() {
      var params = new URLSearchParams(window.location.search);
      params.set("min", minEl.value);
      params.set("prio", prioEl.value);
      params.set("near", nearEl.checked ? "1" : "0");
      return window.location.pathname + "?" + params.toString();
    }

    function applyNow() {
      window.location.href = buildUrl();
    }

    minEl.addEventListener("change", function () {
      track("match_threshold_change", { value: this.value });
      applyNow();
    });
    prioEl.addEventListener("change", function () {
      track("match_priority_change", { value: this.value });
      applyNow();
    });
    if (!nearEl.disabled) {
      nearEl.addEventListener("change", function () {
        track("match_near_toggle", { value: this.checked });
        applyNow();
      });
    }
  }

  function bindSeenAndDismiss() {
    document.addEventListener("click", async function (e) {
      var seenEl = e.target.closest(".js-seen");
      if (seenEl) {
        var seenReqId = seenEl.getAttribute("data-req-id");
        if (seenReqId) {
          postJson("/volunteer/match/" + seenReqId + "/seen", {});
        }
        return;
      }

      var dismissBtn = e.target.closest(".js-dismiss");
      if (!dismissBtn) return;
      var reqId = dismissBtn.getAttribute("data-req-id");
      if (!reqId) return;

      var ok = await postJson("/volunteer/match/" + reqId + "/dismiss", {});
      if (!ok) return;
      track("match_dismiss", { request_id: reqId });
      var card = dismissBtn.closest(".hc-card, .match-card, .hc-match-card, article, .card");
      if (card) card.remove();
    });
  }

  function bindMatchModal() {
    var modal = document.getElementById("matchModal");
    var mmPct = document.getElementById("mmPct");
    var mmTitle = document.getElementById("mmTitle");
    var mmBreakdown = document.getElementById("mmBreakdown");
    var mmNote = document.getElementById("mmNote");
    if (!modal || !mmPct || !mmTitle || !mmBreakdown || !mmNote) return;

    function openModal() {
      modal.classList.add("is-open");
      modal.setAttribute("aria-hidden", "false");
    }
    function closeModal() {
      modal.classList.remove("is-open");
      modal.setAttribute("aria-hidden", "true");
    }
    function safeNum(value) {
      var n = Number(value);
      return Number.isFinite(n) ? n : 0;
    }
    function percent(value, max) {
      return Math.max(0, Math.min(100, Math.round((safeNum(value) / max) * 100)));
    }
    function clearNode(node) {
      while (node.firstChild) node.removeChild(node.firstChild);
    }
    function createRow(label, value, max) {
      var safeVal = safeNum(value);
      var pct = percent(safeVal, max);
      var row = document.createElement("div");
      row.className = "hc-row";

      var top = document.createElement("div");
      top.className = "hc-row__top";
      var labelSpan = document.createElement("span");
      labelSpan.className = "hc-row__label";
      labelSpan.textContent = label;
      var valSpan = document.createElement("span");
      valSpan.className = "hc-row__val";
      valSpan.textContent = "+" + safeVal;
      top.appendChild(labelSpan);
      top.appendChild(valSpan);

      var bar = document.createElement("div");
      bar.className = "hc-row__bar";
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-label", label);
      bar.setAttribute("aria-valuemin", "0");
      bar.setAttribute("aria-valuemax", "100");
      bar.setAttribute("aria-valuenow", String(pct));

      var fill = document.createElement("i");
      fill.setAttribute("aria-hidden", "true");
      fill.style.width = pct + "%";
      bar.appendChild(fill);

      row.appendChild(top);
      row.appendChild(bar);
      return row;
    }

    document.addEventListener("click", function (e) {
      var closer = e.target.closest("[data-close='1']");
      if (closer) {
        closeModal();
        return;
      }
      var btn = e.target.closest(".js-match-why");
      if (!btn) return;

      var pct = btn.dataset.pct || "â€”";
      var title = btn.dataset.title || "â€”";
      var reqId = btn.dataset.reqId || "";
      var breakdown = {};
      try {
        breakdown = JSON.parse(btn.dataset.breakdown || "{}");
      } catch (_) {}

      mmPct.textContent = pct + "%";
      mmTitle.textContent = title;

      var skills = safeNum(breakdown.skills || 0);
      var city = safeNum(breakdown.city || breakdown.loc || 0);
      var priority = safeNum(breakdown.priority || 0);
      var activity = safeNum(breakdown.activity || 0);
      var distance = safeNum(breakdown.distance || 0);

      clearNode(mmBreakdown);
      mmBreakdown.appendChild(createRow("Skills match", skills, 45));
      mmBreakdown.appendChild(createRow("Location match", city, 25));
      mmBreakdown.appendChild(createRow("Priority boost", priority, 20));
      mmBreakdown.appendChild(createRow("Volunteer activity", activity, 10));
      if (distance) mmBreakdown.appendChild(createRow("Distance", distance, 20));

      var mult = breakdown.urgency_mult ? "Urgency multiplier: x" + breakdown.urgency_mult : "";
      mmNote.textContent = mult || "This score is an estimate based on your profile + request metadata.";
      track("match_open_modal", { request_id: reqId, pct: pct });
      openModal();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && modal.classList.contains("is-open")) {
        closeModal();
      }
    });
  }

  function bindMyReqTabs() {
    var root = document.querySelector(".hc-myreq");
    if (!root) return;
    var tabs = Array.prototype.slice.call(root.querySelectorAll(".hc-tab[role='tab']"));
    var panels = Array.prototype.slice.call(root.querySelectorAll(".hc-tabPanel[role='tabpanel']"));
    if (!tabs.length || !panels.length) return;

    function activate(tabName) {
      tabs.forEach(function (btn) {
        var isActive = btn.dataset.tab === tabName;
        btn.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      panels.forEach(function (p) {
        var isActive = p.id === "myreq-panel-" + tabName;
        if (isActive) p.removeAttribute("hidden");
        else p.setAttribute("hidden", "hidden");
      });
    }

    tabs.forEach(function (btn) {
      btn.addEventListener("click", function () {
        activate(btn.dataset.tab);
      });
      btn.addEventListener("keydown", function (e) {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        var i = tabs.indexOf(btn);
        var next = e.key === "ArrowRight" ? tabs[(i + 1) % tabs.length] : tabs[(i - 1 + tabs.length) % tabs.length];
        next.focus();
        activate(next.dataset.tab);
      });
    });
  }

  function bindHelpDelegation() {
    var hcI18n = {
      confirmSendInterest: I18N.confirmSendInterest || "Send your interest?",
      pending: I18N.pending || "Pending",
      toastHelpSent: I18N.toastHelpSent || "Help offer sent.",
      newPill: I18N.newPill || "NEW",
      tipTitle: I18N.tipTitle || "Next step",
      tipText: I18N.tipText || "You can review the details and decide afterward.",
      viewDetails: I18N.viewDetails || "View details",
      willHelp: I18N.willHelp || "I can help",
      inviteHint: I18N.inviteHint || "This is an invitation, not a commitment.",
      processing: I18N.processing || "Processing..."
    };

    function startHelp(reqId, btnEl) {
      var ok = window.confirm(hcI18n.confirmSendInterest);
      if (!ok) return;
      if (btnEl) {
        btnEl.disabled = true;
        btnEl.textContent = hcI18n.pending;
        btnEl.classList.add("disabled");
        var note = btnEl.closest(".hc-match-card") ? btnEl.closest(".hc-match-card").querySelector(".hc-waiting-note") : null;
        if (note) note.hidden = false;
      }
      toast(hcI18n.toastHelpSent);
    }

    document.addEventListener("click", function (e) {
      var target = e.target.closest("a, button");
      if (!target) return;

      if (
        target.tagName === "A" &&
        target.getAttribute("href") &&
        target.getAttribute("href") !== "#" &&
        !target.getAttribute("href").startsWith("javascript")
      ) {
        return;
      }

      if (target.classList.contains("js-native-link")) return;
      var el = e.target.closest("[data-hc-action]");
      if (!el) return;

      var action = el.dataset.hcAction;
      var reqId = el.dataset.reqId || "unknown";

      if (action === "details") {
        var href = (el.getAttribute("href") || "").trim();
        if (el.tagName === "A" && href && href !== "#" && !href.startsWith("javascript")) return;
        e.preventDefault();
        e.stopPropagation();
        toast(I18N.toastDetails || "Les details s'afficheront ici.");
        return;
      }

      if (action === "help") {
        e.preventDefault();
        e.stopPropagation();
        startHelp(reqId, el);
      }
    }, true);

    window.hcAfterCardsUpdate = window.hcAfterCardsUpdate || [];

    (function highlightOnLoad() {
      var qs = new URLSearchParams(window.location.search);
      var demo = qs.get("demo_match") === "1";
      var matchesWrap = document.getElementById("hc-matches");
      if (!matchesWrap) return;
      var firstCard = matchesWrap.querySelector(".hc-match-card") || matchesWrap.querySelector(".card") || matchesWrap.firstElementChild;
      if (!firstCard) return;

      if (demo) {
        firstCard.classList.add("hc-match-highlight");
        var header = firstCard.querySelector(".d-flex") || firstCard;
        if (header && !firstCard.querySelector(".hc-new-pill")) {
          var pill = document.createElement("span");
          pill.className = "hc-new-pill";
          pill.textContent = hcI18n.newPill;
          header.appendChild(pill);
        }
        firstCard.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }

      if (CFG.justLoggedIn && firstCard) {
        firstCard.classList.add("hc-match-highlight");
        firstCard.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    })();

    (function hideDemoIfRealExists() {
      function fn() {
        var demoWrap = document.querySelector('[data-demo-wrap="1"]');
        if (!demoWrap) return;
        var hasReal = !!document.querySelector('.hc-match-card[data-req-id]:not([data-demo="1"])');
        demoWrap.style.display = hasReal ? "none" : "";
      }
      fn();
      window.hcAfterCardsUpdate.push(fn);
    })();

    window.addEventListener("load", function () {
      var el = document.querySelector(".hc-match-highlight");
      if (!el) return;
      setTimeout(function () {
        el.classList.remove("hc-match-highlight");
      }, 3500);
    });

    function markSeenOnHover(card) {
      card.addEventListener("mouseenter", function () {
        card.setAttribute("data-seen", "1");
        card.classList.remove("hc-match-highlight");
      }, { once: true });
    }
    document.querySelectorAll(".hc-match-card").forEach(function (card) {
      markSeenOnHover(card);
    });

    (function seenSessionState() {
      var key = "hcSeenRequests";
      var seen = new Set();
      try {
        seen = new Set(JSON.parse(sessionStorage.getItem(key) || "[]"));
      } catch (_) {}

      document.querySelectorAll(".hc-match-card").forEach(function (card, idx) {
        var id = card.dataset.reqId || idx;
        if (seen.has(String(id))) {
          card.classList.add("is-seen");
        }
        card.addEventListener("click", function () {
          seen.add(String(id));
          try {
            sessionStorage.setItem(key, JSON.stringify(Array.from(seen)));
          } catch (_) {}
          card.classList.add("is-seen");
        }, { once: true });
        markSeenOnHover(card);
      });
    })();

    (function firstMatchMicroMessage() {
      var flag = "hcFirstMatchSeen";
      function showBox() {
        var hasMatch = document.querySelector('.hc-match-card:not([data-demo="1"])');
        if (!hasMatch) return;
        try {
          if (localStorage.getItem(flag)) return;
        } catch (_) {}
        var box = document.getElementById("hc-first-match");
        if (box) {
          box.hidden = false;
          try {
            localStorage.setItem(flag, "1");
          } catch (_) {}
        }
      }
      showBox();
      window.hcAfterCardsUpdate.push(showBox);
    })();

    (function bindTooltipsFeature() {
      var tipEl = null;
      var tipId = "hc-help-tip";

      function ensureTip() {
        if (tipEl) return tipEl;
        tipEl = document.createElement("div");
        tipEl.className = "hc-tip";
        tipEl.id = tipId;

        var arrow = document.createElement("div");
        arrow.className = "hc-tip__arrow";
        var title = document.createElement("div");
        title.className = "hc-tip__title";
        title.textContent = hcI18n.tipTitle;
        var text = document.createElement("div");
        text.className = "hc-tip__text";
        text.textContent = hcI18n.tipText;

        tipEl.appendChild(arrow);
        tipEl.appendChild(title);
        tipEl.appendChild(text);
        document.body.appendChild(tipEl);
        return tipEl;
      }

      function showTip(btn) {
        var tip = ensureTip();
        var r = btn.getBoundingClientRect();
        tip.style.top = window.scrollY + r.bottom + 10 + "px";
        tip.style.left = window.scrollX + r.left + "px";
        tip.classList.add("is-on");
      }

      function hideTip() {
        if (tipEl) tipEl.classList.remove("is-on");
      }

      function bindTooltips(root) {
        var scope = root || document;
        scope.querySelectorAll(".hc-match-card .btn.btn-primary").forEach(function (btn) {
          if (btn.dataset.hcTipBound) return;
          btn.dataset.hcTipBound = "1";
          btn.setAttribute("aria-describedby", tipId);
          btn.addEventListener("mouseenter", function () { showTip(btn); });
          btn.addEventListener("mouseleave", hideTip);
          btn.addEventListener("focus", function () { showTip(btn); });
          btn.addEventListener("blur", hideTip);
          btn.addEventListener("touchstart", function (e) { showTip(btn); e.stopPropagation(); }, { passive: true });
          btn.addEventListener("click", function (e) {
            e.stopPropagation();
            if (tipEl && tipEl.classList.contains("is-on")) hideTip();
            else showTip(btn);
          });
        });
      }

      bindTooltips();
      document.addEventListener("click", hideTip);
      window.addEventListener("scroll", hideTip, { passive: true });
      window.hcBindHelpTooltips = bindTooltips;
    })();

    (function pollForMatches() {
      var intervalMs = 25000;
      var cardSel = ".hc-match-card";

      function sig(card) {
        if (card.dataset.demo === "1") return null;
        var id = card.dataset.reqId;
        if (id) return "id:" + id;
        var title = ((card.querySelector(".hc-listItem__title") || {}).textContent || "").trim();
        var meta = ((card.querySelector(".hc-listItem__meta") || {}).textContent || "").trim();
        var body = ((card.querySelector(".hc-listItem__body") || {}).textContent || "").trim().slice(0, 80);
        return "t:" + title + "|m:" + meta + "|b:" + body;
      }

      function currentSet() {
        var list = Array.prototype.slice.call(document.querySelectorAll(cardSel)).map(sig).filter(Boolean);
        return new Set(list);
      }

      var seen = currentSet();
      var busy = false;

      async function poll() {
        if (busy || document.hidden) return;
        busy = true;
        try {
          var res = await fetch(window.location.href, { headers: { "X-Requested-With": "hc-poll" } });
          if (!res.ok) return;

          var html = await res.text();
          var doc = new DOMParser().parseFromString(html, "text/html");
          var newCards = Array.prototype.slice.call(doc.querySelectorAll(cardSel)).filter(function (c) {
            return c.dataset.demo !== "1";
          });
          if (!newCards.length) return;

          var now = new Set(newCards.map(sig));
          var added = Array.from(now).filter(function (x) { return !seen.has(x); });
          if (!added.length) return;

          var curFirst = document.querySelector(cardSel);
          var newFirst = doc.querySelector(cardSel);
          if (!curFirst || !newFirst) return;

          var curParent = curFirst.parentElement;
          var newParent = newFirst.parentElement;
          if (!curParent || !newParent) return;

          curParent.innerHTML = newParent.innerHTML;
          document.querySelectorAll(cardSel).forEach(function (card) {
            if (added.includes(sig(card))) {
              card.classList.add("hc-arrive");
              setTimeout(function () {
                card.classList.remove("hc-arrive");
              }, 1200);
            }
          });

          if (window.hcBindHelpTooltips) window.hcBindHelpTooltips(document);
          if (window.hcAfterCardsUpdate) {
            window.hcAfterCardsUpdate.forEach(function (fn) {
              try { fn(); } catch (_) {}
            });
          }

          seen = currentSet();
          if (window.hcUpdateNotif) window.hcUpdateNotif();
        } catch (_) {
        } finally {
          busy = false;
        }
      }

      setInterval(poll, intervalMs);
    })();

    (function seenStateWithStorage() {
      var key = "hcSeenRequests";
      function bindSeen() {
        var seenSet = new Set();
        try {
          seenSet = new Set(JSON.parse(sessionStorage.getItem(key) || "[]"));
        } catch (_) {}
        document.querySelectorAll(".hc-match-card").forEach(function (card) {
          var id = card.dataset.reqId;
          if (!id) return;

          if (seenSet.has(String(id))) {
            card.classList.add("hc-seen");
            card.setAttribute("data-seen", "1");
          }

          if (card.dataset.hcSeenBound) return;
          card.dataset.hcSeenBound = "1";

          card.addEventListener("click", function () {
            seenSet.add(String(id));
            try {
              sessionStorage.setItem(key, JSON.stringify(Array.from(seenSet)));
            } catch (_) {}
            card.classList.add("hc-seen");
            card.setAttribute("data-seen", "1");
          }, { once: true });

          card.addEventListener("mouseenter", function () {
            card.setAttribute("data-seen", "1");
            card.classList.remove("hc-match-highlight");
          }, { once: true });
        });
      }
      bindSeen();
      window.hcAfterCardsUpdate = window.hcAfterCardsUpdate || [];
      window.hcAfterCardsUpdate.push(bindSeen);
    })();

    (function bindHelpOverlay() {
      var activeHelpBtn = null;
      var overlay = document.getElementById("hc-confirm-overlay");
      var cancelBtn = document.getElementById("hc-confirm-cancel");
      var okBtn = document.getElementById("hc-confirm-ok");
      var successBox = document.getElementById("hc-help-success");
      if (!overlay || !cancelBtn || !okBtn) return;

      function showHelpSuccess() {
        if (!successBox) return;
        successBox.hidden = false;
        setTimeout(function () {
          successBox.hidden = true;
        }, 3500);
      }

      function bindHelpButtons(root) {
        var scope = root || document;
        scope.querySelectorAll(".hc-help-btn").forEach(function (btn) {
          if (btn.dataset.hcConfirmBound) return;
          btn.dataset.hcConfirmBound = "1";
          btn.addEventListener("click", function (e) {
            e.preventDefault();
            if (btn.disabled) return;
            activeHelpBtn = btn;
            overlay.hidden = false;
          });
        });
      }

      bindHelpButtons();
      window.hcAfterCardsUpdate = window.hcAfterCardsUpdate || [];
      window.hcAfterCardsUpdate.push(function () { bindHelpButtons(document); });

      cancelBtn.onclick = function () {
        overlay.hidden = true;
        activeHelpBtn = null;
      };

      okBtn.onclick = function () {
        overlay.hidden = true;
        if (!activeHelpBtn) return;
        activeHelpBtn.classList.add("loading");
        activeHelpBtn.textContent = hcI18n.processing;

        setTimeout(function () {
          activeHelpBtn.classList.remove("loading");
          activeHelpBtn.disabled = true;
          activeHelpBtn.textContent = hcI18n.pending;
          activeHelpBtn.classList.add("disabled");
          var note = activeHelpBtn.closest(".hc-match-card") ? activeHelpBtn.closest(".hc-match-card").querySelector(".hc-waiting-note") : null;
          if (note) note.hidden = false;
          showHelpSuccess();
          activeHelpBtn = null;
        }, 800);
      };
    })();

    (function bindHelpHistory() {
      var helped = [];
      try {
        helped = JSON.parse(localStorage.getItem("hc-helped") || "[]");
      } catch (_) {}

      document.querySelectorAll(".hc-help-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var card = btn.closest(".hc-match-card");
          if (!card) return;
          var id = card.dataset.reqId;
          if (!id) return;
          if (!helped.includes(id)) {
            helped.push(id);
            try {
              localStorage.setItem("hc-helped", JSON.stringify(helped));
            } catch (_) {}
          }
        });
      });

      document.querySelectorAll(".hc-match-card").forEach(function (card) {
        var id = card.dataset.reqId;
        if (!id || !helped.includes(id)) return;
        var btn = card.querySelector(".hc-help-btn");
        if (btn) {
          btn.disabled = true;
          btn.textContent = hcI18n.pending;
          btn.classList.add("disabled");
        }
      });
    })();

    (function bindNotificationBadge() {
      function updateNotif() {
        var newCards = document.querySelectorAll('.hc-match-highlight:not([data-demo="1"])');
        var badge = document.getElementById("hcNotif");
        if (!badge) return;
        if (newCards.length) {
          badge.textContent = String(newCards.length);
          badge.hidden = false;
        } else {
          badge.hidden = true;
        }
      }
      window.hcUpdateNotif = updateNotif;
      updateNotif();
    })();

    (function bindDetailToast() {
      var toastEl = document.getElementById("hc-detail-toast");
      if (!toastEl) return;
      function showDetailToast() {
        toastEl.hidden = false;
        setTimeout(function () { toastEl.hidden = true; }, 2200);
      }
      function bind(root) {
        var scope = root || document;
        scope.querySelectorAll(".hc-detail-link").forEach(function (link) {
          if (link.dataset.hcDetailBound) return;
          link.dataset.hcDetailBound = "1";
          var card = link.closest(".hc-match-card");
          var isDemo = card && card.dataset.demo === "1";
          if (!isDemo) return;
          link.addEventListener("click", function (e) {
            e.preventDefault();
            showDetailToast();
          });
        });
      }
      bind();
      window.hcAfterCardsUpdate = window.hcAfterCardsUpdate || [];
      window.hcAfterCardsUpdate.push(function () { bind(document); });
    })();

    (function prettyTimes() {
      function pretty(ts) {
        var d = new Date(ts);
        if (isNaN(d.getTime())) return null;
        var diff = Math.floor((Date.now() - d.getTime()) / 1000);
        if (diff < 60) return "just now";
        var m = Math.floor(diff / 60);
        if (m < 60) return m + " min ago";
        var h = Math.floor(m / 60);
        if (h < 24) return h + " h ago";
        var days = Math.floor(h / 24);
        return days + " d ago";
      }
      document.querySelectorAll("time.hc-time[datetime]").forEach(function (t) {
        var p = pretty(t.getAttribute("datetime"));
        if (p) t.textContent = p;
      });
    })();

    (function persistentTabs() {
      var tabs = Array.prototype.slice.call(document.querySelectorAll(".hc-tab[data-tab]"));
      var panels = {
        pending: document.getElementById("tab-pending"),
        approved: document.getElementById("tab-approved"),
        rejected: document.getElementById("tab-rejected")
      };
      if (!tabs.length) return;

      function activate(key) {
        tabs.forEach(function (t) {
          var on = t.dataset.tab === key;
          t.classList.toggle("is-active", on);
          t.setAttribute("aria-selected", on ? "true" : "false");
        });
        Object.keys(panels).forEach(function (k) {
          var el = panels[k];
          if (!el) return;
          var on = k === key;
          el.classList.toggle("is-active", on);
          el.hidden = !on;
        });
        try {
          localStorage.setItem("hc_myreq_tab", key);
        } catch (_) {}
      }

      tabs.forEach(function (t) {
        t.addEventListener("click", function () { activate(t.dataset.tab); });
      });

      var saved = null;
      try {
        saved = localStorage.getItem("hc_myreq_tab");
      } catch (_) {}
      if (saved && panels[saved]) activate(saved);
    })();
  }
})();



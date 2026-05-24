/* HelpChain Core UI Engine (Phase 5.1)
 * - window.hc csrf/post helpers
 * - shared actions: copyText, copyFromTarget, clickTarget, confirmSubmit
 * - delegated actions ([data-action])
 * - telemetry (.js-track)
 * - rowlink (.js-rowlink)
 * - a11y drawer state + focus trap
 * - dev cache/service-worker cleanup on localhost
 */
(function () {
  "use strict";

  window.hc = window.hc || {};
  window.hc.csrf = function () {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  };
  window.hc.post = async function (url, data) {
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": window.hc.csrf(),
      },
      body: JSON.stringify(data || {}),
      credentials: "same-origin",
    });
  };

  window.copyText = async function (el) {
    var text = el && el.getAttribute ? el.getAttribute("data-copy") || "" : "";
    if (!text) return;

    try {
      await navigator.clipboard.writeText(text);
      if (el && el.classList) {
        el.classList.add("is-copied");
        setTimeout(function () {
          el.classList.remove("is-copied");
        }, 900);
      }
      return;
    } catch (_) {}

    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "absolute";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      if (el && el.classList) {
        el.classList.add("is-copied");
        setTimeout(function () {
          el.classList.remove("is-copied");
        }, 900);
      }
    } catch (_) {}
    document.body.removeChild(ta);
  };

  window.copyFromTarget = function (el) {
    var selector = el && el.getAttribute ? el.getAttribute("data-target") || "" : "";
    var node = selector ? document.querySelector(selector) : null;
    var text = node ? node.innerText || node.textContent || "" : "";
    if (!text) return;
    var proxy = {
      getAttribute: function (k) {
        return k === "data-copy" ? text : null;
      },
      classList: el && el.classList ? el.classList : { add: function () {}, remove: function () {} },
    };
    return window.copyText(proxy);
  };

  window.clickTarget = function (el) {
    var selector = el && el.getAttribute ? el.getAttribute("data-target") || "" : "";
    var node = selector ? document.querySelector(selector) : null;
    if (node && typeof node.click === "function") node.click();
  };

  window.confirmSubmit = function (el) {
    var msg =
      (el && el.getAttribute && el.getAttribute("data-confirm")) || "Confirmer cette action ?";
    if (!confirm(msg)) return;

    var form = el && el.closest ? el.closest("form") : null;
    if (form) {
      form.submit();
      return;
    }

    var href =
      (el && el.getAttribute && el.getAttribute("data-href")) ||
      (el && el.getAttribute && el.getAttribute("href")) ||
      "";
    if (href) window.location.href = href;
  };

  (function () {
    var submitTracked = new WeakSet();

    function safeTrack(name, payload) {
      try {
        if (typeof window.hcTrack === "function") window.hcTrack(name, payload || {});
      } catch (_) {}
    }

    document.addEventListener("click", function (e) {
      var el = e.target.closest(".js-track");
      if (!el) return;

      var name = el.getAttribute("data-track");
      if (!name) return;

      var payload = { path: window.location.pathname };
      var reqId = el.getAttribute("data-request-id");
      var pct = el.getAttribute("data-pct");

      if (reqId !== null && reqId !== "") payload.request_id = Number(reqId);
      if (pct !== null && pct !== "") payload.pct = Number(pct);

      if (
        el.tagName === "BUTTON" &&
        (el.getAttribute("type") || "").toLowerCase() === "submit"
      ) {
        var formForButton = el.closest("form");
        if (formForButton) submitTracked.add(formForButton);
      }

      safeTrack(name, payload);
    });

    document.addEventListener(
      "submit",
      function (e) {
        var form = e.target.closest("form");
        if (!form) return;

        if (submitTracked.has(form)) {
          submitTracked.delete(form);
          return;
        }

        var el = form.querySelector(".js-track[data-track]");
        if (!el) return;

        var name = el.getAttribute("data-track");
        if (!name) return;

        var payload = { path: window.location.pathname };
        var reqId = el.getAttribute("data-request-id");
        var pct = el.getAttribute("data-pct");

        if (reqId !== null && reqId !== "") payload.request_id = Number(reqId);
        if (pct !== null && pct !== "") payload.pct = Number(pct);

        safeTrack(name, payload);
      },
      true,
    );
  })();

  (function () {
    function hcSendInternalEvent(eventName, props) {
      try {
        var payload = JSON.stringify({
          event: eventName,
          props: props || {},
        });

        if (navigator.sendBeacon) {
          var blob = new Blob([payload], { type: "application/json" });
          navigator.sendBeacon("/events", blob);
          return;
        }

        fetch("/events", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: payload,
          keepalive: true,
          credentials: "same-origin",
        }).catch(function () {});
      } catch (_) {}
    }

    function hcTrack(eventName, props) {
      try {
        if (typeof window.plausible === "function") {
          window.plausible(eventName, { props: props || {} });
        }
      } catch (_) {}

      hcSendInternalEvent(eventName, props);
    }

    document.addEventListener(
      "click",
      function (e) {
        var el = e.target.closest("[data-hc-event]");
        if (!el) return;

        var eventName = el.getAttribute("data-hc-event");
        if (!eventName) return;

        var props = {
          page: window.location.pathname,
          lang: document.documentElement.lang || "bg",
          cta: el.getAttribute("data-hc-cta") || undefined,
          intent: el.getAttribute("data-hc-intent") || undefined,
          category: el.getAttribute("data-hc-category") || undefined,
        };

        hcTrack(eventName, props);
      },
      true,
    );
  })();

  (function () {
    function safeCall(fnName, el) {
      try {
        var fn = window[fnName];
        if (typeof fn === "function") return fn(el);
      } catch (_) {}
      return undefined;
    }

    document.addEventListener("click", function (e) {
      var el = e.target.closest("[data-action]");
      if (!el) return;

      var action = el.getAttribute("data-action");
      if (!action) return;

      if (el.tagName === "A") e.preventDefault();
      safeCall(action, el);
    });

    document.addEventListener(
      "submit",
      function (e) {
        var form = e.target;
        if (!form) return;

        var action = form.getAttribute("data-action");
        if (!action) return;

        safeCall(action, form);
      },
      true,
    );
  })();

  (function () {
    document.addEventListener("click", function (e) {
      var row = e.target.closest(".js-rowlink");
      if (!row) return;

      if (
        e.target.closest(
          "a,button,input,select,textarea,label,form,[data-action],[data-bs-toggle]",
        )
      ) {
        return;
      }

      var href = row.getAttribute("data-href");
      if (href) window.location.href = href;
    });
  })();

  (function () {
    var STORAGE_KEY = "hc_a11y_v1";
    var btn = document.getElementById("hcA11yBtn");
    var drawer = document.getElementById("hcA11yDrawer");
    if (!btn || !drawer) return;

    var closeEls = drawer.querySelectorAll('[data-a11y-close="true"]');
    var contrast = document.getElementById("hcA11yContrast");
    var textScale = document.getElementById("hcA11yTextScale");
    var motion = document.getElementById("hcA11yMotion");
    var simple = document.getElementById("hcA11ySimple");
    var fontDyslexic = document.getElementById("hcA11yFontDyslexic");
    var eco = document.getElementById("hcA11yEco");
    var readAloud = document.getElementById("hcA11yReadAloud");
    var voiceGuidance = document.getElementById("hcA11yVoiceGuidance");
    var speechRate = document.getElementById("hcA11ySpeechRate");
    var speechVolume = document.getElementById("hcA11ySpeechVolume");
    var speechVolumeValue = document.getElementById("hcA11ySpeechVolumeValue");
    var speechVoice = document.getElementById("hcA11ySpeechVoice");
    var audioTestBtn = document.getElementById("hcA11yAudioTest");
    var audioStopBtn = document.getElementById("hcA11yAudioStop");
    var readSelectionBtn = document.getElementById("hcA11yReadSelection");
    var readPageBtn = document.getElementById("hcA11yReadPage");
    var audioPauseBtn = document.getElementById("hcA11yAudioPause");
    var audioResumeBtn = document.getElementById("hcA11yAudioResume");
    var audioReadStopBtn = document.getElementById("hcA11yAudioReadStop");
    var audioReadStatus = document.getElementById("hcA11yAudioReadStatus");
    var audioAutoRead = document.getElementById("hcA11yAutoRead");
    var readHighlight = document.getElementById("hcA11yReadHighlight");
    var audioSupportNote = document.getElementById("hcA11yAudioSupport");
    var selectionAudioToolbar = document.getElementById("hcA11ySelectionAudioToolbar");
    var selectionAudioPlayBtn = document.getElementById("hcA11ySelectionPlay");
    var selectionAudioStopBtn = document.getElementById("hcA11ySelectionStop");
    var mainScreen = document.getElementById("hcA11yMainScreen");
    var langScreen = document.getElementById("hcA11yLangScreen");
    var profileScreen = document.getElementById("hcA11yProfileScreen");
    var langOpen = document.getElementById("hcA11yLangOpen");
    var langBack = document.getElementById("hcA11yLangBack");
    var profileBack = document.getElementById("hcA11yProfileBack");
    var profileTitle = document.getElementById("hcA11yProfileTitle");
    var profileSubtitle = document.getElementById("hcA11yProfileSubtitle");
    var profileDesc = document.getElementById("hcA11yProfileDesc");
    var profileList = document.getElementById("hcA11yProfileList");
    var profileBadges = document.getElementById("hcA11yProfileBadges");
    var profileApply = document.getElementById("hcA11yProfileApply");
    var reset = document.getElementById("hcA11yReset");
    var resetSection = document.getElementById("hcA11yResetSection");
    var systemBtn = document.getElementById("hcA11ySystem");
    var saveProfileBtn = document.getElementById("hcA11ySaveProfile");
    var summary = document.getElementById("hcA11ySummary");
    var summaryText = document.getElementById("hcA11ySummaryText");
    var summaryMode = document.getElementById("hcA11ySummaryMode");
    var paneTabs = Array.from(document.querySelectorAll("[data-a11y-pane-target]"));
    var panes = Array.from(document.querySelectorAll("[data-a11y-pane]"));
    var topHome = document.getElementById("hcA11yHome");
    var topPower = document.getElementById("hcA11yPower");
    var presetButtons = Array.from(document.querySelectorAll("[data-a11y-preset]"));
    var profileOpenButtons = Array.from(document.querySelectorAll("[data-a11y-profile-open]"));
    var currentPane = "display";
    var currentProfileKey = "";
    var USER_PROFILE_KEY = STORAGE_KEY + "_user_profile";
    var PRESETS = {
      reading_comfort: {
        textScale: "large",
        fontDyslexic: true,
        contrast: false,
        motion: false,
        simple: false,
        eco: false,
      },
      dmla: {
        textScale: "xlarge",
        fontDyslexic: false,
        contrast: true,
        motion: false,
        simple: true,
        eco: false,
      },
      presbyopia: {
        textScale: "xlarge",
        fontDyslexic: true,
        contrast: true,
        motion: false,
        simple: true,
        eco: false,
      },
      balanced: {
        textScale: "normal",
        fontDyslexic: false,
        contrast: false,
        motion: false,
        simple: false,
        eco: false,
      },
      focus: {
        textScale: "normal",
        fontDyslexic: false,
        contrast: false,
        motion: true,
        simple: true,
        eco: false,
      },
      high_visibility: {
        textScale: "large",
        fontDyslexic: false,
        contrast: true,
        motion: false,
        simple: false,
        eco: false,
      },
    };
    var PROFILE_META = {
      reading_comfort: {
        title: "Reading comfort",
        subtitle: "Comfort de lecture",
        description:
          "Improves long-form reading comfort with larger text and clearer letter spacing.",
        items: [
          "Text size: Large",
          "Dyslexia-friendly font enabled",
          "Keeps motion and layout unchanged",
          "Works well for articles and forms",
        ],
      },
      dmla: {
        title: "DMLA",
        subtitle: "Central vision comfort",
        description:
          "Boosts contrast and readability to support navigation when central vision is reduced.",
        items: [
          "Text size: Extra large",
          "High contrast enabled",
          "Simplified visuals enabled",
          "Keeps animations available unless separately reduced",
        ],
      },
      presbyopia: {
        title: "Presbyopia",
        subtitle: "Near-reading support",
        description:
          "Optimized for near reading with larger text, cleaner typography, and reduced visual clutter.",
        items: [
          "Text size: Extra large",
          "High contrast enabled",
          "Dyslexia-friendly font enabled",
          "Simplified visuals enabled",
        ],
        badges: ["vision", "reading"],
      },
      balanced: {
        title: "Balanced",
        subtitle: "Visual preset",
        description:
          "Keeps the default look while preserving visuals and motion for a neutral experience.",
        items: [
          "Text size: Normal",
          "No extra contrast",
          "Motion enabled",
          "No simplified filters",
        ],
        badges: ["preset"],
      },
      focus: {
        title: "Focus mode",
        subtitle: "Visual preset",
        description:
          "Reduces motion and visual noise to keep attention on content and actions.",
        items: [
          "Reduce motion enabled",
          "Simplified mode enabled",
          "Text size unchanged",
          "Good for lower cognitive load",
        ],
        badges: ["preset", "focus"],
      },
      high_visibility: {
        title: "High visibility",
        subtitle: "Visual preset",
        description:
          "Combines larger text and stronger contrast for fast readability improvements.",
        items: [
          "High contrast enabled",
          "Text size: Large",
          "Keeps motion and structure mostly unchanged",
          "Quick readability preset",
        ],
        badges: ["preset", "vision"],
      },
    };
    var lastTrigger = null;
    var mqReduceMotion = null;
    var mqMoreContrast = null;
    var speechVoices = [];
    var speechSupported =
      typeof window !== "undefined" &&
      typeof window.speechSynthesis !== "undefined" &&
      typeof window.SpeechSynthesisUtterance !== "undefined";
    var speechReader = {
      active: false,
      paused: false,
      mode: "",
      queue: [],
      index: -1,
      utterance: null,
      currentElement: null,
      suppressEnd: false,
    };
    var toastObserver = null;
    var selectionToolbarFrame = 0;

    function defaultA11yState() {
      return {
        enabled: true,
        contrast: false,
        textScale: "normal",
        motion: false,
        simple: false,
        fontDyslexic: false,
        eco: false,
        readAloud: false,
        voiceGuidance: false,
        speechRate: "normal",
        speechVolume: 80,
        speechVoice: "default",
        readHighlight: true,
        autoRead: false,
      };
    }

    function normalizeSpeechRateValue(value) {
      if (value === "slow" || value === "fast") return value;
      return "normal";
    }

    function normalizeSpeechVolumeValue(value) {
      var n = Number(value);
      if (!Number.isFinite(n)) return 80;
      n = Math.round(n / 5) * 5;
      if (n < 0) return 0;
      if (n > 100) return 100;
      return n;
    }

    function getFocusable(container) {
      return Array.from(
        container.querySelectorAll('a, button, input, select, textarea, [tabindex]:not([tabindex="-1"])'),
      ).filter(function (el) {
        return (
          !el.hasAttribute("disabled") &&
          el.getAttribute("aria-hidden") !== "true" &&
          !el.hidden &&
          el.getClientRects &&
          el.getClientRects().length > 0
        );
      });
    }

    function setHtmlAttr(name, value) {
      document.documentElement.setAttribute(name, value);
    }

    function loadState() {
      try {
        var raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) {
          return defaultA11yState();
        }
        var s = JSON.parse(raw);
        var textScaleValue = "normal";
        if (s.textScale === "large" || s.textScale === "xlarge") {
          textScaleValue = s.textScale;
        } else if (s.text) {
          textScaleValue = "large";
        }
        var d = defaultA11yState();
        return {
          enabled: s.enabled !== false,
          contrast: !!s.contrast,
          textScale: textScaleValue,
          motion: !!s.motion,
          simple: !!s.simple,
          fontDyslexic: !!s.fontDyslexic,
          eco: !!s.eco,
          readAloud: !!s.readAloud,
          voiceGuidance: !!s.voiceGuidance,
          speechRate: normalizeSpeechRateValue(s.speechRate || d.speechRate),
          speechVolume: normalizeSpeechVolumeValue(
            s.speechVolume != null ? s.speechVolume : d.speechVolume,
          ),
          speechVoice:
            typeof s.speechVoice === "string" && s.speechVoice
              ? s.speechVoice
              : d.speechVoice,
          readHighlight:
            typeof s.readHighlight === "boolean" ? s.readHighlight : d.readHighlight,
          autoRead: !!s.autoRead,
        };
      } catch (_) {
        return defaultA11yState();
      }
    }

    function saveState(state) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      } catch (_) {}
    }

    function clearSavedState() {
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch (_) {}
    }

    function hasSavedState() {
      try {
        return !!localStorage.getItem(STORAGE_KEY);
      } catch (_) {
        return false;
      }
    }

    function getSystemDefaults() {
      var prefersReducedMotion = false;
      var prefersMoreContrast = false;
      try {
        if (window.matchMedia) {
          mqReduceMotion = mqReduceMotion || window.matchMedia("(prefers-reduced-motion: reduce)");
          prefersReducedMotion = !!(mqReduceMotion && mqReduceMotion.matches);
          try {
            mqMoreContrast =
              mqMoreContrast || window.matchMedia("(prefers-contrast: more)");
            prefersMoreContrast = !!(mqMoreContrast && mqMoreContrast.matches);
          } catch (_) {}
          if (!prefersMoreContrast) {
            try {
              var mqForcedColors = window.matchMedia("(forced-colors: active)");
              prefersMoreContrast = !!(mqForcedColors && mqForcedColors.matches);
            } catch (_) {}
          }
        }
      } catch (_) {}
      return {
        enabled: true,
        contrast: prefersMoreContrast,
        textScale: "normal",
        motion: prefersReducedMotion,
        simple: false,
        fontDyslexic: false,
        eco: false,
        readAloud: false,
        voiceGuidance: false,
        speechRate: "normal",
        speechVolume: 80,
        speechVoice: "default",
        readHighlight: true,
        autoRead: false,
      };
    }

    function applyEcoRuntime(state) {
      var enabled = !!(state && state.eco);
      try {
        var images = document.querySelectorAll("img");
        images.forEach(function (img) {
          if (enabled) {
            if (!img.getAttribute("loading")) img.setAttribute("loading", "lazy");
            if (!img.getAttribute("decoding")) img.setAttribute("decoding", "async");
            var fetchPriority = (img.getAttribute("fetchpriority") || "").toLowerCase();
            if (fetchPriority === "high" && !img.closest("header, .navbar, .hc-home__hero")) {
              img.setAttribute("fetchpriority", "auto");
            }
          }
        });
      } catch (_) {}
      try {
        var videos = document.querySelectorAll("video");
        videos.forEach(function (video) {
          if (!enabled) return;
          if (video.autoplay && !video.dataset.hcEcoAutoplay) {
            video.dataset.hcEcoAutoplay = "1";
            try {
              video.pause();
            } catch (_) {}
          }
          if (!video.hasAttribute("controls")) {
            video.setAttribute("controls", "controls");
          }
          if ((video.getAttribute("preload") || "").toLowerCase() !== "none") {
            video.setAttribute("preload", "metadata");
          }
        });
      } catch (_) {}
    }

    function applyState(state) {
      var enabled = state.enabled !== false;
      setHtmlAttr("data-a11y-enabled", enabled ? "on" : "off");
      setHtmlAttr("data-a11y-contrast", enabled && state.contrast ? "high" : "normal");
      setHtmlAttr("data-a11y-text", enabled ? (state.textScale || "normal") : "normal");
      setHtmlAttr("data-a11y-motion", enabled && state.motion ? "reduced" : "normal");
      setHtmlAttr("data-a11y-simple", enabled && state.simple ? "on" : "off");
      setHtmlAttr("data-a11y-font", enabled && state.fontDyslexic ? "dyslexic" : "normal");
      setHtmlAttr("data-a11y-eco", enabled && state.eco ? "on" : "off");
      if (contrast) contrast.checked = !!state.contrast;
      if (textScale) textScale.value = state.textScale || "normal";
      if (motion) motion.checked = !!state.motion;
      if (simple) simple.checked = !!state.simple;
      if (fontDyslexic) fontDyslexic.checked = !!state.fontDyslexic;
      if (eco) eco.checked = !!state.eco;
      if (readAloud) readAloud.checked = !!state.readAloud;
      if (voiceGuidance) voiceGuidance.checked = !!state.voiceGuidance;
      if (speechRate) speechRate.value = normalizeSpeechRateValue(state.speechRate);
      if (speechVolume) speechVolume.value = String(normalizeSpeechVolumeValue(state.speechVolume));
      if (speechVolumeValue) {
        speechVolumeValue.value = String(normalizeSpeechVolumeValue(state.speechVolume)) + "%";
        speechVolumeValue.textContent = String(normalizeSpeechVolumeValue(state.speechVolume)) + "%";
      }
      if (speechVoice) {
        var requestedVoice = typeof state.speechVoice === "string" && state.speechVoice
          ? state.speechVoice
          : "default";
        var hasOption = Array.from(speechVoice.options || []).some(function (opt) {
          return opt.value === requestedVoice;
        });
        speechVoice.value = hasOption ? requestedVoice : "default";
      }
      if (readHighlight) readHighlight.checked = state.readHighlight !== false;
      if (audioAutoRead) audioAutoRead.checked = !!state.autoRead;
      if (drawer) drawer.setAttribute("data-a11y-master", enabled ? "on" : "off");
      applyEcoRuntime(enabled ? state : { eco: false });
      updateSummary(state);
      if (!enabled || !state.readAloud) {
        hideSelectionAudioToolbar();
      } else {
        maybeShowSelectionAudioToolbar();
      }
    }

    function currentState() {
      var scale = "normal";
      if (textScale && (textScale.value === "large" || textScale.value === "xlarge")) {
        scale = textScale.value;
      }
      return {
        enabled:
          !topPower || topPower.getAttribute("aria-pressed") !== "false",
        contrast: !!(contrast && contrast.checked),
        textScale: scale,
        motion: !!(motion && motion.checked),
        simple: !!(simple && simple.checked),
        fontDyslexic: !!(fontDyslexic && fontDyslexic.checked),
        eco: !!(eco && eco.checked),
        readAloud: !!(readAloud && readAloud.checked),
        voiceGuidance: !!(voiceGuidance && voiceGuidance.checked),
        speechRate: normalizeSpeechRateValue(speechRate && speechRate.value),
        speechVolume: normalizeSpeechVolumeValue(speechVolume && speechVolume.value),
        speechVoice:
          speechVoice && typeof speechVoice.value === "string" && speechVoice.value
            ? speechVoice.value
            : "default",
        readHighlight: !!(readHighlight ? readHighlight.checked : true),
        autoRead: !!(audioAutoRead && audioAutoRead.checked),
      };
    }

    function updateSummary(state) {
      if (state.enabled === false) {
        if (summaryText) {
          summaryText.textContent =
            summaryText.getAttribute("data-disabled") || "Accessibility paused";
        }
        if (summary) summary.classList.remove("is-active");
        if (reset) reset.disabled = false;
        if (summaryMode) {
          summaryMode.hidden = false;
          summaryMode.textContent =
            summaryMode.getAttribute("data-label-disabled") || "Power off";
        }
        if (topPower) {
          topPower.classList.remove("is-active");
          topPower.setAttribute("aria-pressed", "false");
        }
        updatePresetState({ enabled: false });
        updateActionButtons(state);
        return;
      }
      var active = 0;
      if (state.contrast) active += 1;
      if (state.textScale && state.textScale !== "normal") active += 1;
      if (state.motion) active += 1;
      if (state.simple) active += 1;
      if (state.fontDyslexic) active += 1;
      if (state.eco) active += 1;
      if (state.readAloud) active += 1;
      if (state.voiceGuidance) active += 1;
      if (state.autoRead) active += 1;
      if (normalizeSpeechRateValue(state.speechRate) !== "normal") active += 1;
      if (normalizeSpeechVolumeValue(state.speechVolume) !== 80) active += 1;
      if (state.speechVoice && state.speechVoice !== "default") active += 1;
      if (state.readHighlight === false) active += 1;

      if (summaryText) {
        var noneLabel = summaryText.getAttribute("data-none") || "No options enabled";
        var oneLabel = summaryText.getAttribute("data-one") || "1 option enabled";
        var manyLabel = summaryText.getAttribute("data-many") || "{count} options enabled";
        if (active === 0) {
          summaryText.textContent = noneLabel;
        } else if (active === 1) {
          summaryText.textContent = oneLabel;
        } else {
          summaryText.textContent = manyLabel.replace("{count}", String(active));
        }
      }
      if (summary) {
        summary.classList.toggle("is-active", active > 0);
      }
      if (reset) {
        reset.disabled = active === 0;
      }
      if (summaryMode) {
        var followsSystem = !hasSavedState();
        summaryMode.hidden = !followsSystem;
        if (followsSystem) {
          summaryMode.textContent =
            summaryMode.getAttribute("data-label-system") || "Following system";
        }
      }
      if (topPower) {
        topPower.classList.add("is-active");
        topPower.setAttribute("aria-pressed", "true");
      }
      updatePresetState(state);
      updateActionButtons(state);
    }

    function stateMatchesPreset(state, preset) {
      if (!state || !preset || state.enabled === false) return false;
      return (
        !!state.contrast === !!preset.contrast &&
        (state.textScale || "normal") === (preset.textScale || "normal") &&
        !!state.motion === !!preset.motion &&
        !!state.simple === !!preset.simple &&
        !!state.fontDyslexic === !!preset.fontDyslexic &&
        !!state.eco === !!preset.eco
      );
    }

    function updatePresetState(state) {
      if (!presetButtons.length) return;
      presetButtons.forEach(function (btn) {
        var key = btn.getAttribute("data-a11y-preset") || "";
        var isActive = stateMatchesPreset(state, PRESETS[key]);
        btn.classList.toggle("is-active", isActive);
        btn.setAttribute("aria-pressed", isActive ? "true" : "false");
      });
    }

    function applyPreset(name) {
      var preset = PRESETS[name];
      if (!preset) return;
      var s = currentState();
      s.enabled = true;
      s.contrast = !!preset.contrast;
      s.textScale = preset.textScale || "normal";
      s.motion = !!preset.motion;
      s.simple = !!preset.simple;
      s.fontDyslexic = !!preset.fontDyslexic;
      s.eco = !!preset.eco;
      saveState(s);
      applyState(s);
    }

    function normalizeStateForProfile(state) {
      return {
        enabled: state.enabled !== false,
        contrast: !!state.contrast,
        textScale: state.textScale === "large" || state.textScale === "xlarge" ? state.textScale : "normal",
        motion: !!state.motion,
        simple: !!state.simple,
        fontDyslexic: !!state.fontDyslexic,
        eco: !!state.eco,
        readAloud: !!state.readAloud,
        voiceGuidance: !!state.voiceGuidance,
        speechRate: normalizeSpeechRateValue(state.speechRate),
        speechVolume: normalizeSpeechVolumeValue(state.speechVolume),
        speechVoice:
          typeof state.speechVoice === "string" && state.speechVoice
            ? state.speechVoice
            : "default",
        readHighlight: state.readHighlight !== false,
        autoRead: !!state.autoRead,
      };
    }

    function saveUserProfile(state) {
      try {
        localStorage.setItem(USER_PROFILE_KEY, JSON.stringify(normalizeStateForProfile(state)));
        return true;
      } catch (_) {
        return false;
      }
    }

    function canResetSection(state) {
      var pane = currentPane || "display";
      if (pane === "display") {
        return !!state.contrast || !!state.fontDyslexic || (state.textScale && state.textScale !== "normal");
      }
      if (pane === "nav") {
        return !!state.motion || !!state.simple;
      }
      if (pane === "eco") {
        return !!state.eco;
      }
      if (pane === "audio") {
        return (
          !!state.readAloud ||
          !!state.voiceGuidance ||
          !!state.autoRead ||
          normalizeSpeechRateValue(state.speechRate) !== "normal" ||
          normalizeSpeechVolumeValue(state.speechVolume) !== 80 ||
          (state.speechVoice && state.speechVoice !== "default") ||
          state.readHighlight === false
        );
      }
      return false;
    }

    function resetCurrentSection() {
      var s = currentState();
      var pane = currentPane || "display";
      if (pane === "display") {
        s.contrast = false;
        s.textScale = "normal";
        s.fontDyslexic = false;
      } else if (pane === "nav") {
        s.motion = false;
        s.simple = false;
      } else if (pane === "eco") {
        s.eco = false;
      } else if (pane === "audio") {
        s.readAloud = false;
        s.voiceGuidance = false;
        s.speechRate = "normal";
        s.speechVolume = 80;
        s.speechVoice = "default";
        s.readHighlight = true;
        s.autoRead = false;
      } else {
        return false;
      }
      s.enabled = true;
      saveState(s);
      applyState(s);
      return true;
    }

    function updateActionButtons(state) {
      if (resetSection) {
        resetSection.disabled = !canResetSection(state);
      }
    }

    function showA11yPane(name) {
      currentPane = name || "display";
      paneTabs.forEach(function (tab) {
        var isActive = tab.getAttribute("data-a11y-pane-target") === currentPane;
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
        if (isActive) {
          tab.setAttribute("tabindex", "0");
        } else {
          tab.setAttribute("tabindex", "-1");
        }
      });
      panes.forEach(function (pane) {
        var isActive = pane.getAttribute("data-a11y-pane") === currentPane;
        pane.hidden = !isActive;
        pane.classList.toggle("is-active", isActive);
      });
      updateActionButtons(currentState());
    }

    function renderProfileScreen(key) {
      currentProfileKey = key || "";
      var meta = PROFILE_META[currentProfileKey] || null;
      if (!meta) return;
      if (profileTitle) profileTitle.textContent = meta.title;
      if (profileSubtitle) profileSubtitle.textContent = meta.subtitle || "Profile details";
      if (profileDesc) profileDesc.textContent = meta.description || "";
      if (profileList) {
        profileList.innerHTML = "";
        (meta.items || []).forEach(function (item) {
          var li = document.createElement("li");
          li.textContent = item;
          profileList.appendChild(li);
        });
      }
      if (profileBadges) {
        profileBadges.innerHTML = "";
        (meta.badges || []).forEach(function (badge) {
          var chip = document.createElement("span");
          chip.className = "hc-a11y-profilebadge";
          chip.textContent = String(badge);
          profileBadges.appendChild(chip);
        });
      }
      if (profileApply) {
        profileApply.setAttribute("data-a11y-profile-apply", currentProfileKey);
      }
    }

    function updateAudioSupportUI() {
      var supportedMsg = "Uses your browser or device voice. Availability depends on browser support.";
      var unsupportedMsg = "Speech playback is not available in this browser.";
      if (audioSupportNote) {
        supportedMsg = audioSupportNote.getAttribute("data-supported") || supportedMsg;
        unsupportedMsg = audioSupportNote.getAttribute("data-unsupported") || unsupportedMsg;
        if (speechSupported) {
          audioSupportNote.textContent = supportedMsg;
          audioSupportNote.classList.remove("is-warning");
        } else {
          audioSupportNote.textContent = unsupportedMsg;
          audioSupportNote.classList.add("is-warning");
        }
      }
      [
        speechRate,
        speechVolume,
        speechVoice,
        audioTestBtn,
        audioStopBtn,
        readSelectionBtn,
        readPageBtn,
        audioPauseBtn,
        audioResumeBtn,
        audioReadStopBtn,
      ].forEach(function (el) {
        if (!el) return;
        el.disabled = !speechSupported;
      });
      if (!speechSupported) {
        setAudioReadStatus("unsupported", unsupportedMsg);
      } else if (!audioReadStatus || !audioReadStatus.textContent.trim()) {
        setAudioReadStatus("idle", "Prêt à lire le texte sélectionné ou le contenu de la page.");
      }
      updateAudioPlaybackButtons();
    }

    function populateSpeechVoices() {
      if (!speechVoice) return;
      var previous = speechVoice.value || "default";
      if (previous === "default") {
        try {
          previous = loadState().speechVoice || "default";
        } catch (_) {
          previous = "default";
        }
      }
      var defaultLabel = speechVoice.getAttribute("data-default-label");
      if (!defaultLabel) {
        defaultLabel =
          (speechVoice.options && speechVoice.options[0] && speechVoice.options[0].textContent) ||
          "Default voice";
        speechVoice.setAttribute("data-default-label", defaultLabel);
      }
      speechVoice.innerHTML = "";
      var defaultOpt = document.createElement("option");
      defaultOpt.value = "default";
      defaultOpt.textContent = defaultLabel;
      speechVoice.appendChild(defaultOpt);

      if (!speechSupported) {
        speechVoices = [];
        speechVoice.value = "default";
        return;
      }

      try {
        speechVoices = (window.speechSynthesis.getVoices() || []).slice();
      } catch (_) {
        speechVoices = [];
      }

      speechVoices.forEach(function (voice) {
        if (!voice || !voice.voiceURI) return;
        var opt = document.createElement("option");
        opt.value = voice.voiceURI;
        opt.textContent = voice.name
          ? voice.name + (voice.lang ? " (" + voice.lang + ")" : "")
          : voice.voiceURI;
        speechVoice.appendChild(opt);
      });

      var hasPrevious = Array.from(speechVoice.options).some(function (opt) {
        return opt.value === previous;
      });
      speechVoice.value = hasPrevious ? previous : "default";
    }

    function selectedSpeechVoice() {
      if (!speechSupported || !speechVoice) return null;
      var selected = speechVoice.value || "default";
      if (selected === "default") {
        return autoSpeechVoiceForPageLanguage();
      }
      for (var i = 0; i < speechVoices.length; i += 1) {
        if (speechVoices[i] && speechVoices[i].voiceURI === selected) {
          return speechVoices[i];
        }
      }
      return null;
    }

    function normalizedLangTag(tag) {
      return String(tag || "")
        .trim()
        .replace(/_/g, "-")
        .toLowerCase();
    }

    function autoSpeechVoiceForPageLanguage() {
      if (!speechSupported || !speechVoices.length) return null;
      var pageLang = normalizedLangTag(document.documentElement && document.documentElement.lang);
      if (!pageLang) {
        var docBodyLang = normalizedLangTag(document.body && document.body.getAttribute && document.body.getAttribute("lang"));
        pageLang = docBodyLang || "";
      }
      var pageBase = pageLang.split("-")[0];

      function scoreVoice(v) {
        if (!v) return -1;
        var lang = normalizedLangTag(v.lang);
        var base = lang.split("-")[0];
        var score = 0;
        if (pageLang && lang === pageLang) score += 100;
        else if (pageLang && (lang.indexOf(pageLang + "-") === 0 || pageLang.indexOf(lang + "-") === 0)) score += 85;
        else if (pageBase && base === pageBase) score += 70;
        if (v.localService) score += 8;
        if (v.default) score += 6;
        if (lang) score += 1;
        return score;
      }

      var best = null;
      var bestScore = -1;
      for (var i = 0; i < speechVoices.length; i += 1) {
        var voice = speechVoices[i];
        var score = scoreVoice(voice);
        if (score > bestScore) {
          best = voice;
          bestScore = score;
        }
      }
      if (best && bestScore >= 70) return best;

      for (var j = 0; j < speechVoices.length; j += 1) {
        if (speechVoices[j] && speechVoices[j].default) return speechVoices[j];
      }
      return speechVoices[0] || null;
    }

    function speechRateToNumber(value) {
      if (value === "slow") return 0.85;
      if (value === "fast") return 1.2;
      return 1;
    }

    function audioStatusLabel(key, fallback) {
      if (!audioReadStatus) return fallback || "";
      return audioReadStatus.getAttribute("data-" + key) || fallback || "";
    }

    function setAudioReadStatus(key, fallback) {
      if (!audioReadStatus) return;
      audioReadStatus.textContent = audioStatusLabel(key, fallback);
    }

    function getSelectionRangeForAudio() {
      try {
        var sel = window.getSelection ? window.getSelection() : null;
        if (!sel || !sel.rangeCount) return null;
        var text = normalizeSpokenText(sel.toString());
        if (!text) return null;
        var range = sel.getRangeAt(0);
        if (!range || range.collapsed) return null;
        if (
          range.commonAncestorContainer
        ) {
          var anchorEl =
            range.commonAncestorContainer.nodeType === 1
              ? range.commonAncestorContainer
              : range.commonAncestorContainer.parentElement;
          if (selectionAudioToolbar && anchorEl && selectionAudioToolbar.contains(anchorEl)) {
            return null;
          }
          if (drawer && anchorEl && drawer.contains(anchorEl)) {
            return null;
          }
        }
        if (
          range.startContainer &&
          range.startContainer.parentElement &&
          range.startContainer.parentElement.closest &&
          range.startContainer.parentElement.closest("input, textarea, [contenteditable='true']")
        ) {
          return null;
        }
        return range;
      } catch (_) {
        return null;
      }
    }

    function updateSelectionAudioToolbarButtons() {
      if (!selectionAudioToolbar) return;
      var state = currentState();
      var hasSelection = !!getSelectionRangeForAudio();
      if (selectionAudioPlayBtn) {
        selectionAudioPlayBtn.disabled = !speechSupported || !state.readAloud || !hasSelection;
      }
      if (selectionAudioStopBtn) {
        selectionAudioStopBtn.disabled = !speechSupported || !speechReader.active;
      }
    }

    function hideSelectionAudioToolbar() {
      if (!selectionAudioToolbar) return;
      selectionAudioToolbar.hidden = true;
      if (selectionToolbarFrame) {
        window.cancelAnimationFrame(selectionToolbarFrame);
        selectionToolbarFrame = 0;
      }
      updateSelectionAudioToolbarButtons();
    }

    function positionSelectionAudioToolbar() {
      if (!selectionAudioToolbar || selectionAudioToolbar.hidden) return;
      var range = getSelectionRangeForAudio();
      if (!range) {
        hideSelectionAudioToolbar();
        return;
      }
      var rect = range.getBoundingClientRect();
      if (!rect || (!rect.width && !rect.height)) {
        hideSelectionAudioToolbar();
        return;
      }
      var margin = 10;
      var toolbarRect = selectionAudioToolbar.getBoundingClientRect();
      var left = rect.left + rect.width / 2 - toolbarRect.width / 2;
      var top = rect.top - toolbarRect.height - 10;
      if (top < margin) {
        top = rect.bottom + 10;
      }
      left = Math.max(margin, Math.min(left, window.innerWidth - toolbarRect.width - margin));
      top = Math.max(margin, Math.min(top, window.innerHeight - toolbarRect.height - margin));
      selectionAudioToolbar.style.left = Math.round(left) + "px";
      selectionAudioToolbar.style.top = Math.round(top) + "px";
      updateSelectionAudioToolbarButtons();
    }

    function scheduleSelectionAudioToolbarPosition() {
      if (!selectionAudioToolbar || selectionAudioToolbar.hidden) return;
      if (selectionToolbarFrame) {
        window.cancelAnimationFrame(selectionToolbarFrame);
      }
      selectionToolbarFrame = window.requestAnimationFrame(function () {
        selectionToolbarFrame = 0;
        positionSelectionAudioToolbar();
      });
    }

    function maybeShowSelectionAudioToolbar() {
      if (!selectionAudioToolbar) return;
      var state = currentState();
      if (!state.readAloud || state.enabled === false) {
        hideSelectionAudioToolbar();
        return;
      }
      var range = getSelectionRangeForAudio();
      if (!range) {
        if (!speechReader.active || speechReader.mode !== "selection") {
          hideSelectionAudioToolbar();
        } else {
          updateSelectionAudioToolbarButtons();
        }
        return;
      }
      selectionAudioToolbar.hidden = false;
      scheduleSelectionAudioToolbarPosition();
    }

    function clearReadingHighlight() {
      if (speechReader.currentElement && speechReader.currentElement.classList) {
        speechReader.currentElement.classList.remove("hc-a11y-read-highlight");
      }
      speechReader.currentElement = null;
    }

    function setReadingHighlight(el) {
      clearReadingHighlight();
      if (!el || !el.classList) return;
      var s = currentState();
      if (s.readHighlight === false) return;
      speechReader.currentElement = el;
      el.classList.add("hc-a11y-read-highlight");
      try {
        el.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
      } catch (_) {
        try {
          el.scrollIntoView();
        } catch (_) {}
      }
    }

    function normalizeSpokenText(text) {
      return String(text || "").replace(/\s+/g, " ").trim();
    }

    function chunkSpeechText(text, maxLen) {
      var clean = normalizeSpokenText(text);
      if (!clean) return [];
      var limit = maxLen || 220;
      if (clean.length <= limit) return [clean];
      var parts = [];
      var sentences = clean.match(/[^.!?]+[.!?]*\s*|\S+/g) || [clean];
      sentences.forEach(function (sentence) {
        if (!sentence) return;
        if (sentence.length <= limit) {
          parts.push(sentence);
          return;
        }
        var words = sentence.split(/\s+/);
        var current = "";
        words.forEach(function (word) {
          var next = current ? current + " " + word : word;
          if (next.length > limit && current) {
            parts.push(current);
            current = word;
          } else {
            current = next;
          }
        });
        if (current) parts.push(current);
      });
      return parts.filter(Boolean);
    }

    function isVisibleReadableElement(el) {
      if (!el || el.hidden) return false;
      if (el.closest && el.closest("#hcA11yDrawer")) return false;
      if (el.closest && el.closest("script,style,noscript,nav,footer")) return false;
      if (typeof el.getClientRects === "function" && el.getClientRects().length === 0) return false;
      return true;
    }

    function buildSelectionReadQueue() {
      try {
        var sel = window.getSelection ? window.getSelection() : null;
        var text = sel ? normalizeSpokenText(sel.toString()) : "";
        if (!text) return [];
        return chunkSpeechText(text, 220).map(function (chunk) {
          return { text: chunk, el: null };
        });
      } catch (_) {
        return [];
      }
    }

    function buildPageReadQueue() {
      var root =
        document.querySelector("main") ||
        document.querySelector('[role="main"]') ||
        document.querySelector("article") ||
        document.body;
      if (!root) return [];

      var nodes = Array.from(
        root.querySelectorAll("h1,h2,h3,h4,h5,h6,p,li,blockquote,dt,dd,label,figcaption,td,th"),
      );
      if (!nodes.length && root === document.body) {
        nodes = Array.from(document.querySelectorAll("main, article, section"));
      }
      var items = [];
      var totalChars = 0;
      nodes.forEach(function (node) {
        if (totalChars > 24000) return;
        if (!isVisibleReadableElement(node)) return;
        var text = normalizeSpokenText(node.innerText || node.textContent || "");
        if (!text || text.length < 2) return;
        if (/^(menu|navigation|skip to|close|done)$/i.test(text)) return;
        chunkSpeechText(text, 220).forEach(function (chunk) {
          if (totalChars > 24000) return;
          totalChars += chunk.length;
          items.push({ text: chunk, el: node });
        });
      });
      return items;
    }

    function currentSpeechVoiceObject() {
      return selectedSpeechVoice();
    }

    function createSpeechUtterance(text) {
      var utterance = new window.SpeechSynthesisUtterance(text);
      var s = currentState();
      utterance.rate = speechRateToNumber(s.speechRate);
      utterance.volume = normalizeSpeechVolumeValue(s.speechVolume) / 100;
      var selectedVoiceObj = currentSpeechVoiceObject();
      if (selectedVoiceObj) utterance.voice = selectedVoiceObj;
      return utterance;
    }

    function finishReading(reasonKey) {
      var prevMode = speechReader.mode;
      speechReader.active = false;
      speechReader.paused = false;
      speechReader.mode = "";
      speechReader.queue = [];
      speechReader.index = -1;
      speechReader.utterance = null;
      speechReader.suppressEnd = false;
      clearReadingHighlight();
      if (reasonKey) setAudioReadStatus(reasonKey);
      updateAudioPlaybackButtons();
      updateSelectionAudioToolbarButtons();
      if (prevMode !== "selection") {
        hideSelectionAudioToolbar();
      }
    }

    function speakQueueIndex(index) {
      if (!speechSupported || !speechReader.active) return;
      if (index < 0 || index >= speechReader.queue.length) {
        finishReading("done", "Finished reading.");
        return;
      }
      speechReader.index = index;
      speechReader.paused = false;
      var item = speechReader.queue[index];
      if (!item || !item.text) {
        speakQueueIndex(index + 1);
        return;
      }
      setReadingHighlight(item.el);
      var utterance = createSpeechUtterance(item.text);
      speechReader.utterance = utterance;
      utterance.onend = function () {
        if (!speechReader.active) return;
        if (speechReader.suppressEnd) {
          speechReader.suppressEnd = false;
          return;
        }
        speakQueueIndex(index + 1);
      };
      utterance.onerror = function () {
        if (!speechReader.active) return;
        speakQueueIndex(index + 1);
      };
      try {
        window.speechSynthesis.speak(utterance);
      } catch (_) {
        finishReading("stopped", "Lecture arrêtée.");
      }
      updateAudioPlaybackButtons();
    }

    function startReadingQueue(queue, modeKey) {
      if (!speechSupported) {
        setAudioReadStatus("unsupported", "La lecture audio n’est pas disponible dans ce navigateur.");
        return false;
      }
      if (!queue || !queue.length) return false;
      if (speechReader.active) {
        stopReading("stopped");
      }
      stopSpeechPreview();
      speechReader.active = true;
      speechReader.paused = false;
      speechReader.mode = modeKey || "page";
      speechReader.queue = queue.slice();
      speechReader.index = -1;
      speechReader.utterance = null;
      speechReader.suppressEnd = false;
      setAudioReadStatus(
        modeKey === "selection" ? "reading-selection" : "reading-page",
        modeKey === "selection" ? "Lecture du texte sélectionné…" : "Lecture du contenu de la page…",
      );
      speakQueueIndex(0);
      return true;
    }

    function pauseReading() {
      if (!speechSupported || !speechReader.active || speechReader.paused) return false;
      try {
        window.speechSynthesis.pause();
        speechReader.paused = true;
        setAudioReadStatus("paused", "Reading paused.");
        updateAudioPlaybackButtons();
        return true;
      } catch (_) {
        return false;
      }
    }

    function resumeReading() {
      if (!speechSupported || !speechReader.active || !speechReader.paused) return false;
      try {
        window.speechSynthesis.resume();
        speechReader.paused = false;
        setAudioReadStatus(
          speechReader.mode === "selection" ? "reading-selection" : "reading-page",
          speechReader.mode === "selection" ? "Lecture du texte sélectionné…" : "Lecture du contenu de la page…",
        );
        updateAudioPlaybackButtons();
        return true;
      } catch (_) {
        return false;
      }
    }

    function stopReading(reasonKey) {
      if (!speechSupported) {
        finishReading(reasonKey || "stopped");
        return false;
      }
      if (!speechReader.active && !window.speechSynthesis.speaking) {
        finishReading(reasonKey || "stopped");
        return false;
      }
      speechReader.suppressEnd = true;
      try {
        window.speechSynthesis.cancel();
      } catch (_) {}
      finishReading(reasonKey || "stopped");
      return true;
    }

    function stopSpeechPreview() {
      if (!speechSupported) return;
      try {
        window.speechSynthesis.cancel();
      } catch (_) {}
    }

    function updateAudioPlaybackButtons() {
      var reading = !!speechReader.active;
      var paused = !!speechReader.paused;
      if (audioPauseBtn) audioPauseBtn.disabled = !speechSupported || !reading || paused;
      if (audioResumeBtn) audioResumeBtn.disabled = !speechSupported || !reading || !paused;
      if (audioReadStopBtn) audioReadStopBtn.disabled = !speechSupported || !reading;
      updateSelectionAudioToolbarButtons();
    }

    function speakGuidance(message, opts) {
      if (!speechSupported) return;
      var text = normalizeSpokenText(message);
      if (!text) return;
      var state = currentState();
      if (!state.voiceGuidance) return;
      if ((opts && opts.force) !== true && speechReader.active) return;
      try {
        var utterance = createSpeechUtterance(text);
        utterance.rate = Math.max(0.9, Math.min(1.3, utterance.rate));
        utterance.volume = Math.min(1, utterance.volume);
        window.speechSynthesis.speak(utterance);
      } catch (_) {}
    }

    function initVoiceGuidanceObservers() {
      if (typeof MutationObserver === "undefined") return;
      var toastEl = document.getElementById("hc-toast");
      var lastSpoken = "";
      var lastAt = 0;
      function maybeSpeakGuidanceFromNode(node) {
        if (!node) return;
        var text = normalizeSpokenText(node.textContent || "");
        if (!text) return;
        var now = Date.now();
        if (text === lastSpoken && now - lastAt < 1800) return;
        lastSpoken = text;
        lastAt = now;
        speakGuidance(text);
      }

      if (toastEl) {
        toastObserver = new MutationObserver(function () {
          if (
            toastEl.classList.contains("is-show") ||
            toastEl.classList.contains("hc-toast--show") ||
            !toastEl.hidden
          ) {
            maybeSpeakGuidanceFromNode(toastEl);
          }
        });
        try {
          toastObserver.observe(toastEl, {
            childList: true,
            subtree: true,
            characterData: true,
            attributes: true,
            attributeFilter: ["class", "hidden"],
          });
        } catch (_) {}
      }

      try {
        var bodyObserver = new MutationObserver(function (mutations) {
          mutations.forEach(function (m) {
            if (m.type === "childList") {
              Array.from(m.addedNodes || []).forEach(function (node) {
                if (!node || node.nodeType !== 1) return;
                var el = node;
                if (
                  el.matches &&
                  (el.matches(".hc-toast.hc-toast--show, .hc-toast.is-show, .alert-success, [role='alert']") ||
                    el.matches(".toast.show"))
                ) {
                  maybeSpeakGuidanceFromNode(el);
                  return;
                }
                var nested =
                  el.querySelector &&
                  el.querySelector(".hc-toast.hc-toast--show, .hc-toast.is-show, .alert-success, .toast.show, [role='alert']");
                if (nested) maybeSpeakGuidanceFromNode(nested);
              });
              return;
            }
            if (m.type === "attributes" && m.target && m.target.nodeType === 1) {
              var target = m.target;
              if (
                target.matches &&
                (target.matches(".hc-toast.hc-toast--show, .hc-toast.is-show, .toast.show, .alert-success") ||
                  (target.matches("[role='alert']") && !target.hidden))
              ) {
                maybeSpeakGuidanceFromNode(target);
              }
            }
          });
        });
        bodyObserver.observe(document.body, {
          childList: true,
          subtree: true,
          attributes: true,
          attributeFilter: ["class", "hidden"],
        });
      } catch (_) {}

      document.addEventListener("hc:a11y-voice-guidance", function (e) {
        var detail = e && e.detail ? e.detail : {};
        maybeSpeakGuidanceFromNode({ textContent: detail.message || "" });
      });

      document.addEventListener("submit", function (e) {
        var form = e.target;
        if (!form || !form.classList) return;
        var label =
          (form.getAttribute && (form.getAttribute("data-success-voice") || form.getAttribute("aria-label"))) ||
          "";
        if (!label) return;
        window.setTimeout(function () {
          speakGuidance(label);
        }, 150);
      });
    }
    window.hcA11ySpeakGuidance = speakGuidance;

    function openDrawer(trigger) {
      lastTrigger = trigger || btn;
      showA11yScreen("main");
      showA11yPane(currentPane || "display");
      drawer.hidden = false;
      drawer.inert = false;
      drawer.setAttribute("aria-hidden", "false");
      btn.setAttribute("aria-expanded", "true");
      document.body.classList.add("hc-modal-open");
      var focusable = getFocusable(drawer);
      if (focusable.length) {
        focusable[0].focus({ preventScroll: false });
      } else {
        drawer.focus({ preventScroll: false });
      }
    }

    function closeDrawer() {
      showA11yScreen("main");
      showA11yPane(currentPane || "display");
      currentProfileKey = "";
      stopReading("stopped");
      drawer.hidden = true;
      drawer.inert = true;
      drawer.setAttribute("aria-hidden", "true");
      btn.setAttribute("aria-expanded", "false");
      document.body.classList.remove("hc-modal-open");
      stopSpeechPreview();
      if (lastTrigger) lastTrigger.focus({ preventScroll: false });
    }

    var hasExisting = hasSavedState();
    var initial = loadState();
    if (
      !hasExisting &&
      getSystemDefaults().motion
    ) {
      initial.motion = true;
    }

    function showA11yScreen(name) {
      if (mainScreen) mainScreen.hidden = name !== "main";
      if (langScreen) langScreen.hidden = name !== "lang";
      if (profileScreen) profileScreen.hidden = name !== "profile";
      if (langOpen) langOpen.setAttribute("aria-expanded", name === "lang" ? "true" : "false");
    }
    if (!hasExisting) {
      var sys = getSystemDefaults();
      initial.motion = sys.motion;
      initial.contrast = sys.contrast;
    }
    updateAudioSupportUI();
    populateSpeechVoices();
    if (speechSupported && window.speechSynthesis) {
      try {
        if (typeof window.speechSynthesis.addEventListener === "function") {
          window.speechSynthesis.addEventListener("voiceschanged", populateSpeechVoices);
        }
      } catch (_) {}
    }
    applyState(initial);
    setAudioReadStatus("idle", "Prêt à lire le texte sélectionné ou le contenu de la page.");
    initVoiceGuidanceObservers();
    showA11yPane(currentPane);

    drawer.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        e.preventDefault();
        if (langScreen && !langScreen.hidden) {
          showA11yScreen("main");
          if (langOpen) langOpen.focus({ preventScroll: false });
          return;
        }
        if (profileScreen && !profileScreen.hidden) {
          showA11yScreen("main");
          showA11yPane("nav");
          var srcBtn = currentProfileKey
            ? drawer.querySelector('[data-a11y-profile-open="' + currentProfileKey + '"]')
            : null;
          if (srcBtn) srcBtn.focus({ preventScroll: false });
          return;
        }
        closeDrawer();
        return;
      }
      if (e.key !== "Tab") return;

      var focusables = getFocusable(drawer);
      if (!focusables.length) return;
      var first = focusables[0];
      var last = focusables[focusables.length - 1];

      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    });

    btn.addEventListener("click", function () {
      openDrawer(btn);
    });
    closeEls.forEach(function (el) {
      el.addEventListener("click", closeDrawer);
    });

    if (langOpen && langScreen) {
      langOpen.addEventListener("click", function () {
        showA11yPane("lang");
        showA11yScreen("lang");
        var firstLangBtn = langScreen.querySelector(".hc-a11y-langitem");
        if (firstLangBtn) firstLangBtn.focus({ preventScroll: false });
      });
    }

    if (langBack) {
      langBack.addEventListener("click", function () {
        showA11yPane("lang");
        showA11yScreen("main");
        if (langOpen) langOpen.focus({ preventScroll: false });
      });
    }

    if (profileBack) {
      profileBack.addEventListener("click", function () {
        showA11yPane("nav");
        showA11yScreen("main");
        var srcBtn = currentProfileKey
          ? drawer.querySelector('[data-a11y-profile-open="' + currentProfileKey + '"]')
          : null;
        if (srcBtn) srcBtn.focus({ preventScroll: false });
      });
    }

    if (topHome) {
      topHome.addEventListener("click", function () {
        showA11yScreen("main");
        showA11yPane("display");
        if (paneTabs.length) {
          paneTabs[0].focus({ preventScroll: false });
        }
      });
    }

    if (topPower) {
      topPower.addEventListener("click", function () {
        var s = currentState();
        s.enabled = !(s.enabled !== false);
        if (s.enabled === false) {
          stopReading("stopped");
        }
        saveState(s);
        applyState(s);
      });
    }

    paneTabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        showA11yPane(tab.getAttribute("data-a11y-pane-target") || "display");
      });
      tab.addEventListener("keydown", function (e) {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        e.preventDefault();
        if (!paneTabs.length) return;
        var idx = paneTabs.indexOf(tab);
        if (idx < 0) return;
        var next =
          e.key === "ArrowRight"
            ? (idx + 1) % paneTabs.length
            : (idx - 1 + paneTabs.length) % paneTabs.length;
        var nextTab = paneTabs[next];
        if (!nextTab) return;
        showA11yPane(nextTab.getAttribute("data-a11y-pane-target") || "display");
        nextTab.focus({ preventScroll: false });
      });
    });

    presetButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.hasAttribute("data-a11y-profile-open")) return;
        var key = btn.getAttribute("data-a11y-preset") || "";
        applyPreset(key);
      });
    });

    profileOpenButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var key = btn.getAttribute("data-a11y-profile-open") || "";
        if (!key) return;
        showA11yPane("nav");
        renderProfileScreen(key);
        showA11yScreen("profile");
        if (profileApply) profileApply.focus({ preventScroll: false });
      });
    });

    if (profileApply) {
      profileApply.addEventListener("click", function () {
        var key = profileApply.getAttribute("data-a11y-profile-apply") || currentProfileKey;
        if (!key) return;
        applyPreset(key);
      });
    }

    if (audioTestBtn) {
      audioTestBtn.addEventListener("click", function () {
        if (!speechSupported) return;
        if (speechReader.active) {
          stopReading("stopped");
        }
        var sample =
          audioTestBtn.getAttribute("data-sample-text") ||
          "Test vocal d’accessibilité. Voici un aperçu de vos réglages audio.";
        stopSpeechPreview();
        try {
          var utterance = createSpeechUtterance(sample);
          window.speechSynthesis.speak(utterance);
        } catch (_) {}
      });
    }

    if (audioStopBtn) {
      audioStopBtn.addEventListener("click", function () {
        if (speechReader.active) {
          stopReading("stopped");
          return;
        }
        stopSpeechPreview();
      });
    }

    if (selectionAudioToolbar) {
      selectionAudioToolbar.addEventListener("mousedown", function (e) {
        e.preventDefault();
      });
      selectionAudioToolbar.addEventListener("touchstart", function (e) {
        e.preventDefault();
      }, { passive: false });
    }

    if (selectionAudioPlayBtn) {
      selectionAudioPlayBtn.addEventListener("click", function () {
        var queue = buildSelectionReadQueue();
        if (!queue.length) {
          setAudioReadStatus("no-selection", "Aucun texte sélectionné.");
          hideSelectionAudioToolbar();
          return;
        }
        startReadingQueue(queue, "selection");
        maybeShowSelectionAudioToolbar();
      });
    }

    if (selectionAudioStopBtn) {
      selectionAudioStopBtn.addEventListener("click", function () {
        stopReading("stopped");
        maybeShowSelectionAudioToolbar();
      });
    }

    document.addEventListener("selectionchange", function () {
      maybeShowSelectionAudioToolbar();
    });
    document.addEventListener("mouseup", function () {
      maybeShowSelectionAudioToolbar();
    });
    document.addEventListener("keyup", function () {
      maybeShowSelectionAudioToolbar();
    });
    document.addEventListener("scroll", function () {
      if (!selectionAudioToolbar || selectionAudioToolbar.hidden) return;
      scheduleSelectionAudioToolbarPosition();
    }, true);
    window.addEventListener("resize", function () {
      if (!selectionAudioToolbar || selectionAudioToolbar.hidden) return;
      scheduleSelectionAudioToolbarPosition();
    });

    if (readSelectionBtn) {
      readSelectionBtn.addEventListener("click", function () {
        var queue = buildSelectionReadQueue();
        if (!queue.length) {
          setAudioReadStatus("no-selection", "Aucun texte sélectionné.");
          return;
        }
        startReadingQueue(queue, "selection");
      });
    }

    if (readPageBtn) {
      readPageBtn.addEventListener("click", function () {
        var queue = buildPageReadQueue();
        if (!queue.length) {
          setAudioReadStatus("no-content", "Aucun contenu lisible détecté sur la page.");
          return;
        }
        startReadingQueue(queue, "page");
      });
    }

    if (audioPauseBtn) {
      audioPauseBtn.addEventListener("click", function () {
        pauseReading();
      });
    }

    if (audioResumeBtn) {
      audioResumeBtn.addEventListener("click", function () {
        resumeReading();
      });
    }

    if (audioReadStopBtn) {
      audioReadStopBtn.addEventListener("click", function () {
        stopReading("stopped");
      });
    }

    if (resetSection) {
      resetSection.addEventListener("click", function () {
        stopReading("stopped");
        stopSpeechPreview();
        resetCurrentSection();
      });
    }

    if (saveProfileBtn) {
      saveProfileBtn.addEventListener("click", function () {
        var ok = saveUserProfile(currentState());
        var defaultLabel =
          saveProfileBtn.getAttribute("data-label-default") || saveProfileBtn.textContent || "Enregistrer";
        if (!saveProfileBtn.getAttribute("data-label-default")) {
          saveProfileBtn.setAttribute("data-label-default", defaultLabel);
        }
        if (!ok) return;
        saveProfileBtn.classList.add("is-saved");
        saveProfileBtn.textContent =
          saveProfileBtn.getAttribute("data-label-saved") || "Enregistré";
        window.setTimeout(function () {
          saveProfileBtn.classList.remove("is-saved");
          saveProfileBtn.textContent =
            saveProfileBtn.getAttribute("data-label-default") || defaultLabel;
        }, 1400);
      });
    }

    if (speechVolume) {
      speechVolume.addEventListener("input", function () {
        var value = normalizeSpeechVolumeValue(speechVolume.value);
        if (speechVolumeValue) {
          speechVolumeValue.value = String(value) + "%";
          speechVolumeValue.textContent = String(value) + "%";
        }
      });
    }

    [
      contrast,
      textScale,
      motion,
      simple,
      fontDyslexic,
      eco,
      readAloud,
      voiceGuidance,
      speechRate,
      speechVolume,
      speechVoice,
      readHighlight,
      audioAutoRead,
    ].forEach(function (el) {
      if (!el) return;
      el.addEventListener("change", function () {
        var s = currentState();
        saveState(s);
        applyState(s);
        if (el === readHighlight) {
          if (s.readHighlight === false) {
            clearReadingHighlight();
          } else if (speechReader.active && speechReader.currentElement) {
            setReadingHighlight(speechReader.currentElement);
          }
        }
        if (el === readAloud && s.readAloud === false) {
          stopReading("stopped");
          hideSelectionAudioToolbar();
        } else if (el === readAloud && s.readAloud) {
          maybeShowSelectionAudioToolbar();
        }
      });
    });

    if (reset) {
      reset.addEventListener("click", function () {
        stopReading("stopped");
        stopSpeechPreview();
        var s = defaultA11yState();
        saveState(s);
        applyState(s);
      });
    }

    if (systemBtn) {
      systemBtn.addEventListener("click", function () {
        stopReading("stopped");
        clearSavedState();
        var sys = getSystemDefaults();
        sys.enabled = true;
        applyState(sys);
      });
    }

    function bindMediaChange(query, cb) {
      if (!query) return;
      if (typeof query.addEventListener === "function") {
        query.addEventListener("change", cb);
      } else if (typeof query.addListener === "function") {
        query.addListener(cb);
      }
    }

    bindMediaChange(mqReduceMotion || (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)")), function () {
      if (hasSavedState()) return;
      applyState(getSystemDefaults());
    });
    try {
      bindMediaChange(mqMoreContrast || (window.matchMedia && window.matchMedia("(prefers-contrast: more)")), function () {
        if (hasSavedState()) return;
        applyState(getSystemDefaults());
      });
    } catch (_) {}

    document.addEventListener("keydown", function (e) {
      var isAltA =
        e.altKey &&
        !e.ctrlKey &&
        !e.metaKey &&
        (e.key === "a" || e.key === "A");
      if (!isAltA) return;

      var t = e.target;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.tagName === "SELECT" ||
          t.isContentEditable)
      ) {
        return;
      }

      e.preventDefault();
      if (drawer.hidden) {
        openDrawer(btn);
      } else {
        closeDrawer();
      }
    });
  })();

  (function () {
    var host = window.location.hostname || "";
    if (host !== "127.0.0.1" && host !== "localhost") return;

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.getRegistrations().then(function (regs) {
        regs.forEach(function (r) {
          r.unregister();
        });
      });
    }

    if (window.caches) {
      caches.keys().then(function (keys) {
        keys.forEach(function (k) {
          caches.delete(k);
        });
      });
    }
  })();

  // --- HC: data-hc-width applier (CSP-friendly) ---
  function hcClampPct(v) {
    var n = Number(v);
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(100, n));
  }

  function hcApplyWidths(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll("[data-hc-width]");
    nodes.forEach(function (el) {
      var v = el.getAttribute("data-hc-width");
      var pct = hcClampPct(v);
      el.style.width = pct + "%";
      if (
        el.parentElement &&
        el.parentElement.getAttribute("role") === "progressbar"
      ) {
        el.parentElement.setAttribute("aria-valuenow", String(Math.round(pct)));
      }
    });
  }

  function hcHydrateHeroKPIs() {
    var els = {
      helped: document.querySelector('[data-hc-kpi="helped"]'),
      volunteers: document.querySelector('[data-hc-kpi="volunteers"]'),
      countries: document.querySelector('[data-hc-kpi="countries"]'),
      closed: document.querySelector('[data-hc-kpi="closed"]'),
    };

    if (!els.helped || !els.volunteers || !els.countries) return;

    fetch("/api/pilot-kpi", { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) return null;
        return r.json();
      })
      .then(function (data) {
        if (!data) return;

        var helped = data.helped;
        if (typeof helped === "undefined") helped = data.requests_helped;
        if (typeof helped === "undefined") helped = data.total_helped;

        var volunteers = data.active_volunteers;
        if (typeof volunteers === "undefined") volunteers = data.volunteers;

        var countries = data.countries;
        if (typeof countries === "undefined") countries = data.countries_served;

        var closed = data.closed;
        if (typeof closed === "undefined") closed = data.closed_requests;

        if (typeof helped !== "undefined") els.helped.textContent = String(helped);
        if (typeof volunteers !== "undefined") els.volunteers.textContent = String(volunteers);
        if (typeof countries !== "undefined") els.countries.textContent = String(countries);
        if (els.closed && typeof closed !== "undefined") els.closed.textContent = String(closed);

        if (typeof window.hcTrack === "function") {
          window.hcTrack("home_kpi_loaded", {
            helped: helped,
            active_volunteers: volunteers,
            countries: countries,
            closed: closed,
          });
        }
      })
      .catch(function () {});
  }

  document.addEventListener("DOMContentLoaded", function () {
    hcApplyWidths(document);
    if (document.body.classList.contains("hc-page-home")) {
      hcHydrateHeroKPIs();
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    const desc = document.getElementById("srDesc") || document.getElementById("sr-description");
    const status = document.getElementById("sr-desc-status");
    const email = document.getElementById("srEmail");
    const phone = document.getElementById("srPhone");
    const contactWarning = document.getElementById("srContactWarn");

    if (desc && status) {
      const updateDescStatus = () => {
        const value = desc.value.trim();
        if (value.length >= 20) {
          status.textContent = "✓ Sufficient information";
          status.classList.remove("hc-sr__status--hidden");
          status.classList.add("hc-sr__status--ok");
        } else {
          status.textContent = "";
          status.classList.remove("hc-sr__status--ok");
          status.classList.add("hc-sr__status--hidden");
        }
      };

      desc.addEventListener("input", updateDescStatus);
      updateDescStatus();
    }

    const updateContactState = () => {
      if (!contactWarning) return;
      const hasPhone = phone && phone.value.trim().length >= 6;
      const hasEmail = email && email.value.trim().length >= 3;
      const ok = hasPhone || hasEmail;

      contactWarning.classList.toggle("is-ok", ok);
      contactWarning.classList.toggle("is-warn", !ok);
    };

    if (phone) phone.addEventListener("input", updateContactState);
    if (email) email.addEventListener("input", updateContactState);
    updateContactState();
  });

  (function () {
    function initSubmitRequestAutoTitle() {
      const form = document.querySelector("form[data-hc-autotitle-template]");
      if (!form) return;

      const titleEl = document.getElementById("srTitle");
      const descEl = document.getElementById("srDesc");
      const catEl = document.getElementById("srCategory");
      const hintEl = document.getElementById("srTitleHint");

      if (!titleEl || !descEl) return;

      const tpl = form.getAttribute("data-hc-autotitle-template") || "Request: {snippet}";
      const tplCat =
        form.getAttribute("data-hc-autotitle-template-cat") || "{cat}: {snippet}";
      const hintOk = form.getAttribute("data-hc-hint-ok") || "Sufficient info";
      const hintMin = form.getAttribute("data-hc-hint-min") || "";
      let descTouched = false;

      const USER_EDITED_KEY = "hcTitleUserEdited";
      titleEl.addEventListener("input", () => {
        titleEl.dataset.hcUserEdited = "1";
        titleEl.dataset.hcAutoFilled = "0";
        titleEl.dataset.hcAutofill = "0";
        try {
          sessionStorage.setItem(USER_EDITED_KEY, "1");
        } catch (_) {}
      });

      try {
        if (sessionStorage.getItem(USER_EDITED_KEY) === "1") {
          titleEl.dataset.hcUserEdited = "1";
        }
      } catch (_) {}

      function setHint(state) {
        if (!hintEl) return;
        if (titleEl.dataset.hcUserEdited === "1") {
          hintEl.textContent = "";
          return;
        }
        if (state === "ok") {
          hintEl.textContent = `✓ ${hintOk}`;
          return;
        }
        if (state === "min") {
          hintEl.textContent = hintMin || "";
          const len = (descEl && descEl.value ? descEl.value : "").trim().length;
          if (!descTouched || len === 0) {
            hintEl.textContent = "";
          }
          return;
        }
        hintEl.textContent = "";
      }

      function cleanSnippet(text) {
        const t = (text || "").replace(/\s+/g, " ").trim();
        if (!t) return "";

        const first = t.split(/[\n\r]+/)[0].split(/[.!?]/)[0].trim();
        const base = (first || t).trim();
        const max = 64;
        if (base.length <= max) return base;
        return base.slice(0, max).replace(/\s+\S*$/, "") + "…";
      }

      function buildTitle() {
        const desc = descEl.value || "";
        const descTrim = desc.trim();
        if (!descTrim) {
          setHint("empty");
          return null;
        }
        const snippet = cleanSnippet(desc);
        if (snippet.length < 12) {
          setHint("min");
          return null;
        }

        const cat =
          catEl && catEl.value
            ? (catEl.options[catEl.selectedIndex] &&
                catEl.options[catEl.selectedIndex].textContent) ||
              ""
            : "";
        const category = (cat || "").trim();
        const template = category ? tplCat : tpl;
        return template
          .replace("{cat}", category)
          .replace("{snippet}", snippet)
          .trim();
      }

      function applyAutoTitle() {
        if (titleEl.dataset.hcUserEdited === "1") {
          setHint("min");
          return;
        }

        const isAuto =
          titleEl.dataset.hcAutoFilled === "1" || titleEl.dataset.hcAutofill === "1";
        const canAutoFill = !titleEl.value.trim() || isAuto;
        if (!canAutoFill) {
          setHint("ok");
          return;
        }

        const suggestion = buildTitle();
        if (!suggestion) return;

        titleEl.value = suggestion;
        titleEl.dataset.hcAutoFilled = "1";
        titleEl.dataset.hcAutofill = "1";
        setHint("ok");
      }

      descEl.addEventListener("input", () => {
        descTouched = true;
        applyAutoTitle();
      });
      if (catEl) catEl.addEventListener("change", applyAutoTitle);
      applyAutoTitle();

      document.addEventListener("click", (e) => {
        const a = e.target && e.target.closest ? e.target.closest("a") : null;
        if (!a) return;
        if (a.href && a.href.includes("/lang/")) {
          try {
            sessionStorage.removeItem(USER_EDITED_KEY);
          } catch (_) {}
        }
      });
    }

    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initSubmitRequestAutoTitle);
    } else {
      initSubmitRequestAutoTitle();
    }
  })();

  window.hcApplyWidths = hcApplyWidths;

})();
;(function () {
  if (window.__hcConversionTrackingV1) return;
  window.__hcConversionTrackingV1 = true;

  function sendConversion(eventName, props) {
    try {
      var payload = JSON.stringify({
        event: eventName,
        props: Object.assign({
          category: "conversion",
          url: window.location.pathname,
          title: document.title || "",
          referrer: document.referrer || ""
        }, props || {})
      });

      if (navigator.sendBeacon) {
        navigator.sendBeacon("/events", new Blob([payload], { type: "application/json" }));
        return;
      }

      fetch("/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
        keepalive: true,
        credentials: "same-origin"
      });
    } catch (e) {}
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest("a, button");
    if (!el) return;

    var text = (el.innerText || el.textContent || "").trim();
    var href = el.getAttribute("href") || "";
    var type = null;

    if (href.indexOf("/demo") >= 0 || /démo|demo|planifier/i.test(text)) {
      return; // disabled duplicate CTA tracking; handled by hc-intent-tracking.js
    } else if (href.indexOf("/contact") >= 0 || /contact|échanger/i.test(text)) {
      type = "cta_contact_click";
    } else if (href.indexOf("/demander-acces") >= 0 || /accès|access/i.test(text)) {
      type = "cta_access_request_click";
    } else if (href.indexOf("/professionnels") >= 0 || href.indexOf("/pilote") >= 0 || /pilote|professionnel/i.test(text)) {
      return; // disabled duplicate CTA tracking; handled by hc-intent-tracking.js
    }

    if (!type) return;

    sendConversion(type, {
      action: "click",
      label: text.slice(0, 120),
      href: href
    });
  }, true);

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.action) return;

    var action = form.getAttribute("action") || "";
    var type = null;

    if (action.indexOf("/demo") >= 0) {
      type = "demo_form_submit";
    } else if (action.indexOf("/contact") >= 0) {
      type = "contact_form_submit";
    } else if (action.indexOf("/demander-acces") >= 0) {
      type = "access_request_form_submit";
    } else if (action.indexOf("/professionnels") >= 0 || action.indexOf("/pilote") >= 0) {
      type = "pilot_interest_form_submit";
    }

    if (!type) return;

    sendConversion(type, {
      action: "submit",
      form_action: action
    });
  }, true);
})();
(function () {
  if (window.__hcPageViewTrackingV1) return;
  window.__hcPageViewTrackingV1 = true;

  function sendPageView() {
    try {
      var payload = JSON.stringify({
        event: "page_view",
        props: {
          category: "audience",
          action: "page_view",
          url: window.location.pathname,
          title: document.title || "",
          referrer: document.referrer || "",
          screen: window.screen ? (window.screen.width + "x" + window.screen.height) : "",
          device: window.innerWidth < 768 ? "mobile" : "desktop"
        }
      });

      if (navigator.sendBeacon) {
        navigator.sendBeacon("/events", new Blob([payload], { type: "application/json" }));
        return;
      }

      fetch("/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
        keepalive: true,
        credentials: "same-origin"
      });
    } catch (e) {}
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", sendPageView);
  } else {
    sendPageView();
  }
})();






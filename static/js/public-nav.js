(function () {
  var navRoots = document.querySelectorAll("[data-hc-public-nav]");
  if (!navRoots.length) return;

  var openRoot = null;

  function closeMega(root) {
    if (!root) return;
    var trigger = root.querySelector("[data-hc-mega-trigger]");
    var panel = root.querySelector("[data-hc-mega-panel]");
    root.classList.remove("is-open");
    if (trigger) trigger.setAttribute("aria-expanded", "false");
    if (panel) panel.hidden = true;
    if (openRoot === root) openRoot = null;
  }

  function openMega(root) {
    if (!root) return;
    if (openRoot && openRoot !== root) closeMega(openRoot);
    var trigger = root.querySelector("[data-hc-mega-trigger]");
    var panel = root.querySelector("[data-hc-mega-panel]");
    root.classList.add("is-open");
    if (trigger) trigger.setAttribute("aria-expanded", "true");
    if (panel) panel.hidden = false;
    openRoot = root;
  }

  function containsFocus(root) {
    return root && root.contains(document.activeElement);
  }

  navRoots.forEach(function (navRoot) {
    navRoot.querySelectorAll("[data-hc-mega]").forEach(function (megaRoot) {
      var trigger = megaRoot.querySelector("[data-hc-mega-trigger]");
      var panel = megaRoot.querySelector("[data-hc-mega-panel]");
      var closeTimer = null;
      if (!trigger || !panel) return;

      function cancelPendingClose() {
        if (!closeTimer) return;
        window.clearTimeout(closeTimer);
        closeTimer = null;
      }

      function scheduleClose() {
        cancelPendingClose();
        closeTimer = window.setTimeout(function () {
          closeMega(megaRoot);
        }, 140);
      }

      panel.hidden = true;
      trigger.setAttribute("aria-expanded", "false");

      trigger.addEventListener("click", function (event) {
        event.preventDefault();
        cancelPendingClose();
        if (megaRoot.classList.contains("is-open")) closeMega(megaRoot);
        else openMega(megaRoot);
      });

      trigger.addEventListener("focus", function () {
        cancelPendingClose();
        openMega(megaRoot);
      });

      megaRoot.addEventListener("pointerenter", function () {
        cancelPendingClose();
        openMega(megaRoot);
      });

      megaRoot.addEventListener("pointerleave", function () {
        scheduleClose();
      });

      megaRoot.addEventListener("focusout", function () {
        window.setTimeout(function () {
          if (!containsFocus(megaRoot)) closeMega(megaRoot);
        }, 0);
      });
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && openRoot) {
      var trigger = openRoot.querySelector("[data-hc-mega-trigger]");
      closeMega(openRoot);
      if (trigger) trigger.focus({ preventScroll: true });
    }
  });

  document.addEventListener("pointerdown", function (event) {
    if (openRoot && !openRoot.contains(event.target)) {
      closeMega(openRoot);
    }
  }, true);

  document.addEventListener("click", function (event) {
    if (openRoot && !openRoot.contains(event.target)) {
      closeMega(openRoot);
    }
  });
})();

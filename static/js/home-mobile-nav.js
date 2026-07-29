(function () {
  var body = document.body;
  if (!body || !body.classList.contains("hc-page-home")) return;

  var toggle = document.querySelector(".hc-home-premium__mobile-toggle");
  var panel = document.getElementById("hc-home-mobile-nav");

  if (!toggle || !panel) return;
  if (toggle.dataset.hcBound === "1") return;

  toggle.dataset.hcBound = "1";

  var closeTargets = panel.querySelectorAll("[data-home-mobile-close]");
  var links = panel.querySelectorAll("a");
  var mql = window.matchMedia("(max-width: 991.98px)");

  function closeMenu() {
    body.classList.remove("is-home-mobile-nav-open");
    panel.hidden = true;
    panel.style.display = "none";
    panel.setAttribute("aria-hidden", "true");
    toggle.setAttribute("aria-expanded", "false");
    toggle.classList.remove("is-open");
    toggle.setAttribute("aria-label", "Open navigation");
  }

  function openMenu() {
    body.classList.add("is-home-mobile-nav-open");
    panel.hidden = false;
    panel.style.display = "grid";
    panel.setAttribute("aria-hidden", "false");
    toggle.setAttribute("aria-expanded", "true");
    toggle.classList.add("is-open");
    toggle.setAttribute("aria-label", "Close navigation");
  }

  function syncToggleVisibility() {
    toggle.hidden = !mql.matches;

    if (!mql.matches) {
      closeMenu();
    }
  }

  toggle.addEventListener("click", function () {
    if (body.classList.contains("is-home-mobile-nav-open")) {
      closeMenu();
    } else {
      openMenu();
    }
  });

  closeTargets.forEach(function (node) {
    node.addEventListener("click", closeMenu);
  });

  links.forEach(function (link) {
    link.addEventListener("click", closeMenu);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeMenu();
    }
  });

  if (typeof mql.addEventListener === "function") {
    mql.addEventListener("change", syncToggleVisibility);
  } else if (typeof mql.addListener === "function") {
    mql.addListener(syncToggleVisibility);
  }

  closeMenu();
  syncToggleVisibility();
})();

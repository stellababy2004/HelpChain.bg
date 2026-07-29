(function () {
  function initMobileNav() {
    const toggle = document.getElementById("hcMobileNavToggle");
    const nav = document.getElementById("hcMobileNav");

    if (!toggle || !nav) return;
    if (toggle.dataset.hcBound === "1") return;

    toggle.dataset.hcBound = "1";

    const panel = nav.querySelector(".hc-mobile-nav__panel");
    const closeTargets = nav.querySelectorAll("[data-mobile-nav-close='1']");
    const firstLink = nav.querySelector(".hc-mobile-nav__item a");

    function isOpen() {
      return nav.classList.contains("is-open");
    }

    function openNav() {
      nav.inert = false;
      nav.hidden = false;
      nav.setAttribute("aria-hidden", "false");

      requestAnimationFrame(function () {
        nav.classList.add("is-open");
        document.body.classList.add("hc-mobile-nav-open");
        toggle.setAttribute("aria-expanded", "true");

        if (firstLink) {
          firstLink.focus({ preventScroll: true });
        }
      });
    }

    function closeNav(returnFocus) {
      if (!isOpen()) return;

      if (nav.contains(document.activeElement)) {
        toggle.focus({ preventScroll: true });
      }

      nav.classList.remove("is-open");
      document.body.classList.remove("hc-mobile-nav-open");
      toggle.setAttribute("aria-expanded", "false");

      window.setTimeout(function () {
        nav.setAttribute("aria-hidden", "true");
        nav.inert = true;

        if (returnFocus !== false) {
          toggle.focus({ preventScroll: true });
        }
      }, 220);
    }

    toggle.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();

      if (isOpen()) {
        closeNav(true);
      } else {
        openNav();
      }
    });

    closeTargets.forEach(function (target) {
      target.addEventListener("click", function (event) {
        event.preventDefault();
        closeNav(true);
      });
    });

    nav.querySelectorAll("a[href]").forEach(function (link) {
      link.addEventListener("click", function () {
        if (nav.contains(document.activeElement)) {
          toggle.focus({ preventScroll: true });
        }

        nav.classList.remove("is-open");
        document.body.classList.remove("hc-mobile-nav-open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && isOpen()) {
        closeNav(true);
      }

      if (event.key === "Tab" && isOpen() && panel) {
        const focusable = panel.querySelectorAll(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );

        if (!focusable.length) return;

        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    });

    window.addEventListener("resize", function () {
      if (window.innerWidth >= 1200 && isOpen()) {
        closeNav(false);
      }
    });

    nav.classList.remove("is-open");
    nav.setAttribute("aria-hidden", "true");
    nav.inert = true;
    nav.hidden = false;
    toggle.setAttribute("aria-expanded", "false");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initMobileNav, { once: true });
  } else {
    initMobileNav();
  }

  window.addEventListener("pageshow", initMobileNav);
})();

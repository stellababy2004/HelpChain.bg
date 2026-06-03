(function () {
  const path = window.location.pathname;
  const intentPages = ["/offre", "/deploiement", "/professionnels", "/collectivites"];
  const isIntentPage = intentPages.some((p) => path.startsWith(p));
  const SESSION_DISMISS_KEY = "helpchainLeadModalDismissed";
  const LEGACY_DISMISS_KEY = "hc_lead_capture_closed";

  if (!isIntentPage) return;

  function isDismissed() {
    try {
      if (sessionStorage.getItem(SESSION_DISMISS_KEY) === "1") return true;
      if (localStorage.getItem(LEGACY_DISMISS_KEY) === "1") return true;
    } catch (error) {
      console.warn("HC_LEAD_CAPTURE_STORAGE_READ_FAILED", error);
    }
    return false;
  }

  function persistDismissal() {
    try {
      sessionStorage.setItem(SESSION_DISMISS_KEY, "1");
      localStorage.setItem(LEGACY_DISMISS_KEY, "1");
    } catch (error) {
      console.warn("HC_LEAD_CAPTURE_STORAGE_WRITE_FAILED", error);
    }
  }

  if (isDismissed()) return;

  const isMobileViewport = window.matchMedia("(max-width: 767px)").matches;
  const triggerDelay = isMobileViewport ? 16000 : 8000;
  const scrollThreshold = isMobileViewport ? 0.65 : 0.55;

  function showCapture() {
    if (isDismissed()) return;
    if (document.querySelector(".hc-lead-capture")) return;

    const box = document.createElement("div");
    box.className = "hc-lead-capture is-visible";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "false");
    box.setAttribute("aria-labelledby", "hc-lead-capture-title");
    box.setAttribute("aria-describedby", "hc-lead-capture-text");
    box.innerHTML = `
      <button class="hc-lead-capture__close" type="button" aria-label="Fermer">×</button>

      <div class="hc-lead-capture__title" id="hc-lead-capture-title">
        Voir HelpChain dans votre contexte ?
      </div>

      <div class="hc-lead-capture__text" id="hc-lead-capture-text">
        Recevez un scénario de pilote adapté à votre organisation.
      </div>

      <input
        class="hc-lead-capture__input"
        id="hc-lead-email"
        type="email"
        placeholder="Email professionnel (ex: mairie, association, CCAS)"
        autocomplete="email"
      />

      <div class="hc-lead-capture__reassurance">
        Réponse sous 24h · Pilote progressif · Cadre maîtrisé
      </div>

      <div class="hc-lead-capture__note">
        Créneaux pilote limités cette semaine
      </div>

      <div class="hc-lead-capture__actions">
        <button class="hc-lead-capture__primary" id="hc-lead-submit" type="button">
          Échanger sur un pilote
        </button>
        <a class="hc-lead-capture__secondary" href="/deploiement">
          Voir le déploiement
        </a>
      </div>
    `;

    document.body.appendChild(box);

    box.querySelector(".hc-lead-capture__close").addEventListener("click", function () {
      persistDismissal();
      box.remove();
    });

    box.querySelector("#hc-lead-submit").addEventListener("click", async function () {
      const emailInput = box.querySelector("#hc-lead-email");
      emailInput.value = emailInput.value.toLowerCase();

      const email = emailInput.value.trim();
      if (!email) {
        alert("Veuillez entrer un email professionnel.");
        emailInput.focus();
        return;
      }

      const submitButton = box.querySelector("#hc-lead-submit");
      submitButton.disabled = true;
      submitButton.textContent = "Envoi...";

      try {
        const response = await fetch("/api/lead-capture", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: email,
            page: window.location.pathname,
            intent: "high",
            source: "lead_capture_popup"
          })
        });

        if (!response.ok) throw new Error("Lead capture failed: " + response.status);

        localStorage.setItem("hc_lead_email", email);
        persistDismissal();

        box.innerHTML = `
          <div class="hc-lead-capture__title">C’est fait.</div>

          <div class="hc-lead-capture__text">
            Ajoutez votre contexte pour préparer un échange plus utile.
          </div>

          <div class="hc-lead-capture__actions">
            <a class="hc-lead-capture__primary" href="/contact">
              Finaliser en 30 secondes
            </a>
          </div>
        `;

        console.log("HC_LEAD_CAPTURED", email);
      } catch (error) {
        console.error("HC_LEAD_CAPTURE_API_FAILED", error);

        submitButton.disabled = false;
        submitButton.textContent = "Échanger sur un pilote";

        alert("Erreur temporaire. Vous pouvez finaliser votre demande via le formulaire de contact.");
      }
    });
  }

  // On mobile, wait longer and require stronger intent before showing the prompt.
  setTimeout(showCapture, triggerDelay);

  let maxScroll = 0;
  window.addEventListener("scroll", function () {
    if (isDismissed()) return;

    const scrollTop = window.scrollY || document.documentElement.scrollTop;
    const docHeight = document.documentElement.scrollHeight - window.innerHeight;
    if (docHeight <= 0) return;

    maxScroll = Math.max(maxScroll, scrollTop / docHeight);
    if (maxScroll > scrollThreshold) showCapture();
  });

  // Make function globally available for debug/testing.
  window.showCapture = showCapture;
})();

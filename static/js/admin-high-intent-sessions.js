(function () {
  "use strict";

  function esc(value) {
    return String(value || "").replace(/[&<>"']/g, function (c) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c];
    });
  }

  function labelPath(path) {
    var map = {
      "/": "Accueil",
      "/comment_ca_marche": "Fonctionnement",
      "/offre": "Offre",
      "/deploiement": "Déploiement",
      "/demo": "Accès pilote",
      "/professionnels": "Professionnels",
      "/collectivites": "Collectivités",
      "/securite": "Sécurité",
      "/contact": "Contact",
      "/requests": "Demandes",
      "/requests/dashboard": "Pilotage",
      "/requests/operations": "Operations"
    };

    return map[path] || path;
  }

  function compactPath(paths) {
    var important = {
      "/comment_ca_marche": true,
      "/offre": true,
      "/deploiement": true,
      "/demo": true,
      "/professionnels": true,
      "/collectivites": true,
      "/securite": true,
      "/contact": true,
      "/cas_usage": true
    };

    var raw = paths || [];
    var cleaned = [];
    var seen = {};

    raw.forEach(function (p) {
      if (!important[p]) return;
      if (cleaned.length && cleaned[cleaned.length - 1] === p) return;
      if (seen[p] && cleaned.length >= 3) return;

      cleaned.push(p);
      seen[p] = true;
    });

    if (!cleaned.length) {
      cleaned = raw.filter(function (p) { return p && p !== "/"; }).slice(-4);
    }

    return cleaned.map(function (p) {
      return '<span class="hc-live-path-chip">' + esc(labelPath(p)) + '</span>';
    }).join("");
  }


  function badgeFor(session) {
    if (session.intent === "very_high") return "Priorite elevee";
    if (session.intent === "high") return "Priorite active";
    return "A surveiller";
  }

  function momentumFor(session) {
    if (session.score >= 140) return "Signal fort";
    if (session.score >= 80) return "Signal actif";
    return "Signal en observation";
  }

  async function loadHighIntentSessions() {
    var root = document.getElementById("hc-high-intent-sessions");
    if (!root) return;

    try {
      var res = await fetch("/admin/api/high-intent-sessions", { cache: "no-store" });
      var data = await res.json();
      var sessions = data.sessions || [];

      if (!sessions.length) {
        root.innerHTML = '<div class="hc-empty-state">Aucun signal public qualifie pour le moment.</div>';
        return;
      }

      root.innerHTML = sessions.map(function (s) {
        return '' +
          '<div class="hc-live-intent-row">' +
            '<div class="hc-live-intent-row__head">' +
              '<strong>' + esc(badgeFor(s)) + '</strong>' +
              '<span>Score ' + esc(s.score) + '</span>' +
            '</div>' +

            '<div class="hc-live-intent-row__type">' +
              esc(s.session_type || "Signal public") +
            '</div>' +

            '<div class="hc-live-intent-row__path">' +
              compactPath(s.path) +
            '</div>' +

            '<div class="hc-live-intent-row__meta">' +
              esc(s.events) + ' evenements publics · Dernier signal: ' + esc(s.last_seen) +
            '</div>' +

            '<div class="hc-live-intent-row__action">' +
              '<span>' + esc(momentumFor(s)) + '</span>' +
              '<strong>Action recommandee: ' + esc(s.recommendation || "Continue monitoring") + '</strong>' +
            '</div>' +
          '</div>';
      }).join("");

    } catch (e) {
      root.innerHTML = '<div class="hc-empty-state">Lecture des signaux indisponible.</div>';
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    loadHighIntentSessions();
    window.setInterval(loadHighIntentSessions, 30000);
  });
})();





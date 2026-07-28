(function () {
  var root = document.querySelector("[data-audience-map-root]");
  if (!root) {
    return;
  }

  if (typeof L === "undefined") {
    console.error("[AudienceMap] Leaflet is not loaded. Check script order / CSP.");
    return;
  }

  var mapEl = document.getElementById("audienceMap");
  if (!mapEl) {
    return;
  }
  var liveManager = window.HCMapsLive && window.HCMapsLive.create
    ? window.HCMapsLive.create(root, {
        loadingText: "Lecture d'intelligence territoriale...",
        refreshText: "Actualisation des signaux territoriaux...",
        stableText: "Intelligence live"
      })
    : null;

  var LOCATIONS = [];

  var REVENUE_RADAR = [];

  var DEPARTMENT_SCORES = [];

  var LIVE_SIGNALS = [];

  var PIPELINE_SHORTLIST = [];

  var HEAT_ZONES = [];

  var PRIORITY_META = {
    Haute: { cssClass: "audience-marker--high", popupEyebrow: "Territoire prioritaire", action: "Planifier une prise de contact cette semaine.", intensity: "High", zoom: 12, zIndexOffset: 500 },
    Moyenne: { cssClass: "audience-marker--medium", popupEyebrow: "Territoire a qualifier", action: "Qualifier les interlocuteurs avant priorisation territoriale.", intensity: "Medium", zoom: 11.4, zIndexOffset: 350 },
    Observation: { cssClass: "audience-marker--watch", popupEyebrow: "Territoire en observation", action: "Conserver en observation territoriale.", intensity: "Watch", zoom: 11, zIndexOffset: 220 },
  };
  var QUALIFIED_PAGE_WEIGHTS = {
    "/demo": 40,
    "/contact": 35,
    "/offre": 30,
    "/deploiement": 20,
    "/securite": 15,
  };

  function parseAudiencePayload() {
    var el = document.getElementById("audienceMapPayload");
    if (!el) {
      return {};
    }
    try {
      return JSON.parse(el.textContent || "{}");
    } catch (error) {
      console.error("[AudienceMap] audience payload parse failed:", error);
      return {};
    }
  }

  var audiencePayload = parseAudiencePayload();

  REVENUE_RADAR = Array.isArray(audiencePayload.revenue_radar_rows) ? audiencePayload.revenue_radar_rows : [];
  DEPARTMENT_SCORES = Array.isArray(audiencePayload.department_scores) ? audiencePayload.department_scores : [];
  LIVE_SIGNALS = Array.isArray(audiencePayload.live_signals) ? audiencePayload.live_signals : [];
  var FOUNDER_QUEUE_ACCOUNT_ROWS = Array.isArray(audiencePayload.founder_queue_account_rows)
    ? audiencePayload.founder_queue_account_rows
    : [];
  var FOUNDER_QUEUE_LEAD_ROWS = Array.isArray(audiencePayload.founder_queue_lead_rows)
    ? audiencePayload.founder_queue_lead_rows
    : [];
  var QUALIFIED_SIGNAL_ROWS = Array.isArray(audiencePayload.qualified_signal_rows)
    ? audiencePayload.qualified_signal_rows
    : [];
  LOCATIONS = Array.isArray(audiencePayload.map_locations) ? audiencePayload.map_locations : [];

  var BACKEND_MAP_LOCATIONS = Array.isArray(audiencePayload.map_locations)
    ? audiencePayload.map_locations
    : [];

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el) {
      el.textContent = String(value || "");
    }
  }

  function getPriorityMeta(priority) {
    return PRIORITY_META[priority] || PRIORITY_META.Observation;
  }

  function normalizeText(value) {
    return String(value || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function formatDepartment(point) {
    var number = point.departmentNumber || point.department_code || "";
    var name = point.departmentName || point.department || "";
    return String((number + " " + name).trim() || "Not enough data available");
  }

  function formatEuro(value) {
    try {
      return new Intl.NumberFormat("fr-FR", {
        style: "currency",
        currency: "EUR",
        maximumFractionDigits: 0,
      }).format(Number(value) || 0);
    } catch (error) {
      return "\u20ac" + String(value || 0);
    }
  }

  function euroPerMonth(value) {
    return formatEuro(value) + "/mo";
  }

  function buildPopupHtml(point) {
    var meta = getPriorityMeta(point.priority);
    var sourceLabel = String(point.intelligence_source || "").indexOf("observed") !== -1
      ? "Signal observe"
      : "Not enough data available";
    var structuresLabel = point.structures_label || point.structures || "Not enough data available";
    return [
      '<div class="hc-audience-popup">',
      '<div class="hc-audience-popup__eyebrow">' + escapeHtml(meta.popupEyebrow) + "</div>",
      '<div class="hc-audience-popup__titleWrap">',
      "<strong>" + escapeHtml(point.city) + "</strong>",
      '<span class="hc-audience-popup__dept">Departement ' + escapeHtml(formatDepartment(point)) + "</span>",
      "</div>",
      '<div class="hc-audience-popup__stats">',
      "<span><strong>" + escapeHtml(sourceLabel) + "</strong><em>" + escapeHtml(point.needs || "Not enough data available") + "</em></span>",
      "<span><strong>Structures</strong><em>" + escapeHtml(structuresLabel) + "</em></span>",
      "<span><strong>Priorite</strong><em>" + escapeHtml(point.priority) + "</em></span>",
      "</div>",
      '<div class="hc-audience-popup__recommendation"><strong>Recommandation</strong><p>' + escapeHtml(point.recommendation) + "</p></div>",
      "</div>",
    ].join("");
  }

  function buildMarkerIcon(point) {
    var meta = getPriorityMeta(point.priority);
    return L.divIcon({
      className: "audience-markerWrap",
      html:
        '<span class="audience-marker ' +
        meta.cssClass +
        '" data-priority="' +
        escapeHtml(point.priority) +
        '">' +
        '<span class="audience-marker__core"></span>' +
        '<span class="audience-marker__pulse"></span>' +
        "</span>",
      iconSize: [36, 36],
      iconAnchor: [18, 18],
      popupAnchor: [0, -14],
    });
  }

  function buildActionSuggestion(priority) {
    return getPriorityMeta(priority).action;
  }

  function findFocusSlug(territory, department) {
    var normalizedTerritory = normalizeText(territory);
    var normalizedDepartment = String(department || "").trim();
    var backendMatch = BACKEND_MAP_LOCATIONS.find(function (point) {
      return normalizeText(point.label) === normalizedTerritory ||
        (normalizedDepartment && String(point.department_code || "").trim() === normalizedDepartment);
    });
    if (backendMatch && backendMatch.slug) {
      return backendMatch.slug;
    }
    var locationMatch = LOCATIONS.find(function (point) {
      return normalizeText(point.city) === normalizedTerritory ||
        (normalizedDepartment && String(point.departmentNumber || "").trim() === normalizedDepartment);
    });
    return locationMatch ? locationMatch.slug : "paris";
  }

  function clampScore(value) {
    return Math.max(0, Math.min(100, Number(value) || 0));
  }

  function scorePriority(score) {
    if (score >= 90) {
      return "Tres chaud";
    }
    if (score >= 75) {
      return "Chaud";
    }
    if (score >= 55) {
      return "A qualifier";
    }
    return "Observation";
  }

  function scoreBadgeClass(score) {
    if (score >= 75) {
      return "is-high";
    }
    if (score >= 55) {
      return "is-warm";
    }
    return "is-watch";
  }

  function suggestedActionForScore(score) {
    if (score >= 90) {
      return "Qualifier le signal aujourd'hui";
    }
    if (score >= 75) {
      return "Preparer une prise de contact ciblee";
    }
    if (score >= 55) {
      return "Qualifier les interlocuteurs";
    }
    return "Garder en observation";
  }

  function unavailableOpportunityValue() {
    return null;
  }

  function founderLeadBadgeClass(priority) {
    if (priority === "Tres chaud" || priority === "Chaud") {
      return "is-high";
    }
    if (priority === "A qualifier") {
      return "is-warm";
    }
    return "is-watch";
  }

  function maskEmail(email) {
    var value = String(email || "").trim();
    var parts = value.split("@");
    if (parts.length !== 2) {
      return value;
    }
    var local = parts[0];
    var domain = parts[1];
    if (local.length <= 2) {
      return local.charAt(0) + "***@" + domain;
    }
    return local.slice(0, 2) + "***@" + domain;
  }

  function buildProfessionalLeadsUrl(queryValue) {
    var value = String(queryValue || "").trim();
    if (!value) {
      return "/admin/professional-leads";
    }
    return "/admin/professional-leads?q=" + encodeURIComponent(value);
  }

  function buildMailtoHref(email, subject, body) {
    var value = String(email || "").trim();
    if (!value) {
      return "";
    }
    return "mailto:" + encodeURIComponent(value) +
      "?subject=" + encodeURIComponent(subject) +
      "&body=" + encodeURIComponent(body);
  }

  function renderActionLink(label, className, href, title) {
    if (!href) {
      return '<button type="button" class="' + escapeHtml(className) + '" disabled title="' + escapeHtml(title || "Action non disponible") + '">' + escapeHtml(label) + "</button>";
    }
    return '<a class="' + escapeHtml(className) + '" href="' + escapeHtml(href) + '" title="' + escapeHtml(title || "") + '">' + escapeHtml(label) + "</a>";
  }

  function getEstimatedDemand(row) {
    var city = cityRegistry[row.focusSlug];
    return city ? city.needs : 0;
  }

  function buildFounderAction(row) {
    var estimatedDemand = getEstimatedDemand(row);
    if (row.priority === "Haute" && estimatedDemand >= 8) {
      return "Outreach now";
    }
    if (row.priority === "Moyenne") {
      return "Email cible aujourd'hui";
    }
    if (row.priority === "Observation") {
      return "Garder en observation";
    }
    return "Preparer une qualification territoriale";
  }

  function buildFounderReason(row) {
    var estimatedDemand = getEstimatedDemand(row);
    if (row.priority === "Haute" && estimatedDemand >= 8) {
      return "Signaux recurrents et estimation territoriale elevee.";
    }
    if (row.priority === "Moyenne") {
      return "Interet qualifie avec marge pour une relance structuree.";
    }
    if (row.priority === "Observation") {
      return "Momentum utile, mais encore trop leger pour accelerer.";
    }
    return "Signaux a consolider avant passage en priorite haute.";
  }

  function buildFounderBadge(row) {
    var estimatedDemand = getEstimatedDemand(row);
    if (row.priority === "Haute" && estimatedDemand >= 8) {
      return "Haute priorite";
    }
    if (row.priority === "Moyenne" || row.priority === "Haute") {
      return "Chaud";
    }
    return "Faible";
  }

  function founderBadgeClass(label) {
    if (label === "Haute priorite") {
      return "is-high";
    }
    if (label === "Chaud") {
      return "is-warm";
    }
    return "is-watch";
  }

  function founderSortWeight(row) {
    var territory = row.territory || "";
    if (territory.indexOf("Paris") === 0) {
      return 0;
    }
    if (territory.indexOf("Hauts-de-Seine") === 0) {
      return 1;
    }
    if (territory.indexOf("Seine-Saint-Denis") === 0) {
      return 2;
    }
    return 3;
  }

  function buildFounderQueue() {
    return REVENUE_RADAR.slice()
      .sort(function (a, b) {
        var orderDiff = founderSortWeight(a) - founderSortWeight(b);
        if (orderDiff !== 0) {
          return orderDiff;
        }
        return b.score - a.score;
      })
      .map(function (row) {
        var badge = buildFounderBadge(row);
        return {
          territory: row.territory,
          reason: buildFounderReason(row),
          action: buildFounderAction(row),
          badge: badge,
          badgeClass: founderBadgeClass(badge),
          focusSlug: row.focusSlug,
        };
      });
  }

  function estimatedOpportunityThisWeek() {
    return null;
  }

  function buildQualifiedQueue() {
    var grouped = {};
    QUALIFIED_SIGNAL_ROWS.forEach(function (row) {
      var territory = String(row.territory || "").trim();
      var department = String(row.department || "").trim();
      var page = String(row.page || "").trim();
      if (!territory || !page) {
        return;
      }
      var key = territory + "||" + department;
      if (!grouped[key]) {
        grouped[key] = {
          territory: territory,
          department: department,
          pages: {},
          totalCount: 0,
          repeatSignals: 0,
          uniquePagesSeen: 0,
          lastSeen: "recent",
          focusSlug: String(row.focus_slug || "").trim() || findFocusSlug(territory, department),
        };
      }
      grouped[key].pages[page] = (grouped[key].pages[page] || 0) + (Number(row.count) || 0);
      grouped[key].totalCount += Number(row.count) || 0;
      grouped[key].repeatSignals += Number(row.repeat_sessions) || 0;
      grouped[key].uniquePagesSeen = Math.max(grouped[key].uniquePagesSeen, Number(row.unique_pages_seen) || 0);
      if (row.last_seen) {
        grouped[key].lastSeen = row.last_seen;
      }
    });

    return Object.keys(grouped)
      .map(function (key) {
        var item = grouped[key];
        var pages = Object.keys(item.pages);
        var score = 0;
        pages.forEach(function (page) {
          score += Number(QUALIFIED_PAGE_WEIGHTS[page] || 0);
        });
        if (item.repeatSignals > 0 || item.totalCount >= 2) {
          score += 10;
        }
        if (Math.max(item.uniquePagesSeen, pages.length) >= 3) {
          score += 8;
        }
        if (["75", "92", "93"].indexOf(item.department) !== -1) {
          score += 5;
        }
        score = clampScore(score);
        var territoryLabel = item.territory + (item.department ? " (" + item.department + ")" : "");
        var detectedSignal = pages.slice(0, 3).join(" + ");
        if (detectedSignal) {
          detectedSignal += " vues " + item.lastSeen;
        } else {
          detectedSignal = "Pas encore assez de signaux qualifies.";
        }
        return {
          territory: territoryLabel,
          detectedSignal: detectedSignal,
          score: score,
          scoreLabel: "Signal " + score,
          priority: scorePriority(score),
          badgeClass: scoreBadgeClass(score),
          sourceLabel: "Signal qualifie",
          action: suggestedActionForScore(score),
          focusSlug: item.focusSlug || "paris",
          estimatedValue: unavailableOpportunityValue(),
        };
      })
      .sort(function (a, b) {
        return b.score - a.score;
      });
  }

  function buildQualifiedLeadQueue() {
    return FOUNDER_QUEUE_LEAD_ROWS.slice()
      .sort(function (a, b) {
        var scoreDiff = (Number(b.score) || 0) - (Number(a.score) || 0);
        if (scoreDiff !== 0) {
          return scoreDiff;
        }
        return String(a.organization || a.email || "").localeCompare(String(b.organization || b.email || ""));
      })
      .map(function (row) {
        var pagesViewed = Array.isArray(row.pages_viewed) ? row.pages_viewed : [];
        var territoryLabel = String(row.organization || row.email || "Lead qualifie");
        if (row.email && row.organization && row.organization !== row.email) {
          territoryLabel += " - " + row.email;
        }
        return {
          kind: "lead",
          organization: String(row.organization || "").trim(),
          organizationName: String(row.organization_name || "").trim(),
          organizationDomain: String(row.organization_domain || "").trim(),
          organizationType: String(row.organization_type || "").trim(),
          territoryHint: String(row.territory_hint || "").trim(),
          organizationConfidence: String(row.organization_confidence || "").trim(),
          salesNote: String(row.sales_note || "").trim(),
          email: String(row.email || "").trim(),
          actionEmail: String(row.action_email || row.email || "").trim(),
          emailMasked: maskEmail(row.email),
          pagesViewed: pagesViewed,
          territory: territoryLabel,
          detectedSignal: pagesViewed,
          score: Number(row.score) || 0,
          scoreLabel: "Signal " + (Number(row.score) || 0),
          priority: String(row.priority || "Observation"),
          badgeClass: founderLeadBadgeClass(String(row.priority || "")),
          sourceLabel: "Signal qualifie",
          action: suggestedActionForScore(Number(row.score) || 0),
          focusSlug: String(row.focus_slug || "").trim() || findFocusSlug(row.territory, row.department),
          estimatedValue: unavailableOpportunityValue(),
        };
      });
  }

  function buildAccountQueue() {
    return FOUNDER_QUEUE_ACCOUNT_ROWS.slice()
      .sort(function (a, b) {
        var scoreDiff = (Number(b.best_score) || 0) - (Number(a.best_score) || 0);
        if (scoreDiff !== 0) {
          return scoreDiff;
        }
        return String(b.last_activity || "").localeCompare(String(a.last_activity || ""));
      })
      .map(function (row) {
        var pagesViewed = Array.isArray(row.pages_viewed) ? row.pages_viewed : [];
        var emails = Array.isArray(row.emails) ? row.emails : [];
        var bestScore = Number(row.best_score) || 0;
        var avgScore = Number(row.avg_score) || 0;
        var confidence = String(row.confidence || "low").trim() || "low";
        return {
          kind: "account",
          accountName: String(row.account_name || "").trim(),
          domain: String(row.domain || "").trim(),
          organizationType: String(row.organization_type || "").trim(),
          territoryHint: String(row.territory_hint || "").trim(),
          confidence: confidence,
          leadCount: Number(row.lead_count) || 0,
          emails: emails,
          actionEmail: String(row.action_email || "").trim(),
          pagesViewed: pagesViewed,
          bestScore: bestScore,
          avgScore: avgScore,
          score: bestScore,
          scoreLabel: "Signal " + bestScore,
          priority: String(row.priority || scorePriority(bestScore)),
          badgeClass: founderLeadBadgeClass(String(row.priority || scorePriority(bestScore))),
          sourceLabel: String(row.source_label || "Signal qualifie"),
          action: String(row.recommended_action || suggestedActionForScore(bestScore)),
          lastActivity: String(row.last_activity || "recent"),
          focusSlug: String(row.focus_slug || "").trim() || findFocusSlug(row.territory_hint, ""),
          salesNote: String(row.sales_note || "").trim(),
          estimatedValue: unavailableOpportunityValue(),
        };
      });
  }

  function buildEstimatedQueue() {
    return buildFounderQueue().map(function (item) {
      var fallbackSignal = 0;
      if (item.badge === "Haute priorite") {
        fallbackSignal = 82;
      } else if (item.badge === "Chaud") {
        fallbackSignal = 68;
      } else {
        fallbackSignal = 42;
      }
      return {
        kind: "estimated",
        territory: item.territory,
        detectedSignal: item.reason,
        score: fallbackSignal,
        scoreLabel: "Signal " + fallbackSignal,
        priority: item.badge,
        badgeClass: item.badgeClass,
        sourceLabel: "Signal analytique",
        action: item.action,
        focusSlug: item.focusSlug,
        estimatedValue: 0,
      };
    });
  }

  function founderQueueRows() {
    var accountQueue = buildAccountQueue();
    if (accountQueue.length) {
      return {
        mode: "qualified",
        source: "account",
        rows: accountQueue,
      };
    }
    var leadQueue = buildQualifiedLeadQueue();
    if (leadQueue.length) {
      return {
        mode: "qualified",
        source: "lead",
        rows: leadQueue,
      };
    }
    var qualifiedQueue = buildQualifiedQueue();
    if (qualifiedQueue.length) {
      return {
        mode: "qualified",
        source: "qualified",
        rows: qualifiedQueue,
      };
    }
    return {
      mode: "estimated",
      source: "estimated",
      rows: buildEstimatedQueue(),
    };
  }

  function estimatedOpportunityMode() {
    var queue = founderQueueRows();
    if (queue.mode === "qualified") {
      return {
        title: "Opportunite qualifiee cette semaine",
        modeLabel: "Signal qualifie",
        label: "Signal qualifie + niveau d'activité",
        context: "Aucun montant n'est calcule sans opportunite CRM.",
        value: null,
      };
    }
    return {
      title: "Opportunite cette semaine",
      modeLabel: "Not enough data available",
      label: "Niveau d'activité",
      context: "Not enough data available: no CRM opportunity amount is linked to audience signals.",
      value: estimatedOpportunityThisWeek(),
    };
  }

  function renderFounderQueueEmptyState() {
    if (!founderQueueEl) {
      return;
    }
    founderQueueEl.innerHTML = [
      '<div class="hc-empty-state">',
      '<div class="hc-empty-state__title">Pas encore assez de signaux comptes.</div>',
      '<div class="hc-empty-state__text">Les recommandations s\'affineront avec les prochains signaux qualifiés.</div>',
      "</div>",
    ].join("");
  }

  function runSafeRender(label, renderFn, onError) {
    try {
      renderFn();
    } catch (error) {
      console.error("[AudienceMap] " + label + " failed:", error);
      if (typeof onError === "function") {
        onError();
      }
    }
  }

  function createControl(position, className, innerHtml) {
    var control = L.control({ position: position });
    control.onAdd = function () {
      var el = L.DomUtil.create("div", className);
      el.innerHTML = innerHtml;
      L.DomEvent.disableClickPropagation(el);
      return el;
    };
    control.addTo(map);
    return control;
  }

  function pulseSidebar() {
    var card = document.getElementById("audienceMapDetailCard");
    if (!card) {
      return;
    }
    card.classList.remove("is-refreshing");
    window.requestAnimationFrame(function () {
      card.classList.add("is-refreshing");
      window.setTimeout(function () {
        card.classList.remove("is-refreshing");
      }, 280);
    });
  }

  function recommendationLogic() {
    return [];
  }

  var defaultLat = Number(root.dataset.defaultLat || 48.8566);
  var defaultLng = Number(root.dataset.defaultLng || 2.3522);
  var defaultZoom = Number(root.dataset.defaultZoom || 10);
  var totalNeeds = LOCATIONS.reduce(function (sum, point) { return sum + (Number(point.needs) || 0); }, 0);
  var totalStructures = LOCATIONS.reduce(function (sum, point) { return sum + (Number(point.structures) || 0); }, 0);
  var highPriorityCount = LOCATIONS.filter(function (point) { return point.priority === "Haute"; }).length;
  var mediumPriorityCount = LOCATIONS.filter(function (point) { return point.priority === "Moyenne"; }).length;

  function audiencePressureClass(level) {
    if (level === "critical") return "hc-pressure-critical";
    if (level === "elevated") return "hc-pressure-elevated";
    if (level === "watch") return "hc-pressure-watch";
    return "hc-pressure-calm";
  }

  function setAudiencePressure(el, level) {
    if (!el) {
      return;
    }
    el.classList.remove("hc-pressure-calm", "hc-pressure-watch", "hc-pressure-elevated", "hc-pressure-critical");
    el.classList.add(audiencePressureClass(level));
  }

  function audienceNarrativeSummary() {
    if (highPriorityCount >= 4) {
      return {
        level: "elevated",
        title: "Attention operationnelle",
        note: highPriorityCount + " territoires prioritaires a suivre sur l'Ile-de-France."
      };
    }
    if (mediumPriorityCount >= 4) {
      return {
        level: "watch",
        title: "Charge territoriale moderee",
        note: mediumPriorityCount + " territoires a qualifier avant acceleration."
      };
    }
    return {
      level: "calm",
      title: "Lecture territoriale stable",
      note: "Lecture territoriale stable sur les signaux actuellement cartographies."
    };
  }

  var map = L.map(mapEl, {
    zoomControl: true,
    attributionControl: true,
  }).setView([defaultLat, defaultLng], defaultZoom);

  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    subdomains: "abcd",
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
  }).addTo(map);

  var heatLayer = L.layerGroup().addTo(map);
  var overlayLayer = L.layerGroup().addTo(map);
  var markerLayer = L.layerGroup().addTo(map);

  HEAT_ZONES.forEach(function (zone) {
    L.circle([zone.lat, zone.lng], {
      radius: zone.radius,
      stroke: false,
      color: zone.color,
      fillColor: zone.color,
      fillOpacity: Math.min(zone.fillOpacity, 0.045),
      opacity: zone.opacity,
    }).addTo(heatLayer);
  });

  var expansionRing = L.circle([48.8566, 2.3522], {
    radius: 32000,
    color: "#2563eb",
    weight: 2,
    opacity: 0.25,
    fillColor: "#2563eb",
    fillOpacity: 0.035,
    dashArray: "8 6",
  }).addTo(overlayLayer);

  var coreRing = L.circle([48.8566, 2.3522], {
    radius: 15000,
    color: "#2563eb",
    weight: 1.5,
    opacity: 0.22,
    fillColor: "#2563eb",
    fillOpacity: 0.04,
  }).addTo(overlayLayer);

  L.marker([48.8566, 2.3522], {
    interactive: false,
    keyboard: false,
    icon: L.divIcon({
      className: "audience-zone-labelWrap",
      html: '<span class="audience-zone-label">Zone cible IDF</span>',
      iconSize: [120, 24],
      iconAnchor: [60, 12],
    }),
  }).addTo(overlayLayer);

  var summaryNarrative = audienceNarrativeSummary();

  createControl(
    "topleft",
    "audience-map-summary",
    [
      "<div class=\"audience-map-summary__eyebrow\">Ile-de-France</div>",
      "<div class=\"audience-map-summary__title\">Territoires suivis: " + LOCATIONS.length + "</div>",
      "<div class=\"audience-map-summary__stats\">",
      "<span><strong>" + totalNeeds + "</strong><em>Signaux / estimation</em></span>",
      "<span><strong>" + totalStructures + "</strong><em>Structures probables</em></span>",
      "</div>",
      '<div class="audience-map-summary__status ' + summaryNarrative.level + '">',
      '<div class="audience-map-summary__status-label">Etat territorial</div>',
      '<div class="audience-map-summary__status-value">' + summaryNarrative.title + "</div>",
      '<div class="audience-map-summary__status-note">' + summaryNarrative.note + "</div>",
      "</div>",
    ].join("")
  );

  createControl(
    "bottomleft",
    "audience-map-legend",
    [
      '<div class="audience-map-legend__title">Analyse territoriale</div>',
      '<div class="audience-map-legend__item"><span class="audience-map-legend__dot audience-map-legend__dot--high"></span>Haute</div>',
      '<div class="audience-map-legend__item"><span class="audience-map-legend__dot audience-map-legend__dot--medium"></span>Moyenne</div>',
      '<div class="audience-map-legend__item"><span class="audience-map-legend__dot audience-map-legend__dot--watch"></span>Observation</div>',
      '<div class="audience-map-legend__section">Zones</div>',
      '<div class="audience-map-legend__item"><span class="audience-map-legend__zone audience-map-legend__zone--core"></span>coeur</div>',
      '<div class="audience-map-legend__item"><span class="audience-map-legend__zone audience-map-legend__zone--expansion"></span>extension</div>',
      "</div>",
    ].join("")
  );

  setAudiencePressure(document.querySelector(".audience-map-summary"), summaryNarrative.level);
  setAudiencePressure(document.querySelector(".audience-map-summary__status"), summaryNarrative.level);
  setAudiencePressure(document.querySelector(".audience-map-legend"), "calm");

  var markerRegistry = {};
  var cityRegistry = {};
  var activeSlug = null;
  var cityListEl = document.getElementById("audienceMapCityList");
  var radarEl = document.getElementById("audienceRevenueRadar");
  var radarEmptyEl = document.getElementById("audienceRevenueRadarEmpty");
  var deptScoresEl = document.getElementById("audienceDepartmentScores");
  var liveSignalsEl = document.getElementById("audienceLiveSignals");
  var recommendationsEl = document.getElementById("audienceRecommendations");
  var forecastEl = document.getElementById("audienceForecastPanel");
  var shortlistEl = document.getElementById("audienceProspectionShortlist");
  var founderQueueEl = document.getElementById("audienceFounderQueue");
  var estimatedOpportunityEl = document.getElementById("audienceEstimatedOpportunityValue");
  var opportunityTitleEl = document.getElementById("audienceOpportunityTitle");
  var opportunityLabelEl = document.getElementById("audienceOpportunityLabel");
  var opportunityContextEl = document.getElementById("audienceOpportunityContext");
  var founderQueueModeEl = document.getElementById("audienceFounderQueueMode");

  function setMarkerState(marker, state, enabled) {
    var icon = marker && marker.getElement();
    if (!icon) {
      return;
    }
    icon.classList.toggle(state, enabled);
  }

  function setActiveMarker(slug) {
    if (activeSlug && markerRegistry[activeSlug]) {
      markerRegistry[activeSlug].setZIndexOffset(0);
      setMarkerState(markerRegistry[activeSlug], "is-active", false);
    }
    activeSlug = slug;
    if (markerRegistry[slug]) {
      markerRegistry[slug].setZIndexOffset(getPriorityMeta(cityRegistry[slug].priority).zIndexOffset);
      setMarkerState(markerRegistry[slug], "is-active", true);
    }
  }

  function highlightButtons(container, attribute, value) {
    if (!container) {
      return;
    }
    Array.prototype.forEach.call(container.querySelectorAll("[" + attribute + "]"), function (node) {
      node.classList.toggle("is-active", node.getAttribute(attribute) === value);
    });
  }

  function syncActivePanels(slug) {
    highlightButtons(cityListEl, "data-city-slug", slug);
    highlightButtons(radarEl, "data-radar-focus", slug);
    highlightButtons(deptScoresEl, "data-dept-focus", slug);
    highlightButtons(liveSignalsEl, "data-signal-focus", slug);
    highlightButtons(recommendationsEl, "data-rec-focus", slug);
    highlightButtons(founderQueueEl, "data-founder-focus", slug);
  }

  function updateSidebar(point) {
    var detailCard = document.getElementById("audienceMapDetailCard");
    setText("audienceMapDetailCity", point.city);
    setText("audienceMapDetailDemand", point.needs || "Not enough data available");
    setText("audienceMapDetailStructures", point.structures_label || point.structures || "Not enough data available");
    setText("audienceMapDetailDepartment", formatDepartment(point));
    setText("audienceMapDetailPriority", point.priority);
    setText("audienceMapDetailIntensity", getPriorityMeta(point.priority).intensity);
    setText("audienceMapDetailRecommendation", point.recommendation);
    setText("audienceMapDetailAction", buildActionSuggestion(point.priority));
    setAudiencePressure(
      detailCard,
      point.priority === "Haute" ? "elevated" : (point.priority === "Moyenne" ? "watch" : "calm")
    );
    syncActivePanels(point.slug);
    pulseSidebar();
  }

  function focusCity(slug) {
    var marker = markerRegistry[slug];
    var point = cityRegistry[slug];
    if (!marker || !point) {
      return;
    }
    if (liveManager) liveManager.refresh("Lecture du territoire...");
    map.flyTo([point.lat, point.lng], getPriorityMeta(point.priority).zoom, {
      duration: 0.6,
    });
    marker.openPopup();
    updateSidebar(point);
    setActiveMarker(slug);
    if (liveManager) liveManager.stable("Territoire synchronise", 220);
  }

  function scoreClass(score) {
    if (score >= 70) {
      return "is-hot";
    }
    if (score >= 40) {
      return "is-warm";
    }
    return "is-cold";
  }

  function renderRevenueRadar() {
    if (!radarEl) {
      return;
    }
    radarEl.innerHTML = "";
    if (!REVENUE_RADAR.length) {
      if (radarEmptyEl) {
        radarEmptyEl.classList.remove("d-none");
      }
      return;
    }
    REVENUE_RADAR.forEach(function (row, index) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "audience-radar-row";
      button.setAttribute("data-radar-focus", row.focusSlug);
      button.innerHTML = [
        '<span class="audience-radar-row__rank">' + (index + 1) + ".</span>",
        '<span class="audience-radar-row__main">',
        '<strong>' + escapeHtml(row.territory) + "</strong>",
        '<span class="audience-radar-row__meta">Intent: ' + escapeHtml(row.priority) + " - " + escapeHtml(row.repeatLabel) + "</span>",
        '<span class="audience-radar-row__pages">Pages vues: ' + escapeHtml(row.pages.join(" ")) + "</span>",
        "</span>",
        '<span class="audience-radar-row__score">',
        '<span class="audience-inline-tag">Signal analytique</span>',
        '<span class="audience-radar-row__badge">' + escapeHtml(row.priority) + "</span>",
        '<strong>Signal: ' + escapeHtml(row.score) + "</strong>",
        '<em>Montant: Not enough data available</em>',
        "</span>",
      ].join("");
      button.addEventListener("click", function () {
        focusCity(row.focusSlug);
      });
      radarEl.appendChild(button);
    });
  }

  function renderDepartmentScores() {
    if (!deptScoresEl) {
      return;
    }
    deptScoresEl.innerHTML = "";
    DEPARTMENT_SCORES.forEach(function (dept) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "audience-opportunity-card " + scoreClass(dept.score);
      button.setAttribute("data-dept-focus", dept.focusSlug);
      button.innerHTML = [
        '<span class="audience-opportunity-card__label">' + escapeHtml(dept.number + " " + dept.name) + "</span>",
        '<strong class="audience-opportunity-card__score">' + escapeHtml(dept.score) + "</strong>",
      ].join("");
      button.addEventListener("click", function () {
        focusCity(dept.focusSlug);
      });
      deptScoresEl.appendChild(button);
    });
  }

  function renderLiveSignals() {
    if (!liveSignalsEl) {
      return;
    }
    liveSignalsEl.innerHTML = "";
    LIVE_SIGNALS.forEach(function (signal) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "audience-signal-item";
      button.setAttribute("data-signal-focus", signal.focusSlug);
      button.innerHTML =
        '<span class="audience-signal-item__dot" aria-hidden="true"></span>' +
        '<span class="audience-signal-item__content"><strong>' + escapeHtml(signal.territory) + "</strong><span>" + escapeHtml(signal.detail) + '</span><em>' + escapeHtml(signal.when) + "</em></span>";
      button.addEventListener("click", function () {
        focusCity(signal.focusSlug);
      });
      liveSignalsEl.appendChild(button);
    });
  }

  function renderRecommendations() {
    if (!recommendationsEl) {
      return;
    }
    recommendationsEl.innerHTML = "";
    var recommendations = recommendationLogic();
    if (!recommendations.length) {
      recommendationsEl.innerHTML = [
        '<div class="hc-empty-state">',
        '<div class="hc-empty-state__title">Not enough data available</div>',
        '<div class="hc-empty-state__text">No recommendation is shown until it can be traced to observed signals.</div>',
        "</div>",
      ].join("");
      return;
    }
    recommendations.forEach(function (item, index) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "audience-action-item";
      button.setAttribute("data-rec-focus", item.focusSlug);
      button.innerHTML =
        '<span class="audience-action-item__index">' + (index + 1) + "</span>" +
        '<span class="audience-action-item__content"><strong>' + escapeHtml(item.title) + "</strong><span>" + escapeHtml(item.reason) + "</span></span>";
      button.addEventListener("click", function () {
        focusCity(item.focusSlug);
      });
      recommendationsEl.appendChild(button);
    });
  }

  function renderFounderSalesQueue() {
    if (!founderQueueEl) {
      return;
    }
    var queue = founderQueueRows();
    founderQueueEl.innerHTML = "";
    if (founderQueueModeEl) {
      founderQueueModeEl.textContent = queue.mode === "qualified" ? "Signal qualifie" : "Signal analytique";
    }
    if (!queue.rows.length) {
      renderFounderQueueEmptyState();
      return;
    }
    queue.rows.forEach(function (item) {
      if (queue.source === "account" && item.kind === "account") {
        var accountCard = document.createElement("div");
        accountCard.className = "audience-founder-row";
        accountCard.setAttribute("role", "group");
        var accountLeadsUrl = buildProfessionalLeadsUrl(item.domain || item.accountName);
        var accountEmailHref = buildMailtoHref(
          item.actionEmail,
          "HelpChain - échange sur la coordination",
          "Bonjour,\n\nJe me permets de revenir vers vous au sujet de HelpChain, une solution de coordination pour structures locales.\n\nSeriez-vous disponible pour un échange de 15 minutes cette semaine ?\n\nBien cordialement,\nStela"
        );
        var accountCallHref = item.actionEmail
          ? buildMailtoHref(
              item.actionEmail,
              "Planification échange HelpChain",
              "Bonjour,\nJe vous propose de planifier un échange court autour de vos besoins de coordination.\nQuels créneaux vous conviendraient cette semaine ?\nBien cordialement,\nStela"
            )
          : accountLeadsUrl;
        var accountLines = [];
        accountLines.push('<span class="audience-founder-row__reason">Compte detecte</span>');
        if (item.domain) {
          accountLines.push('<span class="audience-founder-row__reason">Domaine: ' + escapeHtml(item.domain) + "</span>");
        }
        if (item.organizationType) {
          accountLines.push('<span class="audience-founder-row__reason">Type probable: ' + escapeHtml(item.organizationType) + "</span>");
        }
        if (item.territoryHint) {
          accountLines.push('<span class="audience-founder-row__reason">Territoire: ' + escapeHtml(item.territoryHint) + "</span>");
        }
        accountLines.push('<span class="audience-founder-row__reason">Contacts detectes: ' + escapeHtml(item.leadCount) + "</span>");
        if (item.emails.length) {
          accountLines.push('<span class="audience-founder-row__reason">Emails: ' + escapeHtml(item.emails.join(", ")) + "</span>");
        }
        accountLines.push('<span class="audience-founder-row__reason">Pages vues: ' + escapeHtml(item.pagesViewed.join(", ") || "Pages non renseignees") + "</span>");
        accountLines.push('<span class="audience-founder-row__reason">Derniere activite: ' + escapeHtml(item.lastActivity) + "</span>");
        accountLines.push('<span class="audience-founder-row__reason"><span class="audience-inline-tag">ACCOUNT</span> <span class="audience-inline-tag">' + escapeHtml(item.sourceLabel) + '</span> <span class="audience-inline-tag">Confiance ' + escapeHtml(item.confidence) + "</span></span>");
        if (item.salesNote) {
          accountLines.push('<span class="audience-founder-row__reason">Note de qualification: ' + escapeHtml(item.salesNote) + "</span>");
        }
        accountCard.innerHTML = [
          '<span class="audience-founder-row__main">',
          '<strong>' + escapeHtml(item.accountName || item.domain || "Compte probable") + "</strong>",
          '<span class="audience-founder-row__reason">Organisation probable: ' + escapeHtml(item.accountName || item.domain || "-") + "</span>",
          '<span class="audience-founder-row__reason"><span class="audience-inline-tag">' + escapeHtml(item.scoreLabel) + '</span> <span class="audience-inline-tag">Niveau moyen ' + escapeHtml(item.avgScore) + "</span></span>",
          accountLines.join(""),
          '<span class="audience-founder-row__action">Action suggeree: ' + escapeHtml(item.action) + "</span>",
          '<span class="d-flex flex-wrap gap-2 mt-2">' +
            renderActionLink("Voir activité", "btn btn-sm btn-outline-primary", accountLeadsUrl, "Ouvrir l'activité qualifiée") +
            renderActionLink("Préparer contact", "btn btn-sm btn-outline-primary", accountEmailHref, item.actionEmail ? "Préparer un contact" : "Email non disponible") +
            renderActionLink("Planifier échange", "btn btn-sm btn-primary", accountCallHref, item.actionEmail ? "Proposer un échange" : "Ouvrir l'activité qualifiée") +
          "</span>",
          "</span>",
          '<span class="audience-founder-row__badge ' + escapeHtml(item.badgeClass) + '">' + escapeHtml(item.priority) + "</span>",
        ].join("");
        founderQueueEl.appendChild(accountCard);
        return;
      }
      if (queue.source === "lead" && item.kind === "lead") {
        var card = document.createElement("div");
        card.className = "audience-founder-row";
        card.setAttribute("role", "group");
        var leadSearchTerm = item.organizationDomain || item.email || item.organizationName || item.organization;
        var leadLeadsUrl = buildProfessionalLeadsUrl(leadSearchTerm);
        var leadEmailHref = buildMailtoHref(
          item.actionEmail,
          "HelpChain - échange sur la coordination",
          "Bonjour,\n\nJe me permets de revenir vers vous au sujet de HelpChain, une solution de coordination pour structures locales.\n\nSeriez-vous disponible pour un échange de 15 minutes cette semaine ?\n\nBien cordialement,\nStela"
        );
        var leadCallHref = item.actionEmail
          ? buildMailtoHref(
              item.actionEmail,
              "Planification échange HelpChain",
              "Bonjour,\nJe vous propose de planifier un échange court autour de vos besoins de coordination.\nQuels créneaux vous conviendraient cette semaine ?\nBien cordialement,\nStela"
            )
          : leadLeadsUrl;
        var intelligenceLines = [];
        if (item.organizationName) {
          intelligenceLines.push('<span class="audience-founder-row__reason">Organisation détectée: ' + escapeHtml(item.organizationName) + "</span>");
        }
        if (item.organizationType) {
          intelligenceLines.push('<span class="audience-founder-row__reason">Type probable: ' + escapeHtml(item.organizationType) + "</span>");
        }
        if (item.territoryHint) {
          intelligenceLines.push('<span class="audience-founder-row__reason">Territoire: ' + escapeHtml(item.territoryHint) + "</span>");
        }
        if (item.organizationDomain) {
          intelligenceLines.push('<span class="audience-founder-row__reason">Domaine: ' + escapeHtml(item.organizationDomain) + "</span>");
        }
        if (item.organizationConfidence) {
          intelligenceLines.push('<span class="audience-founder-row__reason">Confiance: ' + escapeHtml(item.organizationConfidence) + "</span>");
        }
        if (item.salesNote) {
          intelligenceLines.push('<span class="audience-founder-row__reason">Note de qualification: ' + escapeHtml(item.salesNote) + "</span>");
        }
        card.innerHTML = [
          '<span class="audience-founder-row__main">',
          '<strong>' + escapeHtml(item.organizationName || item.organization || item.emailMasked || item.territory) + "</strong>",
          '<span class="audience-founder-row__reason">' + escapeHtml(item.emailMasked || item.email || "-") + "</span>",
          '<span class="audience-founder-row__reason"><span class="audience-inline-tag">' + escapeHtml(item.scoreLabel) + '</span> <span class="audience-inline-tag">' + escapeHtml(item.sourceLabel) + "</span></span>",
          '<span class="audience-founder-row__reason audience-founder-row__journey">' +
((item.pagesViewed || []).map(function(page) {
  return '<span class="audience-inline-tag audience-inline-tag--page">' + escapeHtml(page) + '</span>';
}).join("") || '<span class="audience-inline-tag audience-inline-tag--page">Pages non renseignees</span>') +
"</span>",
          intelligenceLines.join(""),
          '<span class="audience-founder-row__action">' + escapeHtml(item.action) + "</span>",
          '<span class="d-flex flex-wrap gap-2 mt-2">' +
            renderActionLink("Voir activité", "btn btn-sm btn-outline-primary", leadLeadsUrl, "Ouvrir l'activité qualifiée") +
            renderActionLink("Préparer contact", "btn btn-sm btn-outline-primary", leadEmailHref, item.actionEmail ? "Préparer un contact" : "Email non disponible") +
            renderActionLink("Planifier échange", "btn btn-sm btn-primary", leadCallHref, item.actionEmail ? "Proposer un échange" : "Ouvrir l'activité qualifiée") +
          "</span>",
          "</span>",
          '<span class="audience-founder-row__badge ' + escapeHtml(item.badgeClass) + '">' + escapeHtml(item.priority) + "</span>",
        ].join("");
        founderQueueEl.appendChild(card);
        return;
      }

      var button = document.createElement("button");
      button.type = "button";
      button.className = "audience-founder-row";
      button.setAttribute("data-founder-focus", item.focusSlug);
      button.innerHTML = [
        '<span class="audience-founder-row__main">',
        '<strong>' + escapeHtml(item.territory) + "</strong>",
        '<span class="audience-founder-row__reason"><span class="audience-inline-tag">' + escapeHtml(item.scoreLabel) + '</span> <span class="audience-inline-tag">' + escapeHtml(item.sourceLabel) + "</span></span>",
        '<span class="audience-founder-row__journey">' +
((item.detectedSignal || []).map(function(page) {
  return '<span class="audience-inline-tag audience-inline-tag--page">' + escapeHtml(page) + '</span>';
}).join("") || '<span class="audience-inline-tag audience-inline-tag--page">Aucun parcours</span>') +
"</span>",
        '<span class="audience-founder-row__action">' + escapeHtml(item.action) + "</span>",
        "</span>",
        '<span class="audience-founder-row__badge ' + escapeHtml(item.badgeClass) + '">' + escapeHtml(item.priority) + "</span>",
      ].join("");
      button.addEventListener("click", function () {
        focusCity(item.focusSlug);
      });
      founderQueueEl.appendChild(button);
    });
  }

  function renderEstimatedOpportunity() {
    if (!estimatedOpportunityEl) {
      return;
    }
    var mode = estimatedOpportunityMode();
    estimatedOpportunityEl.textContent = "Not enough data available";
    if (opportunityTitleEl) {
      opportunityTitleEl.innerHTML =
        escapeHtml(mode.title) +
        ' <span class="audience-data-tag audience-data-tag--estimated" id="audienceOpportunityMode">' +
        escapeHtml(mode.modeLabel) +
        "</span>";
    }
    if (opportunityLabelEl) {
      opportunityLabelEl.textContent = mode.label;
    }
    if (opportunityContextEl) {
      opportunityContextEl.textContent = mode.context;
    }
  }

  function renderForecast() {
    if (!forecastEl) {
      return;
    }
    var hotCount = DEPARTMENT_SCORES.filter(function (dept) { return dept.score >= 70; }).length;
    if (!hotCount) {
      forecastEl.innerHTML = [
        '<div class="hc-empty-state">',
        '<div class="hc-empty-state__title">Données insuffisantes.</div>',
        '<div class="hc-empty-state__text">Les tendances apparaîtront lorsqu’un volume suffisant de signaux territoriaux sera observé.</div>',
        '</div>',
      ].join("");
      return;
    }
    var territoriesToFollow = hotCount;
    var coordinationPoints = Math.max(1, Math.ceil(hotCount / 2));
    forecastEl.innerHTML = [
      '<div class="audience-forecast-card__grid">',
      '<span><strong>' + territoriesToFollow + '</strong><em>Territoires à suivre <span class="audience-inline-tag">Analyse territoriale</span></em></span>',
      '<span><strong>' + coordinationPoints + '</strong><em>Points de coordination <span class="audience-inline-tag">Analyse territoriale</span></em></span>',
      '<span><strong>Stable</strong><em>Tendance récente <span class="audience-inline-tag">Signal consolidé</span></em></span>',
      '<span><strong>Moyenne</strong><em>Confiance <span class="audience-inline-tag">Analyse territoriale</span></em></span>',
      "</div>",
    ].join("");
  }

  function renderShortlist() {
    if (!shortlistEl) {
      return;
    }
    shortlistEl.innerHTML = "";
    PIPELINE_SHORTLIST.forEach(function (item, index) {
      var row = document.createElement("div");
      row.className = "audience-shortlist-item";
      row.innerHTML = '<span class="audience-shortlist-item__index">' + (index + 1) + '</span><span class="audience-shortlist-item__label">' + escapeHtml(item) + "</span>";
      shortlistEl.appendChild(row);
    });
  }

  LOCATIONS.forEach(function (point) {
    cityRegistry[point.slug] = point;
    var marker = L.marker([point.lat, point.lng], {
      icon: buildMarkerIcon(point),
      title: point.city + " - " + point.needs + " signaux",
      riseOnHover: true,
      keyboard: true,
    });
    marker.bindPopup(buildPopupHtml(point), { closeButton: false, offset: [0, -4] });
    marker.on("mouseover", function () { setMarkerState(marker, "is-hover", true); });
    marker.on("mouseout", function () { setMarkerState(marker, "is-hover", false); });
    marker.on("click", function () { focusCity(point.slug); });
    marker.on("popupopen", function () {
      updateSidebar(point);
      setActiveMarker(point.slug);
    });
    marker.addTo(markerLayer);
    markerRegistry[point.slug] = marker;
  });

  if (cityListEl) {
    LOCATIONS.slice()
      .sort(function (a, b) { return (Number(b.needs) || 0) - (Number(a.needs) || 0); })
      .slice(0, 5)
      .forEach(function (point, index) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "audience-city-item";
        button.setAttribute("data-city-slug", point.slug);
        button.setAttribute("title", point.city + " - " + (point.needs || "Not enough data available") + " signaux");
        button.innerHTML =
          '<span class="audience-city-item__rank">' + (index + 1) + '.</span>' +
          '<span class="audience-city-item__label">' + escapeHtml(point.city) + '</span>' +
          '<span class="audience-city-item__meta">' + escapeHtml(point.needs || "Not enough data available") + ' signaux</span>' +
          '<span class="audience-city-item__arrow" aria-hidden="true">›</span>';
        button.addEventListener("click", function () {
          focusCity(point.slug);
        });
        cityListEl.appendChild(button);
      });
  }

  if (liveManager) liveManager.loading("Actualisation des signaux territoriaux...");
  runSafeRender("Estimated Opportunity", renderEstimatedOpportunity);
  runSafeRender("Signal Radar", renderRevenueRadar);
  runSafeRender("Department Scores", renderDepartmentScores);
  runSafeRender("Live Signals", renderLiveSignals);
  runSafeRender("Recommendations", renderRecommendations);
  runSafeRender("TerritorialPriorityQueue", renderFounderSalesQueue, renderFounderQueueEmptyState);
  runSafeRender("Forecast", renderForecast);
  runSafeRender("Shortlist", renderShortlist);

  if (LOCATIONS.length) {
    var markerGroup = L.featureGroup(
      LOCATIONS.map(function (point) { return markerRegistry[point.slug]; })
    );
    map.fitBounds(markerGroup.getBounds().pad(0.16));
    if (map.getZoom() > defaultZoom) {
      map.setZoom(defaultZoom);
    }
  }

  heatLayer.eachLayer(function (layer) { layer.bringToBack(); });
  expansionRing.bringToBack();
  coreRing.bringToBack();
  window.setTimeout(function () {
    map.invalidateSize();
    if (LOCATIONS.length) {
      focusCity(LOCATIONS[0].slug);
    }
    if (liveManager) liveManager.stable("Intelligence live", 260);
  }, 0);
})();



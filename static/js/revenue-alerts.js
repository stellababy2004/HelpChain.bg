async function loadRevenueAlerts() {
  const box = document.getElementById("revAlerts");
  if (!box) return;

  const res = await fetch("/admin/api/revenue-alerts", {
    credentials: "same-origin"
  });

  const data = await res.json();
  const alerts = data.alerts || [];

  if (!alerts.length) {
    box.innerHTML = '<div class="hc-alert-empty">No hot visitor detected.</div>';
    return;
  }

  box.innerHTML = alerts.map(a => `
    <article class="hc-hot-alert hc-hot-alert--${String(a.level).toLowerCase()}">
      <div>
        <strong>${a.level}</strong>
        <span>${a.message}</span>
        <small>Session ${a.session} · score ${a.score} · ${a.estimated_value_label || "Not enough data available"}</small>
        <small>${(a.score_components || []).map(c => `+${c.points} ${c.label}`).join(" · ") || "Explain: Not enough data available"}</small>
      </div>
      <div class="hc-hot-alert__pages">
        ${(a.pages || []).map(p => `<span>${p}</span>`).join("")}
      </div>
    </article>
  `).join("");
}

document.addEventListener("DOMContentLoaded", loadRevenueAlerts);

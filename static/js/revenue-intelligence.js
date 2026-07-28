function hcCopyText(text) {
  navigator.clipboard?.writeText(text);
}

function hcDismissVisitor(label) {
  localStorage.setItem("hc_rev_dismissed_" + label, "1");
  loadRevenue();
}

async function loadRevenue() {
  const res = await fetch("/admin/api/revenue-intelligence", {
    credentials: "same-origin"
  });
  const data = await res.json();

  document.getElementById("revTotal").textContent =
    data.total_estimated_revenue_label || "Not enough data available";

  const sessionsAll = (data.sessions || [])
    .sort((a, b) => (b.score || 0) - (a.score || 0));

  let lost = 0;
  sessionsAll.forEach(s => {
    if ((s.tier || "") === "HOT" && s.value_available) {
      lost += s.value;
    }
  });

  const lostEl = document.getElementById("revLost");
  if (lostEl) lostEl.textContent = lost > 0 ? lost + " EUR" : "Not enough data available";

  const sessions = sessionsAll.slice(0, 10);

  const rows = sessions.map((s, i) => {
    const label = "Visitor #" + (i + 1);
    const tier = String(s.tier || "COLD").toLowerCase();
    const pagesList = s.pages || [];
    const dismissed = localStorage.getItem("hc_rev_dismissed_" + label) === "1";

    const pages = pagesList
      .slice(0, 4)
      .map(p => `<span class="hc-rev-chip">${p}</span>`)
      .join("");

    const scoreExplain = (s.score_components || [])
      .map(component => `+${component.points} ${component.label}`)
      .join("; ");
    const valueLabel = s.value_available ? `${s.value} EUR` : (s.value_label || "Not enough data available");
    const note = `${label} | ${s.tier || "COLD"} | score ${s.score || 0} | value ${valueLabel} | explain: ${scoreExplain || "Not enough data available"} | pages: ${pagesList.join(", ")}`;
    const actionClass = (s.tier === "HOT" || s.tier === "WARM") ? "" : "hc-rev-actions--muted";

    return `
      <tr class="hc-rev-row hc-rev-row--${tier} ${dismissed ? "hc-rev-row--dismissed" : ""}">
        <td><strong>${label}</strong></td>
        <td><strong>${s.score || 0}</strong></td>
        <td><span class="hc-rev-tier hc-rev-tier--${tier}">${s.tier || "COLD"}</span></td>
        <td class="hc-rev-value"><strong>${valueLabel}</strong></td>
        <td><div class="hc-rev-pages">${pages || '<span class="text-muted">-</span>'}</div></td>
        <td>
          <div class="hc-rev-actions ${actionClass}">
            <button type="button" class="hc-rev-action hc-rev-action--primary" onclick='hcCopyText(${JSON.stringify(note)})'>Copy insight</button>
            <a class="hc-rev-action" href="/admin/professional-leads">Review leads</a>
            <button type="button" class="hc-rev-action" onclick='hcDismissVisitor(${JSON.stringify(label)})'>Ignore</button>
          </div>
        </td>
      </tr>
    `;
  }).join("");

  document.getElementById("revBody").innerHTML =
    rows || "<tr><td colspan=6>No data</td></tr>";
}

document.addEventListener("DOMContentLoaded", loadRevenue);

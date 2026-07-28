async function loadActions() {
  const res = await fetch("/admin/api/revenue-intelligence", {
    credentials: "same-origin"
  });
  const data = await res.json();

  const container = document.getElementById("revActions");

  const actions = (data.sessions || [])
    .filter(s => s.tier === "HOT" || s.tier === "WARM")
    .slice(0, 5)
    .map(s => {
      const pages = (s.pages || []).join(", ");
      const action = "Review explainable signal";
      const scoreExplain = (s.score_components || [])
        .map(component => `+${component.points} ${component.label}`)
        .join(" · ") || "Explain: Not enough data available";
      const valueLabel = s.value_label || "Not enough data available";

      return `
        <div style="
          border:1px solid #fde68a;
          background:#fffbeb;
          padding:12px;
          border-radius:10px;
          margin-bottom:10px;
        ">
          <strong>${action}</strong><br/>
          <small>Score ${s.score} • ${valueLabel} • ${pages}</small><br/>
          <small>${scoreExplain}</small>
        </div>
      `;
    }).join("");

  container.innerHTML = actions || "<div class='text-muted'>No actionable signals</div>";
}

document.addEventListener("DOMContentLoaded", loadActions);

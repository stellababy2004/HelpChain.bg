async function dispatchRevenueAlerts() {
  try {
    const res = await fetch("/admin/api/revenue-alert-dispatch", {
      method: "POST",
      credentials: "same-origin"
    });

    const data = await res.json();

    if (data.count > 0) {
      console.log("HelpChain revenue alerts dispatched:", data);
    }
  } catch (e) {
    console.warn("Revenue alert dispatch failed", e);
  }
}

// Automatic dispatch disabled: alerts must be triggered explicitly.


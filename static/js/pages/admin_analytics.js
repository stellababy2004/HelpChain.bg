(function () {
  function readJson(id, fallback) {
    var node = document.getElementById(id);
    if (!node) return fallback;
    try {
      return JSON.parse(node.textContent || "null") || fallback;
    } catch (error) {
      console.error("Failed to parse JSON block:", id, error);
      return fallback;
    }
  }

  var trendsData = readJson("trendsData", {});
  var categoryStats = readJson("categoryStats", {});
  var predictions = readJson("predictions", {});
  var geoData = readJson("geoData", {});
  var config = readJson("analyticsPageConfig", {});

  var trendsChart;
  var categoryChart;
  var geoChart;
  var predictionChart;
  var liveUpdateInterval;

  function hasSeriesData(series) {
    return Array.isArray(series) && series.length > 0;
  }

  function initializeCharts() {
    try {
      var trendsCanvas = document.getElementById("trendsChart");
      var trendsCtx = trendsCanvas ? trendsCanvas.getContext("2d") : null;
      if (
        trendsCtx &&
        (hasSeriesData(trendsData.requests) ||
          hasSeriesData(trendsData.completed) ||
          hasSeriesData(trendsData.volunteers))
      ) {
        trendsChart = new Chart(trendsCtx, {
          type: "line",
          data: {
            labels: trendsData.labels || [],
            datasets: [
              {
                label: "Заявки",
                data: trendsData.requests || [],
                borderColor: "#667eea",
                backgroundColor: "rgba(102, 126, 234, 0.1)",
                tension: 0.4,
                fill: true,
              },
              {
                label: "Завършени",
                data: trendsData.completed || [],
                borderColor: "#28a745",
                backgroundColor: "rgba(40, 167, 69, 0.1)",
                tension: 0.4,
                fill: true,
              },
              {
                label: "Доброволци",
                data: trendsData.volunteers || [],
                borderColor: "#ffc107",
                backgroundColor: "rgba(255, 193, 7, 0.1)",
                tension: 0.4,
                fill: true,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: "top" } },
            scales: { y: { beginAtZero: true } },
          },
        });
      }
    } catch (error) {
      console.error("Error initializing trends chart:", error);
    }

    try {
      var categoryCanvas = document.getElementById("categoryChart");
      var categoryCtx = categoryCanvas ? categoryCanvas.getContext("2d") : null;
      if (categoryCtx && hasSeriesData(categoryStats.counts)) {
        categoryChart = new Chart(categoryCtx, {
          type: "doughnut",
          data: {
            labels: categoryStats.categories || [],
            datasets: [
              {
                data: categoryStats.counts || [],
                backgroundColor: [
                  "#667eea",
                  "#28a745",
                  "#ffc107",
                  "#dc3545",
                  "#6f42c1",
                  "#20c997",
                ],
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: "bottom" } },
          },
        });
      }
    } catch (error) {
      console.error("Error initializing category chart:", error);
    }

    try {
      var geoCanvas = document.getElementById("geoChart");
      if (
        geoCanvas &&
        (hasSeriesData(geoData.requests) || hasSeriesData(geoData.volunteers))
      ) {
        geoChart = new Chart(geoCanvas.getContext("2d"), {
          type: "bar",
          data: {
            labels: ["Requests", "Volunteers"],
            datasets: [
              {
                label: "Mapped records",
                data: [
                  (geoData.requests || []).length,
                  (geoData.volunteers || []).length,
                ],
                backgroundColor: "#667eea",
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: { y: { beginAtZero: true } },
          },
        });
      }
    } catch (error) {
      console.error("Error initializing geo chart:", error);
    }

    try {
      var predictionCanvas = document.getElementById("predictionChart");
      if (
        predictionCanvas &&
        (hasSeriesData(predictions.requests_predicted) ||
          hasSeriesData(predictions.volunteers_predicted))
      ) {
        predictionChart = new Chart(predictionCanvas.getContext("2d"), {
          type: "line",
          data: {
            labels: predictions.labels || [],
            datasets: [
              {
                label: "Прогноза заявки",
                data: predictions.requests_predicted || [],
                borderColor: "#dc3545",
                backgroundColor: "rgba(220, 53, 69, 0.1)",
                borderDash: [5, 5],
                tension: 0.4,
                fill: true,
              },
              {
                label: "Прогноза доброволци",
                data: predictions.volunteers_predicted || [],
                borderColor: "#ffc107",
                backgroundColor: "rgba(255, 193, 7, 0.1)",
                borderDash: [5, 5],
                tension: 0.4,
                fill: true,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: "top" } },
            scales: { y: { beginAtZero: true } },
          },
        });
      }
    } catch (error) {
      console.error("Error initializing prediction chart:", error);
    }
  }

  function startLiveUpdates() {
    liveUpdateInterval = window.setInterval(updateLiveStats, 30000);
  }

  function updateLiveStats() {
    fetch(config.liveUrl || "/api/analytics/live")
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        var totalRequests = document.getElementById("totalRequests");
        if (totalRequests) {
          totalRequests.textContent = data.requests_today || 0;
        }
      })
      .catch(function (error) {
        console.error("Error updating live stats:", error);
      });
  }

  window.updateTimeRange = function updateTimeRange() {
    var days = document.getElementById("timeRange").value;
    window.location.href = (config.dashboardUrl || "/admin_analytics") + "?days=" + days;
  };

  window.updateFilters = function updateFilters() {
    console.log("Filters updated");
  };

  window.updateTrendChart = function updateTrendChart() {
    var months = document.getElementById("trendPeriod").value;
    var trendsCanvas = document.getElementById("trendsChart");
    if (!trendsChart || !trendsCanvas) return;

    var chartContainer = trendsCanvas.parentElement;
    chartContainer.style.position = "relative";
    chartContainer.innerHTML =
      '<div class="loading-overlay"><div class="spinner"></div></div>' +
      chartContainer.innerHTML;

    fetch((config.trendsUrl || "/api/analytics/trends") + "?months=" + months)
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        trendsChart.data.labels = data.labels;
        trendsChart.data.datasets[0].data = data.requests;
        trendsChart.data.datasets[1].data = data.completed;
        trendsChart.data.datasets[2].data = data.volunteers;
        trendsChart.update();

        var loadingOverlay = chartContainer.querySelector(".loading-overlay");
        if (loadingOverlay) loadingOverlay.remove();
      })
      .catch(function (error) {
        console.error("Error updating trend chart:", error);
        var loadingOverlay = chartContainer.querySelector(".loading-overlay");
        if (loadingOverlay) loadingOverlay.remove();
      });
  };

  window.refreshData = function refreshData() {
    document.querySelectorAll(".chart-container").forEach(function (container) {
      container.style.position = "relative";
      container.innerHTML =
        '<div class="loading-overlay"><div class="spinner"></div></div>' +
        container.innerHTML;
    });

    window.setTimeout(function () {
      window.location.reload();
    }, 1000);
  };

  window.exportData = function exportData(elOrFormat) {
    var format =
      typeof elOrFormat === "string"
        ? elOrFormat
        : (elOrFormat && elOrFormat.getAttribute("data-format")) || "json";

    var confirmMsg =
      typeof elOrFormat === "string"
        ? ""
        : (elOrFormat && elOrFormat.getAttribute("data-confirm")) || config.exportConfirm || "";

    if (confirmMsg && !window.confirm(confirmMsg)) return;

    var url = (config.exportUrl || "/api/analytics/export") + "?format=" + format + "&type=dashboard";
    window.open(url, "_blank");
  };

  document.addEventListener("DOMContentLoaded", function () {
    initializeCharts();
    startLiveUpdates();
  });

  window.addEventListener("beforeunload", function () {
    if (liveUpdateInterval) {
      window.clearInterval(liveUpdateInterval);
    }
  });

  (function bindDelegatedActions() {
    function safeCall(fnName, el) {
      try {
        var fn = window[fnName];
        if (typeof fn === "function") return fn(el);
      } catch (error) {
        console.error("Delegated action failed:", fnName, error);
      }
      return undefined;
    }

    document.addEventListener("click", function (event) {
      var el = event.target.closest("[data-action]");
      if (!el) return;
      var action = el.getAttribute("data-action");
      if (!action) return;
      if (el.tagName === "A") event.preventDefault();
      safeCall(action, el);
    });
  })();
})();

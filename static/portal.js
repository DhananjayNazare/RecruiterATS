(() => {
  "use strict";
  const badge = document.getElementById("portalAiStatus");
  if (!badge) return;
  const label = badge.querySelector("span");
  const icon = badge.querySelector("i");
  const url = badge.dataset.statusUrl;
  const render = (state, message) => {
    badge.classList.remove("checking", "connected", "disconnected", "unconfigured");
    badge.classList.add(state);
    badge.title = message || "AI interview service status";
    if (label) {
      label.textContent = state === "connected" ? "AI Connected" :
        state === "unconfigured" ? "AI Not Configured" :
        state === "checking" ? "Checking AI…" : "AI Disconnected";
    }
    if (icon) {
      icon.className = state === "connected" ? "bi bi-check-circle-fill" :
        state === "checking" ? "bi bi-arrow-repeat" : "bi bi-exclamation-circle-fill";
      icon.setAttribute("aria-hidden", "true");
    }
  };
  render("checking", "Checking whether the AI interview service is reachable");
  fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
    .then(response => {
      if (!response.ok) throw new Error("AI status could not be checked");
      return response.json();
    })
    .then(data => {
      if (data.connected) render("connected", data.message);
      else if (!data.configured) render("unconfigured", data.message);
      else render("disconnected", data.message);
    })
    .catch(() => render("disconnected", "AI connection status is unavailable. Refresh to retry."));
})();

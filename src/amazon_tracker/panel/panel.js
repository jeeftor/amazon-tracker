"use strict";
const byId = (id) => document.getElementById(id);
const label = (value) => (value || "—").replaceAll("_", " ");
let requestPending = false;
let operationRunning = false;

function showBrowser() {
  const viewer = byId("viewer");
  if (!viewer.src) {
    viewer.src = `http://127.0.0.1:${viewer.dataset.port}/vnc.html?autoconnect=true&resize=scale&reconnect=true`;
  }
  byId("browser-panel").hidden = false;
}

async function refresh() {
  try {
    const response = await fetch("/api/v1/status");
    if (!response.ok) throw new Error("Your tracker is unavailable.");
    const state = await response.json();
    byId("session").textContent = label(state.session.state);
    byId("verified").textContent = state.session.last_verified_at
      ? new Date(state.session.last_verified_at).toLocaleString() : "Never";
    byId("reason").textContent = label(state.session.reason);
    byId("browser").textContent = state.browser.running ? "Running" : "Stopped";
    byId("mode").textContent = label(state.browser.mode);
    byId("pages").textContent = state.browser.pages.join(", ") || "None";
    byId("lease").textContent = state.browser.interactive_seconds_remaining
      ? `${state.browser.interactive_seconds_remaining} seconds` : "—";
    byId("browser-error").textContent = state.browser.error ? label(state.browser.error) : "";
    operationRunning = state.operation.state === "running";
    if (!requestPending) {
      byId("message").textContent = state.operation.error ? label(state.operation.error)
        : operationRunning ? "Your browser is working…"
        : !state.browser.running ? "Your browser is stopped. Select Open Amazon / Login to retry."
        : state.operation.state === "complete" ? "Your browser action completed." : "Your tracker is ready.";
    }
    if (state.browser.mode === "interactive") showBrowser();
  } catch (error) {
    byId("message").textContent = error.message;
  } finally {
    document.querySelectorAll("button[data-action], #restart").forEach((button) => {
      button.disabled = requestPending || operationRunning;
    });
  }
}

async function act(action) {
  requestPending = true;
  const restart = action === "restart";
  try {
    const response = await fetch(`/api/v1/${restart ? "browser/restart" : `session/${action}`}`, {
      method: "POST",
      headers: {"X-Tracker-Request": "1", ...(restart ? {"X-Confirm-Restart": "yes"} : {})},
    });
    const body = await response.json();
    if (!response.ok) throw new Error(label(body.error));
    if (action === "open-login") showBrowser();
    byId("message").textContent = "Your browser is working…";
  } catch (error) {
    byId("message").textContent = error.message;
    return;
  } finally {
    requestPending = false;
  }
  await refresh();
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => act(button.dataset.action));
});
byId("show-browser").addEventListener("click", showBrowser);
byId("restart").addEventListener("click", () => {
  if (window.confirm("Restart your browser? End your interactive session first. Your saved Amazon login will be preserved.")) {
    act("restart");
  }
});
refresh();
setInterval(refresh, 2000);

"use strict";
const byId = (id) => document.getElementById(id);
const label = (value) => (value || "—").replaceAll("_", " ");
let requestPending = false;
let operationRunning = false;
let currentAction = null;

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
    for (const [output, test] of Object.entries(state.notifications.tests)) {
      const result = byId(`${output}-test-result`);
      if (result) result.textContent = test.state === "sent"
        ? `Test accepted${test.checked_at ? ` at ${new Date(test.checked_at).toLocaleTimeString()}` : ""}. Check your destination.`
        : test.error ? label(test.error) : label(test.state);
    }
    const build = state.build;
    byId("build").textContent = build
      ? `v${build.version} · ${build.sha ? build.sha.slice(0, 12) : "commit unknown"}`
        + (build.dirty === true ? " · dirty" : build.dirty === null ? " · source state unknown" : "")
      : "Version unavailable";
    byId("build").title = build?.sha || "This build has no Git stamp.";
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
    currentAction = state.operation.action;
    byId("package-count").textContent = `${state.tracker.discovered_shipments} discovered`;
    const discovery = state.tracker.last_discovery;
    if (discovery) {
      byId("discovery-detail").textContent = `Last checked ${new Date(discovery.observed_at).toLocaleString()}. `
        + `${discovery.pages_scanned} order pages checked. `
        + (discovery.complete ? "Reached the end of recent orders. " : "Scan is partial; more orders may exist. ")
        + (discovery.unsupported_links ? `${discovery.unsupported_links} links need parser support.` : "");
    }
    const packagesResponse = await fetch("/api/v1/shipments");
    if (packagesResponse.ok) {
      const packages = await packagesResponse.json();
      byId("packages").replaceChildren(...packages.map((shipment) => {
        const item = document.createElement("li");
        let description = shipment.status_checked_at
          ? "Delivery status not recognized" : "Delivery status not checked";
        if (shipment.status === "delivered") {
          const date = shipment.delivery_date_label;
          const checked = new Date(shipment.status_observed_at).toLocaleString();
          description = date === "today" || date === "yesterday"
            ? `Delivered — Amazon said “${date}” when checked ${checked}`
            : `Delivered ${date || ""}`.trim();
        }
        item.textContent = `${shipment.shipment_id} — ${description}`
          + (shipment.is_stale ? " (saved result; needs refresh)" : "");
        return item;
      }));
    }
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
      button.disabled = requestPending || (operationRunning
        && !(currentAction === "refresh" && button.dataset.action === "open-login"));
    });
  }
}

async function act(action) {
  requestPending = true;
  const restart = action === "restart";
  try {
    const path = restart ? "browser/restart" : action === "refresh" ? "refresh" : `session/${action}`;
    const response = await fetch(`/api/v1/${path}`, {
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

const notificationFields = {
  mqtt: [
    ["enabled", "Enable MQTT when announcements are available", "checkbox"],
    ["host", "Broker hostname or IP address", "text"],
    ["port", "Broker port", "number"],
    ["username", "Username (optional)", "text"],
    ["password", "Password (optional)", "password"],
    ["tls", "Use TLS with certificate verification", "checkbox"],
    ["base_topic", "Base topic", "text"],
  ],
  telegram: [
    ["enabled", "Enable Telegram when announcements are available", "checkbox"],
    ["bot_token", "Bot token from @BotFather", "password"],
    ["chat_id", "Destination chat ID", "text"],
  ],
};

function renderNotificationForm(output, settings) {
  const form = document.createElement("form");
  form.id = `${output}-form`;
  const fields = document.createElement("fieldset");
  const legend = document.createElement("legend");
  legend.textContent = output === "mqtt" ? "MQTT / Home Assistant" : "Telegram";
  fields.append(legend);
  const inputs = [];
  for (const [suffix, title, type] of notificationFields[output]) {
    const name = `${output}_${suffix}`;
    const managed = settings.managed.includes(name);
    const labelElement = document.createElement("label");
    const input = document.createElement("input");
    input.type = type;
    input.name = name;
    input.disabled = managed;
    input.autocomplete = type === "password" ? "new-password" : "off";
    input.spellcheck = false;
    if (type === "checkbox") input.checked = settings.values[name];
    else if (type !== "password") input.value = settings.values[name];
    if (type === "number") { input.min = "1"; input.max = "65535"; }
    if (type === "password") input.placeholder = settings.secrets_configured[name] ? "Configured — leave blank to keep" : "Not configured";
    labelElement.append(document.createTextNode(title), input);
    fields.append(labelElement);
    let clear = null;
    if (type === "password" && !managed) {
      clear = document.createElement("input");
      clear.type = "checkbox";
      const clearLabel = document.createElement("label");
      clearLabel.append(clear, document.createTextNode(` Clear saved ${suffix.replaceAll("_", " ")}`));
      fields.append(clearLabel);
      clear.addEventListener("change", () => { input.disabled = clear.checked; input.value = ""; });
    }
    if (managed) {
      const note = document.createElement("small");
      note.textContent = `Managed by ${name.toUpperCase()}`;
      labelElement.append(note);
    }
    inputs.push({name, type, input, managed, clear});
  }
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "Save settings";
  const test = document.createElement("button");
  test.type = "button";
  test.textContent = "Send test";
  test.disabled = !settings.ready[output];
  const actions = document.createElement("div");
  actions.className = "actions";
  actions.append(save, test);
  const result = document.createElement("p");
  result.id = `${output}-test-result`;
  result.className = "test-result";
  result.setAttribute("role", "status");
  const hint = document.createElement("p");
  hint.textContent = output === "mqtt"
    ? `Tests publish to ${settings.values.mqtt_base_topic}/test without retention. In Docker, use your broker's network address; localhost means this container.`
    : "Create your bot using @BotFather, then open a chat with your bot and select Start. Enter that chat’s numeric ID (or your channel’s @username). Your bot needs permission to post there.";
  fields.append(actions, result, hint);
  form.append(fields);
  form.addEventListener("input", () => {
    test.disabled = true;
    byId("notification-message").textContent = "Save your changes before sending a test.";
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const patch = {};
    for (const {name, type, input, managed, clear} of inputs) {
      if (managed) continue;
      if (type === "password") {
        if (clear?.checked) patch[name] = "";
        else if (input.value) patch[name] = input.value;
      } else {
        const value = type === "checkbox" ? input.checked : type === "number" ? Number(input.value) : input.value;
        if (value !== settings.values[name]) patch[name] = value;
      }
    }
    fields.disabled = true;
    try {
      const response = await fetch("/api/v1/settings/notifications", {
        method: "PATCH", headers: {"X-Tracker-Request": "1", "Content-Type": "application/json"},
        body: JSON.stringify(patch),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(label(body.error));
      renderNotificationForm(output, body);
      byId("notification-message").textContent = "Your settings are saved. Select Send test to check your destination. Automatic announcements are not active yet.";
    } catch (error) {
      byId("notification-message").textContent = error.message;
    } finally {
      inputs.filter(({type}) => type === "password").forEach(({input}) => { input.value = ""; });
      fields.disabled = false;
    }
  });
  test.addEventListener("click", async () => {
    fields.disabled = true;
    byId("notification-message").textContent = "Sending your test…";
    try {
      const response = await fetch(`/api/v1/notifications/${output}/test`, {
        method: "POST", headers: {"X-Tracker-Request": "1"},
      });
      const body = await response.json();
      if (!response.ok) throw new Error(label(body.error));
      byId("notification-message").textContent = "Your test was accepted. Check your destination; this does not enable automatic delivery announcements.";
    } catch (error) {
      byId("notification-message").textContent = error.message;
    } finally { fields.disabled = false; }
    await refresh();
  });
  const previous = byId(form.id);
  if (previous) previous.replaceWith(form);
  else byId("notification-forms").append(form);
}

async function loadNotificationSettings() {
  try {
    const response = await fetch("/api/v1/settings/notifications");
    if (!response.ok) throw new Error("Your notification settings are unavailable. Reload to retry.");
    const settings = await response.json();
    for (const output of ["mqtt", "telegram"]) renderNotificationForm(output, settings);
    byId("notification-message").textContent = "Your settings are ready.";
  } catch (error) { byId("notification-message").textContent = error.message; }
}
loadNotificationSettings();

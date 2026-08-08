"use strict";

const root = document.documentElement;
const themeOrder = ["system", "light", "dark"];
const appRoot = document.querySelector('meta[name="openlearn-root"]')?.content.replace(/\/$/, "") || "";

function appUrl(url) {
  if (!appRoot || !url?.startsWith("/") || url === appRoot || url.startsWith(`${appRoot}/`)) {
    return url;
  }
  return `${appRoot}${url}`;
}

function announce(message) {
  const region = document.querySelector("[data-live-region]");
  if (region) region.textContent = message;
}

function setTheme(theme) {
  const selected = themeOrder.includes(theme) ? theme : "system";
  root.dataset.theme = selected;
  const label = document.querySelector("[data-theme-label]");
  if (label) label.textContent = selected[0].toUpperCase() + selected.slice(1);
}

try {
  setTheme(localStorage.getItem("openlearn-theme") || "system");
} catch (_error) {
  setTheme("system");
}

document.querySelector("[data-theme-toggle]")?.addEventListener("click", () => {
  const next = themeOrder[(themeOrder.indexOf(root.dataset.theme) + 1) % themeOrder.length];
  setTheme(next);
  try { localStorage.setItem("openlearn-theme", next); } catch (_error) { /* optional */ }
  announce(`Theme changed to ${next}.`);
});

function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || "";
}

async function requestJson(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET") headers.set("X-CSRF-Token", csrfToken());
  const response = await fetch(appUrl(url), {...options, headers, credentials: "same-origin"});
  let body;
  try { body = await response.json(); } catch (_error) { body = {error: "The local server returned an unreadable response."}; }
  if (!response.ok) {
    const error = new Error(body.error || "The request could not be completed.");
    error.payload = body;
    throw error;
  }
  return body;
}

function formPayload(form) {
  const payload = {};
  for (const element of form.elements) {
    if (!element.name || element.disabled) continue;
    if (element.type === "checkbox") payload[element.name] = element.checked;
    else if (element.type === "radio") {
      if (element.checked) payload[element.name] = element.value;
    } else payload[element.name] = element.value;
  }
  return payload;
}

for (const uuidField of document.querySelectorAll("[data-uuid]")) {
  if (!uuidField.value) uuidField.value = crypto.randomUUID();
}

const providerSelect = document.querySelector("#provider");
const providerModel = document.querySelector("#model");
const providerBaseUrl = document.querySelector("#base-url");

if (providerSelect && providerModel && providerBaseUrl) {
  const selectedDefaults = () => {
    const option = providerSelect.selectedOptions[0];
    return {
      model: option?.dataset.defaultModel || "",
      baseUrl: option?.dataset.defaultBaseUrl || "",
    };
  };
  let previousProvider = providerSelect.value;
  let customValues = previousProvider === "custom"
    ? {model: providerModel.value, baseUrl: providerBaseUrl.value}
    : {model: "", baseUrl: ""};
  for (const field of [providerModel, providerBaseUrl]) {
    field.addEventListener("input", () => { field.dataset.userEdited = "true"; });
  }
  providerSelect.addEventListener("change", () => {
    const nextDefaults = selectedDefaults();
    if (previousProvider === "custom") {
      customValues = {model: providerModel.value, baseUrl: providerBaseUrl.value};
    }
    const values = providerSelect.value === "custom" ? customValues : nextDefaults;
    providerModel.value = values.model;
    providerBaseUrl.value = values.baseUrl;
    delete providerModel.dataset.userEdited;
    delete providerBaseUrl.dataset.userEdited;
    previousProvider = providerSelect.value;
  });
}

for (const choice of document.querySelectorAll("[data-template-choice]")) {
  choice.addEventListener("click", () => {
    for (const other of document.querySelectorAll("[data-template-choice]")) other.setAttribute("aria-pressed", "false");
    choice.setAttribute("aria-pressed", "true");
    const form = document.querySelector(".create-form");
    if (!form) return;
    form.elements.template_id.value = choice.dataset.templateId;
    form.elements.title.value = choice.dataset.title;
    form.elements.goal.value = choice.dataset.goal;
    form.elements.experience.focus();
    announce(`${choice.dataset.title} selected.`);
  });
}

for (const form of document.querySelectorAll("[data-json-form]")) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    const errorBox = form.querySelector("[data-form-error]");
    const status = form.querySelector("[data-form-status]");
    if (errorBox) errorBox.hidden = true;
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    if (status) status.textContent = form.dataset.endpoint === "/api/setup" ? "Testing connection…" : "Saving course and preparing the first lesson…";
    try {
      const result = await requestJson(form.dataset.endpoint, {
        method: "POST",
        body: JSON.stringify(formPayload(form)),
      });
      if (form.elements.api_key) form.elements.api_key.value = "";
      if (form.dataset.endpoint === "/api/setup" && result.ready === false) {
        if (form.elements.save_unverified) form.elements.save_unverified.checked = false;
        const message = result.message || "Saved locally. Validate the connection before teaching starts.";
        if (status) status.textContent = message;
        announce(message);
        return;
      }
      announce("Saved successfully.");
      const destination = result.initialization_url || result.focus_url || result.redirect || form.dataset.successUrl;
      if (destination) window.location.assign(appUrl(destination));
    } catch (error) {
      if (form.elements.api_key) form.elements.api_key.value = "";
      if (errorBox) {
        errorBox.textContent = error.message;
        errorBox.hidden = false;
        errorBox.focus();
      }
      if (status) status.textContent = "Nothing was lost. Correct the issue and try again.";
    } finally {
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
    }
  });
}

for (const intent of document.querySelectorAll('input[name="intent"]')) {
  intent.addEventListener("change", () => {
    const readout = document.querySelector("[data-intent-label]");
    if (readout) readout.textContent = intent.parentElement.textContent.trim();
  });
}

const turnForm = document.querySelector("[data-turn-form]");
const focusShell = document.querySelector("[data-focus-shell]");
const initializationShell = document.querySelector("[data-initialization-shell]");
let turnInFlight = false;

function setInitializationState(message, retryable = false) {
  const status = initializationShell?.querySelector("[data-initialization-status]");
  const retry = initializationShell?.querySelector("[data-initialization-retry]");
  if (status) {
    status.textContent = message;
    status.classList.toggle("error", retryable);
  }
  if (retry) retry.hidden = !retryable;
  announce(message);
}

async function pollInitialization() {
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 700));
    const result = await requestJson(initializationShell.dataset.statusUrl);
    const state = result.state || "working";
    const labels = {
      saved: "Your course is saved locally.",
      generating: "Preparing a useful first lesson…",
      validating: "Checking the lesson before showing it…",
    };
    if (state === "committed") {
      window.location.assign(initializationShell.dataset.focusUrl);
      return;
    }
    if (state === "retryable_error" || state === "conflict") {
      setInitializationState(
        result.error || "Your course is safe. Retry preparing the first lesson.",
        true,
      );
      return;
    }
    setInitializationState(labels[state] || "Preparing your first lesson…");
  }
}

if (initializationShell) {
  const initialState = initializationShell.dataset.operationState;
  if (initialState === "retryable_error" || initialState === "conflict") {
    setInitializationState(
      initializationShell.dataset.operationError
        || "Your course is safe. Retry preparing the first lesson.",
      true,
    );
  } else {
    pollInitialization().catch((error) => setInitializationState(error.message, true));
  }
  initializationShell.querySelector("[data-initialization-retry]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    setInitializationState("Retrying your saved first lesson…");
    try {
      const result = await requestJson(initializationShell.dataset.retryUrl, {
        method: "POST",
        body: "{}",
      });
      if (result.state === "committed") {
        window.location.assign(initializationShell.dataset.focusUrl);
        return;
      }
      await pollInitialization();
    } catch (error) {
      setInitializationState(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
}

function setOperationState(message, isError = false) {
  const state = document.querySelector("[data-operation-state]");
  if (!state) return;
  state.hidden = false;
  state.textContent = message;
  state.classList.toggle("error", isError);
  announce(message);
}

function lockTurnForm(locked) {
  if (!turnForm) return;
  for (const control of turnForm.elements) control.disabled = locked;
  for (const control of document.querySelectorAll("[data-navigation-intent]")) control.disabled = locked;
  turnForm.setAttribute("aria-busy", String(locked));
  turnInFlight = locked;
}

async function pollOperation(operationId) {
  const slug = focusShell.dataset.courseSlug;
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 700));
    const result = await requestJson(`/api/courses/${encodeURIComponent(slug)}/operations/${encodeURIComponent(operationId)}`);
    const state = result.state || "working";
    const labels = {
      saved: "Your response is saved locally.",
      judging: "Checking your reasoning…",
      generating: "Preparing the next useful move…",
      validating: "Checking the lesson before showing it…",
    };
    setOperationState(labels[state] || result.message || "Working…");
    if (state === "committed") {
      window.location.reload();
      return;
    }
    if (state === "conflict") {
      setOperationState("This course changed elsewhere. Refresh to continue from the newest move.", true);
      lockTurnForm(false);
      return;
    }
    if (state === "retryable_error") {
      setOperationState(result.error || "Your response is saved. Retry when the provider is available.", true);
      lockTurnForm(false);
      return;
    }
  }
}

if (focusShell?.dataset.operationId) {
  if (focusShell.dataset.operationState === "retryable_error") {
    setOperationState(focusShell.dataset.operationError || "Your response is saved. Retry when the provider is available.", true);
  } else {
    lockTurnForm(true);
    setOperationState("Resuming your saved tutor turn…");
    pollOperation(focusShell.dataset.operationId).catch((error) => {
      setOperationState(error.message, true);
      lockTurnForm(false);
    });
  }
}

async function submitTurn(overrideIntent = null) {
  if (!turnForm || !focusShell || turnInFlight) return;
  const payload = formPayload(turnForm);
  if (overrideIntent) {
    payload.intent = overrideIntent;
    payload.text = "";
  }
  lockTurnForm(true);
  setOperationState("Saving your response locally…");
  try {
    const result = await requestJson(`/api/courses/${encodeURIComponent(focusShell.dataset.courseSlug)}/turns`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (result.operation_id) await pollOperation(result.operation_id);
    else if (result.state === "committed") window.location.reload();
    else if (result.state === "retryable_error") {
      setOperationState(result.error || "Your response is saved. Retry when the provider is available.", true);
      lockTurnForm(false);
    } else {
      setOperationState(result.message || "Your response is saved.");
      lockTurnForm(false);
    }
  } catch (error) {
    setOperationState(error.message, true);
    lockTurnForm(false);
  }
}

turnForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  submitTurn();
});

turnForm?.querySelector("textarea")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    turnForm.requestSubmit();
  }
});

for (const button of document.querySelectorAll("[data-navigation-intent]")) {
  button.addEventListener("click", () => {
    const draft = turnForm?.elements.text.value.trim();
    if (draft && !window.confirm("Discard your unsent response and continue?")) return;
    submitTurn(button.dataset.navigationIntent);
  });
}

function historyItem(item) {
  const article = document.createElement("article");
  article.className = "history-item";
  const kind = document.createElement("p");
  kind.className = "eyebrow";
  kind.textContent = item.kind || "Lesson step";
  article.append(kind);
  if (item.title) {
    const title = document.createElement("h3");
    title.textContent = item.title;
    article.append(title);
  }
  for (const block of item.blocks || [{kind: "paragraph", text: item.content || ""}]) {
    if (block.kind === "code") {
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = block.text || "";
      pre.append(code);
      article.append(pre);
    } else if (block.kind === "unordered_list" || block.kind === "ordered_list") {
      const list = document.createElement(block.kind === "ordered_list" ? "ol" : "ul");
      for (const value of block.items || []) {
        const itemNode = document.createElement("li");
        itemNode.textContent = value;
        list.append(itemNode);
      }
      article.append(list);
    } else {
      const content = document.createElement("p");
      content.textContent = block.text || "";
      article.append(content);
    }
  }
  return article;
}

async function loadHistory(drawer, page = 1) {
  const target = drawer.querySelector("[data-history-content]");
  if (!target || target.dataset.loading === "true") return;
  target.dataset.loading = "true";
  try {
    const separator = drawer.dataset.historyUrl.includes("?") ? "&" : "?";
    const history = await requestJson(`${drawer.dataset.historyUrl}${separator}page=${page}`);
    if (page === 1) target.replaceChildren();
    target.querySelector("[data-history-more]")?.remove();
    if (page === 1 && !history.items?.length) {
      const empty = document.createElement("p");
      empty.className = "quiet-copy";
      empty.textContent = "No earlier moves in this session yet.";
      target.append(empty);
    } else {
      for (const item of history.items) target.append(historyItem(item));
    }
    if (history.has_more) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "history-more";
      more.dataset.historyMore = "true";
      more.textContent = "Load earlier moves";
      more.addEventListener("click", () => loadHistory(drawer, Number(history.page) + 1));
      target.append(more);
    } else {
      target.dataset.loaded = "true";
    }
  } catch (error) {
    if (page === 1) target.textContent = error.message;
    else announce(error.message);
  } finally {
    delete target.dataset.loading;
  }
}

let activeDrawerOpener = null;
let drawerOpenVersion = 0;

function closeDrawers({restoreFocus = true} = {}) {
  const opener = activeDrawerOpener;
  drawerOpenVersion += 1;
  for (const drawer of document.querySelectorAll(".drawer")) drawer.hidden = true;
  for (const button of document.querySelectorAll("[data-drawer-toggle]")) button.setAttribute("aria-expanded", "false");
  activeDrawerOpener = null;
  if (restoreFocus && opener?.isConnected) opener.focus();
}

for (const button of document.querySelectorAll("[data-drawer-toggle]")) {
  button.addEventListener("click", async () => {
    const drawer = document.getElementById(button.dataset.drawerToggle);
    const opening = drawer.hidden;
    closeDrawers({restoreFocus: false});
    drawer.hidden = !opening;
    button.setAttribute("aria-expanded", String(opening));
    if (opening) {
      activeDrawerOpener = button;
      const openVersion = drawerOpenVersion;
      if (drawer.dataset.historyUrl && !drawer.querySelector("[data-history-content]")?.dataset.loaded) {
        await loadHistory(drawer);
      }
      if (!drawer.hidden && activeDrawerOpener === button && openVersion === drawerOpenVersion) {
        drawer.querySelector("[data-drawer-close]")?.focus();
      }
    }
  });
}

for (const close of document.querySelectorAll("[data-drawer-close]")) close.addEventListener("click", closeDrawers);
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDrawers(); });

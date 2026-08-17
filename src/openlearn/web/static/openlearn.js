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
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
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

function initializeUuidFields(scope = document) {
  for (const uuidField of scope.querySelectorAll("[data-uuid]")) {
    if (!uuidField.value) uuidField.value = crypto.randomUUID();
  }
}
initializeUuidFields();

const providerSelect = document.querySelector("#provider");
const providerModel = document.querySelector("#model");
const providerBaseUrl = document.querySelector("#base-url");
const providerExplanation = document.querySelector("[data-provider-explanation]");
const providerKeyLabel = document.querySelector("[data-api-key-label]");
const providerKey = document.querySelector("#api-key");

if (providerKey) {
  const supportsMaskedText = CSS.supports("-webkit-text-security", "disc");
  if (!supportsMaskedText) providerKey.type = "password";
  document.querySelector("[data-secret-toggle]")?.addEventListener("click", (event) => {
    const revealed = providerKey.dataset.revealed !== "true";
    providerKey.dataset.revealed = String(revealed);
    if (!supportsMaskedText) providerKey.type = revealed ? "text" : "password";
    event.currentTarget.setAttribute("aria-pressed", String(revealed));
    event.currentTarget.textContent = revealed ? "Hide" : "Show";
    providerKey.focus();
  });
}

if (providerSelect && providerModel && providerBaseUrl) {
  const updateProviderPresentation = () => {
    const option = providerSelect.selectedOptions[0];
    if (providerExplanation) providerExplanation.textContent = option?.dataset.explanation || "";
    if (providerKeyLabel) {
      const saved = providerKeyLabel.dataset.keyConfigured === "true";
      providerKeyLabel.textContent = option?.dataset.keyRequired === "false"
        ? "API key (not needed for this provider)"
        : `API key${saved ? " (already saved)" : ""}`;
    }
  };
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
    updateProviderPresentation();
  });
  updateProviderPresentation();
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

for (const strip of document.querySelectorAll("[data-starter-strip]")) {
  const track = strip.querySelector("[data-starter-track]");
  for (const button of strip.querySelectorAll("[data-starter-scroll]")) {
    button.addEventListener("click", () => {
      const direction = button.dataset.starterScroll === "previous" ? -1 : 1;
      track?.scrollBy({left: direction * Math.max(260, track.clientWidth * 0.82), behavior: "smooth"});
    });
  }
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
        return;
      }
      announce("Saved successfully.");
      const destination = result.setup_url || result.placement_url || result.initialization_url || result.focus_url || result.redirect || form.dataset.successUrl;
      if (destination) window.location.assign(appUrl(destination));
    } catch (error) {
      if (form.elements.api_key && !error.payload?.retain_secret) form.elements.api_key.value = "";
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

for (const form of document.querySelectorAll("[data-enter-flow]")) {
  const fields = [...form.querySelectorAll('input:not([type="hidden"]), textarea')]
    .filter((field) => !field.disabled);
  form.addEventListener("keydown", (event) => {
    if (
      event.key !== "Enter"
      || event.shiftKey
      || event.altKey
      || event.ctrlKey
      || event.metaKey
      || event.isComposing
    ) return;
    const index = fields.indexOf(event.target);
    if (index < 0) return;
    event.preventDefault();
    if (index < fields.length - 1) fields[index + 1].focus();
    else form.requestSubmit();
  });
}

let dashboardPreviewRequest = 0;
const DASHBOARD_RENDERED = "rendered";
const DASHBOARD_STALE = "stale";
const DASHBOARD_UNAVAILABLE = "unavailable";

function dashboardUrlForCourse(slug, proposal = null) {
  const url = new URL(window.location.href);
  url.hash = "";
  url.searchParams.set("course", slug);
  if (proposal) url.searchParams.set("proposal", proposal);
  else url.searchParams.delete("proposal");
  return url;
}

async function renderDashboard(url, {history = "push", focusSlug = null} = {}) {
  const shell = document.querySelector("[data-selected-course]");
  if (!shell) return DASHBOARD_UNAVAILABLE;
  const requestId = ++dashboardPreviewRequest;
  shell.dataset.previewLoading = "true";
  shell.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(url, {
      headers: {Accept: "text/html"},
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error("The course preview could not be loaded.");
    const documentNext = new DOMParser().parseFromString(await response.text(), "text/html");
    const shellNext = documentNext.querySelector("[data-selected-course]");
    if (requestId !== dashboardPreviewRequest) return DASHBOARD_STALE;
    if (!shellNext) return DASHBOARD_UNAVAILABLE;
    shell.replaceWith(shellNext);
    initializeUuidFields(shellNext);
    document.title = documentNext.title;
    if (history === "push") window.history.pushState({openlearnDashboard: true}, "", url);
    else if (history === "replace") window.history.replaceState({openlearnDashboard: true}, "", url);
    const selected = shellNext.querySelector("[data-course-preview-link][aria-current='true']");
    if (focusSlug) shellNext.querySelector(`[data-course-slug="${CSS.escape(focusSlug)}"]`)?.focus();
    announce(`${selected?.dataset.courseTitle || "Course"} preview updated. Continue learning when you are ready to switch.`);
    return DASHBOARD_RENDERED;
  } finally {
    if (requestId === dashboardPreviewRequest) {
      const current = document.querySelector("[data-selected-course]");
      delete current?.dataset.previewLoading;
      current?.removeAttribute("aria-busy");
    }
  }
}

document.addEventListener("click", async (event) => {
  const link = event.target.closest("[data-course-preview-link]");
  if (
    !link
    || event.defaultPrevented
    || event.button !== 0
    || event.metaKey
    || event.ctrlKey
    || event.shiftKey
    || event.altKey
  ) return;
  event.preventDefault();
  link.setAttribute("aria-busy", "true");
  try {
    const result = await renderDashboard(link.href, {focusSlug: link.dataset.courseSlug});
    if (result === DASHBOARD_UNAVAILABLE) {
      window.location.assign(link.href);
    }
  } catch (_error) {
    window.location.assign(link.href);
  }
});

window.addEventListener("popstate", async () => {
  if (!document.querySelector("[data-selected-course]")) return;
  try {
    await renderDashboard(window.location.href, {history: "none"});
  } catch (_error) {
    window.location.reload();
  }
});

function followUpStatus(form) {
  const panel = form.closest("[data-follow-up-panel]");
  let status = panel?.querySelector("[data-follow-up-status]");
  if (!status && panel) {
    status = document.createElement("p");
    status.className = "follow-up-status";
    status.dataset.followUpStatus = "";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    panel.append(status);
  }
  return {panel, status};
}

async function requestFollowUp(form) {
  const {panel, status} = followUpStatus(form);
  const submit = form.querySelector('[type="submit"]');
  const action = form.elements.action.value;
  const submissionId = form.elements.submission_id.value;
  const slug = document.querySelector("[data-selected-course]")?.dataset.selectedCourse;
  const previewGeneration = dashboardPreviewRequest;
  if (!submit || !slug || form.dataset.submitting === "true") return;
  form.dataset.submitting = "true";
  submit.disabled = true;
  submit.setAttribute("aria-busy", "true");
  panel?.setAttribute("aria-busy", "true");
  if (status) {
    status.hidden = false;
    status.dataset.state = "pending";
    status.textContent = action === "confirm"
      ? "Creating your course…"
      : action === "retry"
        ? "Trying the proposal again…"
        : "Building a focused proposal…";
  }
  announce(status?.textContent || "Working on your follow-up course.");
  try {
    const result = await requestJson(form.dataset.endpoint, {
      method: "POST",
      body: JSON.stringify(formPayload(form)),
    });
    const selectedSlug = result.course_slug || slug;
    const proposal = action === "confirm" ? null : submissionId;
    if (status) status.textContent = action === "confirm" ? "Course created." : "Proposal ready.";
    const currentSlug = document.querySelector("[data-selected-course]")?.dataset.selectedCourse;
    if (previewGeneration === dashboardPreviewRequest && currentSlug === slug) {
      await renderDashboard(dashboardUrlForCourse(selectedSlug, proposal), {history: "push"});
      announce(action === "confirm" ? "Follow-up course created and selected." : "Your focused course proposal is ready to review.");
    } else {
      announce(action === "confirm" ? "Follow-up course created. Your current course preview was kept." : "Your focused course proposal is ready. Your current course preview was kept.");
    }
  } catch (error) {
    if (error.payload?.state === "setup_required") {
      const setupUrl = new URL(appUrl("/setup"), window.location.origin);
      const returnUrl = dashboardUrlForCourse(slug);
      setupUrl.searchParams.set("next", `${returnUrl.pathname}${returnUrl.search}`);
      window.location.assign(setupUrl);
      return;
    }
    if (status) {
      status.hidden = false;
      status.dataset.state = "error";
      status.setAttribute("role", "alert");
      status.textContent = error.message;
      status.focus();
    }
    announce(error.message);
  } finally {
    delete form.dataset.submitting;
    submit.disabled = false;
    submit.removeAttribute("aria-busy");
    panel?.removeAttribute("aria-busy");
  }
}

document.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-follow-up-form]");
  if (!form) return;
  event.preventDefault();
  void requestFollowUp(form);
});

const turnForm = document.querySelector("[data-turn-form]");
const chatForm = document.querySelector("[data-chat-form]");
const focusShell = document.querySelector("[data-focus-shell]");
const initializationShell = document.querySelector("[data-initialization-shell]");
const toolSurface = document.querySelector("[data-tool-surface]");
let turnInFlight = false;
let progressionInFlight = false;
let chatInFlight = false;
let chatRefreshGeneration = 0;
let latestAppliedCourseRevision = Number(focusShell?.dataset.revision || 0);
let latestAppliedChatRevision = Number(focusShell?.dataset.chatRevision || 0);

let activeToolOpener = null;
let preparedVideo = null;
let videoRequestGeneration = 0;
let codeRevision = null;
let codeDirty = false;
let codeEditVersion = 0;
let toolOpenVersion = 0;
let surfaceMotionVersion = 0;
const focusLayoutAnimations = new Map();

const availableTools = new Set(["chat", "code", "video", "sources"]);

function chatDraftStorageKey() {
  return focusShell?.dataset.courseSlug
    ? `openlearn:${focusShell.dataset.courseSlug}:chat-draft`
    : "";
}

function storeChatDraft() {
  const textarea = chatForm?.elements.text;
  const key = chatDraftStorageKey();
  if (!textarea || !key) return;
  try {
    if (textarea.value) {
      window.sessionStorage.setItem(key, JSON.stringify({
        text: textarea.value,
        source_lesson_id: chatForm.elements.source_lesson_id?.value || "",
        source_lesson_title: chatForm.elements.source_lesson_title?.value || "",
        source_lesson_revision: chatForm.elements.source_lesson_revision?.value || "",
      }));
    }
    else window.sessionStorage.removeItem(key);
  } catch (_error) { /* storage is an optional draft safeguard */ }
}

function restoreChatDraft() {
  const textarea = chatForm?.elements.text;
  const key = chatDraftStorageKey();
  if (!textarea || !key || textarea.value) return;
  try {
    const stored = window.sessionStorage.getItem(key);
    if (!stored) return;
    let draft;
    try { draft = JSON.parse(stored); }
    catch (_error) { draft = {text: stored}; }
    if (!draft || typeof draft.text !== "string") return;
    textarea.value = draft.text;
    for (const name of [
      "source_lesson_id",
      "source_lesson_title",
      "source_lesson_revision",
    ]) {
      if (chatForm.elements[name] && typeof draft[name] === "string") {
        chatForm.elements[name].value = draft[name];
      }
    }
  }
  catch (_error) { /* storage is an optional draft safeguard */ }
}

restoreChatDraft();

function reducedMotionRequested() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

function compactFocusLayout() {
  return window.matchMedia?.("(max-width: 860px)").matches ?? false;
}

function cssTimeMilliseconds(value) {
  const time = value.trim();
  const amount = Number.parseFloat(time);
  if (!Number.isFinite(amount)) return 0;
  return time.endsWith("ms") ? amount : amount * 1000;
}

function transitionFocusLayout(updateLayout) {
  const elements = [
    focusShell?.querySelector(".tool-rail"),
    focusShell?.querySelector(".focus-column"),
  ].filter(Boolean);
  if (reducedMotionRequested() || compactFocusLayout()) {
    for (const element of elements) {
      focusLayoutAnimations.get(element)?.cancel();
      focusLayoutAnimations.delete(element);
    }
    updateLayout();
    return;
  }
  const before = new Map(
    elements.map((element) => [element, element.getBoundingClientRect()])
  );
  for (const element of elements) {
    focusLayoutAnimations.get(element)?.cancel();
    focusLayoutAnimations.delete(element);
  }
  updateLayout();
  const duration = cssTimeMilliseconds(
    getComputedStyle(focusShell).getPropertyValue("--surface-motion-duration")
  ) || 720;
  for (const element of elements) {
    if (typeof element.animate !== "function") continue;
    const after = element.getBoundingClientRect();
    const offsetX = before.get(element).left - after.left;
    if (Math.abs(offsetX) < 0.5) continue;
    const animation = element.animate(
      [{transform: `translateX(${offsetX}px)`}, {transform: "translateX(0)"}],
      {duration, easing: "cubic-bezier(0.4, 0, 0.2, 1)"}
    );
    focusLayoutAnimations.set(element, animation);
    animation.addEventListener(
      "finish",
      () => {
        if (focusLayoutAnimations.get(element) === animation) {
          focusLayoutAnimations.delete(element);
        }
      },
      {once: true}
    );
  }
}

function finishSurfaceMotion(surface, token, callback) {
  let finished = false;
  let fallbackTimer = null;
  const finish = (event) => {
    if (event?.target && event.target !== surface) return;
    if (finished) return;
    finished = true;
    surface.removeEventListener("animationend", finish);
    if (fallbackTimer !== null) window.clearTimeout(fallbackTimer);
    if (surface.dataset.motionToken === token) callback();
  };
  if (reducedMotionRequested()) {
    finish();
    return;
  }
  surface.addEventListener("animationend", finish);
  const style = getComputedStyle(surface);
  const durations = style.animationDuration.split(",").map(cssTimeMilliseconds);
  const delays = style.animationDelay.split(",").map(cssTimeMilliseconds);
  const fallbackDelay = durations.reduce(
    (longest, duration, index) => Math.max(longest, duration + (delays[index] || 0)),
    0
  );
  fallbackTimer = window.setTimeout(finish, fallbackDelay + 100);
}

function revealSurface(surface, beforeMotion = () => {}) {
  const token = String(++surfaceMotionVersion);
  surface.dataset.motionToken = token;
  surface.hidden = false;
  surface.removeAttribute("inert");
  surface.removeAttribute("aria-hidden");
  beforeMotion();
  surface.dataset.motion = "enter";
  finishSurfaceMotion(surface, token, () => {
    if (surface.dataset.motion === "enter") {
      delete surface.dataset.motion;
      delete surface.dataset.motionToken;
    }
  });
}

function hideSurface(surface, onHidden = () => {}) {
  if (surface.hidden) {
    onHidden();
    return;
  }
  if (surface.dataset.motion === "exit") return;
  const token = String(++surfaceMotionVersion);
  surface.dataset.motionToken = token;
  surface.setAttribute("inert", "");
  surface.setAttribute("aria-hidden", "true");
  surface.dataset.motion = "exit";
  finishSurfaceMotion(surface, token, () => {
    surface.hidden = true;
    delete surface.dataset.motion;
    delete surface.dataset.motionToken;
    onHidden();
  });
}

function toolStatus(message, isError = false) {
  const status = toolSurface?.querySelector("[data-tool-status]");
  if (!status) return;
  status.textContent = message;
  status.classList.toggle("error", isError);
  status.setAttribute("aria-live", isError ? "assertive" : "polite");
  if (isError) status.focus();
}

function toolEndpoint(suffix) {
  return `/api/courses/${encodeURIComponent(focusShell.dataset.courseSlug)}/tools/${suffix}`;
}

function toolFromUrl() {
  const tool = new URL(window.location.href).searchParams.get("tool");
  return availableTools.has(tool) ? tool : null;
}

function setToolUrl(tool, {replace = false} = {}) {
  const url = new URL(window.location.href);
  if (tool) url.searchParams.set("tool", tool);
  else url.searchParams.delete("tool");
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next === current) return;
  window.history[replace ? "replaceState" : "pushState"]({}, "", next);
}

function confirmDiscardCodeChanges() {
  if (!codeDirty) return true;
  return window.confirm("Discard unsaved changes to this Python draft?");
}

function clearPreparedVideo() {
  preparedVideo = null;
  const consent = toolSurface?.querySelector("[data-video-consent]");
  if (consent) consent.hidden = true;
  toolSurface?.querySelector("[data-video-frame]")?.replaceChildren();
}

function invalidatePreparedVideo() {
  videoRequestGeneration += 1;
  clearPreparedVideo();
}

function renderCodeResult(result) {
  const region = toolSurface?.querySelector("[data-code-result]");
  if (!region) return;
  region.hidden = false;
  region.querySelector("[data-code-result-kind]").textContent = result.kind || result.status || "saved";
  const output = [result.stdout, result.stderr].filter((value) => value !== undefined && value !== null && value !== "").join("\n");
  region.querySelector("[data-code-output]").textContent = output.length ? output : result.message || "No output.";
}

function renderSources(result) {
  const region = toolSurface?.querySelector("[data-source-results]");
  if (!region) return;
  const sources = result.sources || result.items || [];
  region.replaceChildren();
  if (!sources.length && !result.imported?.length && !result.skipped?.length && !result.failed?.length) {
    const empty = document.createElement("p");
    empty.className = "quiet-copy";
    empty.textContent = result.message || "No imported sources yet.";
    region.append(empty);
  } else for (const source of sources) {
    const item = document.createElement("article");
    item.className = "source-result";
    const title = document.createElement("strong");
    title.textContent = source.label || source.name || source.path || "Imported source";
    const detail = document.createElement("p");
    detail.className = "field-note";
    detail.textContent = source.detail || source.status || source.kind || "available locally";
    item.append(title, detail);
    region.append(item);
  }
  for (const group of ["imported", "skipped", "failed"]) {
    for (const detail of result[group] || []) {
      const item = document.createElement("p");
      item.className = `source-result${group === "failed" ? " error" : ""}`;
      item.textContent = `${group}: ${detail.label}${detail.message ? ` - ${detail.message}` : ""}`;
      region.append(item);
    }
  }
}

async function loadToolState(tool) {
  if (tool === "chat") {
    await refreshChat();
  } else if (tool === "code") {
    const result = await requestJson(toolEndpoint("code"));
    const draft = toolSurface.querySelector("[data-code-draft]");
    codeRevision = result.revision || null;
    if (!codeDirty) {
      draft.value = result.source || result.draft || "";
      codeDirty = false;
    }
    if (result.result) renderCodeResult(result.result);
    else toolSurface.querySelector("[data-code-result]").hidden = true;
  } else if (tool === "sources") {
    renderSources(await requestJson(toolEndpoint("sources")));
  }
}

async function openTool(tool, opener, {updateUrl = true} = {}) {
  if (!toolSurface || !focusShell || !availableTools.has(tool)) return false;
  const currentTool = focusShell.dataset.toolActive;
  if (
    currentTool === tool
    && !toolSurface.hidden
    && toolSurface.getAttribute("aria-hidden") !== "true"
  ) {
    if (updateUrl) setToolUrl(tool);
    toolSurface.querySelector("[data-tool-close]")?.focus();
    return true;
  }
  if (currentTool === "code" && codeDirty && !confirmDiscardCodeChanges()) return false;
  if (currentTool === "code" && codeDirty) codeDirty = false;
  const openVersion = ++toolOpenVersion;
  activeToolOpener = opener;
  for (const button of document.querySelectorAll("[data-tool-open]")) {
    button.setAttribute("aria-expanded", String(button === opener));
  }
  for (const panel of toolSurface.querySelectorAll("[data-tool-panel]")) {
    panel.hidden = panel.dataset.toolPanel !== tool;
  }
  const titles = {chat: "Tutor chat", code: "Code workbench", video: "Video player", sources: "Course sources"};
  toolSurface.querySelector("[data-tool-title]").textContent = titles[tool] || "Learning tool";
  if (toolSurface.hidden || toolSurface.getAttribute("aria-hidden") === "true") {
    revealSurface(toolSurface, () => {
      transitionFocusLayout(() => { focusShell.dataset.toolActive = tool; });
    });
  } else {
    transitionFocusLayout(() => { focusShell.dataset.toolActive = tool; });
  }
  if (updateUrl) setToolUrl(tool);
  toolStatus(
    tool === "chat"
      ? "Your lesson stays open while you ask."
      : tool === "video"
        ? "Video stays private until you load it."
        : "Loading local tool state…"
  );
  try {
    await loadToolState(tool);
    if (openVersion === toolOpenVersion && tool !== "video") toolStatus("Ready.");
  } catch (error) {
    if (openVersion === toolOpenVersion) toolStatus(error.message, true);
  }
  if (openVersion === toolOpenVersion && !toolSurface.hidden) {
    toolSurface.querySelector("[data-tool-close]")?.focus();
  }
  return true;
}

function closeTool({updateUrl = true} = {}) {
  if (!toolSurface || !focusShell) return false;
  const currentTool = focusShell.dataset.toolActive;
  if (currentTool === "code" && codeDirty && !confirmDiscardCodeChanges()) return false;
  if (currentTool === "code" && codeDirty) codeDirty = false;
  toolOpenVersion += 1;
  for (const button of document.querySelectorAll("[data-tool-open]")) button.setAttribute("aria-expanded", "false");
  if (updateUrl) setToolUrl(null, {replace: true});
  const opener = activeToolOpener;
  activeToolOpener = null;
  opener?.focus();
  toolSurface.querySelector("[data-video-frame]")?.replaceChildren();
  if (compactFocusLayout()) {
    hideSurface(toolSurface, () => {
      if (focusShell.dataset.toolActive === currentTool) {
        delete focusShell.dataset.toolActive;
      }
    });
  } else if (focusShell.dataset.toolActive === currentTool) {
    transitionFocusLayout(() => { delete focusShell.dataset.toolActive; });
    hideSurface(toolSurface);
  } else {
    hideSurface(toolSurface);
  }
  return true;
}

for (const button of document.querySelectorAll("[data-tool-open]")) {
  button.addEventListener("click", () => openTool(button.dataset.toolOpen, button));
}
toolSurface?.querySelector("[data-tool-close]")?.addEventListener("click", () => closeTool());

toolSurface?.querySelector("[data-code-draft]")?.addEventListener("input", () => {
  if (!codeDirty) toolStatus("Unsaved Python draft.");
  codeDirty = true;
  codeEditVersion += 1;
});

window.addEventListener("beforeunload", (event) => {
  if (!codeDirty) return;
  event.preventDefault();
  event.returnValue = "";
});

toolSurface?.querySelector("[data-code-save]")?.addEventListener("click", async () => {
  const draft = toolSurface.querySelector("[data-code-draft]");
  const source = draft.value;
  const editVersion = codeEditVersion;
  try {
    const result = await requestJson(toolEndpoint("code"), {
      method: "POST",
      body: JSON.stringify({
        action: "save",
        source,
        expected_revision: codeRevision,
      }),
    });
    codeRevision = result.revision || codeRevision;
    codeDirty = editVersion !== codeEditVersion;
    toolSurface.querySelector("[data-code-result]").hidden = true;
    toolStatus(
      codeDirty
        ? "Saved the submitted draft. Newer edits remain unsaved."
        : result.message || "Draft saved locally.",
    );
  } catch (error) { toolStatus(error.message, true); }
});

toolSurface?.querySelector("[data-code-run]")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const draft = toolSurface.querySelector("[data-code-draft]");
  const source = draft.value;
  const editVersion = codeEditVersion;
  button.disabled = true;
  toolStatus("Running in the bounded local workspace…");
  try {
    const result = await requestJson(toolEndpoint("code"), {
      method: "POST",
      body: JSON.stringify({
        action: "run",
        source,
        expected_revision: codeRevision,
      }),
    });
    codeRevision = result.revision || codeRevision;
    codeDirty = editVersion !== codeEditVersion;
    renderCodeResult(result.result || result);
    toolStatus(
      codeDirty
        ? "Run complete for the submitted draft. Newer edits remain unsaved."
        : result.message || "Run complete.",
    );
  } catch (error) { toolStatus(error.message, true); }
  finally { button.disabled = false; }
});

toolSurface?.querySelector("[data-code-reset]")?.addEventListener("click", async () => {
  if (!window.confirm("Reset this saved draft?")) return;
  const editVersion = codeEditVersion;
  try {
    const result = await requestJson(toolEndpoint("code"), {
      method: "POST",
      body: JSON.stringify({action: "reset", source: "", expected_revision: codeRevision}),
    });
    codeRevision = result.revision || null;
    codeDirty = editVersion !== codeEditVersion;
    if (!codeDirty) {
      toolSurface.querySelector("[data-code-draft]").value = result.source || result.draft || "";
      toolSurface.querySelector("[data-code-result]").hidden = true;
    }
    toolStatus(
      codeDirty
        ? "Saved draft reset. Newer edits remain unsaved."
        : "Draft reset.",
    );
  } catch (error) { toolStatus(error.message, true); }
});

toolSurface?.querySelector("[data-video-form]")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  invalidatePreparedVideo();
  const requestGeneration = videoRequestGeneration;
  try {
    const descriptor = await requestJson(toolEndpoint("video"), {
      method: "POST",
      body: JSON.stringify({url: form.elements.url.value}),
    });
    if (requestGeneration !== videoRequestGeneration) return;
    preparedVideo = descriptor;
    const consent = toolSurface.querySelector("[data-video-consent]");
    consent.hidden = false;
    consent.querySelector("[data-video-title]").textContent = preparedVideo.label || "Video ready to load.";
    toolSurface.querySelector("[data-video-frame]").replaceChildren();
    toolStatus("Validated locally. YouTube has not been contacted.");
  } catch (error) {
    if (requestGeneration === videoRequestGeneration) toolStatus(error.message, true);
  }
});

toolSurface?.querySelector("#video-url")?.addEventListener("input", invalidatePreparedVideo);

toolSurface?.querySelector("[data-video-load]")?.addEventListener("click", () => {
  if (!preparedVideo?.embed_url) return;
  const frame = document.createElement("iframe");
  frame.src = preparedVideo.embed_url;
  frame.title = preparedVideo.label || "YouTube lesson video";
  frame.loading = "lazy";
  frame.referrerPolicy = "no-referrer";
  frame.allow = "accelerometer; encrypted-media; picture-in-picture";
  frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-presentation");
  frame.setAttribute("allowfullscreen", "");
  toolSurface.querySelector("[data-video-frame]").replaceChildren(frame);
  toolStatus("Video loaded from YouTube's privacy-enhanced player.");
});

if (focusShell) {
  const requestedTool = toolFromUrl();
  const hasToolParameter = new URL(window.location.href).searchParams.has("tool");
  if (requestedTool) {
    const opener = document.querySelector(`[data-tool-open="${requestedTool}"]`);
    openTool(requestedTool, opener, {updateUrl: false});
  } else if (hasToolParameter) {
    setToolUrl(null, {replace: true});
  }

  window.addEventListener("popstate", async () => {
    const nextTool = toolFromUrl();
    const currentTool = focusShell.dataset.toolActive || null;
    if (nextTool) {
      const opener = document.querySelector(`[data-tool-open="${nextTool}"]`);
      const opened = await openTool(nextTool, opener, {updateUrl: false});
      if (!opened && currentTool) setToolUrl(currentTool, {replace: true});
    } else if (!toolSurface.hidden) {
      const closed = closeTool({updateUrl: false});
      if (!closed && currentTool) setToolUrl(currentTool, {replace: true});
    }
  });
}

async function submitSourceForm(form, suffix, body) {
  const button = form.querySelector("button[type='submit']");
  button.disabled = true;
  toolStatus("Importing selected source…");
  try {
    const result = await requestJson(toolEndpoint(`sources/${suffix}`), {method: "POST", body});
    renderSources(result);
    toolStatus(result.message || "Source import complete.");
    form.reset();
  } catch (error) { toolStatus(error.message, true); }
  finally { button.disabled = false; }
}

toolSurface?.querySelector("[data-source-file-form]")?.addEventListener("submit", (event) => {
  event.preventDefault();
  submitSourceForm(event.currentTarget, "file", new FormData(event.currentTarget));
});
toolSurface?.querySelector("[data-source-folder-form]")?.addEventListener("submit", (event) => {
  event.preventDefault();
  submitSourceForm(event.currentTarget, "folder", JSON.stringify({path: event.currentTarget.elements.path.value}));
});
toolSurface?.querySelector("[data-source-github-form]")?.addEventListener("submit", (event) => {
  event.preventDefault();
  submitSourceForm(event.currentTarget, "github", JSON.stringify({url: event.currentTarget.elements.url.value}));
});

function setInitializationState(message, retryable = false) {
  const status = initializationShell?.querySelector("[data-initialization-status]");
  const retry = initializationShell?.querySelector("[data-initialization-retry]");
  if (status) {
    status.textContent = message;
    status.classList.toggle("error", retryable);
    status.setAttribute("aria-live", retryable ? "assertive" : "polite");
  }
  if (retry) retry.hidden = !retryable;
  if (retryable) status?.focus();
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

function setOperationState(message, isError = false, result = null) {
  const state = document.querySelector("[data-operation-state]");
  if (!state) return;
  state.hidden = false;
  const messageNode = state.querySelector("[data-operation-message]");
  if (messageNode) messageNode.textContent = message;
  else state.textContent = message;
  state.classList.toggle("error", isError);
  state.setAttribute("aria-live", isError ? "assertive" : "polite");
  const recovery = state.querySelector("[data-provider-recovery]");
  if (recovery) {
    recovery.hidden = !isError || result?.show_provider_recovery !== true;
  }
  if (isError) state.focus();
}

let tutorPreviewTarget = "";
let tutorPreviewVisible = "";
let tutorPreviewFrame = null;
let tutorPreviewLastAt = 0;
let tutorPreviewCommitted = false;
let tutorPreviewCommitRate = 2200;
let tutorPreviewDrainResolve = null;
let tutorPreviewTextNode = null;
const tutorPreviewHeightCache = new Map();

function tutorPreviewNodes() {
  const surface = document.querySelector("[data-current-move]");
  const region = surface?.querySelector("[data-tutor-stream-preview]");
  const text = region?.querySelector("[data-tutor-stream-text]");
  return { surface, region, text };
}

function previewTextNode(text) {
  if (!text) return null;
  if (!tutorPreviewTextNode || tutorPreviewTextNode.parentNode !== text) {
    text.replaceChildren();
    tutorPreviewTextNode = document.createTextNode("");
    text.append(tutorPreviewTextNode);
  }
  return tutorPreviewTextNode;
}

function setPreviewSlotHeight(region, height) {
  if (!region || !Number.isFinite(height)) return;
  region.dataset.streamOpen = "true";
  region.style.height = `${Math.ceil(height)}px`;
}

function openTutorPreviewSlot(region) {
  if (!region || !region.hidden) return;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  region.hidden = false;
  region.setAttribute("aria-busy", "true");
  if (reduceMotion) {
    setPreviewSlotHeight(region, 176);
    return;
  }
  region.style.height = "0px";
  window.requestAnimationFrame(() => setPreviewSlotHeight(region, 176));
}

function measureTutorPreviewHeight(region, finalPreview) {
  const width = Math.ceil(region.getBoundingClientRect().width);
  const cacheKey = `${width}:${finalPreview}`;
  const cached = tutorPreviewHeightCache.get(cacheKey);
  if (cached !== undefined) return cached;
  const clone = region.cloneNode(true);
  const cloneText = clone.querySelector("[data-tutor-stream-text]");
  clone.hidden = false;
  clone.removeAttribute("data-stream-open");
  clone.setAttribute("aria-hidden", "true");
  clone.inert = true;
  if (cloneText) cloneText.textContent = finalPreview || "Lesson ready.";
  Object.assign(clone.style, {
    animation: "none",
    height: "auto",
    left: "-10000px",
    maxHeight: "none",
    overflow: "visible",
    pointerEvents: "none",
    position: "fixed",
    top: "0",
    transition: "none",
    visibility: "hidden",
    width: `${width}px`,
  });
  document.body.append(clone);
  const measured = Math.ceil(clone.getBoundingClientRect().height);
  clone.remove();
  const height = Math.max(144, Math.min(measured, Math.max(240, window.innerHeight * 0.45)));
  tutorPreviewHeightCache.set(cacheKey, height);
  if (tutorPreviewHeightCache.size > 8) {
    tutorPreviewHeightCache.delete(tutorPreviewHeightCache.keys().next().value);
  }
  return height;
}

function paintTutorPreview(now) {
  tutorPreviewFrame = null;
  const { region, text } = tutorPreviewNodes();
  if (!region || !text) return;
  const node = previewTextNode(text);
  if (!node) return;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduceMotion && tutorPreviewLastAt && now - tutorPreviewLastAt < 32) {
    tutorPreviewFrame = window.requestAnimationFrame(paintTutorPreview);
    return;
  }
  const previousLength = tutorPreviewVisible.length;
  if (reduceMotion) {
    tutorPreviewVisible = tutorPreviewTarget;
  } else if (tutorPreviewVisible.length < tutorPreviewTarget.length) {
    const elapsed = tutorPreviewLastAt ? Math.min(now - tutorPreviewLastAt, 80) : 16;
    const rate = tutorPreviewCommitted ? tutorPreviewCommitRate : 240;
    const count = Math.max(8, Math.floor((elapsed * rate) / 1000));
    tutorPreviewVisible = tutorPreviewTarget.slice(
      0,
      Math.min(tutorPreviewVisible.length + count, tutorPreviewTarget.length),
    );
  }
  tutorPreviewLastAt = now;
  if (tutorPreviewVisible) {
    if (node.data === "Thinking through your answer…") node.data = "";
    const suffix = tutorPreviewVisible.slice(previousLength);
    if (suffix) node.appendData(suffix);
  } else if (!node.data) {
    node.data = "Thinking through your answer…";
  }
  text.classList.toggle("stream-placeholder", !tutorPreviewVisible);
  if (tutorPreviewVisible.length < tutorPreviewTarget.length) {
    tutorPreviewFrame = window.requestAnimationFrame(paintTutorPreview);
  } else if (tutorPreviewDrainResolve) {
    const resolve = tutorPreviewDrainResolve;
    tutorPreviewDrainResolve = null;
    resolve();
  }
}

function scheduleTutorPreview() {
  if (tutorPreviewFrame === null) {
    tutorPreviewFrame = window.requestAnimationFrame(paintTutorPreview);
  }
}

function renderTutorPreview(preview) {
  const { surface, region, text } = tutorPreviewNodes();
  if (!surface || !region || !text) return;
  const visible = (preview || "").trimStart();
  if (!surface.dataset.streaming) {
    openTutorPreviewSlot(region);
    surface.dataset.streaming = "true";
    const node = previewTextNode(text);
    if (node) node.data = "Thinking through your answer…";
    text.classList.add("stream-placeholder");
  }
  if (visible === tutorPreviewTarget) return;
  if (!visible.startsWith(tutorPreviewVisible)) {
    let commonLength = 0;
    while (
      commonLength < visible.length
      && commonLength < tutorPreviewVisible.length
      && visible[commonLength] === tutorPreviewVisible[commonLength]
    ) commonLength += 1;
    tutorPreviewVisible = tutorPreviewVisible.slice(0, commonLength);
    const node = previewTextNode(text);
    if (node) node.data = tutorPreviewVisible;
  }
  tutorPreviewTarget = visible;
  scheduleTutorPreview();
}

async function finishTutorPreview(finalPreview) {
  renderTutorPreview(finalPreview);
  tutorPreviewCommitted = true;
  tutorPreviewCommitRate = Math.max(
    2200,
    Math.ceil((tutorPreviewTarget.length - tutorPreviewVisible.length) / 1.8),
  );
  scheduleTutorPreview();
  if (tutorPreviewVisible.length < tutorPreviewTarget.length) {
    await Promise.race([
      new Promise((resolve) => { tutorPreviewDrainResolve = resolve; }),
      new Promise((resolve) => window.setTimeout(resolve, 2200)),
    ]);
  }
  if (tutorPreviewVisible !== tutorPreviewTarget) {
    tutorPreviewVisible = tutorPreviewTarget;
    const { region, text } = tutorPreviewNodes();
    const node = previewTextNode(text);
    if (node) node.data = tutorPreviewVisible;
    if (region) region.setAttribute("aria-busy", "false");
  }
  const { region } = tutorPreviewNodes();
  if (region) {
    region.setAttribute("aria-busy", "false");
    setPreviewSlotHeight(region, measureTutorPreviewHeight(region, tutorPreviewTarget));
  }
}

function restoreTutorSurfaceAfterError() {
  const { surface, region } = tutorPreviewNodes();
  if (!surface || !region) return;
  if (tutorPreviewFrame !== null) window.cancelAnimationFrame(tutorPreviewFrame);
  tutorPreviewFrame = null;
  tutorPreviewCommitted = false;
  tutorPreviewCommitRate = 2200;
  tutorPreviewTarget = "";
  tutorPreviewVisible = "";
  tutorPreviewTextNode = null;
  region.hidden = true;
  region.style.height = "";
  region.removeAttribute("data-stream-open");
  region.removeAttribute("aria-busy");
  surface.querySelector("[data-move-content]")?.removeAttribute("hidden");
  surface.querySelector("[data-move-prompt]")?.removeAttribute("hidden");
  delete surface.dataset.streaming;
}

function lockTurnForm(locked) {
  if (turnForm) {
    for (const control of turnForm.elements) control.disabled = locked;
    turnForm.setAttribute("aria-busy", String(locked));
  }
  for (const control of document.querySelectorAll(
    "[data-navigation-intent], [data-progression-action]",
  )) control.disabled = locked;
  turnInFlight = locked;
}

function lockProgressionControls(locked) {
  for (const control of document.querySelectorAll(
    "[data-progression-action], [data-navigation-intent]",
  )) control.disabled = locked;
  progressionInFlight = locked;
}

async function waitForOperation(operationId, setStatus, previewSink = null) {
  const slug = focusShell.dataset.courseSlug;
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 250));
    const result = await requestJson(`/api/courses/${encodeURIComponent(slug)}/operations/${encodeURIComponent(operationId)}`);
    const state = result.state || "working";
    const labels = {
      saved: "Your response is saved locally.",
      judging: "Checking your reasoning…",
      generating: "Preparing the next useful move…",
      validating: "Checking the lesson before showing it…",
    };
    if (previewSink && (state === "generating" || result.preview_text)) {
      previewSink(result.preview_text || "");
    }
    setStatus(labels[state] || result.message || "Working…", false, result);
    if (["committed", "conflict", "retryable_error"].includes(state)) return result;
  }
}

function syncNextLessonHandoff() {
  const button = document.querySelector("[data-show-next-lesson]");
  if (!button) return;
  button.disabled = chatInFlight;
  button.setAttribute("aria-disabled", String(chatInFlight));
}

function showNextLessonHandoff() {
  const state = document.querySelector("[data-operation-state]");
  if (!state) return;
  setOperationState(
    chatInFlight
      ? "Lesson ready. Finish your tutor question, then show the next lesson."
      : "Lesson ready. Show it when you are ready.",
  );
  let actions = state.querySelector("[data-operation-actions]");
  if (!actions) {
    actions = document.createElement("div");
    actions.className = "operation-actions";
    actions.dataset.operationActions = "true";
    state.append(actions);
  }
  for (const staleAction of actions.querySelectorAll("[data-progression-action]")) {
    staleAction.remove();
  }
  let button = actions.querySelector("[data-show-next-lesson]");
  if (!button) {
    button = document.createElement("button");
    button.type = "button";
    button.className = "primary-action compact";
    button.dataset.showNextLesson = "true";
    button.textContent = "Show next lesson";
    button.addEventListener("click", () => {
      if (chatInFlight) {
        setOperationState("Finish your tutor question before opening the next lesson.");
        return;
      }
      storeChatDraft();
      window.location.reload();
    });
    actions.append(button);
  }
  syncNextLessonHandoff();
}

async function pollOperation(operationId) {
  const result = await waitForOperation(
    operationId,
    setOperationState,
    (preview) => renderTutorPreview(preview),
  );
  if (result.state === "committed") {
    clearTurnComposer();
    await finishTutorPreview(result.preview_text || "");
    showNextLessonHandoff();
    return;
  }
  restoreTutorSurfaceAfterError();
  if (result.state === "conflict") {
    setOperationState("This course changed elsewhere. Refresh to continue from the newest move.", true);
  } else {
    setOperationState(result.error || "Your response is saved. Retry when the provider is available.", true, result);
  }
  lockTurnForm(false);
}

function clearTurnComposer() {
  if (!turnForm) return;
  const textarea = turnForm.elements.text;
  textarea.value = "";
  textarea.defaultValue = "";
}

if (focusShell?.dataset.operationState) {
  if (["provider-error", "busy", "stale-conflict", "caught-up"].includes(
    focusShell.dataset.operationState,
  )) {
    setOperationState(
      focusShell.dataset.operationMessage
        || focusShell.dataset.operationError
        || "Your saved course position needs attention.",
      ["provider-error", "stale-conflict"].includes(focusShell.dataset.operationState),
      { show_provider_recovery: focusShell.dataset.operationProviderRecovery === "true" },
    );
  } else {
    lockTurnForm(true);
    setOperationState("Resuming your saved tutor turn…");
    pollOperation(focusShell.dataset.operationId).catch((error) => {
      setOperationState(error.message, true);
      lockTurnForm(false);
    });
  }
}

for (const button of document.querySelectorAll("[data-progression-action]")) {
  button.addEventListener("click", async () => {
    if (progressionInFlight || turnInFlight) return;
    const action = button.dataset.progressionAction;
    if (action === "refresh") {
      window.location.reload();
      return;
    }
    const operationId = focusShell?.dataset.operationId;
    if (!operationId) return;
    lockProgressionControls(true);
    setOperationState(
      action === "cancel" ? "Cancelling the saved target…" : "Resuming the saved target…",
    );
    try {
      const result = await requestJson(
        `/api/courses/${encodeURIComponent(focusShell.dataset.courseSlug)}/progression`,
        {
          method: "POST",
          body: JSON.stringify({ action, operation_id: operationId }),
        },
      );
      if (result.state === "busy") {
        setOperationState(result.error || "Another interface is still finishing this target.");
        lockProgressionControls(false);
        return;
      }
      if (["provider-error", "stale-conflict"].includes(result.state)) {
        setOperationState(result.error || "The saved target could not be resumed.", true, result);
        lockProgressionControls(false);
        return;
      }
      window.location.reload();
    } catch (error) {
      setOperationState(error.message, true);
      lockProgressionControls(false);
    }
  });
}

async function submitTurn(overrideIntent = null) {
  if (!focusShell || turnInFlight || progressionInFlight) return;
  const payload = turnForm
    ? formPayload(turnForm)
    : {
        intent: "next",
        text: "",
        submission_id: crypto.randomUUID(),
        expected_revision: Number(focusShell.dataset.revision || 0),
      };
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
    else if (result.state === "committed") {
      clearTurnComposer();
      showNextLessonHandoff();
    }
    else if (result.state === "retryable_error") {
      setOperationState(result.error || "Your response is saved. Retry when the provider is available.", true, result);
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
  if (!turnForm.reportValidity()) return;
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

document.addEventListener("keydown", (event) => {
  if (
    event.key !== "Enter"
    || event.shiftKey
    || event.altKey
    || event.metaKey
    || event.ctrlKey
    || event.isComposing
    || turnForm
    || focusShell?.dataset.toolActive
    || document.querySelector(".drawer:not([hidden])")
    || event.target.closest?.("button, a, input, textarea, select")
  ) return;
  event.preventDefault();
  submitTurn("next");
});

function appendPresentationBlocks(container, blocks) {
  for (const block of blocks || []) {
    if (block.kind === "code") {
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = block.text || "";
      pre.append(code);
      container.append(pre);
    } else if (block.kind === "unordered_list" || block.kind === "ordered_list") {
      const list = document.createElement(block.kind === "ordered_list" ? "ol" : "ul");
      for (const value of block.items || []) {
        const itemNode = document.createElement("li");
        itemNode.textContent = value;
        list.append(itemNode);
      }
      container.append(list);
    } else {
      const content = document.createElement("p");
      content.textContent = block.text || "";
      container.append(content);
    }
  }
}

function chatExchange(exchange) {
  const article = document.createElement("article");
  article.className = "chat-exchange";
  article.dataset.sourceLessonId = exchange.source_lesson_id || "";
  const source = document.createElement("p");
  source.className = "chat-source-label quiet-copy";
  source.textContent = `About: ${exchange.source_lesson_title || "Saved lesson"}`;
  const learner = document.createElement("div");
  learner.className = "chat-learner";
  const learnerLabel = document.createElement("span");
  learnerLabel.textContent = "You";
  const question = document.createElement("p");
  question.textContent = exchange.question || "";
  learner.append(learnerLabel, question);
  const tutor = document.createElement("div");
  tutor.className = "chat-tutor";
  const tutorLabel = document.createElement("span");
  tutorLabel.textContent = "Tutor";
  tutor.append(tutorLabel);
  appendPresentationBlocks(tutor, exchange.blocks || []);
  article.append(source, learner, tutor);
  return article;
}

function renderChatConversation(conversation) {
  const region = toolSurface?.querySelector("[data-chat-conversation]");
  if (!region) return;
  region.replaceChildren();
  if (!conversation?.length) {
    const empty = document.createElement("p");
    empty.className = "quiet-copy";
    empty.dataset.chatEmpty = "true";
    empty.textContent = "No questions in this lesson yet.";
    region.append(empty);
    return;
  }
  for (const exchange of conversation) region.append(chatExchange(exchange));
  region.scrollTop = region.scrollHeight;
}

function setCourseRevision(revision) {
  if (
    !focusShell
    || !Number.isInteger(revision)
    || revision < latestAppliedCourseRevision
  ) return false;
  latestAppliedCourseRevision = revision;
  focusShell.dataset.revision = String(revision);
  for (const field of document.querySelectorAll('input[name="expected_revision"]')) {
    field.value = String(revision);
  }
  return true;
}

async function refreshChat() {
  if (!focusShell) return;
  const generation = ++chatRefreshGeneration;
  const result = await requestJson(`/api/courses/${encodeURIComponent(focusShell.dataset.courseSlug)}/chat`);
  const courseRevision = Number(result.course_revision ?? result.revision);
  const chatRevision = Number(result.chat_revision ?? result.revision);
  setCourseRevision(courseRevision);
  if (
    generation !== chatRefreshGeneration
    || !Number.isInteger(chatRevision)
    || chatRevision < latestAppliedChatRevision
  ) return false;
  renderChatConversation(result.conversation || []);
  latestAppliedChatRevision = chatRevision;
  focusShell.dataset.chatRevision = String(chatRevision);
  return true;
}

function setChatStatus(message, isError = false) {
  const status = chatForm?.querySelector("[data-chat-status]");
  if (!status) return;
  status.textContent = message;
  status.classList.toggle("error", isError);
}

function lockChatForm(locked) {
  if (!chatForm) return;
  for (const control of chatForm.elements) control.disabled = locked;
  chatForm.setAttribute("aria-busy", String(locked));
  chatInFlight = locked;
  syncNextLessonHandoff();
  if (!locked && document.querySelector("[data-show-next-lesson]")) {
    showNextLessonHandoff();
  }
}

chatForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!chatForm.reportValidity()) return;
  const payload = formPayload(chatForm);
  storeChatDraft();
  lockChatForm(true);
  setChatStatus("Saving your question locally…");
  try {
    const result = await requestJson(`/api/courses/${encodeURIComponent(focusShell.dataset.courseSlug)}/turns`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const completed = result.operation_id
      ? await waitForOperation(result.operation_id, (message) => setChatStatus(message))
      : result;
    if (completed.state === "committed") {
      chatForm.elements.text.value = "";
      storeChatDraft();
      chatForm.elements.submission_id.value = crypto.randomUUID();
      await refreshChat();
      setChatStatus("Answered. Your lesson is still open.");
    } else if (completed.state === "conflict") {
      setChatStatus("This course changed elsewhere. Refresh before asking again.", true);
    } else {
      setChatStatus(completed.error || "Your question is saved. Retry when the provider is available.", true);
    }
  } catch (error) {
    setChatStatus(error.message, true);
  } finally {
    lockChatForm(false);
  }
});

chatForm?.querySelector("textarea")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

chatForm?.querySelector("textarea")?.addEventListener("input", storeChatDraft);

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
  appendPresentationBlocks(
    article,
    item.blocks || [{kind: "paragraph", text: item.content || ""}],
  );
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
  for (const drawer of document.querySelectorAll(".drawer")) hideSurface(drawer);
  for (const button of document.querySelectorAll("[data-drawer-toggle]")) button.setAttribute("aria-expanded", "false");
  activeDrawerOpener = null;
  if (restoreFocus && opener?.isConnected) opener.focus();
}

for (const button of document.querySelectorAll("[data-drawer-toggle]")) {
  button.addEventListener("click", async () => {
    const drawer = document.getElementById(button.dataset.drawerToggle);
    const opening = drawer.hidden || drawer.getAttribute("aria-hidden") === "true";
    closeDrawers({restoreFocus: false});
    button.setAttribute("aria-expanded", String(opening));
    if (opening) {
      revealSurface(drawer);
      activeDrawerOpener = button;
      const openVersion = drawerOpenVersion;
      if (drawer.dataset.historyUrl && !drawer.querySelector("[data-history-content]")?.dataset.loaded) {
        await loadHistory(drawer);
      }
      if (
        !drawer.hidden
        && drawer.getAttribute("aria-hidden") !== "true"
        && activeDrawerOpener === button
        && openVersion === drawerOpenVersion
      ) {
        drawer.querySelector("[data-drawer-close]")?.focus();
      }
    }
  });
}

for (const close of document.querySelectorAll("[data-drawer-close]")) close.addEventListener("click", closeDrawers);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (
    toolSurface
    && !toolSurface.hidden
    && toolSurface.getAttribute("aria-hidden") !== "true"
  ) closeTool();
  else closeDrawers();
});

const placementShell = document.querySelector("[data-placement-shell]");
const placementStatus = placementShell?.querySelector("[data-placement-status]");

function lockPlacement(locked) {
  for (const control of placementShell?.querySelectorAll("button") || []) {
    control.disabled = locked;
  }
  placementShell?.setAttribute("aria-busy", String(locked));
}

function placementSubmissionStorageKey(action) {
  return placementShell
    ? `openlearn-placement:${placementShell.dataset.courseSlug}:${action}`
    : "";
}

function clearStablePlacementSubmission(action) {
  placementSubmissionIds.delete(action);
  const key = placementSubmissionStorageKey(action);
  if (key) window.sessionStorage.removeItem(key);
}

function finishPlacementAction(result, action) {
  if (action) clearStablePlacementSubmission(action);
  const destination = result.initialization_url || result.setup_url;
  if (destination) window.location.assign(appUrl(destination));
  else window.location.reload();
}

async function runPlacementAction(action, values = {}) {
  if (!placementShell) return;
  lockPlacement(true);
  if (placementStatus) placementStatus.textContent = "Saving locally…";
  try {
    const result = await requestJson(`/api/courses/${encodeURIComponent(placementShell.dataset.courseSlug)}/placement`, {
      method: "POST",
      body: JSON.stringify({action, ...values}),
    });
    finishPlacementAction(result, action);
  } catch (error) {
    if (error.payload?.setup_url) {
      window.location.assign(appUrl(error.payload.setup_url));
      return;
    }
    if (placementStatus) {
      placementStatus.textContent = error.message;
      placementStatus.setAttribute("aria-live", "assertive");
      placementStatus.focus();
    }
    lockPlacement(false);
  }
}

placementShell?.querySelector("[data-placement-draft-form]")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const stage = event.currentTarget.dataset.placementStage;
  lockPlacement(true);
  if (placementStatus) placementStatus.textContent = "Saving your answer locally…";
  try {
    const saved = await requestJson(`/api/courses/${encodeURIComponent(placementShell.dataset.courseSlug)}/placement`, {
      method: "POST",
      body: JSON.stringify({
        action: "save_draft",
        stage,
        text: placementShell.querySelector("textarea")?.value || "",
        expected_updated_at: placementShell.dataset.updatedAt || null,
      }),
    });
    placementShell.dataset.updatedAt = saved.updated_at || placementShell.dataset.updatedAt;
    if (placementStatus) placementStatus.textContent = "Answer saved. Preparing the next step…";
    const result = await requestJson(`/api/courses/${encodeURIComponent(placementShell.dataset.courseSlug)}/placement`, {
      method: "POST",
      body: JSON.stringify({action: "submit", stage, submission_id: crypto.randomUUID()}),
    });
    finishPlacementAction(result);
  } catch (error) {
    if (placementStatus) {
      placementStatus.textContent = error.message;
      placementStatus.setAttribute("aria-live", "assertive");
      placementStatus.focus();
    }
    lockPlacement(false);
  }
});

const confidenceForm = placementShell?.querySelector("[data-confidence-form]");

if (confidenceForm) {
  const context = confidenceForm.querySelector("[data-confidence-context]");
  const quiz = confidenceForm.querySelector("[data-confidence-quiz]");
  const review = confidenceForm.querySelector("[data-confidence-review]");
  const complete = confidenceForm.querySelector("[data-confidence-complete]");
  const position = confidenceForm.querySelector("[data-confidence-position]");
  const previous = confidenceForm.querySelector("[data-confidence-previous]");
  const questions = [...confidenceForm.querySelectorAll("[data-confidence-question]")];
  const reviewTopics = [...confidenceForm.querySelectorAll("[data-review-topic]")];
  let activeQuestions = [];
  let currentQuestion = 0;
  let advancingQuestion = false;

  const selectedFocus = () => confidenceForm.querySelector('input[name="interview_focus"]:checked')?.value || "coding";
  const trackIsActive = (track) => selectedFocus() === "balanced" || selectedFocus() === track;

  const prepareReview = () => {
    for (const topic of reviewTopics) {
      const active = trackIsActive(topic.dataset.topicTrack);
      topic.hidden = !active;
      for (const input of topic.querySelectorAll("input")) input.disabled = !active;
    }
  };

  const showQuestion = (index) => {
    for (const question of questions) question.hidden = true;
    currentQuestion = Math.max(0, Math.min(index, activeQuestions.length));
    const finished = currentQuestion >= activeQuestions.length;
    complete.hidden = !finished;
    previous.hidden = currentQuestion === 0;
    if (position) {
      position.textContent = finished
        ? `${activeQuestions.length} answered`
        : `Question ${currentQuestion + 1} of ${activeQuestions.length}`;
    }
    if (!finished) activeQuestions[currentQuestion].hidden = false;
  };

  const advanceQuestion = async (question) => {
    if (!reducedMotionRequested()) {
      const outgoing = question.animate(
        [
          {opacity: 1, transform: "translateX(0)"},
          {opacity: 0, transform: "translateX(-2rem)"},
        ],
        {duration: 260, easing: "cubic-bezier(0.4, 0, 0.2, 1)"},
      );
      await outgoing.finished.catch(() => {});
    }
    showQuestion(currentQuestion + 1);
    const incoming = activeQuestions[currentQuestion];
    if (incoming && !reducedMotionRequested()) {
      incoming.animate(
        [
          {opacity: 0, transform: "translateX(2rem)"},
          {opacity: 1, transform: "translateX(0)"},
        ],
        {duration: 320, easing: "cubic-bezier(0.16, 1, 0.3, 1)"},
      );
    }
  };

  confidenceForm.querySelector("[data-start-confidence-quiz]")?.addEventListener("click", () => {
    activeQuestions = questions.filter((question) => trackIsActive(question.dataset.topicTrack));
    prepareReview();
    context.hidden = true;
    review.hidden = true;
    quiz.hidden = false;
    showQuestion(0);
    activeQuestions[0]?.querySelector("button")?.focus();
  });

  for (const question of questions) {
    for (const button of question.querySelectorAll("[data-confidence-rating]")) {
      button.addEventListener("click", async () => {
        if (advancingQuestion) return;
        advancingQuestion = true;
        previous.disabled = true;
        const value = button.dataset.confidenceRating;
        const topicId = question.dataset.topicId;
        for (const option of question.querySelectorAll("[data-confidence-rating]")) {
          option.setAttribute("aria-pressed", String(option === button));
        }
        const reviewInput = confidenceForm.querySelector(
          `input[name="rating_${CSS.escape(topicId)}"][value="${CSS.escape(value)}"]`,
        );
        if (reviewInput) reviewInput.checked = true;
        await advanceQuestion(question);
        advancingQuestion = false;
        previous.disabled = false;
        (activeQuestions[currentQuestion]?.querySelector("button") || complete.querySelector("button"))?.focus();
      });
    }
  }

  previous?.addEventListener("click", () => {
    if (advancingQuestion) return;
    showQuestion(currentQuestion - 1);
    const question = activeQuestions[currentQuestion];
    (question?.querySelector('[aria-pressed="true"]') || question?.querySelector("button"))?.focus();
  });

  confidenceForm.querySelector("[data-review-confidence]")?.addEventListener("click", () => {
    quiz.hidden = true;
    review.hidden = false;
    review.querySelector("input:checked")?.focus();
  });

  confidenceForm.querySelector("[data-return-confidence-summary]")?.addEventListener("click", () => {
    review.hidden = true;
    quiz.hidden = false;
    showQuestion(activeQuestions.length);
    complete.querySelector("button")?.focus();
  });

  confidenceForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = new FormData(confidenceForm);
    const ratings = {};
    for (const [name, value] of values.entries()) {
      if (name.startsWith("rating_")) ratings[name.slice(7)] = Number(value);
    }
    await runPlacementAction("save_confidence", {
      role_family: values.get("role_family") || "",
      target_level: values.get("target_level") || "",
      interview_focus: values.get("interview_focus") || "",
      ratings,
    });
  });
}

async function confirmPlacementOutline() {
  const outline = placementShell?.querySelector("#placement-outline")?.value || "";
  const form = placementShell?.querySelector("[data-outline-form]");
  await runPlacementAction("confirm_outline", {
    outline,
    submission_id: stablePlacementSubmission("confirm"),
    expected_revision: Number(placementShell.dataset.courseRevision || 0),
    ...outlineChangeValues(form),
  });
}

const placementSubmissionIds = new Map();
function stablePlacementSubmission(action) {
  if (placementSubmissionIds.has(action)) return placementSubmissionIds.get(action);
  const key = placementSubmissionStorageKey(action);
  const saved = key ? window.sessionStorage.getItem(key) : null;
  placementSubmissionIds.set(action, saved || crypto.randomUUID());
  if (key && !saved) window.sessionStorage.setItem(key, placementSubmissionIds.get(action));
  return placementSubmissionIds.get(action);
}

function outlineChangeValues(form) {
  const values = form ? new FormData(form) : new FormData();
  const ratings = {};
  for (const [name, value] of values.entries()) {
    if (name.startsWith("rating_")) ratings[name.slice(7)] = Number(value);
  }
  return {
    role_family: values.get("role_family") || "",
    target_level: values.get("target_level") || "",
    interview_focus: values.get("interview_focus") || "",
    interview_date: values.get("interview_date") ?? "",
    weekly_minutes: Number(values.get("weekly_minutes")),
    session_minutes: Number(values.get("session_minutes")),
    ratings,
    pacing_posture_override: values.get("pacing_posture_override") || null,
    optional_skill_ids: values.getAll("optional_skill_ids"),
  };
}

placementShell?.querySelector("[data-outline-form]")?.addEventListener("submit", (event) => {
  event.preventDefault();
  previewPlacementOutline(event.currentTarget);
});

let pendingOutlineChange = null;
const outlineList = placementShell?.querySelector("[data-outline-list]");
const committedOutlineMarkup = outlineList?.innerHTML || "";
const committedOutlineText = placementShell?.querySelector("#placement-outline")?.value || "";

function renderOutlineItems(items) {
  if (!outlineList || !Array.isArray(items)) return;
  outlineList.replaceChildren();
  for (const item of items) {
    const row = document.createElement("li");
    if (item.locked) row.classList.add("outline-locked");
    const details = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.title || "Course unit";
    const outcome = document.createElement("p");
    outcome.textContent = item.outcome || "";
    const habit = document.createElement("p");
    habit.className = "outline-habit";
    const habitLabel = document.createElement("span");
    habitLabel.textContent = "Interview habit";
    habit.append(habitLabel, ` ${item.interview_habit || ""}`);
    details.append(title, outcome, habit);
    const emphasis = document.createElement("span");
    const emphasisLabel = item.emphasis || "Learn";
    emphasis.className = `outline-emphasis ${emphasisLabel.toLowerCase()}`;
    emphasis.textContent = `${emphasisLabel}${item.locked ? " · locked" : ""}`;
    row.append(details, emphasis);
    outlineList.append(row);
  }
}

async function previewPlacementOutline(form) {
  const values = outlineChangeValues(form);
  lockPlacement(true);
  if (placementStatus) placementStatus.textContent = "Previewing route…";
  try {
    const result = await requestJson(`/api/courses/${encodeURIComponent(placementShell.dataset.courseSlug)}/placement`, {
      method: "POST",
      body: JSON.stringify({action: "preview_outline", ...values}),
    });
    pendingOutlineChange = values;
    const previewText = placementShell.querySelector("#placement-outline");
    if (previewText) previewText.value = result.outline || "";
    renderOutlineItems(result.outline_items);
    outlineEditor.hidden = true;
    outlineActions.hidden = true;
    const confirmation = placementShell.querySelector("[data-outline-preview-confirm]");
    confirmation.hidden = false;
    if (placementStatus) placementStatus.textContent = "Preview ready. Confirm to save it.";
    confirmation.querySelector("[data-outline-preview-heading], button")?.focus();
  } catch (error) {
    if (placementStatus) placementStatus.textContent = error.message;
  } finally {
    lockPlacement(false);
  }
}

placementShell?.querySelector("[data-accept-outline-preview]")?.addEventListener("click", () => {
  if (!pendingOutlineChange) return;
  runPlacementAction("change_outline", {
    ...pendingOutlineChange,
    submission_id: stablePlacementSubmission("change"),
    expected_revision: Number(placementShell.dataset.courseRevision || 0),
  });
});

placementShell?.querySelector("[data-cancel-outline-preview]")?.addEventListener("click", () => {
  pendingOutlineChange = null;
  placementShell.querySelector("[data-outline-preview-confirm]").hidden = true;
  if (outlineList) outlineList.innerHTML = committedOutlineMarkup;
  outlineEditor.querySelector("form")?.reset();
  const outlineText = placementShell.querySelector("#placement-outline");
  if (outlineText) outlineText.value = committedOutlineText;
  updateOutlineConfidenceFields();
  outlineEditor.hidden = true;
  outlineActions.hidden = false;
  outlineActions.querySelector("[data-change-outline]")?.focus();
});

placementShell?.querySelector("[data-confirm-outline]")?.addEventListener("click", () => {
  confirmPlacementOutline();
});

const outlineActions = placementShell?.querySelector("[data-outline-actions]");
const outlineEditor = placementShell?.querySelector("[data-outline-editor]");

function updateOutlineConfidenceFields() {
  const focus = outlineEditor?.querySelector('[name="interview_focus"]')?.value || "coding";
  for (const field of outlineEditor?.querySelectorAll("[data-outline-confidence-topic]") || []) {
    const visible = focus === "balanced"
      || field.dataset.topicTrack === (focus === "system_design" ? "system_design" : "coding");
    field.hidden = !visible;
    const select = field.querySelector("select");
    if (select) select.disabled = !visible;
  }
}

outlineEditor?.querySelector('[name="interview_focus"]')?.addEventListener(
  "change", updateOutlineConfidenceFields,
);
updateOutlineConfidenceFields();

placementShell?.querySelector("[data-change-outline]")?.addEventListener("click", () => {
  outlineActions.hidden = true;
  outlineEditor.hidden = false;
  outlineEditor.querySelector("textarea")?.focus();
});

placementShell?.querySelector("[data-cancel-outline]")?.addEventListener("click", () => {
  outlineEditor.hidden = true;
  outlineActions.hidden = false;
  outlineActions.querySelector("[data-change-outline]")?.focus();
});

for (const button of document.querySelectorAll("[data-placement-action]")) {
  button.addEventListener("click", () => runPlacementAction(button.dataset.placementAction, {
    stage: button.dataset.stage || null,
    submission_id: ["submit", "skip"].includes(button.dataset.placementAction)
      ? stablePlacementSubmission(button.dataset.placementAction)
      : null,
    expected_revision: button.dataset.placementAction === "skip"
      ? Number(placementShell?.dataset.courseRevision || 0)
      : null,
  }));
}

for (const button of document.querySelectorAll("[data-review-grade]")) {
  button.addEventListener("click", async () => {
    const item = button.closest("[data-review-item]");
    if (!item) return;
    for (const control of item.querySelectorAll("button")) control.disabled = true;
    try {
      await requestJson("/api/review", {
        method: "POST",
        body: JSON.stringify({
          slug: item.dataset.slug,
          concept: item.dataset.concept,
          due: item.dataset.due,
          result: button.dataset.reviewGrade,
        }),
      });
      item.remove();
      announce("Review result saved and the schedule was updated.");
    } catch (error) {
      announce(error.message);
      for (const control of item.querySelectorAll("button")) control.disabled = false;
    }
  });
}

const dataManagement = document.querySelector("[data-data-management]");
const dataStatus = dataManagement?.querySelector("[data-data-status]");

for (const form of document.querySelectorAll("[data-data-form]")) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    if (dataStatus) dataStatus.textContent = "Verifying local data…";
    try {
      const result = await requestJson("/api/data", {
        method: "POST",
        body: JSON.stringify(formPayload(form)),
      });
      const message = result.message || (result.archive ? `Verified backup created at ${result.archive}.` : "Local data operation completed.");
      if (dataStatus) dataStatus.textContent = message;
    } catch (error) {
      if (dataStatus) {
        dataStatus.textContent = error.message;
        dataStatus.setAttribute("aria-live", "assertive");
        dataStatus.focus();
      }
    } finally {
      submit.disabled = false;
    }
  });
}

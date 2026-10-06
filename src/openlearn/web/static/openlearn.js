"use strict";

const root = document.documentElement;
const themeOrder = ["system", "dark", "light"];
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
  const toggle = document.querySelector("[data-theme-toggle]");
  if (toggle) {
    const next = themeOrder[(themeOrder.indexOf(selected) + 1) % themeOrder.length];
    toggle.setAttribute(
      "aria-label",
      `Theme: ${selected[0].toUpperCase() + selected.slice(1)}. Activate to use ${next} theme`,
    );
  }
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

for (const picker of document.querySelectorAll("[data-folder-picker]")) {
  const input = picker.querySelector("input");
  const button = picker.querySelector("[data-folder-browse]");
  const status = picker.querySelector("[data-folder-picker-status]");
  button.hidden = false;
  button.addEventListener("click", async () => {
    const initialValue = input.value;
    const sourceKind = picker.closest("form")?.elements.source_kind;
    const initialKind = sourceKind?.value;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    status.hidden = false;
    status.textContent = "Choose a folder in the folder chooser.";
    try {
      const result = await requestJson("/api/sources/folder-picker", {method: "POST"});
      if (result.path && !button.hidden && !input.disabled && input.value === initialValue && sourceKind?.value === initialKind) {
        input.value = result.path;
        input.dispatchEvent(new Event("input", {bubbles: true}));
        input.dispatchEvent(new Event("change", {bubbles: true}));
        input.focus();
      }
      status.textContent = "";
      status.hidden = true;
    } catch (error) {
      if (!button.hidden) status.textContent = error.message;
      else status.hidden = true;
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  });
}

const createForm = document.querySelector(".create-form");
const creationDraftKey = `openlearn-course-draft:${appRoot}:${window.location.pathname}`;
const creationDraftFields = {title: 160, goal: 4000, experience: 4000, submission_id: 36, source_kind: 16, source_value: 2048};
// Historical defaults from the retired web picker, only for migrating old tab drafts.
// Keep this snapshot independent of future backend template edits; learner edits survive.
const retiredCreationDefaults = {
  "algorithms": {"title": "Algorithms & Data Structures", "goal": "Understand and implement core CS algorithms and data structures"},
  "git": {"title": "Git & GitHub", "goal": "Use Git confidently for daily development work"},
  "http-apis": {"title": "HTTP & APIs", "goal": "Understand how the web works and build and consume REST APIs"},
  "linux-cli": {"title": "Linux CLI", "goal": "Navigate and automate tasks in a Linux/Unix terminal"},
  "networking": {"title": "Computer Networking", "goal": "Understand how computer networks function from physical to application layer"},
  "python-basics": {"title": "Python Basics", "goal": "Write and understand fundamental Python programs"},
  "sql": {"title": "SQL Fundamentals", "goal": "Query and manage relational databases with SQL"},
  "technical-interview-prep": {"title": "Technical Interview Prep", "goal": "Prepare for LeetCode-style coding interviews with algorithms, data structures, and clear solution reasoning"},
  "vim": {"title": "Vim", "goal": "Edit text efficiently using Vim for real daily work"},
};
function saveCreationDraft() {
  if (!createForm) return;
  const draft = {};
  for (const [name, limit] of Object.entries(creationDraftFields)) {
    if (createForm.elements[name]) draft[name] = createForm.elements[name].value.slice(0, limit);
  }
  try { sessionStorage.setItem(creationDraftKey, JSON.stringify(draft)); } catch (_error) { /* optional */ }
}
if (createForm) {
  try {
    const creationUrl = new URL(location.href);
    if (creationUrl.searchParams.get("new") === "1") {
      sessionStorage.removeItem(creationDraftKey);
      creationUrl.searchParams.delete("new");
      history.replaceState(history.state, "", creationUrl);
    }
    const legacySourceDraft = createForm.dataset.creationMode === "course" && new URLSearchParams(location.search).get("sources") === "1"
      ? sessionStorage.getItem(`openlearn-course-draft:${appRoot}:${appRoot}/courses/from-source`) : null;
    const draft = JSON.parse(sessionStorage.getItem(creationDraftKey) || legacySourceDraft || "null");
    if (draft && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(draft.submission_id)) {
      const retired = Object.hasOwn(retiredCreationDefaults, draft.template_id) ? retiredCreationDefaults[draft.template_id] : null;
      for (const [name, limit] of Object.entries(creationDraftFields)) {
        if (createForm.elements[name] && typeof draft[name] === "string" && draft[name].length <= limit) {
          createForm.elements[name].value = retired && draft[name] === retired[name] ? "" : draft[name];
        }
      }
      if (retired || legacySourceDraft) saveCreationDraft();
    }
  } catch (_error) { /* optional */ }
  createForm.addEventListener("input", saveCreationDraft);
  createForm.addEventListener("change", saveCreationDraft);
  const kind = createForm.querySelector("[data-source-kind]");
  if (kind) {
    const file = createForm.elements.source_file;
    const value = createForm.elements.source_value;
    const browse = createForm.querySelector("[data-folder-browse]");
    const fileButton = createForm.querySelector("[data-source-pick-file]");
    const githubButton = createForm.querySelector("[data-source-github]");
    createForm.querySelector("[data-source-kind-field]").hidden = true;
    createForm.querySelector("[data-source-local-controls]").hidden = false;
    createForm.querySelector("[data-source-file-field]").classList.add("sr-only");
    file.tabIndex = -1;
    createForm.querySelector("[data-source-folder-action]").append(browse);
    browse.textContent = "Choose folder";
    fileButton.addEventListener("click", () => file.click());
    githubButton.addEventListener("click", () => {
      file.value = "";
      value.value = "";
      kind.value = kind.value === "github" ? (createForm.dataset.creationMode === "course" ? "" : "file") : "github";
      updateSourceFields();
      saveCreationDraft();
      if (kind.value === "github") value.focus();
    });
    const suggestCourseName = () => {
      if (createForm.elements.title.value.trim()) return;
      const label = kind.value === "file" ? file.files[0]?.name?.replace(/\.[^.]+$/, "")
        : kind.value === "folder" ? value.value.replace(/[\\/]+$/, "").split(/[\\/]/).pop() : "";
      if (label) {
        createForm.elements.title.value = label.replace(/[_-]+/g, " ").trim().slice(0, 160);
        saveCreationDraft();
      }
    };
    const updateSourceFields = () => {
      const valueLabel = createForm.querySelector("[data-source-value-label]");
      if (valueLabel) valueLabel.textContent = kind.value === "github" ? "Public GitHub repository URL" : "Local folder path";
      value.placeholder = kind.value === "github" ? "https://github.com/owner/repository" : "/path/to/your/notes";
      const hasSource = Boolean(kind.value);
      file.disabled = false;
      value.disabled = false;
      browse.hidden = kind.value === "github";
      githubButton.textContent = kind.value === "github" ? "Use file or folder" : "Use GitHub";
      if (browse.hidden) createForm.querySelector("[data-folder-picker-status]").hidden = true;
      createForm.querySelector("[data-source-value-field]").hidden = false;
      createForm.querySelector("[data-source-file-note]").hidden = kind.value === "github";
      if (kind.value !== "github") value.placeholder = "Or paste a folder path";
      const selected = kind.value === "file" ? file.files[0]?.name || ""
        : hasSource ? value.value.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "" : "";
      createForm.querySelector("[data-source-actions]").hidden = !selected;
      const selection = createForm.querySelector("[data-source-selection]");
      if (selection) {
        selection.textContent = selected;
        selection.title = selected;
      }
      if (createForm.dataset.creationMode === "course") {
        createForm.dataset.endpoint = hasSource ? "/courses/from-source" : "/api/courses";
        createForm.toggleAttribute("data-multipart", hasSource);
        const status = createForm.querySelector("[data-form-status]");
        if (createForm.querySelector("[data-form-error]").hidden && !createForm.dataset.submitting) {
          status.textContent = hasSource ? "Your source stays local until you review and approve a tutor request." : "You can add a source later, too.";
        }
      }
    };
    kind.addEventListener("change", updateSourceFields);
    file.addEventListener("change", () => {
      if (!file.files.length) return;
      kind.value = "file";
      value.value = "";
      suggestCourseName();
      updateSourceFields();
      saveCreationDraft();
    });
    value.addEventListener("input", () => {
      if (kind.value !== "github") {
        if (value.value.trim()) {
          kind.value = "folder";
          file.value = "";
        } else if (kind.value === "folder") kind.value = createForm.dataset.creationMode === "course" ? "" : "file";
      }
      updateSourceFields();
      saveCreationDraft();
    });
    value.addEventListener("change", suggestCourseName);
    createForm.querySelector("[data-source-change]").addEventListener("click", () => {
      if (kind.value === "file") file.click();
      else if (kind.value === "folder") browse.click();
      else { value.focus(); value.select(); }
    });
    createForm.querySelector("[data-source-remove]").addEventListener("click", () => {
      file.value = "";
      value.value = "";
      kind.value = createForm.dataset.creationMode === "course" ? "" : "file";
      updateSourceFields();
      saveCreationDraft();
      fileButton.focus();
    });
    updateSourceFields();
    if (kind.value && createForm.querySelector("[data-creation-sources]")) createForm.querySelector("[data-creation-sources]").open = true;
  }
  if (createForm.elements.experience?.value && createForm.querySelector("[data-creation-experience]")) createForm.querySelector("[data-creation-experience]").open = true;
}

const providerDialog = document.querySelector("[data-provider-setup-dialog]");
let pendingProviderResume = null;
function openProviderSetup(message, resume) {
  if (!providerDialog) return false;
  pendingProviderResume = resume;
  providerDialog.querySelector("[data-provider-recovery-reason]").textContent = message;
  if (!providerDialog.open) providerDialog.showModal();
  providerDialog.querySelector("#provider-recovery-title").focus();
  return true;
}
providerDialog?.querySelector("[data-provider-setup-cancel]")?.addEventListener("click", () => providerDialog.close());
providerDialog?.addEventListener("close", () => {
  pendingProviderResume = null;
  const key = providerDialog.querySelector('[name="api_key"]');
  if (key) key.value = "";
});
if (createForm?.dataset.providerRecoveryMessage) {
  openProviderSetup(createForm.dataset.providerRecoveryMessage, () => createForm.requestSubmit());
}

const starterResumeForm = document.querySelector("[data-starter-resume-form]");
if (starterResumeForm) {
  window.requestAnimationFrame(() => starterResumeForm.requestSubmit());
}

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

for (const form of document.querySelectorAll("[data-json-form]")) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (form.dataset.submitting === "true") return;
    form.dataset.submitting = "true";
    if (form === createForm) saveCreationDraft();
    const submit = form.querySelector('[type="submit"]');
    const errorBox = form.querySelector("[data-form-error]");
    const status = form.querySelector("[data-form-status]");
    const creationReset = form.querySelector("[data-creation-reset]");
    if (errorBox) errorBox.hidden = true;
    if (creationReset) creationReset.hidden = true;
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    if (status) status.textContent = form.hasAttribute("data-multipart") ? "Saving course and screening the source…" : form.dataset.endpoint === "/api/setup" ? "Testing connection…" : "Saving course and preparing the first lesson…";
    try {
      const result = await requestJson(form.dataset.endpoint, {
        method: "POST",
        body: form.hasAttribute("data-multipart") ? new FormData(form) : JSON.stringify(formPayload(form)),
      });
      if (form.elements.api_key) form.elements.api_key.value = "";
      if (form.dataset.endpoint === "/api/setup" && result.ready === false) {
        if (form.elements.save_unverified) form.elements.save_unverified.checked = false;
        const message = result.message || "Saved locally. Validate the connection before teaching starts.";
        if (status) status.textContent = message;
        return;
      }
      if (form.hasAttribute("data-provider-recovery-form")) {
        const resume = providerDialog.open ? pendingProviderResume : null;
        pendingProviderResume = null;
        providerDialog.close();
        if (resume) resume();
        return;
      }
      if (form === createForm) {
        try {
          sessionStorage.removeItem(creationDraftKey);
          if (new URLSearchParams(location.search).get("sources") === "1") {
            sessionStorage.removeItem(`openlearn-course-draft:${appRoot}:${appRoot}/courses/from-source`);
          }
        } catch (_error) { /* optional */ }
      }
      announce("Saved successfully.");
      const destination = result.setup_url || result.placement_url || result.initialization_url || result.focus_url || result.redirect || form.dataset.successUrl;
      if (destination) window.location.assign(appUrl(destination));
    } catch (error) {
      if (creationReset) creationReset.hidden = error.payload?.state !== "source_required";
      if (form.elements.api_key && !error.payload?.retain_secret) form.elements.api_key.value = "";
      if (form === createForm && error.payload?.state === "setup_required") {
        if (openProviderSetup(error.message, () => createForm.requestSubmit())) {
          if (status) status.textContent = "Lesson input saved. Test the tutor connection to continue.";
          return;
        }
      }
      if (errorBox) {
        errorBox.textContent = error.message;
        errorBox.hidden = false;
        errorBox.focus();
      }
      if (status) status.textContent = "Nothing was lost. Correct the issue and try again.";
    } finally {
      delete form.dataset.submitting;
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
    }
  });
}

for (const form of document.querySelectorAll("[data-enter-flow]")) {
  form.addEventListener("keydown", (event) => {
    if (
      event.key !== "Enter"
      || event.shiftKey
      || event.altKey
      || event.ctrlKey
      || event.metaKey
      || event.isComposing
    ) return;
    const fields = [...form.querySelectorAll('input:not([type="hidden"]):not([type="file"]), textarea')]
      .filter((field) => !field.disabled);
    const index = fields.indexOf(event.target);
    if (index < 0) return;
    event.preventDefault();
    if (index < fields.length - 1) {
      const next = fields[index + 1];
      const disclosure = next.closest("details");
      if (disclosure) disclosure.open = true;
      next.focus();
    }
    else form.requestSubmit();
  });
}

function closeCourseMenus(except = null) {
  for (const menu of document.querySelectorAll("details[data-course-menu][open]")) {
    if (menu !== except) menu.open = false;
  }
}

// Delegate so course previews replaced by renderDashboard retain native menus.
document.addEventListener("click", (event) => {
  const menu = event.target.closest("details[data-course-menu]");
  const summary = event.target.closest("details[data-course-menu] > summary");
  if (summary) closeCourseMenus(menu);
  else if (!menu || event.target.closest("[data-course-menu-panel] a")) closeCourseMenus();
});

document.addEventListener("toggle", (event) => {
  if (event.target.matches("details[data-course-menu]") && event.target.open) {
    closeCourseMenus(event.target);
  }
}, true);

document.addEventListener("focusout", (event) => {
  const menu = event.target.closest("details[data-course-menu]");
  if (menu && !menu.contains(event.relatedTarget)) menu.open = false;
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const menu = document.querySelector("details[data-course-menu][open]");
  if (!menu) return;
  event.preventDefault();
  closeCourseMenus();
  menu.querySelector(":scope > summary")?.focus();
});

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
let toolOpenVersion = 0;
let surfaceMotionVersion = 0;
const focusLayoutAnimations = new Map();

const availableTools = new Set(["chat", "sources", "options"]);

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
  const openVersion = ++toolOpenVersion;
  activeToolOpener = opener;
  for (const button of document.querySelectorAll("[data-tool-open]")) {
    button.setAttribute("aria-expanded", String(button === opener));
  }
  for (const panel of toolSurface.querySelectorAll("[data-tool-panel]")) {
    panel.hidden = panel.dataset.toolPanel !== tool;
  }
  const titles = {chat: "Tutor chat", sources: "Course sources", options: "Course options"};
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
      : tool === "options"
        ? "Local course options."
        : "Loading local tool state…"
  );
  try {
    await loadToolState(tool);
    if (openVersion === toolOpenVersion) toolStatus("Ready.");
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
  toolOpenVersion += 1;
  for (const button of document.querySelectorAll("[data-tool-open]")) button.setAttribute("aria-expanded", "false");
  if (updateUrl) setToolUrl(null, {replace: true});
  const opener = activeToolOpener;
  activeToolOpener = null;
  opener?.focus();
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
  button.addEventListener("click", () => {
    const tool = button.dataset.toolOpen;
    const alreadyOpen = focusShell?.dataset.toolActive === tool
      && !toolSurface?.hidden
      && toolSurface?.getAttribute("aria-hidden") !== "true";
    if (alreadyOpen) closeTool();
    else openTool(tool, button);
  });
}
toolSurface?.querySelector("[data-tool-close]")?.addEventListener("click", () => closeTool());


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
    if (!nextTool && new URL(window.location.href).searchParams.has("tool")) {
      setToolUrl(null, {replace: true});
    }
    const currentTool = focusShell.dataset.toolActive || null;
    if (nextTool) {
      const opener = document.querySelector(`[data-tool-open="${nextTool}"]`);
      const opened = await openTool(nextTool, opener, {updateUrl: false});
      if (!opened && currentTool) setToolUrl(currentTool, {replace: true});
    } else if (!toolSurface.hidden) {
      const closed = closeTool({updateUrl: false});
      if (!closed && currentTool) setToolUrl(currentTool, {replace: true});
      else if (new URL(window.location.href).searchParams.has("tool")) {
        setToolUrl(null, {replace: true});
      }
    } else if (new URL(window.location.href).searchParams.has("tool")) {
      setToolUrl(null, {replace: true});
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
      if (result.error_code === "provider_credentials") {
        openProviderSetup(result.error || "The provider rejected the saved credentials. Test the connection.", retryInitialization);
      }
      setInitializationState(
        result.error || "Your course is safe. Retry preparing the first lesson.",
        true,
      );
      return;
    }
    setInitializationState(labels[state] || "Preparing your first lesson…");
  }
}

async function retryInitialization() {
  const button = initializationShell.querySelector("[data-initialization-retry]");
  if (button.disabled) return;
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
    if (error.payload?.state === "setup_required") openProviderSetup(error.message, retryInitialization);
  } finally {
    button.disabled = false;
  }
}

if (initializationShell) {
  const initialState = initializationShell.dataset.operationState;
  if (initializationShell.dataset.providerBlocked === "true") {
    setInitializationState(initializationShell.dataset.providerError || "Test the provider connection before teaching starts.", true);
    if (initializationShell.dataset.providerSetupRequired === "true") {
      openProviderSetup(initializationShell.dataset.providerError, retryInitialization);
    }
  } else if (initialState === "retryable_error" || initialState === "conflict") {
    setInitializationState(initializationShell.dataset.operationError || "Your course is safe. Retry preparing the first lesson.", true);
    if (initializationShell.dataset.operationErrorCode === "provider_credentials") {
      openProviderSetup(initializationShell.dataset.operationError, retryInitialization);
    }
  } else {
    pollInitialization().catch((error) => setInitializationState(error.message, true));
  }
  initializationShell.querySelector("[data-initialization-retry]")?.addEventListener("click", retryInitialization);
}

function setOperationState(message, isError = false, result = null) {
  const state = document.querySelector("[data-operation-state]:not([data-focus-shell])");
  if (!state) return;
  state.hidden = false;
  const messageNode = state.querySelector("[data-operation-message]");
  if (messageNode) {
    if (messageNode.textContent !== message) messageNode.textContent = message;
  } else state.textContent = message;
  state.classList.toggle("error", isError);
  state.setAttribute("aria-live", isError ? "assertive" : "polite");
  const recovery = state.querySelector("[data-provider-recovery]");
  if (recovery) {
    recovery.hidden = !isError || result?.show_provider_recovery !== true;
  }
  if (isError) state.focus();
}

const navigationIntents = new Set(["next", "skip", "practice"]);

function turnProcessingMessage(intent) {
  if (navigationIntents.has(intent)) return "Preparing…";
  return intent === "answer" ? "Checking answer…" : "Processing…";
}

function setTurnProcessing(active) {
  const state = document.querySelector("[data-operation-state]:not([data-focus-shell])");
  const indicator = state?.querySelector("[data-operation-indicator]");
  if (indicator) indicator.hidden = !active;
  if (state) state.classList.toggle("processing", active);
  document.querySelector("[data-current-move]")?.setAttribute("aria-busy", String(active));
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
  setTurnProcessing(locked);
}

function lockProgressionControls(locked) {
  for (const control of document.querySelectorAll(
    "[data-progression-action], [data-navigation-intent]",
  )) control.disabled = locked;
  progressionInFlight = locked;
}

async function waitForOperation(operationId, setStatus) {
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
  setTurnProcessing(false);
  const state = document.querySelector("[data-operation-state]:not([data-focus-shell])");
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

async function pollOperation(operationId, submittedIntent = "") {
  const result = await waitForOperation(
    operationId,
    () => setOperationState(turnProcessingMessage(submittedIntent)),
  );
  if (result.state === "committed") {
    if (
      (result.message_kind === "answer" || submittedIntent === "answer"
        || navigationIntents.has(submittedIntent))
      && !chatInFlight
    ) {
      storeChatDraft();
      window.location.reload();
      return;
    }
    clearTurnComposer();
    showNextLessonHandoff();
    return;
  }
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
    setOperationState(turnProcessingMessage(""));
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

let sourcePreviewInFlight = false;
async function approveSourceRequest(payload) {
  if (sourcePreviewInFlight) return false;
  if (!focusShell?.querySelector("[data-source-mode]")?.checked) return true;
  const dialog = focusShell.querySelector("[data-source-preview]");
  if (!dialog || dialog.open) return false;
  payload.source_mode = true;
  sourcePreviewInFlight = true;
  try {
    const result = await requestJson(`/api/courses/${encodeURIComponent(focusShell.dataset.courseSlug)}/source-preview`, {
      method: "POST", body: JSON.stringify(payload),
    });
    if (!result.ok) throw new Error(result.error || "Source preview is unavailable.");
    dialog.querySelector("[data-source-disclosure]").textContent = result.disclosure;
    dialog.querySelector("[data-source-preview-text]").textContent = result.preview;
    return await new Promise((resolve) => {
      const finish = (approved) => {
        dialog.close();
        dialog.oncancel = null;
        dialog.querySelector("[data-source-cancel]").onclick = null;
        dialog.querySelector("[data-source-send]").onclick = null;
        if (approved) payload.source_approval = result.approval;
        resolve(approved);
      };
      dialog.oncancel = (event) => { event.preventDefault(); finish(false); };
      dialog.querySelector("[data-source-cancel]").onclick = () => finish(false);
      dialog.querySelector("[data-source-send]").onclick = () => finish(true);
      dialog.showModal();
    });
  } finally {
    sourcePreviewInFlight = false;
  }
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
  try {
    if (!await approveSourceRequest(payload)) return;
  } catch (error) {
    setOperationState(error.message, true);
    return;
  }
  lockTurnForm(true);
  setOperationState(turnProcessingMessage(payload.intent));
  try {
    const result = await requestJson(`/api/courses/${encodeURIComponent(focusShell.dataset.courseSlug)}/turns`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (result.operation_id) await pollOperation(result.operation_id, payload.intent);
    else if (result.state === "committed") {
      if (
        (result.message_kind === "answer" || payload.intent === "answer"
          || navigationIntents.has(payload.intent))
        && !chatInFlight
      ) {
        storeChatDraft();
        window.location.reload();
      } else {
        clearTurnComposer();
        showNextLessonHandoff();
      }
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
    || !focusShell
    || turnForm
    || document.querySelector("[data-review-shell]")
    || focusShell?.dataset.toolActive
    || document.querySelector(".drawer:not([hidden])")
    || event.target.closest?.("button, a, input, textarea, select, summary")
  ) return;
  event.preventDefault();
  submitTurn("next");
});

function appendMath(target, tex, display = false) {
  target.classList.add("math-expression", "math-fallback");
  target.dataset.mathDisplay = display ? "true" : "false";
  if (window.OpenLearnMath) window.OpenLearnMath.render(target, tex, display);
  else target.textContent = tex;
}

function appendPresentationText(container, text, parts) {
  if (!parts) {
    container.textContent = text || "";
    return;
  }
  for (const part of parts) {
    if (part.kind === "math") {
      const span = document.createElement("span");
      appendMath(span, part.text);
      container.append(span);
    } else container.append(document.createTextNode(part.text || ""));
  }
}

function appendPresentationBlocks(container, blocks) {
  for (const block of blocks || []) {
    if (block.kind === "code") {
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = block.text || "";
      pre.append(code);
      container.append(pre);
    } else if (block.kind === "math") {
      const formula = document.createElement("div");
      appendMath(formula, block.text, true);
      container.append(formula);
    } else if (block.kind === "unordered_list" || block.kind === "ordered_list") {
      const list = document.createElement(block.kind === "ordered_list" ? "ol" : "ul");
      for (const [index, value] of (block.items || []).entries()) {
        const itemNode = document.createElement("li");
        appendPresentationText(itemNode, value, block.item_parts?.[index]);
        list.append(itemNode);
      }
      container.append(list);
    } else if (block.kind === "definition") {
      const note = document.createElement("aside");
      note.className = "term-note";
      const term = document.createElement("strong");
      term.textContent = block.term || "Term";
      const definition = document.createElement("span");
      definition.textContent = block.text || "";
      note.append(term, definition);
      container.append(note);
    } else {
      const content = document.createElement("p");
      if (block.inline?.length) {
        for (const segment of block.inline) {
          const fragment = document.createElement(segment.strong ? "strong" : "span");
          appendPresentationText(fragment, segment.text, segment.parts);
          content.append(fragment);
        }
      } else {
        appendPresentationText(content, block.text, block.parts);
      }
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
  try {
    if (!await approveSourceRequest(payload)) return;
  } catch (error) {
    setChatStatus(error.message, true);
    return;
  }
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

function initializeReview() {
  const shell = document.querySelector("[data-review-shell]");
  if (!shell) return;
  const panel = shell.querySelector("[data-review-panel]");
  const summary = shell.querySelector("[data-review-summary]");
  const snapshot = JSON.parse(shell.querySelector("[data-review-snapshot]").textContent);
  let items = snapshot.items;
  let clockOffset = Date.parse(snapshot.server_time) - Date.now();
  if (!Number.isFinite(clockOffset)) clockOffset = 0;
  let current = null;
  let mode = "question";
  let revealed = null;
  let pending = null;
  let busy = false;
  let operation = 0;
  let timer = null;
  let dockObserver = null;
  let updateActionDock = null;
  let notice = null;
  let noticeTimer = null;
  let noticeFadeTimer = null;
  let noticeRemaining = 0;
  let noticeStartedAt = 0;
  let lockedFocus = null;
  const skipped = new Set();
  const receipts = new Set();
  const reviewedCards = new Set();
  const hadItems = items.length > 0;
  const meanings = {
    again: "Forgotten or incorrect", hard: "Correct with difficulty",
    good: "Correct with effort", easy: "Correct with little effort",
  };
  const results = ["again", "hard", "good", "easy"];
  const itemKey = (item) => JSON.stringify([item.slug, item.concept]);
  const now = () => Date.now() + clockOffset;
  const waiting = (item) => item.relearn_at && Date.parse(item.relearn_at) > now();
  const sessionUrl = () => `/api/review/session${shell.dataset.course ? `?course=${encodeURIComponent(shell.dataset.course)}` : ""}`;
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function heading(text, tag = "h2") {
    const element = node(tag, text);
    element.tabIndex = -1;
    return element;
  }
  function button(text, action, className = "secondary-action") {
    const element = node("button", text, className);
    element.type = "button";
    element.addEventListener("click", action);
    return element;
  }
  function positionNotice() {
    const bottom = document.querySelector(".site-header")?.getBoundingClientRect().bottom || 0;
    shell.style.setProperty("--review-notice-top", `${Math.max(16, bottom + 8)}px`);
  }
  function clearNotice() {
    clearTimeout(noticeTimer);
    clearTimeout(noticeFadeTimer);
    noticeTimer = noticeFadeTimer = null;
    notice?.remove();
    notice = null;
    noticeRemaining = 0;
  }
  function pauseNotice() {
    if (!notice) return;
    if (noticeTimer !== null) noticeRemaining = Math.max(0, noticeRemaining - (performance.now() - noticeStartedAt));
    clearTimeout(noticeTimer);
    clearTimeout(noticeFadeTimer);
    noticeTimer = noticeFadeTimer = null;
    notice.classList.remove("is-leaving");
  }
  function resumeNotice() {
    if (!notice || document.hidden || notice.matches(":hover") || notice.contains(document.activeElement) || noticeTimer !== null) return;
    noticeStartedAt = performance.now();
    noticeTimer = setTimeout(() => {
      noticeTimer = null;
      noticeRemaining = 0;
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) clearNotice();
      else {
        notice.classList.add("is-leaving");
        noticeFadeTimer = setTimeout(clearNotice, 160);
      }
    }, noticeRemaining);
  }
  function showNotice(message) {
    if (notice?.dataset.message !== message) {
      clearNotice();
      notice = node("aside", undefined, "review-toast");
      notice.dataset.reviewToast = "";
      notice.dataset.message = message;
      notice.setAttribute("role", "alert");
      const icon = node("span", "!", "review-toast-icon");
      icon.setAttribute("aria-hidden", "true");
      const close = button("×", () => {
        const restore = notice?.contains(document.activeElement);
        clearNotice();
        if (restore) {
          const target = panel.querySelector("[data-review-retry]:not(:disabled)")
            || panel.querySelector("[data-review-primary]:not(:disabled)")
            || panel.querySelector("[data-review-grade]:not(:disabled), .review-footer button:not(:disabled)")
            || panel.querySelector("[data-review-topic]");
          focusHeading(target);
        }
      }, "quiet-action review-toast-dismiss");
      close.setAttribute("aria-label", "Dismiss notification");
      notice.append(icon, node("p", message), close);
      notice.addEventListener("pointerenter", pauseNotice);
      notice.addEventListener("pointerleave", resumeNotice);
      notice.addEventListener("focusin", pauseNotice);
      notice.addEventListener("focusout", () => queueMicrotask(resumeNotice));
      shell.append(notice);
    } else pauseNotice();
    noticeRemaining = 8000;
    positionNotice();
    resumeNotice();
  }
  window.addEventListener("scroll", positionNotice, {passive: true});
  window.addEventListener("resize", positionNotice);
  document.addEventListener("visibilitychange", () => document.hidden ? pauseNotice() : resumeNotice());
  function clearFeedback() {
    clearNotice();
    const area = panel.querySelector("[data-review-recovery]");
    area?.querySelector("[data-review-open-course]")?.remove();
    const status = area?.querySelector("[data-review-status]");
    if (status) { status.textContent = ""; status.hidden = true; }
  }
  function setPrimaryLabel(label) {
    const control = panel.querySelector("[data-review-primary]");
    if (!control) return;
    control.textContent = label;
    control.setAttribute("aria-label", label);
    control.title = mode === "question" ? `${label} (Space)` : label;
  }
  function setRecovery(message, retry, label, {primary = false, missingSource = false} = {}) {
    let area = panel.querySelector("[data-review-recovery]");
    if (!area) {
      area = node("div", undefined, "review-recovery");
      area.dataset.reviewRecovery = "";
      const footer = panel.querySelector(".review-footer");
      if (footer) panel.insertBefore(area, footer);
      else panel.append(area);
    }
    let status = area.querySelector("[data-review-status]");
    if (!status) {
      status = node("p", undefined, "review-status");
      status.dataset.reviewStatus = "";
      status.id = "review-action-status";
      area.append(status);
    }
    status.hidden = false;
    status.textContent = message;
    let control;
    if (primary) {
      setPrimaryLabel(label);
      control = area.querySelector("[data-review-primary]");
    } else {
      control = area.querySelector("[data-review-retry]");
      if (!control) {
        control = button(label, () => { if (!busy) retry(); });
        control.dataset.reviewRetry = "";
        area.append(control);
      }
      control.textContent = label;
    }
    control?.setAttribute("aria-describedby", status.id);
    area.querySelector("[data-review-open-course]")?.remove();
    if (missingSource) {
      const link = node("a", "Open course", "quiet-link");
      link.dataset.reviewOpenCourse = "";
      link.href = appUrl(`/courses/${encodeURIComponent(current.slug)}`);
      area.append(link);
    }
    showNotice(message);
  }
  function reviewHeaderHeight() {
    const header = document.querySelector(".site-header");
    return header && ["sticky", "fixed"].includes(getComputedStyle(header).position) ? header.offsetHeight : 0;
  }
  function clearActionDock() {
    dockObserver?.disconnect();
    dockObserver = null;
    updateActionDock = null;
    panel.style.setProperty("--review-dock-height", "0px");
    panel.style.setProperty("--review-header-offset", `${reviewHeaderHeight() + 16}px`);
  }
  function configureActionDock(footer) {
    const update = () => {
      if (!panel.contains(footer)) return;
      const headerHeight = reviewHeaderHeight();
      const dockHeight = footer.offsetHeight;
      footer.classList.toggle("review-footer--flow", dockHeight > window.innerHeight * 0.34
        || window.innerHeight - headerHeight - dockHeight < 230);
      const sticky = getComputedStyle(footer).position === "sticky";
      panel.style.setProperty("--review-dock-height", `${sticky ? dockHeight : 0}px`);
      panel.style.setProperty("--review-header-offset", `${headerHeight + 16}px`);
    };
    updateActionDock = update;
    dockObserver?.disconnect();
    dockObserver = new ResizeObserver(update);
    dockObserver.observe(footer);
    dockObserver.observe(document.documentElement);
    update();
  }
  window.addEventListener("resize", () => updateActionDock?.());
  function focusHeading(element, block = "nearest") {
    element?.focus({preventScroll: true});
    element?.scrollIntoView({block});
  }
  function appendProse(container, text, className, kind) {
    const prose = node("div", undefined, "review-prose");
    prose.dataset.reviewProse = kind;
    prose.setAttribute("role", "group");
    prose.setAttribute("aria-label", kind === "answer" ? "Answer" : "Explanation");
    // Keep original characters and line boundaries; only wrap reliable prose.
    for (const part of (text || "").split(/(\r?\n[ \t]*\r?\n(?:[ \t]*\r?\n)*)/)) {
      if (!part) continue;
      if (!part.trim()) { prose.append(document.createTextNode(part)); continue; }
      const structured = /[\r\n`{}[\]\\=<>_^]|\b(?:e\.g|i\.e|Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|approx|Fig|Eq|Corp|Inc)\./i.test(part);
      let start = 0;
      if (!structured && part.split(/\s+/).length > 45) {
        // Boundaries require an ordinary long word, punctuation, and a new
        // capitalized sentence. Ambiguous abbreviations/formulas stay intact.
        for (const match of part.matchAll(/[a-z]{4,}[.!?](?:["'])?(\s+)(?=[A-Z])/g)) {
          const separatorStart = match.index + match[0].length - match[1].length;
          if (part.slice(start, separatorStart).split(/\s+/).length < 20) continue;
          prose.append(node("p", part.slice(start, separatorStart), className), document.createTextNode(match[1]));
          start = separatorStart + match[1].length;
        }
      }
      if (start < part.length) prose.append(node("p", part.slice(start), className));
    }
    container.append(prose);
  }
  function updateSummary() {
    const returns = items.filter(waiting).length;
    const skippedCount = items.filter((item) => skipped.has(itemKey(item))).length;
    summary.textContent = `${items.length} remaining · ${receipts.size} reviewed${returns ? ` · ${returns} returns soon` : ""}${skippedCount ? ` · ${skippedCount} skipped` : ""}`;
  }
  function lock(value) {
    if (value) lockedFocus = panel.contains(document.activeElement) ? document.activeElement : null;
    busy = value;
    panel.setAttribute("aria-busy", String(value));
    for (const control of panel.querySelectorAll("button, summary")) {
      if (control.tagName === "BUTTON") control.disabled = value;
      else if (value) control.setAttribute("aria-disabled", "true");
      else control.removeAttribute("aria-disabled");
    }
    for (const link of panel.querySelectorAll("a")) {
      if (value) link.setAttribute("aria-disabled", "true");
      else link.removeAttribute("aria-disabled");
    }
    if (!value && lockedFocus && document.activeElement === document.body) {
      const target = lockedFocus.isConnected && !lockedFocus.disabled
        ? lockedFocus : panel.querySelector("[data-review-retry]:not(:disabled)");
      target?.focus({preventScroll: true});
    }
  }
  panel.addEventListener("click", (event) => {
    if (busy && event.target.closest("a, summary")) event.preventDefault();
  });
  function replaceItem(item, previous = current) {
    items = items.filter((value) => itemKey(value) !== itemKey(previous));
    if (item) items.push(item);
  }
  function payload() {
    return {
      slug: current.slug, concept: current.concept, card_id: current.card_id,
      content_version: current.content_version, review_revision: current.review_revision,
    };
  }
  async function post(url, body) {
    const result = await requestJson(url, {method: "POST", body: JSON.stringify(body)});
    if (result.ok !== true) {
      const error = new Error(result.error || "The review operation could not be completed.");
      error.payload = result;
      throw error;
    }
    return result;
  }
  function conflict(error) { return error.payload?.state === "conflict"; }
  function addSkip(ratings = null) {
    const footer = node("footer", undefined, ratings ? "review-footer review-footer--ratings" : "review-footer");
    if (ratings) {
      footer.dataset.reviewActions = "";
      footer.append(ratings);
    }
    footer.append(button("Skip for now", () => {
      if (busy || pending) return;
      skipped.add(itemKey(current));
      showNext(true);
      announce("Skipped for this session. This review remains due.");
    }, "quiet-action"));
    panel.append(footer);
    if (ratings) configureActionDock(footer);
  }
  function showConflict() {
    mode = "conflict";
    pending = null;
    clearFeedback();
    clearActionDock();
    panel.replaceChildren();
    const title = heading(current?.concept || "Review");
    title.dataset.reviewTopic = "";
    panel.append(title);
    setRecovery("This card changed in another tab.", () => refresh(true), "Refresh review");
    panel.querySelector("[data-review-retry]").dataset.reviewRefresh = "";
  }
  function showCard(focus = false) {
    clearTimeout(timer);
    clearFeedback();
    clearActionDock();
    mode = current.state === "needs_preparation" ? "preparation" : "question";
    revealed = null;
    pending = null;
    panel.replaceChildren();
    const title = heading(current.concept);
    title.className = "review-topic";
    title.dataset.reviewTopic = "";
    panel.append(node("p", current.course, "review-course"), title);
    let question = null;
    if (mode === "preparation") panel.append(node("p", "Create a review question from your course material."));
    else {
      question = node("p", current.question, "review-question");
      question.tabIndex = -1;
      question.dataset.reviewQuestion = "";
      panel.append(question);
    }
    const area = node("div", undefined, "review-recovery");
    area.dataset.reviewRecovery = "";
    const control = button(mode === "preparation" ? "Prepare card" : "Show answer and explanation", mode === "preparation" ? prepare : reveal, "primary-action");
    control.dataset.reviewPrimary = "";
    if (mode === "question") {
      control.setAttribute("aria-keyshortcuts", "Space");
      control.title = "Show answer and explanation (Space)";
    }
    area.append(control);
    panel.append(area);
    addSkip();
    updateSummary();
    if (focus) focusHeading(question || title);
  }
  async function prepare() {
    if (busy) return;
    const id = ++operation;
    const identity = itemKey(current);
    clearFeedback();
    setPrimaryLabel("Preparing…");
    lock(true);
    announce("Preparing question and answer.");
    try {
      const result = await post("/api/review/prepare", {
        slug: current.slug, concept: current.concept, review_revision: current.review_revision,
      });
      if (id !== operation || identity !== itemKey(current)) return;
      if (!result.item?.question || result.item.state !== "question") throw new Error("The prepared card could not be confirmed.");
      replaceItem(result.item);
      current = result.item;
      showCard(true);
      announce("Review card prepared. Recall the answer before revealing it.");
    } catch (error) {
      if (id !== operation) return;
      if (conflict(error)) showConflict();
      else {
        const needsSource = error.payload?.state === "needs_source" || error.payload?.code === "missing_source";
        setRecovery(needsSource ? "More course material is needed." : "Could not prepare this card.",
          prepare, "Try again", {primary: true, missingSource: needsSource});
      }
    } finally { if (id === operation) lock(false); }
  }
  function showAnswer() {
    mode = "revealed";
    clearFeedback();
    panel.querySelector("[data-review-recovery]")?.remove();
    panel.querySelector(".review-footer")?.remove();
    const answer = node("section", undefined, "review-answer");
    answer.setAttribute("aria-label", "Answer and explanation");
    appendProse(answer, revealed.answer, "review-answer-text", "answer");
    if (revealed.explanation) appendProse(answer, revealed.explanation, "review-explanation", "explanation");
    const target = answer.querySelector(".review-answer-text");
    target.tabIndex = -1;
    target.dataset.reviewAnswer = "";
    const ratings = node("div", undefined, "review-ratings");
    for (const result of results) {
      const preview = revealed.ratings.find((rating) => rating.result === result);
      const control = button("", () => grade(result), "secondary-action review-rating");
      control.dataset.reviewGrade = result;
      const shortcut = String(results.indexOf(result) + 1);
      const description = `${preview.label}, ${preview.interval}. ${meanings[result]}. Shortcut ${shortcut}.`;
      control.setAttribute("aria-label", description);
      control.setAttribute("aria-keyshortcuts", shortcut);
      control.title = description;
      control.append(node("span", preview.label, "review-rating-label"),
        node("span", preview.interval, "review-rating-interval"));
      ratings.append(control);
    }
    panel.append(answer);
    addSkip(ratings);
    focusHeading(target, "start");
    announce("Answer and explanation revealed. Choose a rating.");
  }
  async function reveal() {
    if (busy || mode !== "question") return;
    const id = ++operation;
    const identity = itemKey(current);
    clearFeedback();
    setPrimaryLabel("Show answer and explanation");
    lock(true);
    try {
      const result = await post("/api/review/reveal", payload());
      if (id !== operation || identity !== itemKey(current)) return;
      if (!result.answer || !result.reveal_token || !Array.isArray(result.ratings)
        || !results.every((value) => result.ratings.some((rating) => rating.result === value && rating.interval && rating.label))) {
        throw new Error("The reference answer or review intervals could not be loaded.");
      }
      revealed = result;
      showAnswer();
    } catch (error) {
      if (id !== operation) return;
      if (conflict(error)) showConflict();
      else setRecovery("Could not show this answer.", reveal, "Try again", {primary: true});
    } finally { if (id === operation) lock(false); }
  }
  async function grade(result) {
    if (busy || (mode !== "revealed" && mode !== "uncertain")) return;
    if (!pending) pending = {...payload(), reveal_token: revealed.reveal_token, result, submission_id: crypto.randomUUID()};
    const submitted = pending;
    const previous = current;
    const id = ++operation;
    clearFeedback();
    lock(true);
    announce("Saving your review rating.");
    try {
      const receipt = await post("/api/review", submitted);
      if (id !== operation || pending !== submitted) return;
      if (receipt.state !== "committed" || receipt.submission_id !== submitted.submission_id) {
        throw new Error("The server did not confirm a committed rating.");
      }
      receipts.add(receipt.submission_id);
      reviewedCards.add(itemKey(previous));
      replaceItem(receipt.item, previous);
      pending = null;
      current = null;
      showNext(true);
      announce("Review rating saved and schedule updated.");
    } catch (error) {
      if (id !== operation || pending !== submitted) return;
      if (conflict(error)) showConflict();
      else {
        mode = "uncertain";
        setRecovery("Could not confirm this rating was saved.", () => grade(submitted.result), "Retry saving");
      }
    } finally {
      if (id === operation) {
        lock(false);
        if (pending) {
          for (const control of panel.querySelectorAll("[data-review-grade], .review-footer button")) control.disabled = true;
          if (document.activeElement === document.body || document.activeElement.matches?.("[data-review-grade]")) {
            focusHeading(panel.querySelector("[data-review-retry]"));
          }
        }
      }
    }
  }
  function showNext(focus = false) {
    clearTimeout(timer);
    clearFeedback();
    updateSummary();
    current = items.find((item) => !skipped.has(itemKey(item)) && !waiting(item));
    if (current) { showCard(focus); return; }
    mode = "rest";
    clearActionDock();
    panel.replaceChildren();
    const waitingItems = items.filter(waiting);
    let title;
    if (waitingItems.length) {
      title = heading("Your next card returns soon");
      const at = Math.min(...waitingItems.map((item) => Date.parse(item.relearn_at)));
      const time = node("time", new Date(at).toLocaleTimeString([], {hour: "numeric", minute: "2-digit", second: "2-digit"}));
      time.dateTime = new Date(at).toISOString();
      const line = node("p", "Next review at ");
      line.append(time);
      const countdown = node("p", "", "review-countdown");
      countdown.dataset.reviewCountdown = "";
      panel.append(title, line, countdown, node("p", "You can leave and resume later.", "quiet-copy"));
      const tick = () => {
        if (mode !== "rest") return;
        const seconds = Math.max(0, Math.ceil((at - now()) / 1000));
        countdown.textContent = seconds ? `Returns in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : "Checking which cards are ready…";
        if (seconds) timer = setTimeout(tick, 1000);
        else refresh(true);
      };
      tick();
    } else if (items.length) {
      title = heading("Reviews remain due");
      panel.append(title, node("p", "You skipped these cards for now. Their review schedules have not changed."));
    } else {
      title = heading(hadItems || reviewedCards.size ? "Review complete" : "Nothing is due");
      panel.append(title, node("p", reviewedCards.size ? `${reviewedCards.size} distinct card${reviewedCards.size === 1 ? "" : "s"} reviewed. Come back when more are due.` : "Keep learning and your review tray will update."));
    }
    if (items.some((item) => skipped.has(itemKey(item)))) {
      if (waitingItems.length) panel.append(node("p", "Skipped reviews also remain due.", "quiet-copy"));
      panel.append(button("Try skipped cards again", () => {
        if (busy) return;
        skipped.clear();
        showNext(true);
      }));
    }
    if (focus) focusHeading(title);
  }
  async function refresh(focus = false) {
    if (busy || pending) return;
    clearTimeout(timer);
    const id = ++operation;
    clearFeedback();
    lock(true);
    try {
      const next = await requestJson(sessionUrl());
      if (id !== operation) return;
      if (!Array.isArray(next.items)) throw new Error("The review queue could not be confirmed.");
      items = next.items;
      const offset = Date.parse(next.server_time) - Date.now();
      if (Number.isFinite(offset)) clockOffset = offset;
      showNext(focus);
    } catch (error) {
      if (id === operation) setRecovery(mode === "conflict" ? "This card changed. Refresh could not be completed." : "Could not refresh this review.", () => refresh(true), "Retry refresh");
    } finally { if (id === operation) lock(false); }
  }
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.repeat || event.isComposing || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey
      || event.target.closest?.("button, a, input, textarea, select, summary, [contenteditable]:not([contenteditable='false']), [role='button']") || busy) return;
    if (event.code === "Space" && mode === "question") { event.preventDefault(); reveal(); }
    else if (mode === "revealed" && results[Number(event.key) - 1]) {
      event.preventDefault();
      grade(results[Number(event.key) - 1]);
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && mode === "rest") refresh(true);
  });
  showNext();
}
initializeReview();

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

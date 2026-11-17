/* Suggestions remain separate until explicit review; all model text is textContent. */
let aiTargets = null;
let aiJobId = null;
let aiPollTimer = null;
let aiSnapshot = null;
let aiOffset = 0;
const aiSelected = new Set();
const aiTerminal = new Set(["completed", "completed_with_errors", "failed"]);

function aiElement(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function aiButton(text, action) {
  const button = aiElement("button", text, "button button-secondary");
  button.type = "button";
  button.addEventListener("click", action);
  return button;
}

const aiConfig = aiElement("dialog", null, "slide-dialog");
aiConfig.id = "ai-config-dialog";
aiConfig.setAttribute("aria-label", "Generate local AI metadata");
aiConfig.innerHTML = `<h2>Generate AI Metadata</h2><p>Suggestions require review. Manual and approved values are protected.</p>
  <form id="ai-config-form" class="metadata-form">
    <label>Installed model<select id="ai-model" required></select></label>
    <label>Timeout per request (seconds)<input id="ai-timeout" type="number" min="10" max="1800" required></label>
    <label>Context length<input id="ai-context" type="number" min="4096" max="32768" required></label>
    <label>Concurrent requests to this model<input id="ai-concurrency" type="number" min="1" max="4" required></label>
    <label>Retries for transient failures<input id="ai-retries" type="number" min="0" max="3" required value="1"></label>
    <label><input id="ai-include-slides" type="checkbox" checked> Analyse individual slides too</label>
    <label><input id="ai-force" type="checkbox"> Regenerate unchanged content</label>
    <p id="ai-config-error" role="alert"></p>
    <div class="management-actions"><button type="button" id="ai-config-cancel" class="button button-secondary">Cancel</button><button type="submit" class="button button-primary">Generate suggestions</button></div>
  </form>`;
document.body.append(aiConfig);
document.getElementById("ai-config-cancel").addEventListener("click", () => aiConfig.close());

async function configureAI(targets, force = false) {
  aiTargets = targets;
  document.getElementById("ai-force").checked = force;
  document.getElementById("ai-include-slides").checked = !targets.slide_ids;
  aiConfig.showModal();
  const error = document.getElementById("ai-config-error");
  error.textContent = "Loading installed models…";
  document.querySelector('#ai-config-form button[type=submit]').disabled = true;
  try {
    const result = await fetchJSON("/api/enrichment/models");
    const select = document.getElementById("ai-model");
    select.replaceChildren(...result.models.map((model) => new Option(model.name, model.name)));
    if (result.models.some((model) => model.name === result.default_model)) select.value = result.default_model;
    document.getElementById("ai-timeout").value = result.defaults.timeout_seconds;
    document.getElementById("ai-context").value = result.defaults.context_length;
    document.getElementById("ai-concurrency").value = result.defaults.concurrency;
    document.getElementById("ai-retries").value = result.defaults.retries ?? 1;
    error.textContent = result.models.length ? "Sequential processing is recommended for 32 GB RAM. Other loaded models must be unloaded first." : "Install qwen3:4b or qwen2.5-coder:14b in local Ollama first.";
    document.querySelector('#ai-config-form button[type=submit]').disabled = !result.models.length;
  } catch (failure) { error.textContent = failure.message; }
}

document.getElementById("ai-config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type=submit]');
  button.disabled = true;
  try {
    const job = await fetchJSON("/api/enrichment/jobs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({
      ...aiTargets, model: document.getElementById("ai-model").value,
      timeout_seconds: Number(document.getElementById("ai-timeout").value), context_length: Number(document.getElementById("ai-context").value),
      concurrency: Number(document.getElementById("ai-concurrency").value), include_slides: document.getElementById("ai-include-slides").checked,
      retries: Number(document.getElementById("ai-retries").value),
      force: document.getElementById("ai-force").checked,
    })});
    aiConfig.close();
    aiOffset = 0;
    aiSelected.clear();
    document.querySelectorAll("[data-ai-result]").forEach((input) => { input.checked = false; });
    await pollAI(job.job_id);
  } catch (failure) { document.getElementById("ai-config-error").textContent = failure.message; }
  finally { button.disabled = false; }
});

const aiPanel = aiElement("section", null, "ai-progress hidden");
aiPanel.id = "ai-progress";
aiPanel.setAttribute("aria-label", "AI enrichment progress");
aiPanel.innerHTML = `<h2>Local AI enrichment</h2><p id="ai-job-progress" role="status"></p><p id="ai-job-current"></p><p>Elapsed <time id="ai-job-elapsed">00:00:00</time></p>
 <progress id="ai-overall-progress" max="100" aria-label="AI enrichment progress"></progress><p id="ai-progress-stage"></p><p id="ai-job-error" role="alert"></p><ul id="ai-job-items"></ul>`;
const aiBatch = aiButton("Accept selected suggestions", () => reviewAI([...aiSelected], "accept"));
let aiPendingJob = [];
const aiAcceptAll = aiButton("Accept all pending suggestions in this job (0)", () => reviewAI([], "accept", null, false, aiJobId));
const aiPrevious = aiButton("Previous results", () => { aiOffset = Math.max(0, aiOffset - 50); if (aiJobId) pollAI(aiJobId); });
const aiNext = aiButton("Next results", () => { aiOffset += 50; if (aiJobId) pollAI(aiJobId); });
aiPanel.append(aiPrevious, aiNext, aiBatch, aiAcceptAll);
document.querySelector("main").prepend(aiPanel);

async function pollAI(id) {
  clearTimeout(aiPollTimer);
  if (id !== aiJobId) {
    aiSnapshot = null;
    document.getElementById("ai-job-progress").textContent = "Loading AI job…";
    document.getElementById("ai-job-elapsed").textContent = "00:00:00";
  }
  aiJobId = id;
  try { localStorage.setItem("slide-navigator:ai-job", id); } catch { /* Optional storage. */ }
  aiPanel.classList.remove("hidden");
  try {
    const job = await fetchJSON(`/api/enrichment/jobs/${encodeURIComponent(id)}`);
    if (id !== aiJobId) return;
    aiSnapshot = {seconds: job.elapsed_seconds, received: performance.now(), running: !aiTerminal.has(job.status)};
    const bar=document.getElementById('ai-overall-progress');
    if(job.progress_percent === null)bar.removeAttribute('value');else bar.value=job.progress_percent;
    document.getElementById('ai-progress-stage').textContent=`${job.stage} · ${job.processed} / ${job.total}${job.progress_percent === null ? '' : ` (${Math.round(job.progress_percent)}%)`}`;
    document.getElementById("ai-job-progress").textContent = `${job.model}: ${job.status} · Decks ${job.deck_progress.processed}/${job.deck_progress.total} · Slides ${job.slide_progress.processed}/${job.slide_progress.total} · ${job.counts.completed} completed · ${job.counts.failed} failed · ${job.counts.skipped} skipped`;
    document.getElementById("ai-job-current").textContent = job.current ? `${job.current.deck}${job.current.slide ? ` · slide ${job.current.slide}` : " · deck analysis"}` : "";
    document.getElementById("ai-job-error").textContent = job.error || "";
    const list = document.getElementById("ai-job-items");
    list.replaceChildren();
    for (const item of job.items.slice(aiOffset, aiOffset + 50)) {
      const entry = aiElement("li");
      if (item.result_id) {
        const input = aiElement("input");
        input.type = "checkbox";
        input.dataset.aiResult = item.result_id;
        input.checked = aiSelected.has(item.result_id);
        input.setAttribute("aria-label", `Select ${item.entity_type} suggestion for approval`);
        input.addEventListener("change", () => { if (input.checked) aiSelected.add(item.result_id); else aiSelected.delete(item.result_id); });
        entry.append(input, aiButton(`Review ${item.entity_type} suggestion`, () => item.entity_type === "deck" ? openDeckDetails(item.entity_id) : openSlideDetails(item.entity_id)));
      }
      entry.append(aiElement("span", `${item.entity_type}: ${item.status}${item.error ? ` — ${item.error}` : ""}`));
      list.append(entry);
    }
    aiPrevious.disabled = !aiOffset;
    aiPendingJob = [...new Set(job.items.filter((item) => item.review_decision === "pending" && item.result_id).map((item) => item.result_id))];
    aiAcceptAll.textContent = `Accept all pending suggestions in this job (${aiPendingJob.length})`;
    aiAcceptAll.disabled = !aiTerminal.has(job.status) || !aiPendingJob.length;
    aiNext.disabled = aiOffset + 50 >= job.items.length;
    if (!aiTerminal.has(job.status)) aiPollTimer = setTimeout(() => pollAI(id), 1200);
    else {
      if (activeDeckId) loadAISuggestions("deck", activeDeckId);
      if (activeSlideId) loadAISuggestions("slide", activeSlideId);
    }
  } catch (error) {
    document.getElementById("ai-job-error").textContent = error.message;
    if (error.status !== 404) aiPollTimer = setTimeout(() => pollAI(id), 2500);
  }
}
setInterval(() => {
  if (!aiSnapshot) return;
  document.getElementById("ai-job-elapsed").textContent = formatImportElapsed(aiSnapshot.seconds + (aiSnapshot.running ? (performance.now() - aiSnapshot.received) / 1000 : 0));
}, 1000);

async function reviewAI(ids, action, edits = null, approveNewTags = false, jobScope = null) {
  if (!ids.length && !jobScope) return managementMessage("Select suggestions to review.", true);
  try {
    const result = await fetchJSON("/api/enrichment/review", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({result_ids: ids, job_id: jobScope, action, edits, approve_new_tags: approveNewTags})});
    const protectedFields = result.results.flatMap((item) => item.protected_fields || []);
    managementMessage(`Suggestions ${action === "accept" ? "accepted" : "rejected"}.${protectedFields.length ? ` Kept protected fields: ${[...new Set(protectedFields)].join(", ")}.` : ""}`);
    aiSelected.clear();
    await refreshManagedLibrary();
    if (activeDeckId) { const id = activeDeckId; await openDeckDetails(id); }
    if (activeSlideId) { const id = activeSlideId; await openSlideDetails(id); }
  } catch (error) { managementMessage(error.message, true); }
}

async function loadAISuggestions(kind, id) {
  const container = document.getElementById(`${kind}-ai-suggestions`);
  if (!container) return;
  container.replaceChildren(aiElement("p", "Loading AI suggestions…"));
  try {
    const entity = await fetchJSON(`/api/${kind === "deck" ? "decks" : "slides"}/${encodeURIComponent(id)}`);
    labelMetadataStates(kind, entity);
    const result = await fetchJSON(`/api/enrichment/${kind}/${encodeURIComponent(id)}/suggestions`);
    if ((kind === "deck" ? activeDeckId : activeSlideId) !== id) return;
    container.replaceChildren();
    if (!result.items.length) container.append(aiElement("p", "No AI suggestions generated. Existing metadata is retained."));
    for (const item of result.items) {
      const suggestion = item.content.suggestion;
      const card = aiElement("article", null, "ai-suggestion");
      card.dataset.aiSuggestion = item.id;
      card.append(aiElement("strong", `${item.model} · ${item.decision} · ${formatDate(item.created_at)}`));
      card.append(aiElement("p", "AI suggestions — unverified. Manual and approved metadata will be retained."));
      const title = aiElement("input"); title.value = suggestion.title; title.maxLength = 250;
      const titleLabel = aiElement("label", "Suggested title"); titleLabel.append(title);
      const description = aiElement("textarea"); description.value = suggestion.description; description.rows = 4; description.maxLength = 20000;
      const descLabel = aiElement("label", "Suggested summary / description"); descLabel.append(description);
      const organisation = aiElement("input"); organisation.value = suggestion.organisation || ""; organisation.maxLength = 200;
      const orgLabel = aiElement("label", "Suggested organisation (content evidence required)"); orgLabel.append(organisation);
      card.append(titleLabel, descLabel);
      const extra = {};
      for (const field of (kind === "deck" ? ["purpose", "industry", "keywords"] : ["industry", "keywords"])) {
        const label = aiElement("label", `Suggested ${field}`);
        const input = aiElement("input"); input.value = field === "keywords" ? suggestion.keywords.join(", ") : suggestion[field] || "";
        input.maxLength = field === "purpose" ? 2000 : field === "industry" ? 200 : 10000;
        label.append(input); card.append(label); extra[field] = input;
      }
      if (kind === "deck") card.append(orgLabel);
      if (suggestion.limited) card.append(aiElement("p", `Limited analysis: ${suggestion.limitation}`, "metadata-error"));
      card.append(aiElement("p", `Keywords / themes: ${suggestion.keywords.join(", ")}`));
      const tagGroup = aiElement("fieldset"); tagGroup.append(aiElement("legend", "Suggested canonical tags (separate from approved assignments)"));
      const allTags = filterOptions.tags || [];
      for (const tag of allTags.filter((tag) => tagCategories(kind).includes(tag.tag_type))) {
        const label = aiElement("label"); const check = aiElement("input"); check.type = "checkbox"; check.value = tag.id; check.checked = suggestion.tag_ids.includes(tag.id);
        label.append(check, document.createTextNode(`${tag.tag_type}: ${tag.name}`)); tagGroup.append(label);
      }
      card.append(tagGroup);
      const approve = aiElement("input"); approve.type = "checkbox";
      const approveLabel = aiElement("label", `Approve creation of proposed new tags: ${suggestion.new_tags.map((tag) => `${tag.tag_type}: ${tag.name}`).join(", ") || "None"}`); approveLabel.prepend(approve);
      card.append(approveLabel);
      if (item.decision === "pending") {
        const accept = () => reviewAI([item.id], "accept", {...suggestion, title: title.value, description: description.value,
          purpose: extra.purpose?.value || null, industry: extra.industry.value || null,
          keywords: extra.keywords.value.split(",").map((word) => word.trim()).filter(Boolean),
          organisation: kind === "deck" ? organisation.value || null : null, tag_ids: [...tagGroup.querySelectorAll("input:checked")].map((input) => input.value)}, approve.checked);
        card.append(aiButton("Accept", accept), aiButton("Edit", () => { title.focus(); description.scrollIntoView({block: "nearest"}); }),
          aiButton("Reject", () => reviewAI([item.id], "reject")));
      }
      card.append(aiButton("Regenerate", () => configureAI(kind === "deck" ? {deck_ids: [id]} : {slide_ids: [id]}, true)));
      container.append(card);
    }
  } catch (error) { container.replaceChildren(aiElement("p", error.message)); }
}

for (const kind of ["deck", "slide"]) {
  const section = aiElement("section", null, "ai-review");
  section.id = `${kind}-ai-review`;
  section.append(aiElement("h3", "AI metadata suggestions"), aiButton("Generate AI Metadata", () => {
    const id = kind === "deck" ? activeDeckId : activeSlideId;
    if (id) configureAI(kind === "deck" ? {deck_ids: [id]} : {slide_ids: [id]});
  }));
  const suggestions = aiElement("div"); suggestions.id = `${kind}-ai-suggestions`;
  const later = aiButton("Review later", () => {
    suggestions.hidden = !suggestions.hidden;
    later.textContent = suggestions.hidden ? "Show suggestions" : "Review later";
    later.setAttribute("aria-expanded", String(!suggestions.hidden));
    managementMessage("Suggestions retained for later review; existing metadata is unchanged.");
  });
  later.id = `${kind}-ai-review-later`;
  later.setAttribute("aria-controls", suggestions.id);
  later.setAttribute("aria-expanded", "true");
  section.append(later);
  section.append(suggestions);
  document.getElementById(`${kind}-edit-form`).after(section);
  for (const field of (kind === "deck" ? ["purpose", "industry", "keywords"] : ["industry", "keywords"])) {
    const label = aiElement("label", field[0].toUpperCase() + field.slice(1));
    const input = aiElement("input"); input.id = `${kind}-edit-${field}`; input.maxLength = field === "purpose" ? 2000 : field === "industry" ? 200 : 10000;
    label.append(input); document.querySelector(`#${kind}-edit-form .metadata-form-footer`).before(label);
  }
}
const aiSelectedDecks = aiButton("Generate AI Metadata for Selected Decks", () => {
  if (!selectedDecks.size) return managementMessage("Select at least one deck.", true);
  configureAI({deck_ids: [...selectedDecks.keys()]});
});
aiSelectedDecks.id = "generate-selected-ai";
document.querySelector("#page-decks .library-toolbar").append(aiSelectedDecks,
  aiButton("Enrich Library", () => configureAI({all_decks: true})));
const aiSelectedSlides = aiButton("Generate AI Metadata for Selected Slides", () => {
  if (!selectedSlides.size) return managementMessage("Select at least one slide.", true);
  configureAI({slide_ids: selectedSlides.values().map((slide) => slide.id)});
});
aiSelectedSlides.id = "generate-selected-slides-ai";
document.querySelector("#page-slides .selection-toolbar").append(aiSelectedSlides);

function populateExtraMetadata(kind, entity) {
  for (const field of (kind === "deck" ? ["purpose", "industry", "keywords"] : ["industry", "keywords"])) {
    document.getElementById(`${kind}-edit-${field}`).value = field === "keywords" ? (entity.keywords || []).join(", ") : entity[field] || "";
  }
}

function labelMetadataStates(kind, entity) {
  const names = {approved: "Approved metadata", user_edited: "User edited / overridden", existing_unreviewed: "Existing metadata — not reviewed"};
  for (const [field, state] of Object.entries(entity.metadata_states || {})) {
    const input = document.getElementById(`${kind}-edit-${field}`);
    if (!input) continue;
    const label = input.closest("label");
    let badge = label.querySelector("[data-metadata-state]");
    if (!badge) { badge = aiElement("small"); badge.dataset.metadataState = field; label.append(badge); }
    badge.className = `metadata-origin ${state}`;
    badge.textContent = names[state] || state;
    if (!input.dataset.originListener) {
      input.dataset.originListener = "true";
      input.addEventListener("input", () => {
        const marker = input.closest("label").querySelector("[data-metadata-state]");
        marker.textContent = "User edit — unsaved"; marker.className = "metadata-origin user_edited";
      });
    }
  }
}

const importAI = aiElement("div", null, "post-import-ai");
importAI.innerHTML = `<label><input id="import-ai-enabled" type="checkbox"> Generate AI suggestions automatically after import (optional)</label>
 <label>AI model<select id="import-ai-model" disabled><option value="">Enable the optional step to load installed models</option></select></label>
 <p>Suggestions require review; tags are not accepted and approved metadata is not overwritten.</p><p id="post-import-ai-status" role="status"></p>`;
document.querySelector(".storage-control").after(importAI);
let importAIOptions = null;
document.getElementById("import-ai-enabled").addEventListener("change", async (event) => {
  const select = document.getElementById("import-ai-model");
  select.disabled = true; importAIOptions = null;
  if (!event.target.checked) return;
  try {
    const state = await fetchJSON("/api/enrichment/models");
    select.replaceChildren(...state.models.map((model) => new Option(model.name, model.name)));
    if (state.models.some((model) => model.name === state.default_model)) select.value = state.default_model;
    importAIOptions = state.defaults;
    select.disabled = !state.models.length;
    document.getElementById("post-import-ai-status").textContent = state.models.length ? "Optional AI step enabled for newly imported decks." : "No supported model is installed. Disable this optional step to import normally.";
  } catch (error) { document.getElementById("post-import-ai-status").textContent = error.message; }
});
function postImportSettings() {
  if (!document.getElementById("import-ai-enabled").checked) return null;
  const model = document.getElementById("import-ai-model").value;
  if (!model || !importAIOptions) {
    showImportMessage("Choose an available AI model or disable the optional AI step.", "error"); return undefined;
  }
  return {...importAIOptions, model, include_slides: true};
}
const aiHistory = aiElement("select"); aiHistory.setAttribute("aria-label", "AI job history"); aiHistory.append(new Option("AI job history", ""));
aiHistory.addEventListener("focus", async () => {
  try { const history = await fetchJSON("/api/enrichment/jobs"); aiHistory.replaceChildren(new Option("AI job history", ""), ...history.items.map((job) => new Option(`${job.model}: ${job.status} · ${formatDate(job.created_at)}`, job.id))); } catch (error) { managementMessage(error.message, true); }
});
aiHistory.addEventListener("change", () => { if (aiHistory.value) { aiOffset = 0; pollAI(aiHistory.value); } });
document.querySelector("#page-decks .library-toolbar").append(aiHistory);
document.addEventListener("DOMContentLoaded", async () => {
  try {
    const history = await fetchJSON("/api/enrichment/jobs");
    const active = history.items.find((job) => !aiTerminal.has(job.status));
    let id = active?.id;
    if (!id) { try { id = localStorage.getItem("slide-navigator:ai-job"); } catch { /* Optional storage. */ } }
    if (id) pollAI(id);
  } catch { /* History can be opened after reconnect. */ }
});

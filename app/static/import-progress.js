/* Read-only polling resumes jobs; reconnecting never submits files again. */
let watchedImportJob = null;
let importItemOffset = 0;
let importBusy = false;
let importPollTimer = null;
let importPollSequence = 0;
let elapsedSnapshot = null;
const terminalImportStates = new Set(["completed", "completed_with_errors", "failed"]);
const importStageLabels = {
  queued: "Waiting to start", discovering_files: "Discovering files",
  extracting_metadata: "Extracting PowerPoint metadata", extracting_slides: "Extracting slides",
  checking_duplicate: "Checking for duplicates", converting_presentation: "Converting legacy PowerPoint",
  saving_metadata: "Saving metadata and slides", generating_thumbnails: "Generating thumbnails (waiting for renderer)",
  saving_thumbnails: "Saving thumbnails", finalising_deck: "Finalising presentation",
  finalising_import: "Finalising import", finished: "Finished", failed: "Failed",
};

function formatImportElapsed(seconds) {
  const value = Math.max(0, Math.floor(seconds));
  return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60].map((part) => String(part).padStart(2, "0")).join(":");
}

function updateImportElapsed() {
  if (!elapsedSnapshot) return;
  const seconds = elapsedSnapshot.seconds + (elapsedSnapshot.running ? (performance.now() - elapsedSnapshot.received) / 1000 : 0);
  document.getElementById("import-elapsed").textContent = formatImportElapsed(seconds);
}
window.setInterval(updateImportElapsed, 1000);

function rememberImportJob(id) {
  watchedImportJob = id;
  try { localStorage.setItem("slide-navigator:last-import", id); } catch { /* Storage may be disabled. */ }
}

function beginImportUpload() {
  importBusy = true;
  clearTimeout(importPollTimer);
  importPollSequence++;
  watchedImportJob = null;
  importItemOffset = 0;
  elapsedSnapshot = null;
  document.getElementById("import-elapsed").textContent = "00:00:00";
  document.getElementById("import-stage").textContent = "Uploading files; ingestion has not started";
  document.getElementById("current-deck-name").textContent = "No current presentation";
  document.getElementById("slide-progress-bar").removeAttribute("value");
  document.getElementById("slide-progress-bar").classList.remove("hidden");
  document.getElementById("slide-progress-value").textContent = "Waiting";
  document.getElementById("slide-progress-count").textContent = "Slide total not yet known";
  document.getElementById("deck-progress-count").textContent = "Deck total will be known after archive discovery";
  document.getElementById("defer-import-review").classList.add("hidden");
  document.getElementById("import-review-panel").open = true;
}

function renderImportJob(job) {
  const terminal = terminalImportStates.has(job.status);
  importBusy = !terminal;
  document.getElementById("import-progress").classList.remove("hidden");
  document.getElementById("progress-title").textContent = terminal
    ? job.status === "failed" ? "Import failed" : "Import complete — review your decks"
    : "Processing presentations…";
  const decks = job.deck_progress;
  const overall = document.getElementById("progress-bar");
  if (job.progress_percent == null) overall.removeAttribute("value"); else overall.value = job.progress_percent;
  document.getElementById("progress-value").textContent = job.progress_percent == null ? "Discovering…" : `${job.progress_percent}%`;
  document.getElementById("deck-progress-count").textContent = decks.total_known
    ? `${decks.processed} / ${decks.total} decks processed · ${decks.successful} successful · ${decks.skipped} skipped (including duplicates) · ${decks.failed} failed`
    : `${decks.total} decks discovered so far; total not yet known`;
  const current = job.current_deck;
  document.getElementById("current-deck-name").textContent = current.filename || (terminal ? "No current presentation" : "Waiting for next presentation");
  const slideBar = document.getElementById("slide-progress-bar");
  slideBar.classList.toggle("hidden", terminal);
  if (current.percent == null) slideBar.removeAttribute("value"); else slideBar.value = current.percent;
  document.getElementById("slide-progress-value").textContent = current.percent == null ? terminal ? "—" : "Waiting" : `${current.percent}%`;
  document.getElementById("slide-progress-count").textContent = current.total == null
    ? terminal ? "Slide processing finished" : "Slide total/progress not yet known for this stage"
    : `${current.processed} / ${current.total} slides processed in this stage`;
  document.getElementById("import-stage").textContent = importStageLabels[job.stage] || job.stage;
  document.getElementById("progress-message").textContent = `${job.imported_count} imported · ${job.slide_count} slides · ${job.duplicate_count} duplicates · ${job.skipped_count} skipped files · ${job.failed_count} failed files (${job.archive_error_count} archives)`;
  elapsedSnapshot = {seconds: job.elapsed_seconds, received: performance.now(), running: !terminal && !!job.started_at};
  updateImportElapsed();
  renderJobItems(job.items);
  document.getElementById("import-items-previous").disabled = importItemOffset === 0;
  document.getElementById("import-items-next").disabled = importItemOffset + 100 >= job.items_total;
  document.getElementById("import-items-page").textContent = job.items_total ? `${importItemOffset + 1}–${Math.min(importItemOffset + 100, job.items_total)} of ${job.items_total} files` : "No files";
  document.getElementById("import-review-heading").textContent = terminal ? "Post-import review and per-file results" : "Per-file results";
  document.getElementById("defer-import-review").classList.toggle("hidden", !terminal);
}

async function watchImportJob(id) {
  rememberImportJob(id);
  clearTimeout(importPollTimer);
  const sequence = ++importPollSequence;
  try {
    const job = await fetchJSON(`/api/import/jobs/${encodeURIComponent(id)}?item_offset=${importItemOffset}&item_limit=100`);
    if (sequence !== importPollSequence) return;
    renderImportJob(job);
    const aiStatus = document.getElementById("post-import-ai-status");
    if (job.enrichment_job_id) {
      aiStatus.textContent = "Optional AI suggestions queued. Review them in AI Enrichment; existing metadata is unchanged.";
      if (aiJobId !== job.enrichment_job_id) pollAI(job.enrichment_job_id);
    } else if (job.enrichment_error) aiStatus.textContent = `Import retained. ${job.enrichment_error}`;
    if (terminalImportStates.has(job.status)) {
      await Promise.all([loadImportHistory(), loadDecks(), loadSlides(), loadFilterOptions()]);
      showImportMessage(job.failed_count || job.status === "failed"
        ? "Import finished with errors. Review the per-file results; successful decks are retained."
        : "Import finished. Review your decks now or return from Import history later.", job.failed_count || job.status === "failed" ? "error" : "success");
      if (job.enrichment_options && !job.enrichment_job_id && !job.enrichment_error && job.status !== "failed") {
        aiStatus.textContent = "Import complete; preparing the optional AI step…";
        importPollTimer = window.setTimeout(() => watchImportJob(id), 800);
      }
      return;
    }
    importPollTimer = window.setTimeout(() => watchImportJob(id), 800);
  } catch (error) {
    if (sequence !== importPollSequence) return;
    if (error.status === 404) {
      importBusy = false;
      watchedImportJob = null;
      elapsedSnapshot = null;
      try { localStorage.removeItem("slide-navigator:last-import"); } catch { /* Optional storage. */ }
      showImportMessage("This import job is no longer available. Open Import history to choose another job.", "error");
      return;
    }
    showImportMessage(`Connection interrupted: ${error.message}. Retrying status; files will not be uploaded again.`, "error");
    importPollTimer = window.setTimeout(() => watchImportJob(id), 2000);
  }
}

async function restoreImportJob() {
  const sequence = importPollSequence;
  let cached;
  try { cached = localStorage.getItem("slide-navigator:last-import"); } catch { /* Optional storage. */ }
  try {
    const active = await fetchJSON("/api/import/jobs?active_only=true&limit=1");
    if (sequence !== importPollSequence) return;
    const id = active.items[0]?.id || cached;
    if (id) await watchImportJob(id);
  } catch {
    if (sequence !== importPollSequence) return;
    if (cached) await watchImportJob(cached);
    else importPollTimer = window.setTimeout(restoreImportJob, 2000);
  }
}

function populateImportMetadata(deck) {
  populateExtraMetadata("deck", deck);
  labelMetadataStates("deck", deck);
  for (const field of ["author", "subject", "subtitle"]) document.getElementById(`deck-edit-${field}`).value = deck[field] || "";
  document.getElementById("deck-edit-review-status").value = deck.review_status || "Not Reviewed";
  const provenance = deck.metadata_provenance || {};
  document.getElementById("deck-metadata-provenance").textContent = ["title", "organisation", "year", "author", "subject", "subtitle", "description"].map((field) => `${field}: ${(provenance[field]?.source || "legacy_unknown").replaceAll("_", " ")}`).join(" · ");
  document.getElementById("deck-raw-metadata").textContent = JSON.stringify({original: deck.source_metadata, provenance, reviewed_at: deck.reviewed_at}, null, 2);
}

document.getElementById("mark-deck-reviewed").addEventListener("click", () => {
  document.getElementById("deck-edit-review-status").value = "Reviewed";
  document.getElementById("deck-edit-form").requestSubmit();
});
document.getElementById("defer-import-review").addEventListener("click", () => {
  document.getElementById("import-review-panel").open = false;
  showImportMessage("Review deferred. Imported decks are saved; return through Import history or Deck Details.", "success");
});
for (const [id, delta] of [["import-items-previous", -100], ["import-items-next", 100]]) {
  document.getElementById(id).addEventListener("click", () => {
    importItemOffset = Math.max(0, importItemOffset + delta);
    if (watchedImportJob) watchImportJob(watchedImportJob);
  });
}
window.addEventListener("online", () => { if (watchedImportJob) watchImportJob(watchedImportJob); else restoreImportJob(); });

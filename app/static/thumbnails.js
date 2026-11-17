let thumbnailRetryTimer = null;
let thumbnailRetryJob = null;
document.getElementById("regenerate-thumbnails").addEventListener("click", async () => {
  if (!activeDeckId) return;
  const button = document.getElementById("regenerate-thumbnails");
  button.disabled = true;
  try {
    const job = await fetchJSON(`/api/decks/${encodeURIComponent(activeDeckId)}/thumbnails`, {method: "POST"});
    thumbnailRetryJob = job.job_id;
    pollThumbnails();
  } catch (error) {
    document.getElementById("thumbnail-retry-status").textContent = error.message;
    button.disabled = false;
  }
});

async function pollThumbnails() {
  clearTimeout(thumbnailRetryTimer);
  try {
    const job = await fetchJSON(`/api/thumbnail-jobs/${encodeURIComponent(thumbnailRetryJob)}`);
    const message = job.status === "completed" ? "Thumbnails ready."
      : job.status === "failed" ? `Thumbnail rendering failed: ${job.error}`
      : `${job.stage.replaceAll("_", " ")}: ${job.total == null ? "waiting for renderer" : `${job.processed}/${job.total} slides`}`;
    document.getElementById("thumbnail-retry-status").textContent = message;
    if (!["completed", "failed"].includes(job.status)) {
      thumbnailRetryTimer = setTimeout(pollThumbnails, 1000); return;
    }
    document.getElementById("regenerate-thumbnails").disabled = false;
    await Promise.all([loadDecks(), loadSlides()]);
    if (activeDeckId === job.deck_id) await loadDeckSlides(deckSlideState.offset);
    if (activeSlideId) {
      const slide = await fetchJSON(`/api/slides/${encodeURIComponent(activeSlideId)}`);
      document.getElementById("dialog-thumbnail").src = thumbnailURL(slide.id, slide.rendering_updated_at);
    }
    managementMessage(message, job.status === "failed");
  } catch (error) {
    document.getElementById("thumbnail-retry-status").textContent = `${error.message}. Retrying status…`;
    thumbnailRetryTimer = setTimeout(pollThumbnails, 2000);
  }
}

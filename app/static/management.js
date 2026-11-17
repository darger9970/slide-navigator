/* Library-only deletion and immediate canonical tag assignment. */
const selectedDecks = new Map();
let pendingDeletion = null;
let newTagTarget = null;

function managementMessage(message, error = false) {
  const status = document.getElementById("library-management-status");
  status.textContent = message;
  status.className = `management-status${error ? " error" : ""}`;
}

function deckThumbnail(deck) {
  const label = document.createElement("label");
  label.className = "deck-thumbnail";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.dataset.selectDeck = deck.id;
  checkbox.checked = selectedDecks.has(deck.id);
  checkbox.setAttribute("aria-label", `Select deck ${deck.name || deck.original_filename}`);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) selectedDecks.set(deck.id, deck); else selectedDecks.delete(deck.id);
    updateDeckSelectionUI();
  });
  label.append(checkbox);
  if (deck.cover_slide_id) {
    const image = document.createElement("img");
    image.src = thumbnailURL(deck.cover_slide_id, deck.cover_thumbnail_version);
    image.alt = `${deck.name} cover preview`;
    image.loading = "lazy";
    label.append(image);
  } else {
    const empty = document.createElement("span");
    empty.textContent = "No slides";
    label.append(empty);
  }
  return label;
}

function createDeckCard(deck) {
  const card = document.createElement("article");
  card.className = "slide-card deck-card";
  card.dataset.deckCard = deck.id;
  const details = document.createElement("div");
  details.className = "slide-card-details";
  const title = document.createElement("strong");
  title.textContent = deck.name || deck.original_filename;
  const count = document.createElement("span");
  count.textContent = `${deck.slide_count} slides`;
  const preview = document.createElement("button");
  preview.type = "button";
  preview.className = "text-button";
  preview.textContent = "Preview / edit";
  preview.addEventListener("click", () => openDeckDetails(deck.id));
  details.append(title, count, inlineTagEditor("decks", deck), preview);
  card.append(deckThumbnail(deck), details);
  return card;
}

function updateDeckSelectionUI() {
  const size = selectedDecks.size;
  document.getElementById("selected-deck-count").textContent = `${size} decks selected`;
  document.getElementById("clear-deck-selection").disabled = !size;
  const button = document.getElementById("delete-decks");
  button.disabled = !size;
  button.textContent = `Delete Selected (${size})`;
  document.querySelectorAll("[data-select-deck]").forEach((input) => {
    input.checked = selectedDecks.has(input.dataset.selectDeck);
  });
  document.querySelectorAll("[data-deck-card]").forEach((card) => {
    card.classList.toggle("is-selected", selectedDecks.has(card.dataset.deckCard));
  });
}

async function refreshManagedLibrary() {
  await loadFilterOptions();
  // Remove structured filters whose source deck was deleted.
  if (slideState.deckId && !filterOptions.decks.some((deck) => deck.id === slideState.deckId)) {
    slideState.deckId = null;
    document.getElementById("slide-deck-filter").value = "";
  }
  for (const [kind, state] of [["deck", deckState], ["slide", slideState]]) {
    if (state.organisationId && !filterOptions.organisations.some((org) => org.id === state.organisationId)) state.organisationId = "";
    if (state.year && !filterOptions.years.some((year) => String(year) === String(state.year))) state.year = "";
    document.getElementById(`${kind}-organisation-filter`).value = state.organisationId;
    document.getElementById(`${kind}-year-filter`).value = state.year;
  }
  await Promise.all([loadDecks(), loadSlides()]);
}

async function requestLibraryDeletion(kind) {
  const ids = kind === "decks" ? [...selectedDecks.keys()] : selectedSlides.values().map((slide) => slide.id);
  if (!ids.length) return;
  const button = document.getElementById(`delete-${kind}`);
  button.disabled = true;
  try {
    const counts = await fetchJSON(`/api/library/${kind}/deletion-preview`, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ids}),
    });
    pendingDeletion = {kind, ids, expected_slide_count: counts.slide_count};
    document.getElementById("delete-library-summary").textContent = kind === "decks"
      ? `Delete ${counts.deck_count} decks and their ${counts.slide_count} associated slides from the library?`
      : `Delete ${counts.slide_count} slides from the library?`;
    document.getElementById("delete-library-error").textContent = "";
    document.getElementById("delete-library-dialog").showModal();
  } catch (error) { managementMessage(error.message, true); }
  finally { button.disabled = false; }
}

function inlineTagEditor(kind, entity) {
  const container = document.createElement("div");
  container.className = "inline-tag-editor";
  container.dataset.inlineKind = kind;
  container.dataset.inlineId = entity.id;
  const badges = tagBadges(entity.tags);
  badges.querySelectorAll(".tag-badge").forEach((badge, index) => {
    const tag = entity.tags[index];
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove tag ${tag.name}`);
    remove.addEventListener("click", () => saveInlineTag(kind, entity, tag.id, true, container));
    badge.append(remove);
  });
  const select = document.createElement("select");
  select.setAttribute("aria-label", `Add tag to ${entity.name || entity.title || "slide"}`);
  select.dataset.addTag = entity.id;
  const populate = () => {
    select.replaceChildren(new Option("Add Tag…", ""));
    const assigned = new Set((entity.tags || []).map((tag) => tag.id));
    const allowed = tagCategories(kind === "decks" ? "deck" : "slide");
    for (const tag of filterOptions.tags || []) {
      if (allowed.includes(tag.tag_type) && !assigned.has(tag.id)) {
        select.append(new Option(`${taxonomyLabels[tag.tag_type]}: ${tag.name}`, tag.id));
      }
    }
    select.append(new Option("Create custom tag…", "__new__"));
  };
  populate();
  select.addEventListener("focus", populate);
  select.addEventListener("change", () => {
    const value = select.value;
    select.value = "";
    if (value === "__new__") openNewTag(kind, entity, container);
    else if (value) saveInlineTag(kind, entity, value, false, container);
  });
  container.append(badges, select);
  return container;
}

async function saveInlineTag(kind, entity, tagId, remove, container) {
  container.querySelectorAll("button, select").forEach((control) => { control.disabled = true; });
  try {
    const result = await fetchJSON(`/api/${kind}/${encodeURIComponent(entity.id)}/tags/${encodeURIComponent(tagId)}`, {
      method: remove ? "DELETE" : "PUT",
    });
    entity.tags = result.tags;
    if (kind === "slides") selectedSlides.refresh(entity);
    await refreshManagedLibrary();
    if (kind === "decks" && activeDeckId === entity.id && document.getElementById("deck-dialog").open) populateTagEditor("deck", entity);
    if (kind === "slides" && activeSlideId === entity.id && document.getElementById("slide-dialog").open) populateTagEditor("slide", entity);
    managementMessage(remove ? "Tag assignment removed." : "Tag assigned.");
    const editor = [...document.querySelectorAll(".inline-tag-editor")].find((item) =>
      item.dataset.inlineKind === kind && item.dataset.inlineId === entity.id && item.checkVisibility());
    editor?.querySelector("select")?.focus();
    return true;
  } catch (error) {
    managementMessage(error.message, true);
    return false;
  } finally { container.querySelectorAll("button, select").forEach((control) => { control.disabled = false; }); }
}

function openNewTag(kind, entity, container) {
  newTagTarget = {kind, entity, container};
  const select = document.getElementById("new-tag-category");
  select.replaceChildren();
  for (const category of tagCategories(kind === "decks" ? "deck" : "slide")) {
    select.append(new Option(taxonomyLabels[category], category));
  }
  document.getElementById("new-tag-name").value = "";
  document.getElementById("new-tag-error").textContent = "";
  document.getElementById("new-tag-dialog").showModal();
  document.getElementById("new-tag-name").focus();
}

document.getElementById("new-tag-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const tag = await fetchJSON("/api/tags", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name: document.getElementById("new-tag-name").value,
        tag_type: document.getElementById("new-tag-category").value})});
    const {kind, entity, container} = newTagTarget;
    if (await saveInlineTag(kind, entity, tag.id, false, container)) document.getElementById("new-tag-dialog").close();
    else document.getElementById("new-tag-error").textContent = "Tag created but assignment failed. Try again or choose the existing tag.";
  } catch (error) { document.getElementById("new-tag-error").textContent = error.message; }
  finally { button.disabled = false; }
});

document.getElementById("confirm-library-delete").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  const {kind, ids, expected_slide_count} = pendingDeletion;
  try {
    const result = await fetchJSON(`/api/library/${kind}/delete`, {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ids, expected_slide_count})});
    if (kind === "decks") ids.forEach((id) => selectedDecks.delete(id));
    for (const slide of selectedSlides.values()) {
      if ((kind === "decks" && ids.includes(slide.deck_id)) || (kind === "slides" && ids.includes(slide.id))) selectedSlides.remove(slide.id);
    }
    selectionChanged();
    renderBuilderStatus();
    for (const id of ["deck-dialog", "slide-dialog"]) document.getElementById(id).close();
    document.getElementById("delete-library-dialog").close();
    pendingDeletion = null;
    await refreshManagedLibrary();
    managementMessage(kind === "decks" ? `Deleted ${result.deck_count} decks and ${result.slide_count} slides from the library.` : `Deleted ${result.slide_count} slides from the library.`);
    document.getElementById(kind === "decks" ? "deck-view-toggle" : "slide-grid-view").focus();
  } catch (error) { document.getElementById("delete-library-error").textContent = error.message; }
  finally { button.disabled = false; }
});

document.getElementById("delete-decks").addEventListener("click", () => requestLibraryDeletion("decks"));
document.getElementById("delete-slides").addEventListener("click", () => requestLibraryDeletion("slides"));
document.getElementById("cancel-library-delete").addEventListener("click", () => document.getElementById("delete-library-dialog").close());
document.getElementById("cancel-new-tag").addEventListener("click", () => document.getElementById("new-tag-dialog").close());
document.getElementById("clear-deck-selection").addEventListener("click", () => { selectedDecks.clear(); updateDeckSelectionUI(); });
document.getElementById("deck-view-toggle").addEventListener("click", (event) => {
  const button = event.currentTarget;
  const grid = button.getAttribute("aria-pressed") !== "true";
  button.setAttribute("aria-pressed", String(grid));
  button.textContent = grid ? "Table view" : "Card view";
  document.getElementById("deck-grid").classList.toggle("hidden", !grid);
  document.getElementById("deck-table-body").closest(".library-table-wrap").classList.toggle("hidden", grid);
});
document.addEventListener("library-tags-updated", () => {
  // Populate options when initial filter loading finishes after the deck request.
  document.querySelectorAll("[data-add-tag]").forEach((select) => select.dispatchEvent(new Event("focus")));
});

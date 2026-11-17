const pages = ["home", "decks", "slides", "import", "search", "builder", "tags", "settings"];
const pageSize = 50;
const pageTitles = {
  home: "Home",
  decks: "Decks",
  slides: "Slides",
  import: "Import",
  search: "Search",
  builder: "Deck Builder",
  tags: "Tag Manager",
  settings: "Settings",
};
const deckState = {
  offset: 0,
  query: "",
  total: 0,
  organisationId: "",
  year: "",
  sortBy: "imported_at",
  sortOrder: "desc",
  deckTypes: new Set(),
  slideTypes: new Set(),
};
const slideState = {
  offset: 0,
  query: "",
  deckId: null,
  organisationId: "",
  year: "",
  total: 0,
  items: [],
  slideTypes: new Set(),
  topics: new Set(),
  sortBy: "slide_number",
  sortOrder: "asc",
};
const deckSlideState = { deckId: null, offset: 0, total: 0 };
let activeSlideId = null;
let activeDeckId = null;
let filterOptions = { organisations: [], years: [], decks: [] };
let searchTimer = null;
const selectedSlides = new SlideSelection("slide-navigator-selection-v1");
let confirmedSlideOrder = null;
let deckRequest = 0;
let slideRequest = 0;

function showPage(name) {
  if (!pages.includes(name)) return;
  for (const page of pages) {
    document.getElementById(`page-${page}`).classList.toggle("hidden", page !== name);
  }
  document.querySelectorAll(".nav-link[data-page]").forEach((link) => {
    const active = link.dataset.page === name;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("menu-button").setAttribute("aria-expanded", "false");
  if (name === "decks") loadDecks();
  if (name === "slides") loadSlides();
  if (name === "import") loadImportHistory();
  if (name === "settings") refreshOllamaStatus();
  if (name === "builder") renderBuilderStatus();
  if (name === "tags") loadTaxonomy();
}

document.querySelectorAll(".nav-link[data-page], [data-go]").forEach((control) => {
  control.addEventListener("click", () => {
    const page = control.dataset.page || control.dataset.go;
    if (page) showPage(page);
  });
});
document.getElementById("header-settings").addEventListener("click", () => showPage("settings"));
document.getElementById("menu-button").addEventListener("click", () => {
  const sidebar = document.getElementById("sidebar");
  const open = sidebar.classList.toggle("open");
  document.getElementById("menu-button").setAttribute("aria-expanded", String(open));
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    document.getElementById("global-search").focus();
  }
  if (event.key === "Escape") {
    document.getElementById("sidebar").classList.remove("open");
    document.getElementById("menu-button").setAttribute("aria-expanded", "false");
  }
});

const globalSearch = document.getElementById("global-search");
document.getElementById("global-search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  document.getElementById("search-page-input").value = globalSearch.value;
  showPage("search");
  document.getElementById("search-page-input").focus();
});
document.getElementById("search-page-form").addEventListener("submit", (event) => {
  event.preventDefault();
});

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail
      : Array.isArray(body.detail) ? body.detail.map((issue) =>
        `${(issue.loc || []).filter((part) => part !== "body").join(".")}: ${issue.msg}`
      ).join("; ") : `Request failed (${response.status})`;
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return body;
}

async function loadFilterOptions() {
  try {
    filterOptions = await fetchJSON("/api/filters");
    for (const c of filterOptions.categories || []) taxonomyLabels[c.id] = c.label;
    renderTagFilters();
    populateSelect("deck-organisation-filter", "All organisations", filterOptions.organisations, (item) => item.name, (item) => item.id);
    populateSelect("slide-organisation-filter", "All organisations", filterOptions.organisations, (item) => item.name, (item) => item.id);
    populateSelect("deck-year-filter", "All years", filterOptions.years, String, String);
    populateSelect("slide-year-filter", "All years", filterOptions.years, String, String);
    populateSelect("slide-deck-filter", "All decks", filterOptions.decks, (item) => item.name, (item) => item.id);
    const datalist = document.getElementById("organisation-options");
    datalist.replaceChildren();
    for (const organisation of filterOptions.organisations) {
      const option = document.createElement("option");
      option.value = organisation.name;
      datalist.append(option);
    }
    document.getElementById("decks-error").classList.add("hidden");
    document.getElementById("slides-error").classList.add("hidden");
    document.dispatchEvent(new Event("library-tags-updated"));
  } catch (error) {
    for (const id of ["decks-error", "slides-error"]) {
      const banner = document.getElementById(id);
      banner.textContent = `Library filters could not be loaded: ${error.message}`;
      banner.className = "selection error";
    }
  }
}

function populateSelect(id, defaultLabel, items, labelFor, valueFor) {
  const select = document.getElementById(id);
  const selected = select.value;
  select.replaceChildren();
  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = defaultLabel;
  select.append(defaultOption);
  for (const item of items) {
    const option = document.createElement("option");
    option.value = valueFor(item);
    option.textContent = labelFor(item);
    select.append(option);
  }
  if ([...select.options].some((option) => option.value === selected)) {
    select.value = selected;
  }
}

function setTableMessage(container, message) {
  container.replaceChildren();
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = container.closest("table").querySelectorAll("thead th").length;
  cell.className = "table-message";
  cell.textContent = message;
  row.append(cell);
  container.append(row);
}

async function loadDecks() {
  const request = ++deckRequest;
  const tbody = document.getElementById("deck-table-body");
  const empty = document.getElementById("decks-empty");
  tbody.setAttribute("aria-busy", "true");
  empty.textContent = "Loading presentations…";
  try {
    const params = new URLSearchParams({
      offset: String(deckState.offset),
      limit: String(pageSize),
      q: deckState.query,
      sort_by: deckState.sortBy,
      sort_order: deckState.sortOrder,
    });
    if (deckState.organisationId) params.set("organisation_id", deckState.organisationId);
    if (deckState.year) params.set("year", deckState.year);
    appendTagFilters(params, deckState, "deck");
    const result = await fetchJSON(`/api/decks?${params}`);
    if (request !== deckRequest) return;
    deckState.total = result.total;
    if (!result.total) deckState.offset = 0;
    if (result.total && deckState.offset >= result.total) {
      deckState.offset = Math.floor((result.total - 1) / pageSize) * pageSize;
      return loadDecks();
    }
    const deckGrid = document.getElementById("deck-grid");
    deckGrid.replaceChildren();
    tbody.replaceChildren();
    for (const deck of result.items) {
      if (selectedDecks.has(deck.id)) selectedDecks.set(deck.id, deck);
      deckGrid.append(createDeckCard(deck));
      const row = document.createElement("tr");
      const nameCell = document.createElement("td");
      const nameButton = document.createElement("button");
      nameButton.className = "text-button";
      nameButton.textContent = deck.name || deck.original_filename;
      nameButton.addEventListener("click", () => openDeckDetails(deck.id));
      nameCell.append(deckThumbnail(deck), nameButton);
      nameCell.append(inlineTagEditor("decks", deck));
      const filenameCell = document.createElement("td");
      filenameCell.textContent = deck.original_filename;
      filenameCell.title = deck.original_filename;
      const organisationCell = document.createElement("td");
      organisationCell.textContent = deck.organisation || "—";
      const yearCell = document.createElement("td");
      yearCell.textContent = deck.year == null ? "—" : String(deck.year);
      const slidesCell = document.createElement("td");
      slidesCell.textContent = String(deck.slide_count);
      const importedCell = document.createElement("td");
      importedCell.textContent = formatDate(deck.imported_at);
      const descriptionCell = document.createElement("td");
      descriptionCell.className = "deck-description-preview";
      descriptionCell.textContent = deck.description || "—";
      descriptionCell.title = deck.description || "";
      row.append(nameCell, filenameCell, organisationCell, yearCell, slidesCell, importedCell, descriptionCell);
      tbody.append(row);
    }
    if (!result.items.length) {
      const message = document.createElement("p");
      message.textContent = "No decks match. Clear filters or import a presentation.";
      deckGrid.append(message);
    }
    document.getElementById("decks-count").textContent =
      `${result.total} presentation${result.total === 1 ? "" : "s"}`;
    empty.classList.toggle("hidden", result.total !== 0);
    empty.textContent = "No decks match. Clear filters or import a presentation.";
    document.getElementById("decks-error").classList.add("hidden");
    updateDeckSortHeaders();
    updatePagination("decks", result.total, deckState.offset);
    updateDeckSelectionUI();
  } catch (error) {
    if (request !== deckRequest) return;
    setTableMessage(tbody, error.message);
    empty.classList.add("hidden");
    document.getElementById("decks-error").textContent = error.message;
    document.getElementById("decks-error").className = "selection error";
    document.getElementById("decks-count").textContent = "Could not load presentations";
  } finally {
    tbody.removeAttribute("aria-busy");
  }
}

function thumbnailURL(id, version) {
  return `/api/slides/${encodeURIComponent(id)}/thumbnail?v=${encodeURIComponent(version || "")}`;
}

function createSlideCard(slide) {
  const card = document.createElement("article");
  card.className = "slide-card";
  const imageButton = document.createElement("button");
  imageButton.className = "slide-preview-button";
  imageButton.type = "button";
  imageButton.dataset.selectSlide = slide.id;
  imageButton.setAttribute("aria-label", `Select ${slide.deck_name}, slide ${slide.slide_number}`);
  const image = document.createElement("img");
  image.src = thumbnailURL(slide.id, slide.rendering_updated_at);
  image.alt = `Slide ${slide.slide_number} preview`;
  image.loading = "lazy";
  imageButton.append(image);
  imageButton.addEventListener("click", () => toggleSlide(slide));
  const check = document.createElement("span");
  check.className = "selection-check";
  check.textContent = "✓";
  check.setAttribute("aria-hidden", "true");
  imageButton.append(check);
  const details = document.createElement("div");
  details.className = "slide-card-details";
  const title = document.createElement("strong");
  title.textContent = slide.title || `Slide ${slide.slide_number}`;
  const titleSource = document.createElement("small");
  titleSource.className = `title-source ${slide.title_source || "fallback"}`;
  titleSource.textContent = titleSourceLabel(slide.title_source);
  const source = document.createElement("span");
  source.textContent = slide.deck_name;
  const description = document.createElement("p");
  description.className = "slide-description-preview";
  description.textContent = slide.description || "No description yet.";
  const status = document.createElement("small");
  status.className = `rendering-status ${slide.rendering_status || "unavailable"}`;
  status.textContent = slide.rendering_status === "available"
    ? "Thumbnail ready"
    : slide.rendering_status === "failed"
      ? "Thumbnail failed"
      : "Thumbnail unavailable";
  details.append(title, titleSource, source, description, status);
  details.append(inlineTagEditor("slides", slide), previewAction(slide));
  card.append(imageButton, details);
  card.dataset.slideCard = slide.id;
  return card;
}

function titleSourceLabel(source) {
  const labels = {
    placeholder: "PowerPoint title",
    inferred: "Inferred title",
    fallback: "Untitled",
    manual: "Manually edited",
    ai_suggested: "AI suggested",
  };
  return labels[source] || "Title source unavailable";
}

function setDescriptionStatus(id, status) {
  const element = document.getElementById(id);
  const label = status || "Not Reviewed";
  const classes = {
    "AI Suggested": "ai-suggested",
    Reviewed: "reviewed",
    "Manually Edited": "manually-edited",
  };
  element.className = `metadata-status ${classes[label] || ""}`;
  element.textContent = `Description status: ${label}`;
}

function appendSlideTableRow(slide) {
  const row = document.createElement("tr");
  const titleCell = document.createElement("td");
  const open = document.createElement("button");
  open.className = "text-button";
  open.textContent = slide.title || `Slide ${slide.slide_number} — Untitled`;
  open.addEventListener("click", () => openSlideDetails(slide.id));
  const number = document.createElement("small");
  number.className = `slide-row-meta title-source ${slide.title_source || "fallback"}`;
  number.textContent = `Slide ${slide.slide_number} · ${titleSourceLabel(slide.title_source)}`;
  titleCell.append(open, number);
  const deckCell = document.createElement("td");
  deckCell.textContent = slide.deck_name;
  deckCell.title = slide.deck_name;
  const textCell = document.createElement("td");
  textCell.className = "slide-text-preview";
  textCell.textContent = slide.description || "No description yet.";
  const previewCell = document.createElement("td");
  previewCell.className = "slide-table-preview-cell";
  const previewButton = document.createElement("button");
  previewButton.className = "slide-table-preview-button";
  previewButton.type = "button";
  previewButton.dataset.selectSlide = slide.id;
  previewButton.setAttribute("aria-label", `Select ${slide.deck_name}, slide ${slide.slide_number}`);
  const preview = document.createElement("img");
  preview.src = thumbnailURL(slide.id, slide.rendering_updated_at);
  preview.alt = `Slide ${slide.slide_number} preview`;
  preview.loading = "lazy";
  previewButton.append(preview);
  previewButton.addEventListener("click", () => toggleSlide(slide));
  const check = document.createElement("span");
  check.className = "selection-check";
  check.textContent = "✓";
  check.setAttribute("aria-hidden", "true");
  previewButton.append(check);
  const previewStatus = document.createElement("small");
  previewStatus.className = `rendering-status ${slide.rendering_status || "unavailable"}`;
  previewStatus.textContent = slide.rendering_status === "available"
    ? "Thumbnail ready"
    : slide.rendering_status === "failed"
      ? "Thumbnail failed"
      : "Placeholder preview";
  previewCell.append(previewButton, previewStatus);
  row.append(titleCell, deckCell, textCell, previewCell);
  titleCell.append(inlineTagEditor("slides", slide));
  row.dataset.slideCard = slide.id;
  return row;
}

async function loadSlides() {
  const request = ++slideRequest;
  const grid = document.getElementById("slide-grid");
  const tbody = document.getElementById("slide-table-body");
  const empty = document.getElementById("slides-empty");
  grid.setAttribute("aria-busy", "true");
  try {
    const params = new URLSearchParams({
      offset: String(slideState.offset),
      limit: String(pageSize),
      q: slideState.query,
      sort_by: slideState.sortBy,
      sort_order: slideState.sortOrder,
    });
    if (slideState.deckId) params.set("deck_id", slideState.deckId);
    if (slideState.organisationId) params.set("organisation_id", slideState.organisationId);
    if (slideState.year) params.set("year", slideState.year);
    appendTagFilters(params, slideState, "slide");
    const result = await fetchJSON(`/api/slides?${params}`);
    if (request !== slideRequest) return;
    slideState.total = result.total;
    if (!result.total) slideState.offset = 0;
    if (result.total && slideState.offset >= result.total) {
      slideState.offset = Math.floor((result.total - 1) / pageSize) * pageSize;
      return loadSlides();
    }
    slideState.items = result.items;
    grid.replaceChildren();
    tbody.replaceChildren();
    for (const slide of result.items) {
      selectedSlides.refresh(slide);
      grid.append(createSlideCard(slide));
      tbody.append(appendSlideTableRow(slide));
    }
    const scope = slideState.deckId ? " in selected deck" : "";
    document.getElementById("slides-count").textContent =
      `${result.total} slide${result.total === 1 ? "" : "s"}${scope}`;
    document.getElementById("slides-error").classList.add("hidden");
    empty.classList.toggle("hidden", result.total !== 0);
    empty.querySelector("h2").textContent = "No slides found";
    empty.querySelector("p").textContent = "Clear filters or import a presentation to browse slides.";
    updateSelectionUI();
    grid.classList.toggle("hidden", result.total === 0 || !isSlideGridView());
    document.getElementById("slide-table-wrap").classList.toggle(
      "hidden",
      result.total === 0 || isSlideGridView()
    );
    updatePagination("slides", result.total, slideState.offset);
  } catch (error) {
    if (request !== slideRequest) return;
    grid.replaceChildren();
    grid.classList.add("hidden");
    document.getElementById("slide-table-wrap").classList.add("hidden");
    empty.classList.remove("hidden");
    const errorBanner = document.getElementById("slides-error");
    errorBanner.textContent = error.message;
    errorBanner.className = "selection error";
    empty.querySelector("h2").textContent = "Could not load slides";
    empty.querySelector("p").textContent = error.message;
  } finally {
    grid.removeAttribute("aria-busy");
  }
}

function isSlideGridView() {
  return document.getElementById("slide-grid-view").getAttribute("aria-pressed") === "true";
}

function setSlideView(gridView) {
  document.getElementById("slide-grid-view").classList.toggle("active", gridView);
  document.getElementById("slide-table-view").classList.toggle("active", !gridView);
  document.getElementById("slide-grid-view").setAttribute("aria-pressed", String(gridView));
  document.getElementById("slide-table-view").setAttribute("aria-pressed", String(!gridView));
  document.getElementById("slide-grid").classList.toggle("hidden", !gridView || !slideState.total);
  document.getElementById("slide-table-wrap").classList.toggle("hidden", gridView || !slideState.total);
}

async function openSlideDetails(slideId) {
  const dialog = document.getElementById("slide-dialog");
  try {
    const slide = await fetchJSON(`/api/slides/${encodeURIComponent(slideId)}`);
    if (document.getElementById("deck-dialog").open) {
      document.getElementById("deck-dialog").close();
    }
    activeDeckId = null;
    activeSlideId = slideId;
    document.getElementById("dialog-title").textContent =
      slide.title || `Slide ${slide.slide_number} — Untitled`;
    document.getElementById("dialog-source").textContent =
      `${slide.deck_name} · Slide ${slide.slide_number} · ${slide.original_filename} · ${titleSourceLabel(slide.title_source)}`;
    document.getElementById("dialog-thumbnail").src =
      thumbnailURL(slideId, slide.rendering_updated_at);
    document.getElementById("dialog-text").textContent =
      slide.text_content || "No slide text was extracted.";
    document.getElementById("dialog-notes").textContent =
      slide.speaker_notes || "No speaker notes were found.";
    document.getElementById("dialog-rendering").textContent =
      slide.rendering_error || `Thumbnail status: ${slide.rendering_status || "unavailable"}.`;
    document.getElementById("slide-edit-title").value = slide.title || "";
    document.getElementById("slide-edit-description").value = slide.description || "";
    populateExtraMetadata("slide", slide);
    populateTagEditor("slide", slide);
    document.getElementById("dialog-source").textContent += ` · Organisation: ${slide.organisation || "Unavailable"} · Year: ${slide.year ?? "Unavailable"}`;
    setDescriptionStatus("slide-edit-status", slide.description_status);
    document.getElementById("slide-edit-error").textContent = "";
    dialog.showModal();
    loadAISuggestions("slide", slideId);
  } catch (error) {
    const errorBanner = document.getElementById("slides-error");
    errorBanner.textContent = error.message;
    errorBanner.className = "selection error";
  }
}

async function openDeckDetails(deckId) {
  const dialog = document.getElementById("deck-dialog");
  try {
    const deck = await fetchJSON(`/api/decks/${encodeURIComponent(deckId)}`);
    activeDeckId = deckId;
    deckSlideState.deckId = deckId;
    deckSlideState.offset = 0;
    document.getElementById("deck-dialog-title").textContent = deck.name;
    document.getElementById("deck-dialog-source").textContent =
      `${deck.original_filename} · ${deck.slide_count} slides · Imported ${formatDate(deck.imported_at)}`;
    document.getElementById("deck-edit-title").value = deck.name || "";
    document.getElementById("deck-edit-organisation").value = deck.organisation || "";
    document.getElementById("deck-edit-year").value = deck.year == null ? "" : String(deck.year);
    document.getElementById("deck-edit-description").value = deck.description || "";
    populateImportMetadata(deck);
    populateTagEditor("deck", deck);
    showDeckSourceLocations(deck);
    setDescriptionStatus("deck-edit-status", deck.description_status);
    document.getElementById("deck-edit-error").textContent = "";
    document.getElementById("thumbnail-retry-status").textContent = `${deck.thumbnail_status.available} previews available; ${deck.thumbnail_status.unavailable + deck.thumbnail_status.failed} unavailable/failed.`;
    await loadDeckSlides(0);
    dialog.showModal();
    loadAISuggestions("deck", deckId);
  } catch (error) {
    const errorBanner = document.getElementById("decks-error");
    errorBanner.textContent = error.message;
    errorBanner.className = "selection error";
  }
}

async function loadDeckSlides(offset) {
  if (!deckSlideState.deckId) return;
  const list = document.getElementById("deck-slides-list");
  list.setAttribute("aria-busy", "true");
  try {
    const params = new URLSearchParams({
      deck_id: deckSlideState.deckId,
      offset: String(offset),
      limit: String(pageSize),
    });
    const result = await fetchJSON(`/api/slides?${params}`);
    deckSlideState.offset = offset;
    deckSlideState.total = result.total;
    document.getElementById("deck-slides-count").textContent =
      `${result.total} slide${result.total === 1 ? "" : "s"}`;
    list.replaceChildren();
    for (const slide of result.items) {
      const item = document.createElement("li");
      const preview = document.createElement("img");
      preview.src = thumbnailURL(slide.id, slide.rendering_updated_at);
      preview.alt = "";
      preview.loading = "lazy";
      const select = document.createElement("button");
      select.type = "button";
      select.className = "slide-preview-button";
      select.dataset.selectSlide = slide.id;
      select.setAttribute("aria-label", `Select slide ${slide.slide_number}: ${slide.title}`);
      select.addEventListener("click", () => toggleSlide(slide));
      select.append(preview);
      const open = document.createElement("button");
      open.className = "text-button";
      open.type = "button";
      open.textContent = `Slide ${slide.slide_number} · ${slide.title}`;
      open.addEventListener("click", () => openSlideDetails(slide.id));
      item.append(select, open);
      list.append(item);
    }
    updateSelectionUI();
    if (!result.items.length) {
      const empty = document.createElement("li");
      empty.className = "deck-slides-empty";
      empty.textContent = "This presentation has no extracted slides.";
      list.append(empty);
    }
    document.getElementById("deck-slides-page").textContent = result.total
      ? `${offset + 1}–${Math.min(offset + pageSize, result.total)} of ${result.total}`
      : "0 slides";
    document.getElementById("deck-slides-previous").disabled = offset <= 0;
    document.getElementById("deck-slides-next").disabled = offset + pageSize >= result.total;
  } catch (error) {
    list.replaceChildren();
    const failure = document.createElement("li");
    failure.className = "deck-slides-empty error-text";
    failure.textContent = error.message;
    list.append(failure);
  } finally {
    list.removeAttribute("aria-busy");
  }
}

document.getElementById("slide-edit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeSlideId) return;
  const error = document.getElementById("slide-edit-error");
  const save = event.currentTarget.querySelector("button[type=submit]");
  save.disabled = true;
  error.textContent = "";
  try {
    const slide = await fetchJSON(`/api/slides/${encodeURIComponent(activeSlideId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: document.getElementById("slide-edit-title").value,
        description: document.getElementById("slide-edit-description").value,
        industry: document.getElementById("slide-edit-industry").value,
        keywords: document.getElementById("slide-edit-keywords").value.split(",").map((word) => word.trim()).filter(Boolean),
        tag_ids: editedTagIds("slide"),
      }),
    });
    document.getElementById("dialog-title").textContent = slide.title;
    document.getElementById("dialog-source").textContent =
      `${slide.deck_name} · Slide ${slide.slide_number} · ${slide.original_filename} · ${titleSourceLabel(slide.title_source)}`;
    document.getElementById("slide-edit-title").value = slide.title;
    document.getElementById("slide-edit-description").value = slide.description || "";
    setDescriptionStatus("slide-edit-status", slide.description_status);
    populateTagEditor("slide", slide);
    document.getElementById("dialog-source").textContent += ` · Organisation: ${slide.organisation || "Unavailable"} · Year: ${slide.year ?? "Unavailable"}`;
    selectedSlides.refresh(slide);
    labelMetadataStates("slide", slide);
    await Promise.all([loadSlides(), loadFilterOptions()]);
  } catch (saveError) {
    error.textContent = saveError.message;
  } finally {
    save.disabled = false;
  }
});

document.getElementById("deck-edit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeDeckId) return;
  const error = document.getElementById("deck-edit-error");
  const save = event.currentTarget.querySelector("button[type=submit]");
  save.disabled = true;
  error.textContent = "";
  const yearValue = document.getElementById("deck-edit-year").value;
  try {
    const deck = await fetchJSON(`/api/decks/${encodeURIComponent(activeDeckId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: document.getElementById("deck-edit-title").value,
        organisation: document.getElementById("deck-edit-organisation").value,
        year: yearValue ? Number(yearValue) : null,
        description: document.getElementById("deck-edit-description").value,
        purpose: document.getElementById("deck-edit-purpose").value,
        industry: document.getElementById("deck-edit-industry").value,
        keywords: document.getElementById("deck-edit-keywords").value.split(",").map((word) => word.trim()).filter(Boolean),
        author: document.getElementById("deck-edit-author").value,
        subject: document.getElementById("deck-edit-subject").value,
        subtitle: document.getElementById("deck-edit-subtitle").value,
        review_status: document.getElementById("deck-edit-review-status").value,
        tag_ids: editedTagIds("deck"),
      }),
    });
    document.getElementById("deck-dialog-title").textContent = deck.name;
    document.getElementById("deck-dialog-source").textContent =
      `${deck.original_filename} · ${deck.slide_count} slides · Imported ${formatDate(deck.imported_at)}`;
    document.getElementById("deck-edit-title").value = deck.name;
    document.getElementById("deck-edit-organisation").value = deck.organisation || "";
    document.getElementById("deck-edit-year").value = deck.year == null ? "" : String(deck.year);
    document.getElementById("deck-edit-description").value = deck.description || "";
    populateImportMetadata(deck);
    setDescriptionStatus("deck-edit-status", deck.description_status);
    populateTagEditor("deck", deck);
    showDeckSourceLocations(deck);
    for (const slide of selectedSlides.values()) {
      if (slide.deck_id === deck.id) selectedSlides.refresh({ ...slide, deck_name: deck.name, organisation: deck.organisation, year: deck.year });
    }
    await Promise.all([loadDecks(), loadFilterOptions()]);
    if (watchedImportJob) await pollImportJob(watchedImportJob);
  } catch (saveError) {
    error.textContent = saveError.message;
  } finally {
    save.disabled = false;
  }
});

document.getElementById("dialog-close").addEventListener("click", () => {
  document.getElementById("slide-dialog").close();
});
document.getElementById("slide-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
document.getElementById("deck-dialog-close").addEventListener("click", () => {
  document.getElementById("deck-dialog").close();
});
document.getElementById("deck-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
document.getElementById("slide-dialog").addEventListener("close", () => {
  activeSlideId = null;
});
document.getElementById("deck-dialog").addEventListener("close", () => {
  activeDeckId = null;
  deckSlideState.deckId = null;
});
document.getElementById("deck-slides-previous").addEventListener("click", () => {
  loadDeckSlides(Math.max(0, deckSlideState.offset - pageSize));
});
document.getElementById("deck-slides-next").addEventListener("click", () => {
  loadDeckSlides(deckSlideState.offset + pageSize);
});
document.getElementById("slide-grid-view").addEventListener("click", () => setSlideView(true));
document.getElementById("slide-table-view").addEventListener("click", () => setSlideView(false));

function updatePagination(kind, total, offset) {
  const prefix = kind === "decks" ? "decks" : "slides";
  const previous = document.getElementById(`${prefix}-previous`);
  const next = document.getElementById(`${prefix}-next`);
  const page = document.getElementById(`${prefix}-page`);
  previous.disabled = offset <= 0;
  next.disabled = offset + pageSize >= total;
  page.textContent = total
    ? `${offset + 1}–${Math.min(offset + pageSize, total)} of ${total}`
    : "0 results";
}

document.getElementById("decks-previous").addEventListener("click", () => {
  deckState.offset = Math.max(0, deckState.offset - pageSize);
  loadDecks();
});
document.getElementById("decks-next").addEventListener("click", () => {
  deckState.offset += pageSize;
  loadDecks();
});
document.getElementById("slides-previous").addEventListener("click", () => {
  slideState.offset = Math.max(0, slideState.offset - pageSize);
  loadSlides();
});
document.getElementById("slides-next").addEventListener("click", () => {
  slideState.offset += pageSize;
  loadSlides();
});

document.getElementById("deck-search").addEventListener("input", (event) => {
  deckState.query = event.target.value;
  deckState.offset = 0;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadDecks, 250);
});
document.getElementById("slide-search").addEventListener("input", (event) => {
  slideState.query = event.target.value;
  slideState.offset = 0;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadSlides, 250);
});
document.getElementById("deck-organisation-filter").addEventListener("change", (event) => {
  deckState.organisationId = event.target.value;
  deckState.offset = 0;
  loadDecks();
});
document.getElementById("deck-year-filter").addEventListener("change", (event) => {
  deckState.year = event.target.value;
  deckState.offset = 0;
  loadDecks();
});
document.getElementById("slide-organisation-filter").addEventListener("change", (event) => {
  slideState.organisationId = event.target.value;
  slideState.offset = 0;
  loadSlides();
});
document.getElementById("slide-year-filter").addEventListener("change", (event) => {
  slideState.year = event.target.value;
  slideState.offset = 0;
  loadSlides();
});
document.getElementById("slide-deck-filter").addEventListener("change", (event) => {
  slideState.deckId = event.target.value || null;
  slideState.offset = 0;
  loadSlides();
});
document.querySelectorAll("button[data-sort-kind='decks']").forEach((button) => {
  button.addEventListener("click", () => {
    if (deckState.sortBy === button.dataset.sort) {
      deckState.sortOrder = deckState.sortOrder === "asc" ? "desc" : "asc";
    } else {
      deckState.sortBy = button.dataset.sort;
      deckState.sortOrder = button.dataset.sort === "imported_at" ? "desc" : "asc";
    }
    deckState.offset = 0;
    loadDecks();
  });
});
function updateDeckSortHeaders() {
  document.querySelectorAll("button[data-sort-kind='decks']").forEach((button) => {
    const active = deckState.sortBy === button.dataset.sort;
    button.closest("th").setAttribute(
      "aria-sort",
      active ? (deckState.sortOrder === "asc" ? "ascending" : "descending") : "none"
    );
    button.dataset.sortIndicator = active
      ? (deckState.sortOrder === "asc" ? "ascending" : "descending")
      : "";
  });
}
document.getElementById("refresh-decks").addEventListener("click", loadDecks);
document.getElementById("refresh-slides").addEventListener("click", loadSlides);

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const folderInput = document.getElementById("folder-input");
const selection = document.getElementById("selection");
const importProgress = document.getElementById("import-progress");

document.getElementById("browse-files").addEventListener("click", (event) => {
  event.stopPropagation();
  fileInput.click();
});
document.getElementById("browse-folder").addEventListener("click", (event) => {
  event.stopPropagation();
  folderInput.click();
});
dropzone.addEventListener("click", (event) => {
  if (event.target === dropzone) fileInput.click();
});
dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});
for (const input of [fileInput, folderInput]) {
  input.addEventListener("change", () => {
    if (input.files?.length) startImport(input.files);
    input.value = "";
  });
}
for (const eventName of ["dragenter", "dragover"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("drag-over");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove("drag-over");
  });
}
dropzone.addEventListener("drop", (event) => {
  if (event.dataTransfer.files.length) startImport(event.dataTransfer.files);
});

function showImportMessage(message, kind = "info") {
  selection.className = `selection ${kind}`;
  selection.textContent = message;
}

function startImport(files) {
  if (!files.length) return;
  const aiSettings = postImportSettings();
  if (aiSettings === undefined) return;
  if (importBusy) { showImportMessage("An import is already active. Wait for it to finish before starting another batch.", "error"); return; }
  beginImportUpload();
  const formData = new FormData();
  if (aiSettings) formData.append("enrichment_options", JSON.stringify(aiSettings));
  const metadata = [];
  for (const file of files) {
    const logicalPath = file.webkitRelativePath || file.name;
    formData.append("files", file, logicalPath);
    metadata.push({ last_modified: file.lastModified || null });
  }
  formData.append("file_metadata", JSON.stringify(metadata));
  formData.append("storage_mode", document.getElementById("storage-mode").value);
  importProgress.classList.remove("hidden");
  document.getElementById("progress-title").textContent = "Uploading presentations…";
  document.getElementById("progress-value").textContent = "Uploading";
  document.getElementById("progress-bar").removeAttribute("value");
  document.getElementById("progress-message").textContent = `${files.length} file${files.length === 1 ? "" : "s"} selected.`;
  document.getElementById("progress-items").replaceChildren();
  const request = new XMLHttpRequest();
  request.open("POST", "/api/import");
  request.upload.addEventListener("progress", (event) => {
    if (!event.lengthComputable) return;
    const percent = Math.round((event.loaded / event.total) * 100);
    document.getElementById("progress-value").textContent = `Upload ${percent}%`;
  });
  request.addEventListener("load", async () => {
    let response;
    try {
      response = JSON.parse(request.responseText);
    } catch {
      importBusy = false;
      showImportMessage("The server returned an unreadable import response.", "error");
      restoreImportJob();
      return;
    }
    if (request.status !== 202) {
      importBusy = false;
      showImportMessage(response.detail || "The upload could not be accepted.", "error");
      document.getElementById("progress-title").textContent = "Import could not start";
      return;
    }
    rememberImportJob(response.job_id);
    await pollImportJob(response.job_id);
  });
  request.addEventListener("error", () => {
    importBusy = false;
    document.getElementById("progress-title").textContent = "Upload failed";
    showImportMessage("The server could not receive the selected files.", "error");
    restoreImportJob();
  });
  request.send(formData);
}

async function pollImportJob(jobId) {
  return watchImportJob(jobId);
}

function renderJobItems(items) {
  const list = document.getElementById("progress-items");
  list.replaceChildren();
  for (const item of items) {
    const entry = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = item.logical_source_path;
    const status = document.createElement("strong");
    status.className = `item-status ${item.status}`;
    status.textContent = item.status;
    entry.append(name, status);
    if (item.message) {
      const message = document.createElement("small");
      message.textContent = item.message;
      entry.append(message);
    }
    if (item.deck_id) {
      for (const rendering of item.rendering_results || []) {
        if (rendering.status === "available") continue;
        const warning = document.createElement("small");
        warning.textContent = `${rendering.slide_count} thumbnail(s) ${rendering.status}: ${rendering.error || "No preview available"}`;
        entry.append(warning);
      }
      const review = document.createElement("button");
      review.type = "button";
      review.className = "text-button";
      review.dataset.reviewDeck = item.deck_id;
      review.textContent = `Review / edit (${item.review_status || "Not Reviewed"})`;
      review.addEventListener("click", () => openDeckDetails(item.deck_id));
      entry.append(review);
    }
    list.append(entry);
  }
}

document.getElementById("refresh-history").addEventListener("click", loadImportHistory);
async function loadImportHistory() {
  const tbody = document.getElementById("import-history-body");
  const empty = document.getElementById("history-empty");
  try {
    const result = await fetchJSON("/api/import/jobs?offset=0&limit=10");
    tbody.replaceChildren();
    for (const job of result.items) {
      const row = document.createElement("tr");
      const started = document.createElement("td");
      started.textContent = formatDate(job.created_at);
      const status = document.createElement("td");
      status.textContent = job.status.replaceAll("_", " ");
      const files = document.createElement("td");
      files.textContent = `${job.imported_count} imported · ${job.duplicate_count} duplicate · ${job.failed_count} failed`;
      const slides = document.createElement("td");
      slides.textContent = String(job.slide_count);
      const results = document.createElement("td");
      const details = document.createElement("button");
      details.className = "text-button";
      details.textContent = "View details";
      details.addEventListener("click", async () => {
        importProgress.classList.remove("hidden");
        importItemOffset = 0;
        await pollImportJob(job.id);
      });
      results.append(details);
      row.append(started, status, files, slides, results);
      tbody.append(row);
    }
    empty.classList.toggle("hidden", result.items.length !== 0);
  } catch (error) {
    setTableMessage(tbody, error.message);
    empty.classList.add("hidden");
  }
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "—" : date.toLocaleString();
}

async function refreshOllamaStatus() {
  const connection = document.getElementById("ollama-connection");
  const modelList = document.getElementById("model-list");
  const message = document.getElementById("ollama-message");
  const refresh = document.getElementById("refresh-ollama");
  refresh.disabled = true;
  refresh.innerHTML = '<span aria-hidden="true">↻</span> Checking…';
  connection.className = "status-pill checking";
  connection.textContent = "Checking…";
  modelList.replaceChildren();
  const loadingRow = document.createElement("div");
  loadingRow.className = "model-row";
  loadingRow.textContent = "Loading model status…";
  modelList.append(loadingRow);
  try {
    const status = await fetchJSON("/api/ollama/status");
    connection.className = `status-pill ${status.connected ? "connected" : "offline"}`;
    connection.textContent = status.connected ? "Connected" : "Not connected";
    modelList.replaceChildren();
    if (status.models.length === 0) {
      const row = document.createElement("div");
      row.className = "model-row";
      row.textContent = "No required models configured";
      modelList.append(row);
    }
    for (const model of status.models) {
      const row = document.createElement("div");
      row.className = "model-row";
      const name = document.createElement("span");
      name.textContent = model.name;
      const state = document.createElement("span");
      state.className = `model-state ${model.available ? "available" : "missing"}`;
      state.textContent = model.available ? "Installed" : "Not installed";
      row.append(name, state);
      modelList.append(row);
    }
    const allAvailable = status.models.every((model) => model.available);
    message.textContent = status.connected
      ? allAvailable
        ? "Ollama is running locally and all required models are available."
        : "Ollama is running, but one or more required models are not installed."
      : status.error || "Could not connect to the local Ollama service.";
  } catch (error) {
    connection.className = "status-pill offline";
    connection.textContent = "Status unavailable";
    modelList.replaceChildren();
    const row = document.createElement("div");
    row.className = "model-row";
    row.textContent = "Unable to retrieve model status.";
    modelList.append(row);
    message.textContent = error.message;
  } finally {
    refresh.disabled = false;
    refresh.innerHTML = '<span aria-hidden="true">↻</span> Refresh';
  }
}
document.getElementById("refresh-ollama").addEventListener("click", refreshOllamaStatus);

const taxonomyLabels = { deck_type: "Deck type", slide_type: "Slide type", topic: "Topic" };

function tagBadges(tags = []) {
  const container = document.createElement("div");
  container.className = "tag-list";
  if (!tags.length) {
    container.textContent = "No tags assigned";
    container.classList.add("muted");
  }
  for (const tag of tags) {
    const badge = document.createElement("span");
    badge.className = "tag-badge";
    badge.textContent = `${taxonomyLabels[tag.tag_type]}: ${tag.name}${tag.status === "archived" ? " (archived)" : ""}`;
    badge.title = tag.assignment_status || "";
    container.append(badge);
  }
  return container;
}

function showDeckSourceLocations(deck) {
  const paths = (deck.source_locations || []).map((source) => source.source_path || source.logical_path);
  document.getElementById("deck-dialog-source").textContent += ` · Source: ${paths.join("; ") || "Unavailable"}`;
}

function appendTagFilters(params, state, kind) {
  for (const id of state.extraTags || []) params.append("tag_id", id);
  for (const id of state.slideTypes) params.append("slide_type", id);
  if (kind === "deck") for (const id of state.deckTypes) params.append("deck_type", id);
  else for (const id of state.topics) params.append("topic", id);
}

function renderTagFilters() {
  const tags = filterOptions.tags || [];
  for (const kind of ["deck", "slide"]) {
    const container = document.getElementById(`${kind}-tag-filters`);
    container.replaceChildren();
    const categories = [...new Set([...(kind === "deck" ? ["deck_type", "slide_type"] : ["slide_type", "topic"]), ...tagCategories(kind)])];
    for (const category of categories) {
      const fieldset = document.createElement("fieldset");
      const legend = document.createElement("legend");
      legend.textContent = category === "slide_type" && kind === "deck" ? "Contains slide type" : taxonomyLabels[category];
      fieldset.append(legend);
      const available = tags.filter((tag) => tag.tag_type === category);
      if (!available.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = "No canonical tags available.";
        fieldset.append(empty);
      }
      for (const tag of available) {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "checkbox";
        const state = kind === "deck" ? deckState : slideState;
        const key = category === "deck_type" ? "deckTypes" : category === "slide_type" ? "slideTypes" : kind === "slide" && category === "topic" ? "topics" : "extraTags";
        if (!state[key]) state[key] = new Set();
        input.checked = state[key].has(tag.id);
        input.addEventListener("change", () => {
          if (input.checked) state[key].add(tag.id); else state[key].delete(tag.id);
          state.offset = 0;
          const page = kind === "deck" ? "decks" : "slides";
          if (document.getElementById(`page-${page}`).classList.contains("hidden")) showPage(page);
          else if (kind === "deck") loadDecks(); else loadSlides();
        });
        const count = kind === "slide" ? tag.slide_count : category === "slide_type" ? tag.containing_deck_count : tag.deck_count;
        label.append(input, document.createTextNode(`${tag.name} (${count})`));
        fieldset.append(label);
      }
      container.append(fieldset);
    }
  }
  const counts = document.getElementById("slide-tag-counts");
  counts.replaceChildren();
  for (const category of ["slide_type", "topic"]) {
    const group = document.createElement("p");
    const values = tags.filter((tag) => tag.tag_type === category);
    group.textContent = `${taxonomyLabels[category]}: ${values.map((tag) => `${tag.name} (${tag.slide_count})`).join(" · ") || "No tags available"}`;
    counts.append(group);
  }
}

function populateTagEditor(kind, entity) {
  const badges = document.getElementById(`${kind}-detail-tags`);
  badges.replaceChildren(inlineTagEditor(kind === "deck" ? "decks" : "slides", entity));
  const fieldset = document.getElementById(`${kind}-tag-editor`);
  fieldset.replaceChildren();
  const legend = document.createElement("legend");
  legend.textContent = "Assigned canonical tags";
  fieldset.append(legend);
  const assigned = new Set((entity.tags || []).map((tag) => tag.id));
  const allowed = tagCategories(kind);
  const available = new Map((filterOptions.tags || []).filter((tag) => allowed.includes(tag.tag_type)).map((tag) => [tag.id, tag]));
  for (const tag of entity.tags || []) available.set(tag.id, tag);
  if (!available.size) {
    const note = document.createElement("p");
    note.textContent = "No canonical tags available. Create a tag from a library card.";
    fieldset.append(note);
  }
  for (const tag of available.values()) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = tag.id;
    input.checked = assigned.has(tag.id);
    label.append(input, document.createTextNode(`${taxonomyLabels[tag.tag_type]}: ${tag.name}${tag.status === "archived" ? " (archived)" : ""}`));
    fieldset.append(label);
  }
}

function editedTagIds(kind) {
  return [...document.querySelectorAll(`#${kind}-tag-editor input:checked`)].map((input) => input.value);
}

function previewAction(slide) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "text-button";
  button.textContent = "Preview / edit";
  button.setAttribute("aria-label", `Preview or edit ${slide.title}, ${slide.deck_name}, slide ${slide.slide_number}`);
  button.addEventListener("click", () => openSlideDetails(slide.id));
  return button;
}

function toggleSlide(slide) {
  selectedSlides.toggle(slide);
  selectionChanged();
}

function selectionChanged() {
  confirmedSlideOrder = null;
  document.getElementById("review-status").textContent = "Selection changed. Confirm the updated order when ready.";
  updateSelectionUI();
}

function updateSelectionUI() {
  const size = selectedSlides.size;
  document.getElementById("delete-slides").textContent = `Delete Selected (${size})`;
  document.getElementById("delete-slides").disabled = size === 0;
  const create = document.getElementById("create-deck");
  create.textContent = `Create Deck (${size})`;
  create.disabled = size === 0;
  document.getElementById("selected-count").textContent = `${size} selected across the library`;
  document.getElementById("clear-selection").disabled = size === 0;
  document.getElementById("confirm-slide-order").disabled = size === 0;
  document.querySelectorAll("[data-select-slide]").forEach((button) => {
    const selected = selectedSlides.has(button.dataset.selectSlide);
    button.setAttribute("aria-pressed", String(selected));
    button.classList.toggle("is-selected", selected);
  });
  document.querySelectorAll("[data-slide-card]").forEach((card) => {
    card.classList.toggle("is-selected", selectedSlides.has(card.dataset.slideCard));
  });
  if (document.getElementById("selected-slides-dialog").open) renderSelectedSlides();
}

function renderSelectedSlides(focusId = null, action = null) {
  const list = document.getElementById("selected-slides-list");
  list.replaceChildren();
  const slides = selectedSlides.values();
  if (!slides.length) {
    const empty = document.createElement("li");
    empty.textContent = "No slides selected. Close this panel and select slides from the library.";
    list.append(empty);
  }
  slides.forEach((slide, index) => {
    const item = document.createElement("li");
    item.draggable = true;
    item.dataset.reviewSlide = slide.id;
    const image = document.createElement("img");
    image.src = thumbnailURL(slide.id, slide.rendering_updated_at);
    image.alt = `Slide ${slide.slide_number} preview`;
    const info = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${index + 1}. ${slide.title}`;
    const source = document.createElement("p");
    source.textContent = `${slide.deck_name} · Slide ${slide.slide_number}`;
    info.append(title, source);
    const controls = document.createElement("div");
    controls.className = "review-controls";
    for (const [label, target, key] of [["Move up", index - 1, "up"], ["Move down", index + 1, "down"], ["Remove", null, "remove"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button button-secondary";
      button.textContent = label;
      button.dataset.action = key;
      button.setAttribute("aria-label", `${label}: ${slide.title}, position ${index + 1}`);
      button.disabled = target !== null && (target < 0 || target >= slides.length);
      button.addEventListener("click", () => {
        if (key === "remove") selectedSlides.remove(slide.id);
        else selectedSlides.move(slide.id, target);
        selectionChanged();
        const nextId = key === "remove" ? selectedSlides.values()[Math.min(index, selectedSlides.size - 1)]?.id : slide.id;
        renderSelectedSlides(nextId, key);
      });
      controls.append(button);
    }
    item.append(image, info, controls);
    item.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", slide.id);
      event.dataTransfer.effectAllowed = "move";
    });
    item.addEventListener("dragover", (event) => { event.preventDefault(); event.dataTransfer.dropEffect = "move"; });
    item.addEventListener("drop", (event) => {
      event.preventDefault();
      if (selectedSlides.move(event.dataTransfer.getData("text/plain"), index)) {
        selectionChanged();
        document.getElementById("review-status").textContent = "Slide order updated.";
      }
    });
    list.append(item);
  });
  if (focusId) {
    const row = [...list.children].find((item) => item.dataset.reviewSlide === focusId);
    const preferred = row?.querySelector(`[data-action="${action}"]`);
    (preferred && !preferred.disabled ? preferred : row?.querySelector("button:not(:disabled)"))?.focus();
  } else if (!slides.length) document.getElementById("close-selected-slides").focus();
}

function renderBuilderStatus() {
  renderNativeBuilder();
}

for (const kind of ["deck", "slide"]) {
  document.getElementById(`clear-${kind}-filters`).addEventListener("click", () => {
    const state = kind === "deck" ? deckState : slideState;
    state.slideTypes.clear();
    if (state.extraTags) state.extraTags.clear();
    if (state.extraTags) state.extraTags.clear();
    if (kind === "deck") state.deckTypes.clear(); else state.topics.clear();
    state.organisationId = "";
    state.year = "";
    state.query = "";
    state.offset = 0;
    if (kind === "slide") { state.deckId = null; document.getElementById("slide-deck-filter").value = ""; }
    document.getElementById(`${kind}-organisation-filter`).value = "";
    document.getElementById(`${kind}-year-filter`).value = "";
    document.getElementById(`${kind}-search`).value = "";
    renderTagFilters();
    showPage(kind === "deck" ? "decks" : "slides");
  });
}
for (const [id, key] of [["slide-sort", "sortBy"], ["slide-sort-order", "sortOrder"]]) {
  document.getElementById(id).addEventListener("change", (event) => {
    slideState[key] = event.target.value;
    slideState.offset = 0;
    loadSlides();
  });
}
document.getElementById("clear-selection").addEventListener("click", () => { selectedSlides.clear(); selectionChanged(); });
document.getElementById("create-deck").addEventListener("click", () => {
  if (!selectedSlides.size) return;
  const dialog = document.getElementById("selected-slides-dialog");
  dialog.showModal();
  renderSelectedSlides();
});
document.getElementById("close-selected-slides").addEventListener("click", () => document.getElementById("selected-slides-dialog").close());
document.getElementById("confirm-slide-order").addEventListener("click", () => {
  if (!selectedSlides.size) return;
  confirmedSlideOrder = selectedSlides.manifest();
  // Future native export integration receives IDs in explicit order, never presentation copies.
  document.dispatchEvent(new CustomEvent("slide-navigator:order-confirmed", { detail: selectedSlides.manifest() }));
  document.getElementById("review-status").textContent = `${selectedSlides.size} slides confirmed in this order for this session. No PowerPoint file has been created.`;
});

showPage("decks");
loadFilterOptions();
loadImportHistory();
refreshOllamaStatus();
restoreImportJob();

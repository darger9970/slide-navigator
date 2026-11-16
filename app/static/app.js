const pages = ["home", "library", "import", "search", "builder", "settings"];
const pageTitles = {
  home: "Home",
  library: "Slide Library",
  import: "Import",
  search: "Search",
  builder: "Deck Builder",
  settings: "Settings",
};

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
  if (name === "settings") refreshOllamaStatus();
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

const globalSearch = document.getElementById("global-search");
document.getElementById("global-search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  document.getElementById("search-page-input").value = globalSearch.value;
  showPage("search");
  document.getElementById("search-page-input").focus();
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    globalSearch.focus();
  }
  if (event.key === "Escape") {
    document.getElementById("sidebar").classList.remove("open");
    document.getElementById("menu-button").setAttribute("aria-expanded", "false");
  }
});
document.getElementById("search-page-form").addEventListener("submit", (event) => {
  event.preventDefault();
});

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const folderInput = document.getElementById("folder-input");
const selection = document.getElementById("selection");

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
  input.addEventListener("change", () => showSelectedFiles(input.files));
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
dropzone.addEventListener("drop", (event) => showSelectedFiles(event.dataTransfer.files));

function showSelectedFiles(files) {
  if (!files || files.length === 0) return;
  const count = files.length;
  selection.textContent = `${count} local item${count === 1 ? "" : "s"} selected. Importing files will be enabled in a later milestone; nothing has been copied or uploaded.`;
  selection.classList.remove("hidden");
}

const librarySearch = document.getElementById("library-search");
const statusFilter = document.getElementById("status-filter");
const libraryCount = document.getElementById("library-count");
const emptyTitle = document.getElementById("empty-title");
const emptyDescription = document.getElementById("empty-description");

function updateLibraryEmptyState() {
  const hasCriteria = librarySearch.value.trim() || statusFilter.value !== "all";
  libraryCount.textContent = hasCriteria ? "0 matching presentations" : "0 presentations";
  emptyTitle.textContent = hasCriteria
    ? "No presentations match your filters"
    : "Your slide library is empty";
  emptyDescription.textContent = hasCriteria
    ? "Try another search or choose a different import status."
    : "Import presentations to see their slides, source folders, and import status here.";
}

librarySearch.addEventListener("input", updateLibraryEmptyState);
statusFilter.addEventListener("change", updateLibraryEmptyState);

function setLibraryView(view) {
  const grid = view === "grid";
  document.getElementById("grid-view").classList.toggle("active", grid);
  document.getElementById("table-view").classList.toggle("active", !grid);
  document.getElementById("grid-view").setAttribute("aria-pressed", String(grid));
  document.getElementById("table-view").setAttribute("aria-pressed", String(!grid));
  document.getElementById("library-content").classList.toggle("hidden", !grid);
  document.getElementById("library-table-wrap").classList.toggle("hidden", grid);
}
document.getElementById("grid-view").addEventListener("click", () => setLibraryView("grid"));
document.getElementById("table-view").addEventListener("click", () => setLibraryView("table"));

document.getElementById("sort-select").addEventListener("change", (event) => {
  const sortValue = event.target.value;
  document.querySelectorAll(".library-table th button[data-sort]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.sort === sortValue));
  });
});
document.querySelectorAll(".library-table th button[data-sort]").forEach((button) => {
  button.addEventListener("click", () => {
    const sort = document.getElementById("sort-select");
    sort.value = button.dataset.sort;
    sort.dispatchEvent(new Event("change"));
  });
});

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
    const response = await fetch("/api/ollama/status");
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    const status = await response.json();
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
refreshOllamaStatus();

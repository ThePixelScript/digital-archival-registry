/**
 * Digital Archival Registry & Museum Collection Tracker
 * Frontend Application Controller (Vanilla JavaScript ES Modules)
 *
 * Implements accessible, responsive collection discovery, relevance-ranked
 * full-text search, multi-faceted filtering, detail inspection, and CRUD
 * operations interacting with the FastAPI / Elasticsearch backend.
 */

// =============================================================================
// 1. Configuration and Global State
// =============================================================================

const API_BASE_URL = (window.location.protocol === "http:" || window.location.protocol === "https:")
  ? `${window.location.origin}/api`
  : "http://127.0.0.1:8000/api";

const state = {
  query: "",
  category: "",
  period: "",
  condition: "",
  displayStatus: "",
  sortBy: "",
  sortOrder: "asc",
  fuzzy: false,
  page: 1,
  pageSize: 10,
  totalResults: 0,
  totalPages: 0,
  isLoading: false,
  activeModal: null,
  lastFocusedElement: null,
  editingArtifactId: null,
  deletingArtifact: null,
  analyticsOpen: false,
  statsData: null,
  categoriesList: [],
  periodsList: [],
  conditionsList: [],
  importFile: null,
  importPreviewData: null
};

let debounceTimer = null;

// =============================================================================
// 2. Safe DOM Construction Helpers (XSS Prevention)
// =============================================================================

/**
 * Creates an HTML element safely with attributes and text/node children.
 */
function el(tag, options = {}, ...children) {
  const element = document.createElement(tag);
  if (options.className) element.className = options.className;
  if (options.id) element.id = options.id;
  if (options.text !== undefined && options.text !== null) {
    element.textContent = String(options.text);
  }
  if (options.attrs) {
    for (const [k, v] of Object.entries(options.attrs)) {
      if (v !== undefined && v !== null) {
        element.setAttribute(k, String(v));
      }
    }
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    if (typeof child === "string" || typeof child === "number") {
      element.appendChild(document.createTextNode(String(child)));
    } else if (child instanceof Node) {
      element.appendChild(child);
    }
  }
  return element;
}

/**
 * Formats a historical year into a human-readable archival string (BCE/CE).
 */
function formatYearRange(earliest, latest) {
  const formatSingle = (y) => {
    if (y < 0) return `${Math.abs(y)} BCE`;
    return `${y} CE`;
  };

  if (earliest === latest) {
    return `c. ${formatSingle(earliest)}`;
  }
  if (earliest < 0 && latest < 0) {
    return `c. ${Math.abs(earliest)} – ${Math.abs(latest)} BCE`;
  }
  if (earliest < 0 && latest >= 0) {
    return `c. ${Math.abs(earliest)} BCE – ${latest} CE`;
  }
  return `c. ${earliest} – ${latest} CE`;
}

/**
 * Formats currency in USD.
 */
function formatCurrency(val) {
  if (val === undefined || val === null || isNaN(val)) return "$0";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0
  }).format(val);
}

/**
 * Returns condition badge CSS class.
 */
function getConditionBadgeClass(cond) {
  const normalized = (cond || "").toLowerCase();
  switch (normalized) {
    case "pristine": return "badge-cond-pristine";
    case "excellent": return "badge-cond-excellent";
    case "good": return "badge-cond-good";
    case "fair": return "badge-cond-fair";
    case "fragile": return "badge-cond-fragile";
    case "critical": return "badge-cond-critical";
    case "degraded": return "badge-cond-degraded";
    default: return "badge-accession";
  }
}

// =============================================================================
// 3. API Communication Layer
// =============================================================================

async function apiRequest(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint}`;
  const response = await fetch(url, {
    headers: {
      "Accept": "application/json",
      "Content-Type": "application/json",
      ...options.headers
    },
    ...options
  });

  if (!response.ok) {
    let errorMessage = `HTTP ${response.status} ${response.statusText}`;
    try {
      const errData = await response.json();
      if (errData && errData.detail) {
        errorMessage = typeof errData.detail === "string"
          ? errData.detail
          : JSON.stringify(errData.detail);
      }
    } catch {
      // Use fallback HTTP status message
    }
    const err = new Error(errorMessage);
    err.status = response.status;
    throw err;
  }

  if (response.status === 204) {
    return null;
  }
  return await response.json();
}

async function checkSystemHealth() {
  const statusIndicator = document.getElementById("status-indicator");
  const statusLabel = document.getElementById("status-label");

  try {
    const health = await apiRequest("/health");
    if (health.status === "ok" && health.elasticsearch && health.elasticsearch.connected) {
      statusIndicator.className = "status-indicator ok";
      const docCount = health.elasticsearch.document_count || 0;
      statusLabel.textContent = `Elasticsearch Online (${docCount} Artifacts)`;
    } else {
      statusIndicator.className = "status-indicator error";
      statusLabel.textContent = "Cluster Degraded";
    }
  } catch (err) {
    statusIndicator.className = "status-indicator error";
    statusLabel.textContent = "API Disconnected";
  }
}

async function fetchStats() {
  try {
    const data = await apiRequest("/stats");
    state.statsData = data;
    renderAnalytics(data);
    populateFacetDropdowns(data);
  } catch (err) {
    console.warn("Unable to fetch collection statistics:", err.message);
  }
}

async function executeSearch() {
  showLoading();
  hideError();
  hideEmptyState();

  const params = new URLSearchParams();
  if (state.query.trim()) params.append("q", state.query.trim());
  if (state.category) params.append("category", state.category);
  if (state.period) params.append("historical_period", state.period);
  if (state.condition) params.append("condition", state.condition);
  if (state.displayStatus !== "") params.append("is_on_display", state.displayStatus);
  if (state.sortBy) params.append("sort_by", state.sortBy);
  if (state.sortOrder) params.append("sort_order", state.sortOrder);
  if (state.fuzzy) params.append("fuzzy", "true");
  params.append("page", String(state.page));
  params.append("page_size", String(state.pageSize));

  try {
    const results = await apiRequest(`/search?${params.toString()}`);
    hideLoading();
    state.totalResults = results.total;
    state.totalPages = results.total_pages;

    updateResultsMeta(results);
    renderArtifactsGrid(results.items);
    updatePagination(results);
    renderActiveFilterChips();
  } catch (err) {
    hideLoading();
    showError(err.message || "Failed to communicate with archival search service.");
  }
}

async function getArtifactById(id) {
  return await apiRequest(`/artifacts/${encodeURIComponent(id)}`);
}

async function createArtifactRecord(payload) {
  return await apiRequest("/artifacts", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

async function updateArtifactRecord(id, payload) {
  return await apiRequest(`/artifacts/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

async function deleteArtifactRecord(id) {
  return await apiRequest(`/artifacts/${encodeURIComponent(id)}`, {
    method: "DELETE"
  });
}

async function exportCollectionCsv() {
  const exportBtn = document.getElementById("btn-export-csv");
  const labelEl = document.getElementById("export-btn-label");
  const originalLabel = labelEl ? labelEl.textContent : "Export CSV";

  try {
    if (exportBtn) exportBtn.disabled = true;
    if (labelEl) labelEl.textContent = "Exporting...";

    const params = new URLSearchParams();
    if (state.query && state.query.trim()) params.append("q", state.query.trim());
    if (state.category) params.append("category", state.category);
    if (state.period) params.append("historical_period", state.period);
    if (state.condition) params.append("condition", state.condition);
    if (state.displayStatus !== "") params.append("is_on_display", state.displayStatus);
    if (state.sortBy) params.append("sort_by", state.sortBy);
    if (state.sortOrder) params.append("sort_order", state.sortOrder);
    if (state.fuzzy) params.append("fuzzy", "true");

    const exportUrl = `${API_BASE_URL}/artifacts/export?${params.toString()}`;
    const response = await fetch(exportUrl);

    if (!response.ok) {
      let errorMessage = `Export failed (HTTP ${response.status})`;
      try {
        const errData = await response.json();
        if (errData && errData.detail) {
          errorMessage = typeof errData.detail === "string" ? errData.detail : JSON.stringify(errData.detail);
        }
      } catch {
        // Fallback to HTTP error
      }
      throw new Error(errorMessage);
    }

    const blob = await response.blob();
    let filename = "museum_artifacts_export.csv";
    const disposition = response.headers.get("content-disposition");
    if (disposition && disposition.includes("filename=")) {
      const match = disposition.match(/filename=["']?([^"';]+)["']?/);
      if (match && match[1]) {
        filename = match[1];
      }
    }

    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(downloadUrl);

    showToast("Archival records exported successfully as CSV.", true);
  } catch (err) {
    showToast(`Failed to export CSV: ${err.message}`, false);
  } finally {
    if (exportBtn) exportBtn.disabled = false;
    if (labelEl) labelEl.textContent = originalLabel;
  }
}

// =============================================================================
// 4. Rendering & UI Updates
// =============================================================================

function showLoading() {
  state.isLoading = true;
  document.getElementById("loading-indicator").removeAttribute("hidden");
  document.getElementById("artifacts-grid").replaceChildren();
  document.getElementById("pagination-container").setAttribute("hidden", "true");
}

function hideLoading() {
  state.isLoading = false;
  document.getElementById("loading-indicator").setAttribute("hidden", "true");
}

function showError(message) {
  const banner = document.getElementById("error-banner");
  const msgEl = document.getElementById("error-message");
  msgEl.textContent = message;
  banner.removeAttribute("hidden");
}

function hideError() {
  document.getElementById("error-banner").setAttribute("hidden", "true");
}

function showEmptyState() {
  document.getElementById("empty-state").removeAttribute("hidden");
  document.getElementById("pagination-container").setAttribute("hidden", "true");
}

function hideEmptyState() {
  document.getElementById("empty-state").setAttribute("hidden", "true");
}

function updateResultsMeta(results) {
  const countEl = document.getElementById("results-count");
  const tookEl = document.getElementById("results-took");

  if (results.total === 0) {
    countEl.textContent = "No matching archival records";
    tookEl.textContent = `(${results.took_ms} ms)`;
  } else {
    const start = (results.page - 1) * results.page_size + 1;
    const end = Math.min(results.page * results.page_size, results.total);
    countEl.textContent = `Showing ${start}–${end} of ${results.total} artifacts`;
    tookEl.textContent = `(Elasticsearch responded in ${results.took_ms} ms)`;
  }
}

function renderArtifactsGrid(items) {
  const grid = document.getElementById("artifacts-grid");
  grid.replaceChildren();

  if (!items || items.length === 0) {
    showEmptyState();
    return;
  }

  for (const item of items) {
    const card = renderArtifactCard(item);
    grid.appendChild(card);
  }
}

function renderArtifactCard(item) {
  const card = el("article", {
    className: "artifact-card",
    attrs: { "aria-labelledby": `title-${item.artifact_id}` }
  });

  // Top Accession & Score Row
  const topRow = el("div", { className: "card-top" });
  const accession = el("span", {
    className: "card-accession",
    text: item.accession_number
  });
  topRow.appendChild(accession);

  if (item.relevance_score !== null && item.relevance_score !== undefined) {
    const scoreBadge = el("span", {
      className: "card-score",
      attrs: { title: "Elasticsearch BM25 Relevance Score" },
      text: `Score: ${Number(item.relevance_score).toFixed(2)}`
    });
    topRow.appendChild(scoreBadge);
  }
  card.appendChild(topRow);

  // Card Title
  const title = el("h3", {
    id: `title-${item.artifact_id}`,
    className: "card-title",
    text: item.name
  });
  card.appendChild(title);

  // Meta Badges
  const metaRow = el("div", { className: "card-meta-row" });
  metaRow.appendChild(el("span", { className: "badge badge-category", text: item.category }));
  metaRow.appendChild(el("span", { className: "badge badge-period", text: item.historical_period }));

  const condBadge = el("span", {
    className: `badge ${getConditionBadgeClass(item.condition)}`,
    text: item.condition
  });
  metaRow.appendChild(condBadge);

  const displayBadge = el("span", {
    className: `badge ${item.is_on_display ? "badge-display-active" : "badge-display-vault"}`,
    text: item.is_on_display ? "Gallery Display" : "Vault Storage"
  });
  metaRow.appendChild(displayBadge);
  card.appendChild(metaRow);

  // Description Excerpt
  const desc = el("p", {
    className: "card-description",
    text: item.description
  });
  card.appendChild(desc);

  // Metadata Details List
  const dl = el("dl", { className: "card-details-dl" });

  dl.appendChild(el("dt", { text: "Origin:" }));
  dl.appendChild(el("dd", { text: `${item.origin_culture} (${item.origin_location})` }));

  dl.appendChild(el("dt", { text: "Chronology:" }));
  dl.appendChild(el("dd", { text: formatYearRange(item.creation_year_earliest, item.creation_year_latest) }));

  if (item.medium_materials && item.medium_materials.length > 0) {
    dl.appendChild(el("dt", { text: "Materials:" }));
    dl.appendChild(el("dd", { text: item.medium_materials.join(", ") }));
  }
  card.appendChild(dl);

  // Archival Tag Chips
  const cardTags = Array.isArray(item.tags)
    ? item.tags
        .filter((t) => t !== null && t !== undefined)
        .map((t) => String(t).trim())
        .filter((t) => t.length > 0)
    : [];

  if (cardTags.length > 0) {
    const tagsWrapper = el("div", {
      className: "card-tags-wrapper",
      attrs: { "aria-label": `Archival tags for ${item.name}` }
    });
    for (const tag of cardTags) {
      tagsWrapper.appendChild(el("span", {
        className: "card-tag-chip",
        text: tag
      }));
    }
    card.appendChild(tagsWrapper);
  }

  // Footer Actions & Valuation
  const footer = el("div", { className: "card-footer" });
  const valuation = el("div", {
    className: "card-valuation",
    text: formatCurrency(item.estimated_value_usd)
  });
  footer.appendChild(valuation);

  const actions = el("div", { className: "card-actions" });

  const btnDetails = el("button", {
    className: "btn btn-secondary btn-card-action",
    attrs: { "type": "button", "aria-label": `Inspect details for ${item.name}` },
    text: "View Details"
  });
  btnDetails.addEventListener("click", () => openDetailModal(item.artifact_id));
  actions.appendChild(btnDetails);

  const btnEdit = el("button", {
    className: "btn btn-secondary btn-card-action",
    attrs: { "type": "button", "aria-label": `Edit record for ${item.name}` },
    text: "Edit"
  });
  btnEdit.addEventListener("click", () => openEditFormModal(item.artifact_id));
  actions.appendChild(btnEdit);

  const btnDelete = el("button", {
    className: "btn btn-danger btn-card-action",
    attrs: { "type": "button", "aria-label": `Delete record for ${item.name}` },
    text: "Delete"
  });
  btnDelete.addEventListener("click", () => openDeleteConfirmModal(item));
  actions.appendChild(btnDelete);

  footer.appendChild(actions);
  card.appendChild(footer);

  return card;
}

function updatePagination(results) {
  const container = document.getElementById("pagination-container");
  const prevBtn = document.getElementById("btn-prev-page");
  const nextBtn = document.getElementById("btn-next-page");
  const infoEl = document.getElementById("pagination-info");

  if (results.total_pages <= 1) {
    container.setAttribute("hidden", "true");
    return;
  }

  container.removeAttribute("hidden");
  infoEl.textContent = `Page ${results.page} of ${results.total_pages}`;
  prevBtn.disabled = results.page <= 1;
  nextBtn.disabled = results.page >= results.total_pages;
}

function renderActiveFilterChips() {
  const chipsContainer = document.getElementById("active-filters-chips");
  chipsContainer.replaceChildren();

  const addChip = (label, onRemove) => {
    const chip = el("span", { className: "filter-chip" });
    chip.appendChild(document.createTextNode(label));
    const btnRemove = el("button", {
      className: "filter-chip-remove",
      attrs: { "type": "button", "aria-label": `Remove filter: ${label}` },
      text: "×"
    });
    btnRemove.addEventListener("click", onRemove);
    chip.appendChild(btnRemove);
    chipsContainer.appendChild(chip);
  };

  if (state.query && state.query.trim()) {
    addChip(`Query: "${state.query.trim()}"`, () => {
      state.query = "";
      document.getElementById("search-input").value = "";
      document.getElementById("search-clear-btn").hidden = true;
      state.page = 1;
      executeSearch();
    });
  }
  if (state.category) {
    addChip(`Category: ${state.category}`, () => {
      state.category = "";
      document.getElementById("filter-category").value = "";
      state.page = 1;
      executeSearch();
    });
  }
  if (state.period) {
    addChip(`Period: ${state.period}`, () => {
      state.period = "";
      document.getElementById("filter-period").value = "";
      state.page = 1;
      executeSearch();
    });
  }
  if (state.condition) {
    addChip(`Condition: ${state.condition}`, () => {
      state.condition = "";
      document.getElementById("filter-condition").value = "";
      state.page = 1;
      executeSearch();
    });
  }
  if (state.displayStatus !== "") {
    const dispLabel = state.displayStatus === "true" ? "Gallery Display" : "Vault Storage";
    addChip(`Status: ${dispLabel}`, () => {
      state.displayStatus = "";
      document.getElementById("filter-display").value = "";
      state.page = 1;
      executeSearch();
    });
  }
  if (state.sortBy) {
    const sortLabels = {
      creation_year_earliest: "Creation Year",
      estimated_value_usd: "Valuation",
      acquisition_date: "Acquisition Date",
      name: "Artifact Name",
      created_at: "Date Added"
    };
    const sLabel = sortLabels[state.sortBy] || state.sortBy;
    const sOrder = state.sortOrder === "desc" ? "High-Low" : "Low-High";
    addChip(`Sorted: ${sLabel} (${sOrder})`, () => {
      state.sortBy = "";
      state.sortOrder = "asc";
      document.getElementById("filter-sort-by").value = "";
      document.getElementById("filter-sort-order").value = "asc";
      state.page = 1;
      executeSearch();
    });
  }
  if (state.fuzzy) {
    addChip("Typo-Tolerance Active", () => {
      state.fuzzy = false;
      document.getElementById("fuzzy-toggle").checked = false;
      state.page = 1;
      executeSearch();
    });
  }
}

function populateFacetDropdowns(stats) {
  if (!stats) return;

  const catSelect = document.getElementById("filter-category");
  const periodSelect = document.getElementById("filter-period");
  const condSelect = document.getElementById("filter-condition");

  const updateSelect = (select, dataDict, currentValue, label) => {
    const existingVal = currentValue || select.value;
    select.replaceChildren();
    select.appendChild(el("option", { attrs: { "value": "" }, text: `All ${label}` }));

    const sortedEntries = Object.entries(dataDict || {}).sort((a, b) => b[1] - a[1]);
    for (const [name, count] of sortedEntries) {
      const opt = el("option", {
        attrs: { "value": name },
        text: `${name} (${count})`
      });
      if (name === existingVal) opt.selected = true;
      select.appendChild(opt);
    }
  };

  updateSelect(catSelect, stats.categories, state.category, "Categories");
  updateSelect(periodSelect, stats.historical_periods, state.period, "Periods");
  updateSelect(condSelect, stats.conditions, state.condition, "Conditions");
}

function renderAnalytics(stats) {
  if (!stats) return;

  document.getElementById("stat-total-count").textContent = Number(stats.total_artifacts).toLocaleString();
  document.getElementById("stat-total-valuation").textContent = formatCurrency(stats.valuation?.sum || 0);
  document.getElementById("stat-avg-valuation").textContent = formatCurrency(stats.valuation?.avg || 0);

  const minVal = formatCurrency(stats.valuation?.min || 0);
  const maxVal = formatCurrency(stats.valuation?.max || 0);
  document.getElementById("stat-valuation-range").textContent = `${minVal} – ${maxVal}`;

  const renderTable = (tbodyId, buckets, total) => {
    const tbody = document.getElementById(tbodyId);
    tbody.replaceChildren();

    const entries = Object.entries(buckets || {}).sort((a, b) => b[1] - a[1]);
    if (entries.length === 0) {
      tbody.appendChild(el("tr", {}, el("td", { attrs: { colspan: "3" }, className: "text-muted", text: "No data recorded." })));
      return;
    }

    for (const [key, count] of entries) {
      const pct = total > 0 ? ((count / total) * 100).toFixed(1) : 0;
      const tr = el("tr");
      tr.appendChild(el("td", { text: key }));
      tr.appendChild(el("td", { className: "num-col", text: String(count) }));

      const barTd = el("td", {
        className: "bar-col",
        attrs: { "aria-label": `${pct}% of collection (${count} artifacts)` }
      });
      const track = el("div", {
        className: "stat-bar-track",
        attrs: {
          "role": "progressbar",
          "aria-valuenow": String(pct),
          "aria-valuemin": "0",
          "aria-valuemax": "100",
          "aria-label": `${key}: ${pct}%`,
          "title": `${pct}% (${count} artifacts)`
        }
      });
      const fill = el("div", { className: "stat-bar-fill" });
      fill.style.width = `${pct}%`;
      track.appendChild(fill);
      barTd.appendChild(track);
      tr.appendChild(barTd);

      tbody.appendChild(tr);
    }
  };

  const total = stats.total_artifacts || 1;
  renderTable("tbody-categories", stats.categories, total);
  renderTable("tbody-periods", stats.historical_periods, total);
  renderTable("tbody-conditions", stats.conditions, total);
}

// =============================================================================
// 5. Accessible Modal & Dialog Management (Focus Trapping & Escape Listener)
// =============================================================================

function openModal(modalId) {
  state.lastFocusedElement = document.activeElement;
  const modal = document.getElementById(modalId);
  if (!modal) return;

  modal.removeAttribute("hidden");
  state.activeModal = modalId;
  document.body.style.overflow = "hidden";

  // Trap focus to first focusable element inside the modal
  const focusable = modal.querySelectorAll(
    "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
  );
  if (focusable.length > 0) {
    focusable[0].focus();
  }
}

function closeModal(modalId) {
  const targetId = modalId || state.activeModal;
  if (!targetId) return;

  const modal = document.getElementById(targetId);
  if (modal) {
    modal.setAttribute("hidden", "true");
  }

  state.activeModal = null;
  document.body.style.overflow = "";

  if (state.lastFocusedElement && typeof state.lastFocusedElement.focus === "function") {
    state.lastFocusedElement.focus();
    state.lastFocusedElement = null;
  }
}

function setupModalKeyListeners() {
  document.addEventListener("keydown", (event) => {
    if (!state.activeModal) return;

    if (event.key === "Escape") {
      event.preventDefault();
      closeModal(state.activeModal);
      return;
    }

    if (event.key === "Tab") {
      const modal = document.getElementById(state.activeModal);
      if (!modal) return;

      const focusable = Array.from(
        modal.querySelectorAll(
          "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
        )
      ).filter((el) => !el.hidden && el.offsetParent !== null && window.getComputedStyle(el).display !== "none");

      if (focusable.length === 0) return;

      const firstEl = focusable[0];
      const lastEl = focusable[focusable.length - 1];

      if (event.shiftKey && document.activeElement === firstEl) {
        event.preventDefault();
        lastEl.focus();
      } else if (!event.shiftKey && document.activeElement === lastEl) {
        event.preventDefault();
        firstEl.focus();
      }
    }
  });

  // Close modals when clicking backdrop overlay
  for (const modalId of ["detail-modal", "form-modal", "delete-modal"]) {
    const modalEl = document.getElementById(modalId);
    if (modalEl) {
      modalEl.addEventListener("click", (evt) => {
        if (evt.target === modalEl) {
          closeModal(modalId);
        }
      });
    }
  }
}

function showToast(message, isSuccess = true) {
  const toast = document.getElementById("toast-notification");
  const msgEl = document.getElementById("toast-message");
  msgEl.textContent = message;

  toast.className = `toast-notification ${isSuccess ? "toast-success" : "toast-error"}`;
  toast.removeAttribute("hidden");

  setTimeout(() => {
    toast.setAttribute("hidden", "true");
  }, 4000);
}

// =============================================================================
// 6. Detail Inspection Modal (All 25 Mapped Attributes)
// =============================================================================

async function openDetailModal(artifactId) {
  try {
    const doc = await getArtifactById(artifactId);

    document.getElementById("detail-title").textContent = doc.name;
    document.getElementById("detail-accession").textContent = doc.accession_number;

    const altEl = document.getElementById("detail-alternate");
    if (doc.alternate_names) {
      altEl.textContent = `Also known as: ${doc.alternate_names}`;
      altEl.style.display = "block";
    } else {
      altEl.textContent = "";
      altEl.style.display = "none";
    }

    // Badges
    const badgeDisp = document.getElementById("detail-badge-display");
    badgeDisp.textContent = doc.is_on_display ? "Gallery Display" : "Vault Storage";
    badgeDisp.className = `badge ${doc.is_on_display ? "badge-display-active" : "badge-display-vault"}`;

    const badgeCond = document.getElementById("detail-badge-condition");
    badgeCond.textContent = doc.condition;
    badgeCond.className = `badge ${getConditionBadgeClass(doc.condition)}`;

    document.getElementById("detail-badge-category").textContent = doc.category;
    document.getElementById("detail-badge-period").textContent = doc.historical_period;

    // Origins & Specs
    document.getElementById("detail-origin-culture").textContent = doc.origin_culture;
    document.getElementById("detail-origin-location").textContent = doc.origin_location;
    document.getElementById("detail-creation-years").textContent = formatYearRange(
      doc.creation_year_earliest,
      doc.creation_year_latest
    );
    document.getElementById("detail-storage-location").textContent = doc.storage_location;

    document.getElementById("detail-materials").textContent = (doc.medium_materials || []).join(", ") || "-";

    const dims = doc.dimensions || {};
    const dimsArr = [];
    if (dims.height_cm) dimsArr.push(`${dims.height_cm} cm (H)`);
    if (dims.width_cm) dimsArr.push(`${dims.width_cm} cm (W)`);
    if (dims.depth_cm) dimsArr.push(`${dims.depth_cm} cm (D)`);
    document.getElementById("detail-dimensions").textContent = dimsArr.length > 0 ? dimsArr.join(" × ") : "Not recorded";
    document.getElementById("detail-weight").textContent = dims.weight_kg ? `${dims.weight_kg} kg` : "Not recorded";

    document.getElementById("detail-valuation").textContent = formatCurrency(doc.estimated_value_usd);
    document.getElementById("detail-acquisition-method").textContent = doc.acquisition_method;
    document.getElementById("detail-acquisition-date").textContent = doc.acquisition_date;

    // Curatorial Texts
    document.getElementById("detail-description").textContent = doc.description;
    document.getElementById("detail-historical-context").textContent = doc.historical_context;
    document.getElementById("detail-provenance").textContent = doc.provenance;
    document.getElementById("detail-restoration-notes").textContent = doc.restoration_notes;

    // Tags
    const tagsContainer = document.getElementById("detail-tags-list");
    tagsContainer.replaceChildren();
    const modalTags = Array.isArray(doc.tags)
      ? doc.tags
          .filter((t) => t !== null && t !== undefined)
          .map((t) => String(t).trim())
          .filter((t) => t.length > 0)
      : [];

    if (modalTags.length > 0) {
      for (const t of modalTags) {
        tagsContainer.appendChild(el("span", { className: "tag-pill", text: t }));
      }
    } else {
      tagsContainer.appendChild(el("span", { className: "text-muted", text: "No archival tags recorded" }));
    }

    // System Audit
    document.getElementById("detail-doc-id").textContent = doc.artifact_id;
    document.getElementById("detail-created-at").textContent = doc.created_at || "Initial seeding";
    document.getElementById("detail-updated-at").textContent = doc.updated_at || "Not modified";

    // Set up edit button click handler
    const btnEdit = document.getElementById("btn-detail-edit");
    const newBtnEdit = btnEdit.cloneNode(true);
    btnEdit.parentNode.replaceChild(newBtnEdit, btnEdit);
    newBtnEdit.addEventListener("click", () => {
      closeModal("detail-modal");
      openEditFormModal(doc.artifact_id);
    });

    openModal("detail-modal");
  } catch (err) {
    showToast(`Error retrieving details: ${err.message}`, false);
  }
}

// =============================================================================
// 7. Artifact Create and Edit Forms (CRUD)
// =============================================================================

function openCreateFormModal() {
  state.editingArtifactId = null;
  const form = document.getElementById("artifact-form");
  form.reset();

  document.getElementById("form-mode").value = "create";
  document.getElementById("form-modal-title").textContent = "Register New Archival Artifact";
  document.getElementById("form-modal-subtitle").textContent =
    "Submit a new artifact record to the Elasticsearch index with complete provenance";

  const idInput = document.getElementById("form-artifact-id");
  idInput.disabled = false;
  idInput.readOnly = false;

  const accInput = document.getElementById("form-accession-number");
  accInput.disabled = false;
  accInput.readOnly = false;

  // Set default acquisition date to today
  document.getElementById("form-acq-date").value = new Date().toISOString().split("T")[0];
  document.getElementById("form-display").checked = true;

  document.getElementById("form-error-alert").setAttribute("hidden", "true");
  openModal("form-modal");
}

function ensureSelectOption(selectEl, value) {
  if (!value || !selectEl) return;
  const exists = Array.from(selectEl.options).some((opt) => opt.value.toLowerCase() === value.toLowerCase());
  if (!exists) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = value;
    selectEl.appendChild(opt);
  }
}

async function openEditFormModal(artifactId) {
  try {
    const doc = await getArtifactById(artifactId);
    state.editingArtifactId = artifactId;

    const form = document.getElementById("artifact-form");
    form.reset();

    document.getElementById("form-mode").value = "edit";
    document.getElementById("form-modal-title").textContent = "Edit Archival Artifact Record";
    document.getElementById("form-modal-subtitle").textContent = `Modifying document ${doc.artifact_id} (${doc.accession_number})`;

    // Primary ID and Accession number cannot be modified in Elasticsearch PUT
    const idInput = document.getElementById("form-artifact-id");
    idInput.value = doc.artifact_id;
    idInput.disabled = true;

    const accInput = document.getElementById("form-accession-number");
    accInput.value = doc.accession_number;
    accInput.readOnly = true;

    document.getElementById("form-name").value = doc.name;
    document.getElementById("form-alternate-names").value = doc.alternate_names || "";

    const catSelect = document.getElementById("form-category");
    ensureSelectOption(catSelect, doc.category);
    catSelect.value = doc.category;

    document.getElementById("form-period").value = doc.historical_period;
    document.getElementById("form-origin-culture").value = doc.origin_culture;
    document.getElementById("form-origin-location").value = doc.origin_location;
    document.getElementById("form-year-earliest").value = doc.creation_year_earliest;
    document.getElementById("form-year-latest").value = doc.creation_year_latest;
    document.getElementById("form-materials").value = (doc.medium_materials || []).join(", ");

    const dims = doc.dimensions || {};
    document.getElementById("form-dim-height").value = dims.height_cm !== null ? dims.height_cm : "";
    document.getElementById("form-dim-width").value = dims.width_cm !== null ? dims.width_cm : "";
    document.getElementById("form-dim-depth").value = dims.depth_cm !== null ? dims.depth_cm : "";
    document.getElementById("form-dim-weight").value = dims.weight_kg !== null ? dims.weight_kg : "";

    const condSelect = document.getElementById("form-condition");
    ensureSelectOption(condSelect, doc.condition);
    condSelect.value = doc.condition;

    document.getElementById("form-storage").value = doc.storage_location;
    document.getElementById("form-display").checked = Boolean(doc.is_on_display);
    document.getElementById("form-value").value = doc.estimated_value_usd;
    document.getElementById("form-acq-date").value = doc.acquisition_date;

    const acqSelect = document.getElementById("form-acq-method");
    ensureSelectOption(acqSelect, doc.acquisition_method);
    acqSelect.value = doc.acquisition_method;

    document.getElementById("form-description").value = doc.description;
    document.getElementById("form-historical-context").value = doc.historical_context;
    document.getElementById("form-provenance").value = doc.provenance;
    document.getElementById("form-restoration-notes").value = doc.restoration_notes;
    document.getElementById("form-tags").value = (doc.tags || []).join(", ");

    document.getElementById("form-error-alert").setAttribute("hidden", "true");
    openModal("form-modal");
  } catch (err) {
    showToast(`Failed to load artifact for editing: ${err.message}`, false);
  }
}

async function handleFormSubmit(event) {
  event.preventDefault();
  const errorAlert = document.getElementById("form-error-alert");
  const errorText = document.getElementById("form-error-text");
  errorAlert.setAttribute("hidden", "true");

  const mode = document.getElementById("form-mode").value;
  const submitBtn = document.getElementById("btn-submit-form");

  try {
    const rawMaterials = document.getElementById("form-materials").value;
    const materials = rawMaterials.split(",").map((s) => s.trim()).filter(Boolean);
    if (materials.length === 0) {
      document.getElementById("form-materials").focus();
      throw new Error("At least one material must be specified (comma-separated).");
    }

    const rawTags = document.getElementById("form-tags").value;
    const tags = rawTags.split(",").map((s) => s.trim()).filter(Boolean);

    const parseNum = (val) => (val !== "" && !isNaN(val) ? Number(val) : null);

    const dimensions = {
      height_cm: parseNum(document.getElementById("form-dim-height").value),
      width_cm: parseNum(document.getElementById("form-dim-width").value),
      depth_cm: parseNum(document.getElementById("form-dim-depth").value),
      weight_kg: parseNum(document.getElementById("form-dim-weight").value)
    };

    const payload = {
      name: document.getElementById("form-name").value.trim(),
      alternate_names: document.getElementById("form-alternate-names").value.trim() || null,
      category: document.getElementById("form-category").value,
      historical_period: document.getElementById("form-period").value.trim(),
      origin_culture: document.getElementById("form-origin-culture").value.trim(),
      origin_location: document.getElementById("form-origin-location").value.trim(),
      creation_year_earliest: parseInt(document.getElementById("form-year-earliest").value, 10),
      creation_year_latest: parseInt(document.getElementById("form-year-latest").value, 10),
      medium_materials: materials,
      dimensions: dimensions,
      condition: document.getElementById("form-condition").value,
      is_on_display: document.getElementById("form-display").checked,
      storage_location: document.getElementById("form-storage").value.trim(),
      estimated_value_usd: parseFloat(document.getElementById("form-value").value),
      acquisition_date: document.getElementById("form-acq-date").value,
      acquisition_method: document.getElementById("form-acq-method").value,
      description: document.getElementById("form-description").value.trim(),
      historical_context: document.getElementById("form-historical-context").value.trim(),
      provenance: document.getElementById("form-provenance").value.trim(),
      restoration_notes: document.getElementById("form-restoration-notes").value.trim(),
      tags: tags
    };

    // Client-side validation checks
    if (!payload.name) {
      document.getElementById("form-name").focus();
      throw new Error("Artifact Title / Name is required.");
    }
    if (!payload.category) {
      document.getElementById("form-category").focus();
      throw new Error("Please select an Archival Category.");
    }
    if (!payload.historical_period) {
      document.getElementById("form-period").focus();
      throw new Error("Historical Period is required.");
    }
    if (!payload.origin_culture) {
      document.getElementById("form-origin-culture").focus();
      throw new Error("Origin Culture / Civilization is required.");
    }
    if (!payload.origin_location) {
      document.getElementById("form-origin-location").focus();
      throw new Error("Geographic Origin Location is required.");
    }
    if (isNaN(payload.creation_year_earliest)) {
      document.getElementById("form-year-earliest").focus();
      throw new Error("Earliest Creation Year must be a valid integer year (negative for BCE).");
    }
    if (isNaN(payload.creation_year_latest)) {
      document.getElementById("form-year-latest").focus();
      throw new Error("Latest Creation Year must be a valid integer year.");
    }
    if (payload.creation_year_earliest > payload.creation_year_latest) {
      document.getElementById("form-year-earliest").focus();
      throw new Error("Earliest Creation Year cannot be greater than Latest Creation Year.");
    }
    if (!payload.condition) {
      document.getElementById("form-condition").focus();
      throw new Error("Please select a Conservation Condition.");
    }
    if (!payload.storage_location) {
      document.getElementById("form-storage").focus();
      throw new Error("Storage / Gallery Location is required.");
    }
    if (isNaN(payload.estimated_value_usd) || payload.estimated_value_usd < 0) {
      document.getElementById("form-value").focus();
      throw new Error("Estimated Valuation (USD) must be a non-negative number.");
    }
    if (!payload.acquisition_date) {
      document.getElementById("form-acq-date").focus();
      throw new Error("Acquisition Date is required.");
    }
    if (!payload.acquisition_method) {
      document.getElementById("form-acq-method").focus();
      throw new Error("Please select an Acquisition Method.");
    }
    if (!payload.description) {
      document.getElementById("form-description").focus();
      throw new Error("Physical Description is required.");
    }
    if (!payload.historical_context) {
      document.getElementById("form-historical-context").focus();
      throw new Error("Historical Context is required.");
    }
    if (!payload.provenance) {
      document.getElementById("form-provenance").focus();
      throw new Error("Provenance & Custody History is required.");
    }
    if (!payload.restoration_notes) {
      document.getElementById("form-restoration-notes").focus();
      throw new Error("Restoration & Conservation Notes are required.");
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Saving...";

    if (mode === "create") {
      const artifactId = document.getElementById("form-artifact-id").value.trim();
      const accessionNumber = document.getElementById("form-accession-number").value.trim();
      if (!artifactId) {
        document.getElementById("form-artifact-id").focus();
        throw new Error("Document ID is required (e.g. art-031).");
      }
      if (!accessionNumber) {
        document.getElementById("form-accession-number").focus();
        throw new Error("Accession Number is required (e.g. ARC-2024-031).");
      }

      payload.artifact_id = artifactId;
      payload.accession_number = accessionNumber;

      await createArtifactRecord(payload);
      showToast(`Artifact '${payload.name}' registered successfully!`);
    } else {
      await updateArtifactRecord(state.editingArtifactId, payload);
      showToast(`Artifact '${payload.name}' updated successfully!`);
    }

    closeModal("form-modal");
    await executeSearch();
    await fetchStats();
    await checkSystemHealth();
  } catch (err) {
    errorText.textContent = err.message || "An error occurred while saving the artifact.";
    errorAlert.removeAttribute("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Save Artifact Record";
  }
}

// =============================================================================
// 8. Permanent Deletion Confirmation
// =============================================================================

function openDeleteConfirmModal(item) {
  state.deletingArtifact = item;
  document.getElementById("delete-target-accession").textContent = item.accession_number;
  document.getElementById("delete-target-name").textContent = item.name;
  document.getElementById("delete-target-id").textContent = `Document ID: ${item.artifact_id}`;
  openModal("delete-modal");
}

async function handleConfirmDelete() {
  if (!state.deletingArtifact) return;

  const btn = document.getElementById("btn-confirm-delete");
  btn.disabled = true;
  btn.textContent = "Deleting...";

  try {
    await deleteArtifactRecord(state.deletingArtifact.artifact_id);
    showToast(`Artifact '${state.deletingArtifact.name}' was permanently deleted.`);
    closeModal("delete-modal");
    state.deletingArtifact = null;

    await executeSearch();
    await fetchStats();
    await checkSystemHealth();
  } catch (err) {
    showToast(`Delete failed: ${err.message}`, false);
  } finally {
    btn.disabled = false;
    btn.textContent = "Delete Permanently";
  }
}

// =============================================================================
// 9. Two-Stage CSV Import Workflow (Preview, Validation, & Bulk Creation)
// =============================================================================

function openImportModal() {
  state.importFile = null;
  state.importPreviewData = null;

  const fileInput = document.getElementById("import-file-input");
  if (fileInput) fileInput.value = "";

  const btnPreview = document.getElementById("btn-preview-import");
  if (btnPreview) btnPreview.disabled = true;

  const btnConfirm = document.getElementById("btn-confirm-import");
  if (btnConfirm) {
    btnConfirm.disabled = true;
    btnConfirm.textContent = "Confirm & Import to Archive";
  }

  const loadingEl = document.getElementById("import-loading");
  if (loadingEl) loadingEl.setAttribute("hidden", "true");

  const resultsContainer = document.getElementById("import-results-container");
  if (resultsContainer) resultsContainer.setAttribute("hidden", "true");

  const errorsWrapper = document.getElementById("import-errors-wrapper");
  if (errorsWrapper) errorsWrapper.setAttribute("hidden", "true");

  const previewWrapper = document.getElementById("import-preview-wrapper");
  if (previewWrapper) previewWrapper.setAttribute("hidden", "true");

  const tbodyErrors = document.getElementById("tbody-import-errors");
  if (tbodyErrors) tbodyErrors.replaceChildren();

  const tbodyPreview = document.getElementById("tbody-import-preview");
  if (tbodyPreview) tbodyPreview.replaceChildren();

  openModal("import-modal");
}

function handleImportFileSelected(event) {
  const file = event.target.files?.[0];
  state.importFile = file || null;

  const btnPreview = document.getElementById("btn-preview-import");
  if (btnPreview) btnPreview.disabled = !file;

  const btnConfirm = document.getElementById("btn-confirm-import");
  if (btnConfirm) btnConfirm.disabled = true;

  const resultsContainer = document.getElementById("import-results-container");
  if (resultsContainer) resultsContainer.setAttribute("hidden", "true");
}

async function handlePreviewImport() {
  if (!state.importFile) return;

  const btnPreview = document.getElementById("btn-preview-import");
  const btnConfirm = document.getElementById("btn-confirm-import");
  const loadingEl = document.getElementById("import-loading");
  const resultsContainer = document.getElementById("import-results-container");

  btnPreview.disabled = true;
  btnConfirm.disabled = true;
  loadingEl.removeAttribute("hidden");
  resultsContainer.setAttribute("hidden", "true");

  try {
    const formData = new FormData();
    formData.append("file", state.importFile);

    const response = await fetch(`${API_BASE_URL}/artifacts/import/preview`, {
      method: "POST",
      body: formData
    });

    const data = await response.json();
    loadingEl.setAttribute("hidden", "true");

    if (!response.ok) {
      const msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      throw new Error(msg || `Preview analysis failed (HTTP ${response.status})`);
    }

    state.importPreviewData = data;
    renderImportPreview(data);
    resultsContainer.removeAttribute("hidden");
  } catch (err) {
    loadingEl.setAttribute("hidden", "true");
    showToast(`Validation preview error: ${err.message}`, false);
  } finally {
    btnPreview.disabled = false;
  }
}

function renderImportPreview(data) {
  document.getElementById("import-badge-total").textContent = String(data.total_rows || 0);
  document.getElementById("import-badge-valid").textContent = String(data.valid_rows || 0);
  document.getElementById("import-badge-error").textContent = String(data.error_rows || 0);
  document.getElementById("import-badge-duplicates").textContent = String(data.duplicate_file_ids?.length || 0);
  document.getElementById("import-badge-conflicts").textContent = String(data.existing_collection_ids?.length || 0);

  const banner = document.getElementById("import-status-banner");
  const icon = document.getElementById("import-status-icon");
  const msg = document.getElementById("import-status-message");
  const btnConfirm = document.getElementById("btn-confirm-import");

  if (data.can_import) {
    banner.className = "import-status-banner status-success";
    icon.textContent = "✓";
    msg.textContent = `All ${data.valid_rows} artifact records are valid and ready to be imported into the archival registry.`;
    btnConfirm.disabled = false;
  } else {
    banner.className = "import-status-banner status-error";
    icon.textContent = "⚠";
    const errCount = data.errors?.length || 0;
    const rowCount = data.error_rows || 0;
    msg.textContent = `Found ${errCount} issue(s) across ${rowCount} row(s). All validation errors must be resolved in your CSV before importing.`;
    btnConfirm.disabled = true;
  }

  const errorsWrapper = document.getElementById("import-errors-wrapper");
  const tbodyErrors = document.getElementById("tbody-import-errors");
  tbodyErrors.replaceChildren();

  if (data.errors && data.errors.length > 0) {
    document.getElementById("import-errors-count").textContent = String(data.errors.length);
    errorsWrapper.removeAttribute("hidden");

    for (const err of data.errors) {
      const tr = el("tr");
      tr.appendChild(el("td", { text: err.row_number > 0 ? `Row ${err.row_number}` : "System" }));
      tr.appendChild(el("td", {}, el("code", { text: err.artifact_id || "—" })));
      tr.appendChild(el("td", {}, el("code", { text: err.field || "general" })));
      tr.appendChild(el("td", { className: "text-danger", text: err.message }));
      tbodyErrors.appendChild(tr);
    }
  } else {
    errorsWrapper.setAttribute("hidden", "true");
  }

  const previewWrapper = document.getElementById("import-preview-wrapper");
  const tbodyPreview = document.getElementById("tbody-import-preview");
  tbodyPreview.replaceChildren();

  if (data.preview_records && data.preview_records.length > 0) {
    document.getElementById("import-preview-count").textContent = String(data.preview_records.length);
    previewWrapper.removeAttribute("hidden");

    for (const rec of data.preview_records) {
      const tr = el("tr");
      tr.appendChild(el("td", {}, el("code", { text: rec.artifact_id })));
      tr.appendChild(el("td", { text: rec.accession_number }));
      tr.appendChild(el("td", { text: rec.name }));
      tr.appendChild(el("td", { text: rec.category }));
      tr.appendChild(el("td", { text: rec.historical_period }));
      tr.appendChild(el("td", { className: "num-col", text: formatCurrency(rec.estimated_value_usd || 0) }));
      const displayBadge = el("span", {
        className: `badge ${rec.is_on_display ? "badge-display" : "badge-vault"}`,
        text: rec.is_on_display ? "Display" : "Storage"
      });
      const tdDisp = el("td");
      tdDisp.appendChild(displayBadge);
      tr.appendChild(tdDisp);
      tbodyPreview.appendChild(tr);
    }
  } else {
    previewWrapper.setAttribute("hidden", "true");
  }
}

async function handleConfirmImport() {
  if (!state.importFile || !state.importPreviewData?.can_import) return;

  const btnConfirm = document.getElementById("btn-confirm-import");
  const btnCancel = document.getElementById("btn-cancel-import");
  const originalConfirmText = btnConfirm.textContent;

  btnConfirm.disabled = true;
  btnCancel.disabled = true;
  btnConfirm.textContent = "Importing Records...";

  try {
    const formData = new FormData();
    formData.append("file", state.importFile);

    const response = await fetch(`${API_BASE_URL}/artifacts/import`, {
      method: "POST",
      body: formData
    });

    const data = await response.json();

    if (!response.ok) {
      let detailMsg = `Import failed (HTTP ${response.status})`;
      if (data && data.detail) {
        if (typeof data.detail === "string") {
          detailMsg = data.detail;
        } else if (data.detail.message) {
          detailMsg = data.detail.message;
        }
      }
      throw new Error(detailMsg);
    }

    showToast(`Successfully imported ${data.imported_count} artifact records into the collection!`);
    closeModal("import-modal");

    await executeSearch();
    await fetchStats();
    await checkSystemHealth();
  } catch (err) {
    showToast(`Import rejected: ${err.message}`, false);
    await handlePreviewImport();
  } finally {
    btnConfirm.disabled = false;
    btnCancel.disabled = false;
    btnConfirm.textContent = originalConfirmText;
  }
}

// =============================================================================
// 10. Event Listeners & Application Bootstrapping
// =============================================================================

function attachEventListeners() {
  // Search Form & Input
  const searchForm = document.getElementById("search-form");
  const searchInput = document.getElementById("search-input");
  const searchClear = document.getElementById("search-clear-btn");
  const fuzzyToggle = document.getElementById("fuzzy-toggle");

  searchForm.addEventListener("submit", (e) => {
    e.preventDefault();
    state.query = searchInput.value;
    state.page = 1;
    executeSearch();
  });

  searchInput.addEventListener("input", () => {
    const val = searchInput.value;
    searchClear.hidden = val.length === 0;

    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      state.query = val;
      state.page = 1;
      executeSearch();
    }, 300);
  });

  searchClear.addEventListener("click", () => {
    searchInput.value = "";
    searchClear.hidden = true;
    state.query = "";
    state.page = 1;
    searchInput.focus();
    executeSearch();
  });

  fuzzyToggle.addEventListener("change", () => {
    state.fuzzy = fuzzyToggle.checked;
    state.page = 1;
    executeSearch();
  });

  // Sidebar Filter Controls
  document.getElementById("filter-category").addEventListener("change", (e) => {
    state.category = e.target.value;
    state.page = 1;
    executeSearch();
  });

  document.getElementById("filter-period").addEventListener("change", (e) => {
    state.period = e.target.value;
    state.page = 1;
    executeSearch();
  });

  document.getElementById("filter-condition").addEventListener("change", (e) => {
    state.condition = e.target.value;
    state.page = 1;
    executeSearch();
  });

  document.getElementById("filter-display").addEventListener("change", (e) => {
    state.displayStatus = e.target.value;
    state.page = 1;
    executeSearch();
  });

  document.getElementById("filter-sort-by").addEventListener("change", (e) => {
    state.sortBy = e.target.value;
    state.page = 1;
    executeSearch();
  });

  document.getElementById("filter-sort-order").addEventListener("change", (e) => {
    state.sortOrder = e.target.value;
    state.page = 1;
    executeSearch();
  });

  const resetAllFilters = () => {
    state.query = "";
    state.category = "";
    state.period = "";
    state.condition = "";
    state.displayStatus = "";
    state.sortBy = "";
    state.sortOrder = "asc";
    state.fuzzy = false;
    state.page = 1;

    searchInput.value = "";
    searchClear.hidden = true;
    fuzzyToggle.checked = false;
    document.getElementById("filter-category").value = "";
    document.getElementById("filter-period").value = "";
    document.getElementById("filter-condition").value = "";
    document.getElementById("filter-display").value = "";
    document.getElementById("filter-sort-by").value = "";
    document.getElementById("filter-sort-order").value = "asc";

    executeSearch();
  };

  document.getElementById("btn-reset-filters").addEventListener("click", resetAllFilters);
  document.getElementById("btn-empty-reset")?.addEventListener("click", resetAllFilters);
  const retryAll = async () => {
    await checkSystemHealth();
    await fetchStats();
    await executeSearch();
  };
  document.getElementById("btn-retry-search").addEventListener("click", retryAll);

  // Pagination & Page Size
  document.getElementById("btn-prev-page").addEventListener("click", () => {
    if (state.page > 1) {
      state.page -= 1;
      executeSearch();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  });

  document.getElementById("btn-next-page").addEventListener("click", () => {
    if (state.page < state.totalPages) {
      state.page += 1;
      executeSearch();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  });

  document.getElementById("page-size-select").addEventListener("change", (e) => {
    state.pageSize = parseInt(e.target.value, 10);
    state.page = 1;
    executeSearch();
  });

  // CSV Export Action
  const exportBtn = document.getElementById("btn-export-csv");
  if (exportBtn) {
    exportBtn.addEventListener("click", exportCollectionCsv);
  }

  // CSV Import Actions
  const btnOpenImport = document.getElementById("btn-open-import");
  if (btnOpenImport) {
    btnOpenImport.addEventListener("click", openImportModal);
  }
  const btnCloseImport = document.getElementById("btn-close-import");
  if (btnCloseImport) {
    btnCloseImport.addEventListener("click", () => closeModal("import-modal"));
  }
  const btnCancelImport = document.getElementById("btn-cancel-import");
  if (btnCancelImport) {
    btnCancelImport.addEventListener("click", () => closeModal("import-modal"));
  }
  const importFileInput = document.getElementById("import-file-input");
  if (importFileInput) {
    importFileInput.addEventListener("change", handleImportFileSelected);
  }
  const btnPreviewImport = document.getElementById("btn-preview-import");
  if (btnPreviewImport) {
    btnPreviewImport.addEventListener("click", handlePreviewImport);
  }
  const btnConfirmImport = document.getElementById("btn-confirm-import");
  if (btnConfirmImport) {
    btnConfirmImport.addEventListener("click", handleConfirmImport);
  }

  // Analytics Toggle
  const toggleAnalyticsBtn = document.getElementById("btn-toggle-analytics");
  const analyticsPanel = document.getElementById("analytics-panel");
  const closeAnalyticsBtn = document.getElementById("btn-close-analytics");

  const setAnalyticsState = (isOpen) => {
    state.analyticsOpen = isOpen;
    toggleAnalyticsBtn.setAttribute("aria-expanded", String(isOpen));
    if (isOpen) {
      analyticsPanel.removeAttribute("hidden");
      fetchStats();
    } else {
      analyticsPanel.setAttribute("hidden", "true");
    }
  };

  toggleAnalyticsBtn.addEventListener("click", () => setAnalyticsState(!state.analyticsOpen));
  closeAnalyticsBtn.addEventListener("click", () => setAnalyticsState(false));

  // Modal Open / Close Buttons
  document.getElementById("btn-open-create").addEventListener("click", openCreateFormModal);
  document.getElementById("btn-close-detail").addEventListener("click", () => closeModal("detail-modal"));
  document.getElementById("btn-detail-close-footer").addEventListener("click", () => closeModal("detail-modal"));
  document.getElementById("btn-close-form").addEventListener("click", () => closeModal("form-modal"));
  document.getElementById("btn-cancel-form").addEventListener("click", () => closeModal("form-modal"));
  document.getElementById("btn-close-delete").addEventListener("click", () => closeModal("delete-modal"));
  document.getElementById("btn-cancel-delete").addEventListener("click", () => closeModal("delete-modal"));

  // Form Submission & Deletion
  document.getElementById("artifact-form").addEventListener("submit", handleFormSubmit);
  document.getElementById("btn-confirm-delete").addEventListener("click", handleConfirmDelete);

  setupModalKeyListeners();
}

// Bootstrapping
document.addEventListener("DOMContentLoaded", async () => {
  attachEventListeners();
  await checkSystemHealth();
  await fetchStats();
  await executeSearch();
});

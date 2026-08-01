(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      let detail = body.detail;
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
      throw new Error(detail || `${res.status} ${res.statusText}`);
    }
    return res.json();
  }

  // ---- Settings menu --------------------------------------------------------

  const sourceToggle = $("#source-toggle");
  const sourceDropdown = $("#source-dropdown");
  const localUploadMenuBtn = $("#local-upload-menu-btn");
  const localUploadInput = $("#local-upload-input");
  const websiteLinkMenuBtn = $("#website-link-menu-btn");
  const websiteLinkModal = $("#website-link-modal");
  const websiteLinkInput = $("#website-link-input");
  const websiteLinkTitleInput = $("#website-link-title-input");
  const websiteLinkMessage = $("#website-link-message");
  const websiteLinkOpenBtn = $("#website-link-open-btn");
  const websiteLinkModalClose = $("#website-link-modal-close");
  const themeToggle = $("#theme-toggle");
  const settingsToggle = $("#settings-toggle");
  const settingsDropdown = $("#settings-dropdown");
  const telegramAdvancedModal = $("#telegram-advanced-modal");

  let themePreference = localStorage.getItem("themePreference") || "";
  const appHeader = document.querySelector("header");

  function syncHeaderOffset() {
    if (!appHeader) return;
    document.documentElement.style.setProperty("--app-header-height", `${appHeader.offsetHeight}px`);
  }

  function applyThemePreference() {
    if (themePreference === "light" || themePreference === "dark") {
      document.documentElement.setAttribute("data-theme", themePreference);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    const isLight = document.documentElement.getAttribute("data-theme") === "light"
      || (!document.documentElement.hasAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: light)").matches);
    themeToggle.textContent = isLight ? "☀️" : "🌙";
    themeToggle.title = isLight ? "Switch to dark theme" : "Switch to light theme";
  }

  function toggleThemePreference() {
    const isCurrentlyLight = document.documentElement.getAttribute("data-theme") === "light"
      || (!document.documentElement.hasAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: light)").matches);
    themePreference = isCurrentlyLight ? "dark" : "light";
    localStorage.setItem("themePreference", themePreference);
    applyThemePreference();
  }

  applyThemePreference();
  syncHeaderOffset();

  if (appHeader) {
    if ("ResizeObserver" in window) {
      new ResizeObserver(() => syncHeaderOffset()).observe(appHeader);
    } else {
      window.addEventListener("resize", syncHeaderOffset);
    }
  }

  sourceToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    const isVisible = sourceDropdown.style.display === "block";
    sourceDropdown.style.display = isVisible ? "none" : "block";
    settingsDropdown.style.display = "none";
  });

  settingsToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    const isVisible = settingsDropdown.style.display === "block";
    settingsDropdown.style.display = isVisible ? "none" : "block";
    sourceDropdown.style.display = "none";
  });

  // Close dropdown when clicking outside
  document.addEventListener("click", () => {
    sourceDropdown.style.display = "none";
    settingsDropdown.style.display = "none";
  });

  sourceDropdown.addEventListener("click", (e) => {
    e.stopPropagation();
  });

  settingsDropdown.addEventListener("click", (e) => {
    e.stopPropagation();
  });

  themeToggle.addEventListener("click", toggleThemePreference);

  async function uploadLocalFile(file) {
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("/api/upload/local", {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      let detail = body.detail;
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
      throw new Error(detail || `${res.status} ${res.statusText}`);
    }
    return res.json();
  }

  localUploadMenuBtn?.addEventListener("click", () => {
    sourceDropdown.style.display = "none";
    localUploadInput?.click();
  });

  localUploadInput?.addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    localUploadMenuBtn.disabled = true;
    localUploadMenuBtn.textContent = "Uploading...";
    try {
      await uploadLocalFile(file);
      await Promise.all([
        loadCatalog(),
        loadDownloadCatalog(),
        loadTagFilters(),
        loadDownloadTagFilters(),
      ]);
    } catch (err) {
      alert(`Upload failed: ${err.message}`);
    } finally {
      localUploadMenuBtn.disabled = false;
      localUploadMenuBtn.textContent = "Upload from local machine";
      e.target.value = "";
    }
  });

  function normalizeWebsiteUrl(value) {
    const trimmed = value.trim();
    if (!trimmed) return "";
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    return `https://${trimmed}`;
  }

  function buildWebsiteDocumentId(url) {
    return `website:${url}`;
  }

  function websiteLabelFromUrl(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, "") || url;
    } catch {
      return url;
    }
  }

  function openWebsiteModal() {
    if (!websiteLinkModal) return;
    websiteLinkMessage.textContent = "";
    websiteLinkInput.value = "";
    websiteLinkTitleInput.value = "";
    websiteLinkModal.style.display = "block";
    websiteLinkInput.focus();
  }

  function closeWebsiteModal() {
    if (!websiteLinkModal) return;
    websiteLinkModal.style.display = "none";
  }

  function openWebsiteFromModal() {
    const url = normalizeWebsiteUrl(websiteLinkInput?.value || "");
    if (!url) {
      websiteLinkMessage.textContent = "Enter a website URL.";
      return;
    }

    try {
      const parsed = new URL(url);
      const title = (websiteLinkTitleInput?.value || "").trim() || websiteLabelFromUrl(parsed.toString());
      openWebsiteTab(parsed.toString(), title);
      closeWebsiteModal();
    } catch {
      websiteLinkMessage.textContent = "Enter a valid http or https URL.";
    }
  }

  websiteLinkMenuBtn?.addEventListener("click", () => {
    sourceDropdown.style.display = "none";
    openWebsiteModal();
  });

  websiteLinkModalClose?.addEventListener("click", closeWebsiteModal);
  websiteLinkOpenBtn?.addEventListener("click", openWebsiteFromModal);
  websiteLinkModal?.addEventListener("click", (e) => {
    if (e.target === websiteLinkModal) {
      closeWebsiteModal();
    }
  });
  websiteLinkInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      openWebsiteFromModal();
    }
  });
  websiteLinkTitleInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      openWebsiteFromModal();
    }
  });

  telegramAdvancedModal?.addEventListener("click", (e) => {
    if (e.target === telegramAdvancedModal) {
      telegramAdvancedModal.style.display = "none";
    }
  });

  // ---- Tabs ----------------------------------------------------------------

  $$(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!btn.dataset.tab) return;
      activateAppTab(btn.dataset.tab);
      sourceDropdown.style.display = "none";
      settingsDropdown.style.display = "none"; // Close dropdown when switching tabs

      // Handle catalog tab (All tab in nav)
      if (btn.dataset.tab === "catalog") {
        $$(".custom-tab-btn").forEach((customBtn) => customBtn.classList.remove("active"));
        activeFilter = null;
        page = 0;
        // Restore checkbox state
        const checkbox = $("#show-downloaded-checkbox");
        if (checkbox) {
          showDownloadedOnly = checkbox.checked;
        }
        loadCatalog();
        if (typeof updateDeleteTabButton === 'function') updateDeleteTabButton();
      }

      // Handle telegram tab
      if (btn.dataset.tab === "telegram") {
        downloadPage = 0;
        downloadSearchTerm = "";
        loadDownloadTagFilters();
        loadDownloadCatalog();
        refreshStatus();
      }

      if (btn.dataset.tab === "arxiv") {
        arxivPage = 0;
        $("#arxiv-message").textContent = "Enter a search term or category.";
        $("#arxiv-body").innerHTML = "";
        $("#arxiv-page-info").textContent = "";
      }

      if (btn.dataset.tab === "status") refreshStatus();
      if (btn.dataset.tab === "config") loadConfig();
    });
  });

  // ---- Catalog ---------------------------------------------------------------

  const PAGE_SIZE = 50;
  let page = 0;
  let searchTerm = "";
  let activeFilter = null;  // Currently active tag filter
  let showDownloadedOnly = true;  // All tab shows downloaded only by default (controlled by checkbox)
  let pollHandle = null;
  let lastCatalogCount = 0;

  // Download tab state
  let downloadPage = 0;
  let downloadSearchTerm = "";
  let downloadActiveTagFilter = null;
  let arxivPage = 0;
  let arxivSearchTerm = "";
  let arxivCategory = "";
  let arxivTotal = 0;
  let telegramLoginStage = "phone";
  let openDocuments = JSON.parse(localStorage.getItem("openDocuments") || "[]")
    .filter((doc) => doc && doc.messageId && doc.filename)
    .map((doc) => ({
      messageId: String(doc.messageId),
      filename: String(doc.filename),
      ext: typeof doc.ext === "string" ? doc.ext : "",
      kind: doc.kind === "website" ? "website" : "file",
      src: doc.kind === "website" && typeof doc.src === "string" ? doc.src : null,
    }))
    .filter((doc) => doc.kind !== "website" || doc.src);
  let activeDocumentId = localStorage.getItem("activeDocumentId");
  let activeAppTab = "catalog";
  let channelHistory = JSON.parse(localStorage.getItem("channelHistory") || "[]")
    .filter((value) => typeof value === "string" && value.trim() !== "");

  function saveOpenDocuments() {
    localStorage.setItem("openDocuments", JSON.stringify(openDocuments));
    if (activeDocumentId) localStorage.setItem("activeDocumentId", activeDocumentId);
    else localStorage.removeItem("activeDocumentId");
  }

  function saveChannelHistory() {
    localStorage.setItem("channelHistory", JSON.stringify(channelHistory.slice(0, 12)));
  }

  function rememberChannel(channel) {
    const normalized = channel.trim();
    if (!normalized) return;
    channelHistory = [normalized, ...channelHistory.filter((value) => value !== normalized)].slice(0, 12);
    saveChannelHistory();
    renderChannelHistory();
  }

  function renderChannelHistory() {
    const datalist = $("#channel-history");
    if (!datalist) return;
    datalist.innerHTML = channelHistory
      .map((channel) => `<option value="${escapeHtml(channel)}"></option>`)
      .join("");
  }

  function fmtSize(bytes) {
    if (!bytes) return "";
    const units = ["B", "KB", "MB", "GB"];
    let n = bytes, i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(1)} ${units[i]}`;
  }

  function statusBadge(status) {
    const s = status || "available";
    return `<span class="badge ${s}">${s.replace("_", " ")}</span>`;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  function truncateLabel(value, max = 28) {
    return value.length > max ? `${value.slice(0, max - 1)}…` : value;
  }

  function renderReaderTabs() {
    const tabs = $("#reader-tabs");
    tabs.innerHTML = openDocuments.map((doc) => `
      <button class="tab-btn reader-doc-tab ${doc.messageId === activeDocumentId ? "active" : ""}" data-doc-id="${doc.messageId}" title="${escapeHtml(doc.filename)}">
        <span class="reader-doc-label">${escapeHtml(truncateLabel(doc.filename))}</span>
        <span class="reader-doc-close" data-close-doc="${doc.messageId}">&times;</span>
      </button>
    `).join("");

    $$(".reader-doc-tab").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        if (e.target.closest("[data-close-doc]")) return;
        activateDocumentTab(btn.dataset.docId);
      });
    });

    $$("[data-close-doc]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        closeDocumentTab(btn.dataset.closeDoc);
      });
    });
  }

  function renderReaderViews() {
    const views = $("#reader-views");
    const wantedIds = new Set(openDocuments.map((doc) => doc.messageId));

    $$("#reader-views .reader-view").forEach((view) => {
      if (!wantedIds.has(view.dataset.docId)) {
        view.remove();
      }
    });

    for (const doc of openDocuments) {
      let view = views.querySelector(`.reader-view[data-doc-id="${doc.messageId}"]`);
      if (!view) {
        view = document.createElement("div");
        view.className = "reader-view";
        view.dataset.docId = doc.messageId;
        view.innerHTML = `
          <iframe
            class="reader-frame"
            title="${escapeHtml(doc.filename)}"></iframe>
        `;
        views.appendChild(view);
      }
      const iframe = view.querySelector(".reader-frame");
      const wantedSrc = doc.kind === "website"
        ? doc.src
        : `/viewer?id=${doc.messageId}&filename=${encodeURIComponent(doc.filename)}&ext=${encodeURIComponent(doc.ext || "")}&embedded=1`;
      if (doc.messageId === activeDocumentId && iframe && iframe.getAttribute("src") !== wantedSrc) {
        iframe.setAttribute("src", wantedSrc);
      }
      view.classList.toggle("active", doc.messageId === activeDocumentId);
    }

    $("#reader-empty").style.display = openDocuments.length === 0 ? "flex" : "none";
  }

  function syncReaderPane() {
    renderReaderTabs();
    renderReaderViews();
    updateMainStage();
    saveOpenDocuments();
  }

  function activateAppTab(tabName) {
    activeAppTab = tabName;
    activeDocumentId = null;
    $$(".tab-btn[data-tab]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });
    $$(".tab").forEach((section) => {
      section.classList.toggle("active", section.id === `tab-${tabName}`);
    });
    updateMainStage();
    saveOpenDocuments();
  }

  function updateMainStage() {
    const documentStage = $("#document-stage");
    const showingDocument = Boolean(activeDocumentId);
    documentStage.classList.toggle("active", showingDocument);
    $$(".tab").forEach((section) => {
      if (section.id === "document-stage") return;
      section.classList.toggle("active", !showingDocument && section.id === `tab-${activeAppTab}`);
    });
    if (showingDocument) {
      $$(".tab-btn[data-tab]").forEach((btn) => btn.classList.remove("active"));
    }
  }

  function activateDocumentTab(messageId) {
    activeDocumentId = String(messageId);
    renderReaderViews();
    $$(".reader-doc-tab").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.docId === activeDocumentId);
    });
    $$(".reader-view").forEach((view) => {
      view.classList.toggle("active", view.dataset.docId === activeDocumentId);
    });
    updateMainStage();
    saveOpenDocuments();
  }

  function closeDocumentTab(messageId) {
    const id = String(messageId);
    const idx = openDocuments.findIndex((doc) => doc.messageId === id);
    if (idx === -1) return;
    openDocuments.splice(idx, 1);
    if (activeDocumentId === id) {
      activeDocumentId = openDocuments[Math.max(0, idx - 1)]?.messageId || openDocuments[idx]?.messageId || null;
    }
    syncReaderPane();
    if (activeDocumentId) activateDocumentTab(activeDocumentId);
    else activateAppTab(activeAppTab);
  }

  window.estrattoCloseDocument = closeDocumentTab;

  async function removeMissingDocument(messageId) {
    try {
      const res = await fetch(`/api/delete/${messageId}`, { method: "POST" });
      if (!res.ok && res.status !== 404) {
        const body = await res.json().catch(() => ({}));
        let detail = body.detail;
        if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
        throw new Error(detail || `${res.status} ${res.statusText}`);
      }
    } catch (e) {
      console.error("Failed to remove missing document:", e);
    }
    closeDocumentTab(messageId);
    await Promise.allSettled([
      loadCatalog(),
      loadDownloadCatalog(),
      loadTagFilters(),
      loadDownloadTagFilters(),
    ]);
  }

  async function openDocumentTab(messageId, filename) {
    const status = await api(`/api/file_status/${messageId}`);
    if (!status.exists) {
      await removeMissingDocument(messageId);
      return;
    }

    const id = String(messageId);
    const existing = openDocuments.find((doc) => doc.messageId === id);
    if (!existing) {
      openDocuments.push({
        messageId: id,
        filename: status.filename || filename,
        ext: status.ext || "",
      });
      syncReaderPane();
    } else {
      existing.filename = status.filename || filename;
      existing.ext = status.ext || existing.ext || "";
      syncReaderPane();
    }
    activeDocumentId = id;
    activateDocumentTab(id);
  }

  function openWebsiteTab(url, title) {
    const id = buildWebsiteDocumentId(url);
    const existing = openDocuments.find((doc) => doc.messageId === id);
    if (!existing) {
      openDocuments.push({
        messageId: id,
        filename: title,
        kind: "website",
        src: url,
      });
      syncReaderPane();
    }
    activeDocumentId = id;
    activateDocumentTab(id);
  }

  function attachFilenameOpenHandlers(rootSelector) {
    $$(`${rootSelector} .filename-link`).forEach((link) => {
      link.addEventListener("click", async (e) => {
        e.preventDefault();
        await openDocumentTab(link.dataset.id, link.dataset.filename);
      });
    });
  }

  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    if (event.data?.type !== "estratto-close-document") return;
    closeDocumentTab(event.data.messageId);
  });

  function renderCatalogTable(items, total) {
    const body = $("#catalog-body");
    body.innerHTML = "";
    for (const item of items) {
      const tr = document.createElement("tr");
      const filename = item.filename ?? "";
      const fileExists = item.file_exists === true;

      // Make filename clickable only if file actually exists on disk
      const filenameDisplay = fileExists
        ? `<span class="filename-container">
             <a href="/viewer?id=${item.message_id}&filename=${encodeURIComponent(filename)}&embedded=1" class="filename-link" data-id="${item.message_id}" data-filename="${escapeHtml(filename)}">${filename}</a>
             <button class="rename-btn" data-id="${item.message_id}" data-filename="${filename}" title="Rename">✏️</button>
           </span>`
        : filename;

      const isDownloaded = item.status && item.status !== "available";
      tr.innerHTML = `
        <td>${filenameDisplay}</td>
        <td>${fmtSize(item.size)}</td>
        <td>${(item.message_date ?? "").slice(0, 10)}</td>
        <td><button class="tag-btn" data-id="${item.message_id}" data-filename="${filename}">🏷️</button></td>
        <td>
          ${isDownloaded ? `<button data-id="${item.message_id}" data-path="${item.final_path || item.staging_path || ""}" class="trash-btn" title="Delete">🗑️</button>` : ""}
        </td>
      `;
      body.appendChild(tr);
    }

    const displayStart = Math.min(page * PAGE_SIZE + 1, total);
    const displayEnd = Math.min(page * PAGE_SIZE + items.length, total);
    $("#page-info").textContent = total > 0 ? `${displayStart}-${displayEnd} of ${total}` : "No items";

    attachFilenameOpenHandlers("#catalog-body");

    // Tag button listeners
    $$("#catalog-body .tag-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        openTagModal(btn.dataset.id, btn.dataset.filename);
      });
    });

    // Rename button listeners
    $$("#catalog-body .rename-btn").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();

        const messageId = btn.dataset.id;
        const oldFilename = btn.dataset.filename;
        const newFilename = prompt("Enter new filename:", oldFilename);

        if (!newFilename || newFilename === oldFilename) {
          return;
        }

        btn.disabled = true;
        try {
          await api(`/api/rename/${messageId}`, {
            method: "POST",
            body: JSON.stringify({ filename: newFilename }),
          });
          loadCatalog();
        } catch (e) {
          alert(`Rename failed: ${e.message}`);
          btn.disabled = false;
        }
      });
    });

    $$("#catalog-body .trash-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const path = btn.dataset.path;
        const filename = path ? path.split("/").pop() : "this file";
        if (!confirm(`Delete ${filename}? This will remove the file from disk and reset its download status.`)) {
          return;
        }

        btn.disabled = true;
        try {
          await api(`/api/delete/${btn.dataset.id}`, { method: "POST" });
          loadCatalog();
        } catch (e) {
          alert(`Delete failed: ${e.message}`);
          btn.disabled = false;
        }
      });
    });
  }

  async function loadCatalog() {
    const params = new URLSearchParams({
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      downloaded_only: "true",
    });

    if (searchTerm) {
      params.set("search", searchTerm);
    }
    if (activeFilter) {
      params.set("search_any", activeFilter);
    }

    const response = await api(`/api/catalog?${params}`);
    lastCatalogCount = response.total;
    renderCatalogTable(response.items, response.total);
  }

  $("#search-input").addEventListener("input", (e) => {
    searchTerm = e.target.value;
    page = 0;
    loadCatalog();
  });

  $("#prev-page").addEventListener("click", () => { if (page > 0) { page--; loadCatalog(); } });
  $("#next-page").addEventListener("click", () => { page++; loadCatalog(); });

  // ---- Download tab --------------------------------------------------------

  async function loadDownloadCatalog() {
    const params = new URLSearchParams({
      limit: PAGE_SIZE,
      offset: downloadPage * PAGE_SIZE,
      downloaded_only: false,  // Download tab shows all files
      source: "telegram",
    });

    if (downloadSearchTerm) {
      params.set("search", downloadSearchTerm);
    }
    if (downloadActiveTagFilter) {
      params.set("search_any", downloadActiveTagFilter);
    }

    const response = await api(`/api/catalog?${params}`);
    const items = response.items;
    const total = response.total;

    renderDownloadTable(items, total);
  }

  function renderDownloadTable(items, total) {
    const body = $("#download-body");
    body.innerHTML = "";

    for (const item of items) {
      const tr = document.createElement("tr");
      const isDownloading = item.status === "downloading";
      const canDownload = !item.status || item.status === "failed";
      const isDownloaded = item.status && item.status !== "available";
      const filename = item.filename ?? "";
      const fileExists = item.file_exists === true;

      // Make filename clickable only if file actually exists on disk
      const filenameDisplay = fileExists
        ? `<span class="filename-container">
             <a href="/viewer?id=${item.message_id}&filename=${encodeURIComponent(filename)}&embedded=1" class="filename-link" data-id="${item.message_id}" data-filename="${escapeHtml(filename)}">${filename}</a>
             <button class="rename-btn" data-id="${item.message_id}" data-filename="${filename}" title="Rename">✏️</button>
           </span>`
        : filename;

      tr.innerHTML = `
        <td>${filenameDisplay}</td>
        <td>${fmtSize(item.size)}</td>
        <td>${(item.message_date ?? "").slice(0, 10)}</td>
        <td><button class="tag-btn" data-id="${item.message_id}" data-filename="${filename}">🏷️</button></td>
        <td>
          ${isDownloading ? `<button class="dl-btn" disabled>⏳</button>` : ""}
          ${!isDownloading && canDownload ? `<button data-id="${item.message_id}" class="dl-btn">Download</button>` : ""}
          ${isDownloaded ? `<button data-id="${item.message_id}" data-path="${item.final_path || item.staging_path || ""}" class="trash-btn" title="Delete">🗑️</button>` : ""}
        </td>
      `;
      body.appendChild(tr);
    }

    const info = $("#download-page-info");
    const start = downloadPage * PAGE_SIZE + 1;
    const end = Math.min((downloadPage + 1) * PAGE_SIZE, total);
    info.textContent = total > 0 ? `Showing ${start}-${end} of ${total}` : "No files";

    $("#download-prev-page").disabled = downloadPage === 0;
    $("#download-next-page").disabled = end >= total;

    attachDownloadEventHandlers();
  }

  function attachDownloadEventHandlers() {
    attachFilenameOpenHandlers("#download-body");

    // Download button listeners
    $$(".dl-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (btn.disabled) return;
        btn.disabled = true;
        btn.textContent = "⏳";
        try {
          await api(`/api/download/${btn.dataset.id}`, { method: "POST" });
          loadDownloadCatalog();
          setTimeout(() => loadDownloadCatalog(), 1000);
        } catch (e) {
          alert(`Download failed: ${e.message}`);
          btn.disabled = false;
          btn.textContent = "Download";
        }
      });
    });

    // Rename button listeners
    $$(".rename-btn").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const messageId = btn.dataset.id;
        const oldFilename = btn.dataset.filename;
        const newFilename = prompt("Enter new filename:", oldFilename);
        if (!newFilename || newFilename === oldFilename) return;
        btn.disabled = true;
        try {
          await api(`/api/rename/${messageId}`, {
            method: "POST",
            body: JSON.stringify({ filename: newFilename }),
          });
          loadDownloadCatalog();
        } catch (e) {
          alert(`Rename failed: ${e.message}`);
          btn.disabled = false;
        }
      });
    });

    // Tag button listeners
    $$(".tag-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        openTagModal(btn.dataset.id, btn.dataset.filename);
      });
    });

    // Trash button listeners
    $$(".trash-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const path = btn.dataset.path;
        const filename = path ? path.split("/").pop() : "this file";
        if (!confirm(`Delete ${filename}? This will remove the file from disk and reset its download status.`)) {
          return;
        }

        btn.disabled = true;
        try {
          await api(`/api/delete/${btn.dataset.id}`, { method: "POST" });
          loadDownloadCatalog();
        } catch (e) {
          alert(`Delete failed: ${e.message}`);
          btn.disabled = false;
        }
      });
    });
  }

  $("#download-search-input").addEventListener("input", (e) => {
    downloadSearchTerm = e.target.value;
    downloadPage = 0;
    loadDownloadCatalog();
  });
  $("#download-prev-page").addEventListener("click", () => { if (downloadPage > 0) { downloadPage--; loadDownloadCatalog(); } });
  $("#download-next-page").addEventListener("click", () => { downloadPage++; loadDownloadCatalog(); });

  async function loadArxivResults() {
    if (!arxivSearchTerm.trim() && !arxivCategory.trim()) {
      $("#arxiv-message").textContent = "Enter a search term or category.";
      $("#arxiv-body").innerHTML = "";
      $("#arxiv-page-info").textContent = "";
      return;
    }

    const params = new URLSearchParams({
      q: arxivSearchTerm,
      category: arxivCategory,
      limit: PAGE_SIZE,
      offset: arxivPage * PAGE_SIZE,
    });
    $("#arxiv-message").textContent = "Searching...";
    try {
      const response = await api(`/api/arxiv/search?${params}`);
      arxivTotal = response.total;
      renderArxivTable(response.items, response.total);
      loadTagFilters();
      loadDownloadTagFilters();
      $("#arxiv-message").textContent = response.total ? "" : "No arXiv results.";
    } catch (e) {
      $("#arxiv-message").textContent = e.message;
      $("#arxiv-body").innerHTML = "";
      $("#arxiv-page-info").textContent = "";
    }
  }

  function renderArxivTable(items, total) {
    const body = $("#arxiv-body");
    body.innerHTML = "";

    for (const item of items) {
      const tr = document.createElement("tr");
      const filename = item.filename ?? "";
      const fileExists = item.file_exists === true;
      const filenameDisplay = fileExists
        ? `<a href="/viewer?id=${item.message_id}&filename=${encodeURIComponent(filename)}&embedded=1" class="filename-link" data-id="${item.message_id}" data-filename="${escapeHtml(filename)}">${escapeHtml(filename)}</a>`
        : escapeHtml(filename);

      tr.innerHTML = `
        <td>
          <div>${filenameDisplay}</div>
          <div class="muted">${escapeHtml((item.authors || []).join(", "))}</div>
          <div class="muted">${escapeHtml(item.arxiv_id || "")}</div>
        </td>
        <td>${(item.message_date ?? "").slice(0, 10)}</td>
        <td>
          ${fileExists || item.status === "downloaded"
            ? ""
            : `<button
                class="arxiv-dl-btn"
                data-id="${item.message_id}"
                data-arxiv-id="${escapeHtml(item.arxiv_id || "")}"
                data-title="${escapeHtml(item.title || "")}"
                data-summary="${escapeHtml(item.summary || "")}"
                data-published="${escapeHtml(item.message_date || "")}"
                data-authors="${escapeHtml(JSON.stringify(item.authors || []))}"
              >Download</button>`}
        </td>
      `;
      body.appendChild(tr);
    }

    const start = total === 0 ? 0 : arxivPage * PAGE_SIZE + 1;
    const end = Math.min((arxivPage + 1) * PAGE_SIZE, total);
    $("#arxiv-page-info").textContent = total > 0 ? `${start}-${end} of ${total}` : "No results";
    $("#arxiv-prev-page").disabled = arxivPage === 0;
    $("#arxiv-next-page").disabled = end >= total;

    attachFilenameOpenHandlers("#arxiv-body");
    $$("#arxiv-body .arxiv-dl-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "⏳";
        try {
          await api("/api/arxiv/download", {
            method: "POST",
            body: JSON.stringify({
              message_id: btn.dataset.id,
              arxiv_id: btn.dataset.arxivId,
              title: btn.dataset.title,
              summary: btn.dataset.summary,
              published: btn.dataset.published,
              authors: JSON.parse(btn.dataset.authors || "[]"),
            }),
          });
          setTimeout(() => {
            loadArxivResults();
            loadTagFilters();
            loadDownloadTagFilters();
          }, 1000);
        } catch (e) {
          alert(`Download failed: ${e.message}`);
          btn.disabled = false;
          btn.textContent = "Download";
        }
      });
    });
  }

  $("#arxiv-search-input").addEventListener("input", (e) => {
    arxivSearchTerm = e.target.value;
  });
  $("#arxiv-category-input").addEventListener("input", (e) => {
    arxivCategory = e.target.value;
  });
  $("#arxiv-search-btn").addEventListener("click", () => {
    arxivPage = 0;
    loadArxivResults();
  });
  $("#arxiv-prev-page").addEventListener("click", () => {
    if (arxivPage > 0) {
      arxivPage--;
      loadArxivResults();
    }
  });
  $("#arxiv-next-page").addEventListener("click", () => {
    if ((arxivPage + 1) * PAGE_SIZE < arxivTotal) {
      arxivPage++;
      loadArxivResults();
    }
  });

  $("#index-btn").addEventListener("click", async () => {
    try {
      const channel = $("#channel-input")?.value.trim();
      if (channel) {
        await api("/api/config", { method: "POST", body: JSON.stringify({ telegram: { channel } }) });
        rememberChannel(channel);
      }
      await api("/api/index", { method: "POST" });
      $("#index-status").textContent = "Indexing...";
      $("#channel-message").textContent = channel ? "Channel saved. Indexing..." : "";
      pollIndexStatus();
    } catch (e) {
      $("#channel-message").textContent = e.message;
      alert(`Could not start indexing: ${e.message}`);
    }
  });

  $("#reset-catalog-menu-btn").addEventListener("click", async () => {
    if (!confirm("Clear all indexed files and tags? You'll need to re-index the channel.")) {
      return;
    }
    try {
      await api("/api/catalog/reset", { method: "POST" });
      downloadSearchTerm = "";
      $("#download-search-input").value = "";
      downloadPage = 0;
      loadDownloadCatalog();
      loadCatalog();
      alert("Catalog reset. Click 'Index channel' to start fresh.");
    } catch (e) {
      alert(`Failed to reset: ${e.message}`);
    }
  });

  async function pollIndexStatus() {
    const s = await api("/api/status");
    if (s.indexing) {
      $("#index-status").textContent = `Indexing... (${s.index_progress} found)`;
      // Only refresh if on first page and there are new items
      if (downloadPage === 0 && s.catalog_count > lastCatalogCount) {
        loadDownloadCatalog();
      }
      setTimeout(pollIndexStatus, 2000);
    } else {
      $("#index-status").textContent = "";
      if (downloadPage === 0) {
        loadDownloadCatalog();
      }
    }
  }

  // Tag modal
  let currentTagMessageId = null;

  async function openTagModal(messageId, filename) {
    currentTagMessageId = messageId;
    const modal = $("#tag-modal");
    const filenameEl = $("#tag-modal-filename");
    const listEl = $("#tag-modal-list");

    filenameEl.textContent = filename;
    listEl.innerHTML = "<p class='muted'>Loading tags...</p>";
    modal.style.display = "flex";

    try {
      const data = await api(`/api/tags/extract/${messageId}`);
      listEl.innerHTML = "";

      if (data.tags.length === 0) {
        listEl.innerHTML = "<p class='muted'>No tags found</p>";
        return;
      }

      for (const tag of data.tags) {
        const item = document.createElement("div");
        item.className = "tag-discovery-item";
        item.innerHTML = `
          <span class="tag-name">${tag}</span>
          <div class="tag-actions">
            <button class="confirm" data-tag="${tag}">✓</button>
            <button class="ignore" data-tag="${tag}">✗</button>
          </div>
        `;
        listEl.appendChild(item);
      }

      // Add listeners
      $$(".tag-discovery-item button.confirm").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const tag = btn.dataset.tag;
          try {
            await api(`/api/tags/confirm/${encodeURIComponent(tag)}?message_id=${currentTagMessageId}`, { method: "POST" });
            btn.parentElement.parentElement.remove();
            loadTagFilters();
            loadDownloadTagFilters();
          } catch (e) {
            alert(`Failed to confirm tag: ${e.message}`);
          }
        });
      });

      $$(".tag-discovery-item button.ignore").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const tag = btn.dataset.tag;
          try {
            await api(`/api/tags/ignore/${encodeURIComponent(tag)}`, { method: "POST" });
            btn.parentElement.parentElement.remove();
          } catch (e) {
            alert(`Failed to ignore tag: ${e.message}`);
          }
        });
      });
    } catch (e) {
      listEl.innerHTML = `<p class='muted'>Error: ${e.message}</p>`;
    }
  }

  $("#tag-modal-close").addEventListener("click", () => {
    $("#tag-modal").style.display = "none";
  });

  $("#tag-modal").addEventListener("click", (e) => {
    if (e.target === $("#tag-modal")) {
      $("#tag-modal").style.display = "none";
    }
  });

  // Tag tabs
  async function loadTagFilters() {
    try {
      const tags = await api("/api/tags/confirmed");
      const container = $("#tag-filters");
      const tabList = $("#tag-tab-list");

      container.style.display = "block";
      tabList.innerHTML = "";

      if (tags.length === 0) {
        return;
      }

      for (const tag of tags) {
        const btn = document.createElement("button");
        btn.className = "tag-tab" + (activeFilter === tag ? " active" : "");
        btn.dataset.tag = tag;
        btn.textContent = tag;
        tabList.appendChild(btn);
      }

      // Add click listeners to tag tabs
      $$("#tag-tab-list .tag-tab").forEach((btn) => {
        btn.addEventListener("click", () => {
          const tag = btn.dataset.tag;

          // Toggle filter
          if (activeFilter === tag) {
            activeFilter = null;  // Deactivate if clicking the same tag
          } else {
            activeFilter = tag;   // Activate this tag
          }

          page = 0;  // Reset to first page
          loadCatalog();
          loadTagFilters();  // Refresh to show active state
        });
      });
    } catch (e) {
      console.error("Failed to load tag tabs:", e);
    }
  }

  async function loadDownloadTagFilters() {
    try {
      const tags = await api("/api/tags/confirmed");
      const container = $("#download-tag-filters");
      const tagList = $("#download-tag-list");

      if (!container || !tagList) return;

      if (tags.length === 0) {
        container.style.display = "none";
        tagList.innerHTML = "";
        return;
      }

      container.style.display = "block";
      tagList.innerHTML = "";

      for (const tag of tags) {
        const btn = document.createElement("button");
        btn.className = "tag-tab" + (downloadActiveTagFilter === tag ? " active" : "");
        btn.dataset.tag = tag;
        btn.textContent = tag;
        tagList.appendChild(btn);
      }

      $$("#download-tag-list .tag-tab").forEach((btn) => {
        btn.addEventListener("click", () => {
          const tag = btn.dataset.tag;
          downloadActiveTagFilter = downloadActiveTagFilter === tag ? null : tag;
          downloadPage = 0;
          loadDownloadCatalog();
          loadDownloadTagFilters();
        });
      });
    } catch (e) {
      console.error("Failed to load download tag filters:", e);
    }
  }

  // ---- Status ------------------------------------------------------------

  function resetTelegramLoginUi() {
    telegramLoginStage = "phone";
    $("#login-step-phone").style.display = "block";
    $("#login-step-code").style.display = "none";
    $("#login-step-password").style.display = "none";
    $("#login-message").textContent = "";
  }

  function applyTelegramLoginStage() {
    const phoneVisible = telegramLoginStage === "phone" || telegramLoginStage === "code" || telegramLoginStage === "password";
    $("#login-step-phone").style.display = phoneVisible ? "block" : "none";
    $("#login-step-code").style.display = telegramLoginStage === "code" || telegramLoginStage === "password" ? "block" : "none";
    $("#login-step-password").style.display = telegramLoginStage === "password" ? "block" : "none";
  }

  async function refreshStatus() {
    const s = await api("/api/status");
    $("#status-cards").innerHTML = `
      <div class="card"><div class="value">${s.telegram_authorized ? "Yes" : "No"}</div><div class="label">Telegram authorized</div></div>
      <div class="card"><div class="value">${s.listening ? "On" : "Off"}</div><div class="label">Live listener</div></div>
      <div class="card"><div class="value">${s.catalog_count}</div><div class="label">Indexed files</div></div>
      ${Object.entries(s.status_counts || {}).map(([k, v]) =>
        `<div class="card"><div class="value">${v}</div><div class="label">${k}</div></div>`
      ).join("")}
    `;

    $("#login-authorized").style.display = s.telegram_authorized ? "block" : "none";
    $("#login-step-keys").style.display = (!s.telegram_authorized && !s.telegram_app_configured) ? "block" : "none";
    $("#login-flow").style.display = (!s.telegram_authorized && s.telegram_app_configured) ? "block" : "none";

    if (!s.telegram_authorized && s.telegram_app_configured) {
      applyTelegramLoginStage();
    }

    const channelInput = $("#channel-input");
    if (s.telegram_authorized && document.activeElement !== channelInput) {
      const realChannel = s.channel && !s.channel.startsWith("YOUR_") ? s.channel : "";
      channelInput.value = realChannel;
      channelInput.placeholder = realChannel ? realChannel : "e.g. mychannel or https://t.me/mychannel";
      if (realChannel) rememberChannel(realChannel);
    }

    const recent = await api("/api/recent?limit=50");
    const body = $("#recent-body");
    body.innerHTML = recent.map((r) => `
      <tr>
        <td>${r.message_id}</td>
        <td>${r.original_filename ?? ""}</td>
        <td>${statusBadge(r.status)}</td>
        <td>${r.final_path ?? ""}</td>
        <td>${r.error ? `<span class="muted" title="${r.error}">error</span>` : ""}</td>
      </tr>
    `).join("");
  }

  $("#listen-start-btn").addEventListener("click", async () => {
    await api("/api/listen/start", { method: "POST" });
    refreshStatus();
  });
  $("#listen-stop-btn").addEventListener("click", async () => {
    await api("/api/listen/stop", { method: "POST" });
    refreshStatus();
  });

  // ---- Telegram login ------------------------------------------------------

  $("#keys-continue-btn").addEventListener("click", async () => {
    const api_id = $("#keys-api-id-input").value.trim();
    const api_hash = $("#keys-api-hash-input").value.trim();
    if (!api_id || !api_hash) return;
    try {
      await api("/api/telegram/set_app_keys", { method: "POST", body: JSON.stringify({ api_id, api_hash }) });
      $("#keys-message").textContent = "";
      refreshStatus();
    } catch (e) {
      $("#keys-message").textContent = e.message;
    }
  });

  $("#send-code-btn").addEventListener("click", async () => {
    const phone = $("#phone-input").value.trim();
    if (!phone) return;
    try {
      await api("/api/telegram/send_code", { method: "POST", body: JSON.stringify({ phone }) });
      telegramLoginStage = "code";
      applyTelegramLoginStage();
      $("#login-message").textContent = "Code sent. Check Telegram.";
    } catch (e) {
      $("#login-message").textContent = e.message;
    }
  });

  $("#verify-code-btn").addEventListener("click", async () => {
    const code = $("#code-input").value.trim();
    try {
      const r = await api("/api/telegram/verify_code", { method: "POST", body: JSON.stringify({ code }) });
      if (r.status === "password_required") {
        telegramLoginStage = "password";
        applyTelegramLoginStage();
        $("#login-message").textContent = "2FA enabled, enter your password.";
      } else {
        $("#login-message").textContent = "Logged in.";
        telegramLoginStage = "phone";
        refreshStatus();
      }
    } catch (e) {
      $("#login-message").textContent = e.message;
    }
  });

  $("#verify-password-btn").addEventListener("click", async () => {
    const password = $("#password-input").value;
    try {
      await api("/api/telegram/verify_password", { method: "POST", body: JSON.stringify({ password }) });
      $("#login-message").textContent = "Logged in.";
      telegramLoginStage = "phone";
      refreshStatus();
    } catch (e) {
      $("#login-message").textContent = e.message;
    }
  });

  $("#logout-btn").addEventListener("click", async () => {
    await api("/api/telegram/logout", { method: "POST" });
    resetTelegramLoginUi();
    refreshStatus();
  });

  // ---- Config --------------------------------------------------------------

  function setNested(obj, path, value) {
    const parts = path.split(".");
    let node = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      node[parts[i]] = node[parts[i]] || {};
      node = node[parts[i]];
    }
    node[parts[parts.length - 1]] = value;
  }

  function getNested(obj, path) {
    return path.split(".").reduce((node, key) => (node == null ? undefined : node[key]), obj);
  }

  async function loadConfig() {
    const cfg = await api("/api/config");
    $$("#config-form input").forEach((input) => {
      const value = getNested(cfg, input.name);
      if (value === undefined || value === null) return;
      if (input.type === "checkbox") input.checked = Boolean(value);
      else input.value = value;
    });
  }

  async function loadTelegramAdvancedConfig() {
    const cfg = await api("/api/config");
    $("#telegram-advanced-api-id").value = getNested(cfg, "telegram.api_id") ?? "";
    $("#telegram-advanced-api-hash").value = getNested(cfg, "telegram.api_hash") ?? "";
    $("#telegram-advanced-session-name").value = getNested(cfg, "telegram.session_name") ?? "";
    $("#telegram-advanced-message").textContent = "";
  }

  $("#config-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const patch = {};
    $$("#config-form input").forEach((input) => {
      let value = input.type === "checkbox" ? input.checked : input.value;
      if (input.type === "number" && value !== "") value = Number(value);
      if (value === "") return;
      setNested(patch, input.name, value);
    });
    try {
      const r = await api("/api/config", { method: "POST", body: JSON.stringify(patch) });
      $("#config-message").textContent = r.note || "Saved.";
    } catch (e) {
      $("#config-message").textContent = `Save failed: ${e.message}`;
    }
  });

  $("#telegram-advanced-btn").addEventListener("click", async () => {
    try {
      await loadTelegramAdvancedConfig();
      telegramAdvancedModal.style.display = "flex";
    } catch (e) {
      alert(`Could not load Telegram settings: ${e.message}`);
    }
  });

  $("#telegram-advanced-modal-close").addEventListener("click", () => {
    telegramAdvancedModal.style.display = "none";
  });

  $("#telegram-advanced-save-btn").addEventListener("click", async () => {
    const message = $("#telegram-advanced-message");
    const apiId = $("#telegram-advanced-api-id").value.trim();
    const apiHash = $("#telegram-advanced-api-hash").value.trim();
    const sessionName = $("#telegram-advanced-session-name").value.trim();
    const patch = { telegram: { session_name: sessionName || "estratto" } };

    if (apiId) patch.telegram.api_id = Number(apiId);
    if (apiHash) patch.telegram.api_hash = apiHash;

    try {
      await api("/api/config", { method: "POST", body: JSON.stringify(patch) });
      message.textContent = "Saved.";
      await refreshStatus();
      await loadConfig();
    } catch (e) {
      message.textContent = `Save failed: ${e.message}`;
    }
  });

  // ---- Custom tabs --------------------------------------------------------

  let customTabs = JSON.parse(localStorage.getItem("customTabs") || "[]");

  function saveCustomTabs() {
    localStorage.setItem("customTabs", JSON.stringify(customTabs));
  }

  function loadCustomTabs() {
    const container = $("#custom-tabs-list");
    container.innerHTML = "";

    for (const tab of customTabs) {
      const btn = document.createElement("button");
      btn.className = "tab-btn custom-tab-btn";
      btn.dataset.customTabId = tab.id;
      btn.dataset.customTabName = tab.name;
      btn.textContent = tab.name;
      container.appendChild(btn);

      // Click to activate custom tab
      btn.addEventListener("click", (e) => {
        // Activate custom tab filter
        activateAppTab("catalog");
        $$(".tab-btn").forEach(b => {
          if (b.dataset.tab) b.classList.remove("active");
        });
        btn.classList.add("active");

        // Hide Index/Reset buttons in custom tabs
        const indexBtn = $("#index-btn");
        const indexStatus = $("#index-status");
        if (indexBtn) indexBtn.style.display = "none";
        if (indexStatus) indexStatus.style.display = "none";

        // Apply combined tag filter (comma-separated for multiple tags)
        activeFilter = tab.tags.join(",");
        searchTerm = "";
        $("#search-input").value = "";
        showDownloadedOnly = true;  // Custom tabs show downloaded only
        page = 0;
        loadCatalog();
        updateDeleteTabButton();
      });
    }
  }

  function updateDeleteTabButton() {
    const deleteBtn = $("#delete-tab-btn");
    if (!deleteBtn) return;

    // Show delete button only when a custom tab is active
    const activeCustomTab = $$(".custom-tab-btn.active")[0];
    if (activeCustomTab) {
      deleteBtn.style.display = "inline-block";
      deleteBtn.dataset.tabId = activeCustomTab.dataset.customTabId;
      deleteBtn.dataset.tabName = activeCustomTab.dataset.customTabName;
    } else {
      deleteBtn.style.display = "none";
      deleteBtn.dataset.tabId = "";
      deleteBtn.dataset.tabName = "";
    }
  }

  // Handle delete tab button
  const deleteTabBtn = $("#delete-tab-btn");
  if (deleteTabBtn) {
    deleteTabBtn.addEventListener("click", () => {
      const tabId = deleteTabBtn.dataset.tabId;
      const tabName = deleteTabBtn.dataset.tabName;

      if (!confirm(`Delete the "${tabName}" tab?`)) {
        return;
      }

      // Remove custom tab
      customTabs = customTabs.filter(t => t.id !== tabId);
      saveCustomTabs();
      loadCustomTabs();

      // Switch to "All" tab
      activeFilter = null;
      showDownloadedOnly = true;
      const allTab = $(".tab-btn[data-tab='catalog']");
      if (allTab) {
        activateAppTab("catalog");
      }
      loadCatalog();
      updateDeleteTabButton();
    });
  }

  // Open custom tab creation modal
  $("#add-custom-tab-btn").addEventListener("click", async () => {
    const modal = $("#custom-tab-modal");
    const tagsList = $("#custom-tab-tags-list");
    const nameInput = $("#custom-tab-name-input");
    const message = $("#custom-tab-message");

    nameInput.value = "";
    message.textContent = "";
    tagsList.innerHTML = "<p class='muted'>Loading tags...</p>";
    modal.style.display = "flex";

    try {
      const tags = await api("/api/tags/confirmed");

      if (tags.length === 0) {
        tagsList.innerHTML = "<p class='muted'>No tags available. Create tags first by clicking 🏷️ on documents.</p>";
        return;
      }

      tagsList.innerHTML = "";

      for (const tag of tags) {
        const item = document.createElement("div");
        item.className = "custom-tab-tag-item";
        const checkboxId = `tag-checkbox-${tag}`;
        item.innerHTML = `
          <input type="checkbox" id="${checkboxId}" value="${tag}">
          <label for="${checkboxId}">${tag}</label>
        `;
        tagsList.appendChild(item);
      }
    } catch (e) {
      tagsList.innerHTML = `<p class='muted'>Error: ${e.message}</p>`;
    }
  });

  // Close custom tab modal
  $("#custom-tab-modal-close").addEventListener("click", () => {
    $("#custom-tab-modal").style.display = "none";
  });

  $("#custom-tab-modal").addEventListener("click", (e) => {
    if (e.target === $("#custom-tab-modal")) {
      $("#custom-tab-modal").style.display = "none";
    }
  });

  // Create custom tab
  $("#custom-tab-create-btn").addEventListener("click", () => {
    const name = $("#custom-tab-name-input").value.trim();
    const message = $("#custom-tab-message");

    if (!name) {
      message.textContent = "Please enter a tab name.";
      return;
    }

    const selectedTags = $$(".custom-tab-tag-item input[type='checkbox']:checked")
      .map(cb => cb.value);

    if (selectedTags.length === 0) {
      message.textContent = "Please select at least one tag.";
      return;
    }

    // Create new custom tab
    const newTab = {
      id: Date.now().toString(),
      name,
      tags: selectedTags,
    };

    customTabs.push(newTab);
    saveCustomTabs();
    loadCustomTabs();

    $("#custom-tab-modal").style.display = "none";
  });

  // ---- Init ------------------------------------------------------------

  loadCatalog();
  loadTagFilters();
  loadDownloadTagFilters();
  loadCustomTabs();
  renderChannelHistory();
  updateDeleteTabButton();
  if (activeDocumentId && openDocuments.some((doc) => doc.messageId === activeDocumentId)) {
    syncReaderPane();
    activateDocumentTab(activeDocumentId);
  } else {
    activeDocumentId = null;
    activateAppTab(activeAppTab);
    syncReaderPane();
  }
  refreshStatus();
  pollHandle = setInterval(refreshStatus, 15000);
})();

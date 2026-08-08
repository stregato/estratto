(() => {
  console.log("=== VIEWER.JS v6 LOADED (STACKED PDF VIEWER) ===");

  const params = new URLSearchParams(window.location.search);
  const messageId = params.get("id");
  const viewerKind = params.get("kind") || "file";
  const filename = params.get("filename") || "Document";
  const extParam = (params.get("ext") || "").toLowerCase();
  const websiteSrc = params.get("src") || "";
  const embedded = params.get("embedded") === "1";

  if (!messageId) {
    alert("No document ID provided");
    window.location.href = "/";
    return;
  }

  document.getElementById("doc-title").textContent = filename;
  const viewerToolbar = document.getElementById("viewer-toolbar");
  const viewerContainer = document.getElementById("viewer-container");
  const backBtn = document.getElementById("back-btn");
  const fullscreenBtn = document.getElementById("viewer-fullscreen-btn");
  const closeBtn = document.getElementById("viewer-close-btn");
  const zoomOutBtn = document.getElementById("zoom-out-btn");
  const zoomInBtn = document.getElementById("zoom-in-btn");
  const zoomInfo = document.getElementById("zoom-info");
  const outlineToggleBtn = document.getElementById("outline-toggle-btn");
  const documentOutline = document.getElementById("document-outline") || document.getElementById("pdf-outline");
  const loadingOverlay = document.getElementById("viewer-loading");
  const loadingText = document.getElementById("viewer-loading-text");

  const prevBtn = document.getElementById("prev-page");
  const nextBtn = document.getElementById("next-page");
  const pageInfo = document.getElementById("page-info");

  function applyThemePreference() {
    let themePreference = "";
    try {
      themePreference = localStorage.getItem("themePreference") || "";
    } catch (e) {
      console.warn("Could not read theme preference:", e);
    }

    if ((themePreference !== "light" && themePreference !== "dark") && embedded && window.parent !== window) {
      try {
        themePreference = window.parent.document.documentElement.getAttribute("data-theme") || "";
      } catch (e) {
        console.warn("Could not read parent theme:", e);
      }
    }

    if (themePreference === "light" || themePreference === "dark") {
      document.documentElement.setAttribute("data-theme", themePreference);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
  }

  applyThemePreference();

  window.addEventListener("storage", (event) => {
    if (event.key === "themePreference") {
      applyThemePreference();
    }
  });

  if (embedded && window.parent !== window) {
    try {
      const parentRoot = window.parent.document.documentElement;
      new MutationObserver(() => applyThemePreference()).observe(parentRoot, {
        attributes: true,
        attributeFilter: ["data-theme"],
      });
    } catch (e) {
      console.warn("Could not observe parent theme:", e);
    }
  }

  if (embedded) {
    backBtn.style.display = "none";
    viewerToolbar.style.height = "42px";
    viewerContainer.style.top = "42px";
    document.documentElement.style.setProperty("--viewer-toolbar-height", "42px");
  } else {
    document.documentElement.style.setProperty("--viewer-toolbar-height", "50px");
  }

  let currentViewer = null;
  let currentPage = 1;
  let totalPages = 1;
  let saveProgressTimer = null;
  let viewerKeyHandler = null;
  let localFullscreen = false;

  function inferExtFromFilename(name) {
    if (!name || !name.includes(".")) return "";
    return name.split(".").pop().toLowerCase();
  }

  async function getFileInfo() {
    try {
      return await api(`/api/file_status/${messageId}`);
    } catch (e) {
      console.warn("Failed to load file metadata:", e);
      return { exists: true, filename, ext: extParam || inferExtFromFilename(filename) };
    }
  }

  function reportViewerError(message) {
    hideLoading();
    alert(message);
    if (embedded && window.parent !== window) {
      try {
        if (typeof window.parent.estrattoSetAppChromeHidden === "function") {
          window.parent.estrattoSetAppChromeHidden(false);
        }
      } catch (e) {
        console.warn("Could not restore parent chrome after viewer error:", e);
      }
    }
    if (embedded) {
      closeViewer();
    } else {
      window.location.href = "/";
    }
  }

  function initWebsiteViewer() {
    const websiteViewer = document.getElementById("website-viewer");
    let websiteScale = 1;

    function applyWebsiteZoom(scale) {
      websiteScale = Math.max(0.5, Math.min(2.5, scale));
      websiteViewer.style.transform = `scale(${websiteScale})`;
      websiteViewer.style.width = `${100 / websiteScale}%`;
      websiteViewer.style.height = `${100 / websiteScale}%`;
      updateZoomInfo(websiteScale);
    }

    websiteViewer.style.display = "block";
    websiteViewer.src = websiteSrc;
    prevBtn.style.display = "none";
    nextBtn.style.display = "none";
    pageInfo.style.display = "none";
    zoomOutBtn.style.display = "inline-block";
    zoomInBtn.style.display = "inline-block";
    zoomInfo.style.display = "inline";
    outlineToggleBtn.style.display = "none";
    zoomOutBtn.onclick = () => applyWebsiteZoom(websiteScale - 0.1);
    zoomInBtn.onclick = () => applyWebsiteZoom(websiteScale + 0.1);
    applyWebsiteZoom(1);
    hideLoading();
    clearViewerKeyHandler();
    currentViewer = "website";
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  function updatePageInfo() {
    pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
  }

  function updateZoomInfo(scale) {
    zoomInfo.textContent = `${Math.round(scale * 100)}%`;
  }

  function clearViewerKeyHandler() {
    if (!viewerKeyHandler) return;
    document.removeEventListener("keydown", viewerKeyHandler);
    viewerKeyHandler = null;
  }

  function updateFullscreenLabel() {
    fullscreenBtn.textContent = (document.fullscreenElement || localFullscreen) ? "Exit Fullscreen" : "Fullscreen";
  }

  function showLoading(message) {
    loadingText.textContent = message;
    loadingOverlay.style.display = "flex";
  }

  function hideLoading() {
    loadingOverlay.style.display = "none";
  }

  function hideLoadingForPdf() {
    loadingOverlay.style.display = "none";
    loadingText.textContent = "";
  }

  async function toggleFullscreen() {
    if (embedded) {
      localFullscreen = !localFullscreen;
      document.body.classList.toggle("reader-fullscreen", localFullscreen);
      if (window.parent !== window) {
        try {
          if (typeof window.parent.estrattoSetAppChromeHidden === "function") {
            window.parent.estrattoSetAppChromeHidden(localFullscreen);
          }
          window.parent.postMessage({ type: "estratto-set-app-chrome-hidden", hidden: localFullscreen }, window.location.origin);
        } catch (e) {
          console.warn("Parent chrome toggle failed:", e);
        }
      }
      updateFullscreenLabel();
      return;
    }

    const target = viewerContainer;
    const canNativeFullscreen = typeof target.requestFullscreen === "function" && typeof document.exitFullscreen === "function";
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else if (canNativeFullscreen) {
      await target.requestFullscreen();
    } else {
      localFullscreen = !localFullscreen;
      document.body.classList.toggle("reader-fullscreen", localFullscreen);
    }
    updateFullscreenLabel();
  }

  function closeViewer() {
    if (embedded && window.parent !== window) {
      try {
        if (typeof window.parent.estrattoSetAppChromeHidden === "function") {
          window.parent.estrattoSetAppChromeHidden(false);
        }
      } catch (e) {
        console.warn("Could not restore parent chrome on close:", e);
      }
    }
    if (embedded && window.parent !== window) {
      try {
        if (typeof window.parent.estrattoCloseDocument === "function") {
          window.parent.estrattoCloseDocument(messageId);
          return;
        }
      } catch (e) {
        console.error("Direct parent close failed:", e);
      }
      window.parent.postMessage({ type: "estratto-close-document", messageId }, "*");
      return;
    }
    window.location.href = "/";
  }

  function saveProgress() {
    // Debounce progress saving
    clearTimeout(saveProgressTimer);
    saveProgressTimer = setTimeout(async () => {
      try {
        await api(`/api/progress/${messageId}`, {
          method: "POST",
          body: JSON.stringify({
            current_page: currentPage,
            total_pages: totalPages,
            scroll_position: document.getElementById("viewer-container").scrollTop,
          }),
        });
      } catch (e) {
        console.error("Failed to save progress:", e);
      }
    }, 1000);
  }

  function savePdfProgress(scrollRatio = 0) {
    clearTimeout(saveProgressTimer);
    saveProgressTimer = setTimeout(async () => {
      try {
        await api(`/api/progress/${messageId}`, {
          method: "POST",
          body: JSON.stringify({
            current_page: currentPage,
            total_pages: totalPages,
            scroll_position: scrollRatio,
          }),
        });
      } catch (e) {
        console.error("Failed to save PDF progress:", e);
      }
    }, 300);
  }

  // PDF Viewer (stacked PDF.js pages with zoom and persistent page restore)
  async function initPdfViewer() {
    console.log(`[PDF Viewer] Initializing stacked PDF.js viewer for message_id=${messageId}, filename=${filename}`);

    const pdfViewer = document.getElementById("pdf-viewer");
    const container = document.getElementById("viewer-container");
    const progress = await api(`/api/progress/${messageId}`);
    let pdfDoc = null;
    let currentScale = 1.2;
    let renderVersion = 0;
    let pageEntries = [];
    let suppressScrollSave = false;
    let outlineOpen = false;
    let estimatedPageWidth = 0;
    let estimatedPageHeight = 0;
    let isRestoringPosition = false;
    const INITIAL_RENDER_RADIUS = 0;
    const ACTIVE_RENDER_RADIUS = 1;
    let outlineLoaded = false;
    let outlineLoading = false;

    pdfViewer.style.display = "flex";
    showLoading("Loading PDF... 0%");
    prevBtn.style.display = "inline-block";
    nextBtn.style.display = "inline-block";
    pageInfo.style.display = "inline";
    zoomOutBtn.style.display = "inline-block";
    zoomInBtn.style.display = "inline-block";
    zoomInfo.style.display = "inline";
    outlineToggleBtn.style.display = "none";

    currentPage = Math.max(1, Number(progress.current_page) || 1);
    const savedScrollRatio = Number(progress.scroll_position) || 0;

    function getPageAnchor(pageNumber) {
      return pageEntries[pageNumber - 1]?.wrapper || null;
    }

    function clampPage(pageNumber) {
      return Math.max(1, Math.min(totalPages, pageNumber));
    }

    function createPageShell(pageNumber) {
      const pageWrapper = document.createElement("div");
      pageWrapper.className = "pdf-page";
      pageWrapper.dataset.page = String(pageNumber);

      const canvas = document.createElement("canvas");
      canvas.className = "pdf-page-canvas";
      canvas.dataset.page = String(pageNumber);
      pageWrapper.appendChild(canvas);

      const textLayer = document.createElement("div");
      textLayer.className = "pdf-text-layer";
      pageWrapper.appendChild(textLayer);

      pdfViewer.appendChild(pageWrapper);
      pageEntries[pageNumber - 1] = {
        wrapper: pageWrapper,
        canvas,
        textLayer,
        width: estimatedPageWidth,
        height: estimatedPageHeight,
        renderedScale: null,
        renderPromise: null,
        renderToken: 0,
      };
      applyPageDimensions(pageNumber, estimatedPageWidth, estimatedPageHeight);
    }

    function applyPageDimensions(pageNumber, width, height) {
      const entry = pageEntries[pageNumber - 1];
      if (!entry) return;
      entry.width = Math.max(1, Math.floor(width));
      entry.height = Math.max(1, Math.floor(height));
      entry.wrapper.style.width = `${entry.width}px`;
      entry.wrapper.style.height = `${entry.height}px`;
      entry.canvas.style.width = `${entry.width}px`;
      entry.canvas.style.height = `${entry.height}px`;
      entry.textLayer.style.width = `${entry.width}px`;
      entry.textLayer.style.height = `${entry.height}px`;
    }

    function clearPageRender(pageNumber) {
      const entry = pageEntries[pageNumber - 1];
      if (!entry || entry.renderedScale === null) return;
      entry.canvas.width = 0;
      entry.canvas.height = 0;
      entry.textLayer.replaceChildren();
      entry.renderedScale = null;
    }

    async function renderPage(pageNumber, version) {
      const entry = pageEntries[pageNumber - 1];
      if (!entry) return;
      if (entry.renderedScale === currentScale) return;
      if (entry.renderPromise) {
        if (entry.renderToken === version) {
          await entry.renderPromise;
          if (entry.renderedScale === currentScale) return;
        } else {
          return;
        }
      }

      entry.renderToken = version;
      entry.renderPromise = (async () => {
        const page = await pdfDoc.getPage(pageNumber);
        if (version !== renderVersion || entry.renderToken !== version) return;
        const viewport = page.getViewport({ scale: currentScale });
        const outputScale = window.devicePixelRatio || 1;
        applyPageDimensions(pageNumber, viewport.width, viewport.height);

        entry.canvas.width = Math.floor(viewport.width * outputScale);
        entry.canvas.height = Math.floor(viewport.height * outputScale);

        await page.render({
          canvasContext: entry.canvas.getContext("2d"),
          viewport,
          transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null,
        }).promise;
        if (version !== renderVersion || entry.renderToken !== version) return;

        const textContent = await page.getTextContent();
        entry.textLayer.replaceChildren();
        await pdfjsLib.renderTextLayer({
          textContentSource: textContent,
          container: entry.textLayer,
          viewport,
          textDivs: [],
        }).promise;
        if (version !== renderVersion || entry.renderToken !== version) return;
        entry.renderedScale = currentScale;
      })();

      try {
        await entry.renderPromise;
      } finally {
        if (entry.renderToken === version) {
          entry.renderPromise = null;
        }
      }
    }

    function trimRenderedPages(centerPage, radius) {
      const keepStart = clampPage(centerPage - radius);
      const keepEnd = clampPage(centerPage + radius);
      for (let pageNumber = 1; pageNumber <= totalPages; pageNumber += 1) {
        if (pageNumber < keepStart || pageNumber > keepEnd) {
          clearPageRender(pageNumber);
        }
      }
    }

    function queueVisibleRender(centerPage, { immediate = false, radius = ACTIVE_RENDER_RADIUS } = {}) {
      if (!pdfDoc) return Promise.resolve();
      const version = renderVersion;
      const start = clampPage(centerPage - radius);
      const end = clampPage(centerPage + radius);
      const run = async () => {
        for (let pageNumber = start; pageNumber <= end; pageNumber += 1) {
          await renderPage(pageNumber, version);
          if (version !== renderVersion) return;
        }
        trimRenderedPages(centerPage, radius);
      };
      if (immediate) {
        return run();
      } else {
        window.setTimeout(() => {
          if (version !== renderVersion) return;
          run().catch((e) => console.error("Deferred PDF render failed:", e));
        }, 0);
      }
      return Promise.resolve();
    }

    function getCurrentPdfPosition() {
      if (!pageEntries.length) {
        return { page: currentPage, ratio: 0 };
      }
      const midpoint = container.scrollTop + container.clientHeight * 0.35;
      let page = 1;
      for (let i = 0; i < pageEntries.length; i++) {
        if (pageEntries[i].wrapper.offsetTop <= midpoint) page = i + 1;
        else break;
      }
      const anchor = getPageAnchor(page);
      const ratio = anchor
        ? Math.max(0, Math.min(1, (container.scrollTop - anchor.offsetTop) / Math.max(anchor.offsetHeight, 1)))
        : 0;
      return { page, ratio };
    }

    async function renderAllPages(restorePage = currentPage, restoreRatio = savedScrollRatio) {
      if (!pdfDoc) return;
      renderVersion += 1;
      const version = renderVersion;
      pdfViewer.innerHTML = "";
      pageEntries = [];
      updateZoomInfo(currentScale);

      for (let pageNumber = 1; pageNumber <= totalPages; pageNumber += 1) {
        createPageShell(pageNumber);
      }

      currentPage = Math.max(1, Math.min(restorePage, totalPages));
      updatePageInfo();
      suppressScrollSave = true;
      isRestoringPosition = true;
      await queueVisibleRender(currentPage, { immediate: true, radius: INITIAL_RENDER_RADIUS });
      if (version !== renderVersion) return;
      requestAnimationFrame(() => {
        const anchor = getPageAnchor(currentPage);
        if (anchor) {
          const pageTop = anchor.offsetTop;
          const pageHeight = anchor.offsetHeight || 1;
          container.scrollTop = pageTop + Math.max(0, Math.min(restoreRatio, 1)) * pageHeight;
        } else {
          container.scrollTop = 0;
        }
        setTimeout(() => {
          isRestoringPosition = false;
          suppressScrollSave = false;
        }, 150);
      });
    }

    function updateEstimatedPageSize(scaleRatio) {
      estimatedPageWidth = Math.max(1, Math.floor(estimatedPageWidth * scaleRatio));
      estimatedPageHeight = Math.max(1, Math.floor(estimatedPageHeight * scaleRatio));
    }

    function resizePageShells(scaleRatio) {
      for (const entry of pageEntries) {
        if (!entry) continue;
        applyPageDimensions(
          Number(entry.wrapper.dataset.page),
          Math.max(1, entry.width * scaleRatio),
          Math.max(1, entry.height * scaleRatio),
        );
        entry.renderedScale = null;
      }
    }

    function applyPdfZoom(nextScale) {
      const previousScale = currentScale;
      if (nextScale === previousScale) return;
      const position = getCurrentPdfPosition();
      const targetPage = clampPage(position.page || currentPage);

      currentPage = targetPage;
      updatePageInfo();
      suppressScrollSave = true;
      currentScale = nextScale;
      renderVersion += 1;
      updateZoomInfo(currentScale);

      const scaleRatio = currentScale / previousScale;
      updateEstimatedPageSize(scaleRatio);
      resizePageShells(scaleRatio);

      requestAnimationFrame(() => {
        const nextAnchor = getPageAnchor(targetPage);
        if (nextAnchor) {
          const pageHeight = Math.max(nextAnchor.offsetHeight, 1);
          container.scrollTop = nextAnchor.offsetTop + Math.max(0, Math.min(position.ratio || 0, 1)) * pageHeight;
        }
        queueVisibleRender(targetPage, { immediate: true, radius: 0 }).catch((e) => {
          console.error("Current PDF page rerender failed:", e);
        });
        queueVisibleRender(targetPage, { radius: ACTIVE_RENDER_RADIUS });
        window.setTimeout(() => {
          suppressScrollSave = false;
          savePdfProgress(Math.max(0, Math.min(position.ratio || 0, 1)));
        }, 120);
      });
    }

    function syncCurrentPageFromScroll() {
      if (!pageEntries.length) return;
      const midpoint = container.scrollTop + container.clientHeight * 0.35;
      let nextPage = 1;
      for (let i = 0; i < pageEntries.length; i++) {
        if (pageEntries[i].wrapper.offsetTop <= midpoint) nextPage = i + 1;
        else break;
      }
      currentPage = nextPage;
      updatePageInfo();
      queueVisibleRender(currentPage);
      const anchor = getPageAnchor(currentPage);
      const scrollRatio = anchor
        ? Math.max(0, Math.min(1, (container.scrollTop - anchor.offsetTop) / Math.max(anchor.offsetHeight, 1)))
        : 0;
      if (!suppressScrollSave) savePdfProgress(scrollRatio);
    }

    function scrollToPage(pageNumber) {
      const anchor = getPageAnchor(pageNumber);
      if (!anchor) return;
      currentPage = pageNumber;
      updatePageInfo();
      suppressScrollSave = true;
      container.scrollTo({ top: anchor.offsetTop, behavior: "smooth" });
      setTimeout(() => {
        suppressScrollSave = false;
        savePdfProgress(0);
      }, 250);
    }

    async function resolveOutlinePage(dest) {
      if (!dest) return null;
      const destination = typeof dest === "string" ? await pdfDoc.getDestination(dest) : dest;
      if (!destination || !destination[0]) return null;
      const pageIndex = await pdfDoc.getPageIndex(destination[0]);
      return pageIndex + 1;
    }

    async function buildOutlineMarkup(items) {
      const list = document.createElement("ul");
      list.className = "outline-list";

      for (const item of items) {
        const entry = document.createElement("li");
        const button = document.createElement("button");
        button.className = "outline-link";
        button.textContent = item.title || "Untitled";
        button.addEventListener("click", async () => {
          const targetPage = await resolveOutlinePage(item.dest);
          if (targetPage) scrollToPage(targetPage);
        });
        entry.appendChild(button);

        if (item.items && item.items.length) {
          entry.appendChild(await buildOutlineMarkup(item.items));
        }
        list.appendChild(entry);
      }
      return list;
    }

    async function loadOutline() {
      if (outlineLoaded || outlineLoading) return;
      outlineLoading = true;
      try {
        const outline = await pdfDoc.getOutline();
        if (!outline || !outline.length) {
          documentOutline.classList.remove("open");
          documentOutline.innerHTML = "";
          outlineToggleBtn.style.display = "none";
          outlineLoaded = true;
          return;
        }

        documentOutline.innerHTML = "";
        documentOutline.appendChild(await buildOutlineMarkup(outline));
        outlineToggleBtn.style.display = "inline-block";
        outlineToggleBtn.textContent = outlineOpen ? "Hide Contents" : "Contents";
        outlineLoaded = true;
      } finally {
        outlineLoading = false;
      }
    }

    pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.4.120/pdf.worker.min.js";
    const loadingTask = pdfjsLib.getDocument({
      url: `/api/file/${messageId}`,
      disableAutoFetch: true,
      disableStream: false,
      disableRange: false,
      rangeChunkSize: 262144,
    });
    loadingTask.onProgress = (progressData) => {
      if (currentViewer === "pdf") return;
      if (!progressData?.total) {
        showLoading("Loading PDF...");
        return;
      }
      const percent = Math.max(0, Math.min(95, Math.round((progressData.loaded / progressData.total) * 100)));
      showLoading(`Loading PDF... ${percent}%`);
    };
    pdfDoc = await loadingTask.promise;
    hideLoadingForPdf();
    totalPages = pdfDoc.numPages || 1;
    if (currentPage > totalPages) currentPage = totalPages;
    const firstPage = await pdfDoc.getPage(1);
    const initialViewport = firstPage.getViewport({ scale: currentScale });
    estimatedPageWidth = Math.max(1, Math.floor(initialViewport.width));
    estimatedPageHeight = Math.max(1, Math.floor(initialViewport.height));
    outlineToggleBtn.style.display = "inline-block";
    outlineToggleBtn.textContent = "Contents";
    await renderAllPages(currentPage, savedScrollRatio);
    hideLoadingForPdf();

    window.setTimeout(() => {
      queueVisibleRender(currentPage, { radius: ACTIVE_RENDER_RADIUS });
    }, 0);

    prevBtn.onclick = async () => {
      if (currentPage <= 1) return;
      scrollToPage(currentPage - 1);
    };

    nextBtn.onclick = async () => {
      if (currentPage >= totalPages) return;
      scrollToPage(currentPage + 1);
    };

    zoomOutBtn.onclick = async () => {
      if (currentScale <= 0.7) return;
      applyPdfZoom(Math.max(0.7, currentScale - 0.15));
    };

    zoomInBtn.onclick = async () => {
      if (currentScale >= 2.5) return;
      applyPdfZoom(Math.min(2.5, currentScale + 0.15));
    };

    outlineToggleBtn.onclick = async () => {
      if (!outlineLoaded) {
        outlineToggleBtn.disabled = true;
        outlineToggleBtn.textContent = "Loading Contents...";
        try {
          await loadOutline();
        } finally {
          outlineToggleBtn.disabled = false;
          if (!outlineLoaded) {
            outlineToggleBtn.textContent = "Contents";
            return;
          }
        }
      }
      outlineOpen = !outlineOpen;
      documentOutline.classList.toggle("open", outlineOpen);
      outlineToggleBtn.textContent = outlineOpen ? "Hide Contents" : "Contents";
    };

    container.addEventListener("scroll", () => {
      syncCurrentPageFromScroll();
    });

    container.addEventListener("wheel", async (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      if (e.deltaY < 0 && currentScale < 2.5) {
        applyPdfZoom(Math.min(2.5, currentScale + 0.1));
      } else if (e.deltaY > 0 && currentScale > 0.7) {
        applyPdfZoom(Math.max(0.7, currentScale - 0.1));
      }
    }, { passive: false });

    clearViewerKeyHandler();
    viewerKeyHandler = async (e) => {
      if (e.key === "ArrowLeft" && currentPage > 1) {
        scrollToPage(currentPage - 1);
      } else if (e.key === "ArrowRight" && currentPage < totalPages) {
        scrollToPage(currentPage + 1);
      }
    };
    document.addEventListener("keydown", viewerKeyHandler);

    currentViewer = "pdf";
  }

  // EPUB Viewer
  async function initEpubViewer() {
    const container = document.getElementById("epub-viewer");
    const viewerContent = document.getElementById("viewer-content");
    const viewerContainer = document.getElementById("viewer-container");
    const stage = document.getElementById("pdf-stage");
    viewerContainer.classList.add("epub-mode");
    container.style.display = "block";
    viewerContent.style.display = "flex";
    showLoading("Loading EPUB...");

    function syncEpubViewport() {
      const height = Math.max(viewerContainer.clientHeight, window.innerHeight - viewerToolbar.offsetHeight);
      stage.style.minHeight = `${height}px`;
      container.style.minHeight = `${height}px`;
      stage.style.height = "auto";
      container.style.height = "auto";
    }

    syncEpubViewport();

    showLoading("Opening EPUB...");
    const book = ePub(`/api/file/${messageId}`);
    const rendition = book.renderTo("epub-viewer", {
      width: "100%",
      height: "100%",
      manager: "continuous",
      flow: "scrolled-doc",
      spread: "none",
    });
    let fontScale = 1;
    let outlineOpen = false;

    rendition.themes.register("light", {
      body: {
        color: "#111827",
        background: "#f8fafc",
      },
      a: {
        color: "#2563eb",
      },
    });
    rendition.themes.register("dark", {
      body: {
        color: "#e5e7eb",
        background: "#111827",
      },
      a: {
        color: "#93c5fd",
      },
    });

    function applyEpubTheme() {
      const theme = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
      rendition.themes.select(theme);
    }

    async function setFontScale(nextScale) {
      fontScale = Math.max(0.85, Math.min(1.8, nextScale));
      const location = rendition.currentLocation();
      const cfi = location?.start?.cfi || null;
      rendition.themes.fontSize(`${Math.round(fontScale * 100)}%`);
      updateZoomInfo(fontScale);
      if (cfi) {
        await rendition.display(cfi);
      }
    }

    async function saveEpubProgress(cfi) {
      clearTimeout(saveProgressTimer);
      saveProgressTimer = setTimeout(async () => {
        try {
          await api(`/api/progress/${messageId}`, {
            method: "POST",
            body: JSON.stringify({
              current_page: currentPage,
              total_pages: totalPages,
              scroll_position: cfi || 0,
            }),
          });
        } catch (e) {
          console.error("Failed to save EPUB progress:", e);
        }
      }, 300);
    }

    async function navigateToEpubTarget(target) {
      if (!target) return;

      let resolvedTarget = target;

      try {
        if (typeof target === "string" && typeof book.cfiFromHref === "function") {
          const cfiTarget = book.cfiFromHref(target);
          if (cfiTarget) {
            resolvedTarget = cfiTarget;
          }
        }
      } catch (e) {
        console.warn("EPUB CFI resolution failed, falling back to href:", target, e);
      }

      await rendition.display(resolvedTarget);
      outlineOpen = false;
      documentOutline.classList.remove("open");
      outlineToggleBtn.textContent = "Contents";
    }

    async function buildEpubOutlineMarkup(items) {
      const list = document.createElement("ul");
      list.className = "outline-list";

      for (const item of items || []) {
        const entry = document.createElement("li");
        const button = document.createElement("button");
        button.className = "outline-link";
        button.textContent = item.label || item.href || "Untitled";
        button.addEventListener("click", async () => {
          if (!item.href) return;
          await navigateToEpubTarget(item.href);
        });
        entry.appendChild(button);

        if (item.subitems && item.subitems.length) {
          entry.appendChild(await buildEpubOutlineMarkup(item.subitems));
        }
        list.appendChild(entry);
      }

      return list;
    }

    async function loadEpubOutline() {
      const toc = book.navigation?.toc || [];
      if (!toc.length) {
        documentOutline.classList.remove("open");
        documentOutline.innerHTML = "";
        outlineToggleBtn.style.display = "none";
        return;
      }

      documentOutline.innerHTML = "";
      documentOutline.appendChild(await buildEpubOutlineMarkup(toc));
      outlineToggleBtn.style.display = "inline-block";
      outlineToggleBtn.textContent = outlineOpen ? "Hide Contents" : "Contents";
    }

    // Load saved progress
    const progress = await api(`/api/progress/${messageId}`);
    const savedCfi = progress.scroll_position || null;

    prevBtn.style.display = "inline-block";
    nextBtn.style.display = "inline-block";
    pageInfo.style.display = "inline";
    zoomOutBtn.style.display = "inline-block";
    zoomInBtn.style.display = "inline-block";
    zoomInfo.style.display = "inline";
    updateZoomInfo(fontScale);
    applyEpubTheme();

    if (savedCfi && savedCfi !== 0) {
      await rendition.display(savedCfi);
    } else {
      await rendition.display();
    }
    hideLoading();

    // Get total locations (approximate page count)
    book.ready.then(() => {
      applyEpubTheme();
      return loadEpubOutline();
    }).then(() => {
      window.setTimeout(() => {
        book.locations.generate(1024).then(() => {
          totalPages = book.locations.total || 1;
          currentPage = Math.max(1, Number(progress.current_page) || 1);
          updatePageInfo();
        }).catch((e) => {
          console.warn("EPUB location generation failed:", e);
        });
      }, 0);
    });

    rendition.on("relocated", (location) => {
      if (book.locations && book.locations.total) {
        currentPage = (location.start.location || 0) + 1;
        totalPages = book.locations.total;
        updatePageInfo();
      }
      saveEpubProgress(location.start.cfi);
    });

    outlineToggleBtn.onclick = () => {
      outlineOpen = !outlineOpen;
      documentOutline.classList.toggle("open", outlineOpen);
      outlineToggleBtn.textContent = outlineOpen ? "Hide Contents" : "Contents";
    };

    prevBtn.onclick = () => {
      viewerContainer.scrollBy({ top: -Math.max(240, viewerContainer.clientHeight * 0.9), behavior: "smooth" });
    };
    nextBtn.onclick = () => {
      viewerContainer.scrollBy({ top: Math.max(240, viewerContainer.clientHeight * 0.9), behavior: "smooth" });
    };
    zoomOutBtn.onclick = async () => setFontScale(fontScale - 0.1);
    zoomInBtn.onclick = async () => setFontScale(fontScale + 0.1);

    viewerContainer.addEventListener("wheel", (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      setFontScale(fontScale + (e.deltaY < 0 ? 0.05 : -0.05));
    }, { passive: false });

    const observer = new MutationObserver(() => applyEpubTheme());
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    window.addEventListener("resize", () => {
      syncEpubViewport();
      rendition.resize(viewerContainer.clientWidth, viewerContainer.clientHeight);
    });

    document.addEventListener("fullscreenchange", () => {
      syncEpubViewport();
      rendition.resize(viewerContainer.clientWidth, viewerContainer.clientHeight);
    });

    // Keyboard navigation
    clearViewerKeyHandler();
    viewerKeyHandler = (e) => {
      if (e.key === "ArrowLeft") {
        viewerContainer.scrollBy({ top: -Math.max(160, viewerContainer.clientHeight * 0.75), behavior: "smooth" });
      } else if (e.key === "ArrowRight") {
        viewerContainer.scrollBy({ top: Math.max(160, viewerContainer.clientHeight * 0.75), behavior: "smooth" });
      }
    };
    document.addEventListener("keydown", viewerKeyHandler);

    currentViewer = "epub";
  }

  // Comic Viewer (CBZ/CBR)
  async function initComicViewer() {
    const container = document.getElementById("comic-viewer");
    container.style.display = "block";
    showLoading("Loading comic archive...");

    // Fetch the CBZ/CBR file
    const response = await fetch(`/api/file/${messageId}`);
    const blob = await response.blob();
    showLoading("Unpacking pages...");

    // Load with JSZip
    const zip = await JSZip.loadAsync(blob);
    const imageFiles = Object.keys(zip.files)
      .filter((name) => /\.(jpg|jpeg|png|gif|webp)$/i.test(name))
      .sort();

    totalPages = imageFiles.length;

    // Load saved progress
    const progress = await api(`/api/progress/${messageId}`);
    currentPage = progress.current_page || 1;

    let imageElements = [];

    async function loadImages() {
      for (let i = 0; i < imageFiles.length; i++) {
        const file = zip.files[imageFiles[i]];
        const blob = await file.async("blob");
        const url = URL.createObjectURL(blob);

        const img = document.createElement("img");
        img.src = url;
        img.dataset.page = i + 1;
        container.appendChild(img);
        imageElements.push(img);
      }
    }

    await loadImages();
    updatePageInfo();
    hideLoading();

    // Scroll to saved page
    if (currentPage > 1 && imageElements[currentPage - 1]) {
      imageElements[currentPage - 1].scrollIntoView({ behavior: "smooth" });
    }

    // Track scroll position
    const viewerContainer = document.getElementById("viewer-container");
    let scrollTimeout;
    viewerContainer.addEventListener("scroll", () => {
      clearTimeout(scrollTimeout);
      scrollTimeout = setTimeout(() => {
        // Find which image is in view
        const scrollTop = viewerContainer.scrollTop;
        const containerHeight = viewerContainer.clientHeight;

        for (let i = 0; i < imageElements.length; i++) {
          const img = imageElements[i];
          const rect = img.getBoundingClientRect();
          if (rect.top >= 0 && rect.top < containerHeight / 2) {
            currentPage = i + 1;
            updatePageInfo();
            saveProgress();
            break;
          }
        }
      }, 200);
    });

    prevBtn.onclick = () => {
      if (currentPage > 1) {
        currentPage--;
        imageElements[currentPage - 1].scrollIntoView({ behavior: "smooth" });
        updatePageInfo();
        saveProgress();
      }
    };

    nextBtn.onclick = () => {
      if (currentPage < totalPages) {
        currentPage++;
        imageElements[currentPage - 1].scrollIntoView({ behavior: "smooth" });
        updatePageInfo();
        saveProgress();
      }
    };

    // Keyboard navigation
    document.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" && currentPage > 1) {
        currentPage--;
        imageElements[currentPage - 1].scrollIntoView({ behavior: "smooth" });
      } else if (e.key === "ArrowRight" && currentPage < totalPages) {
        currentPage++;
        imageElements[currentPage - 1].scrollIntoView({ behavior: "smooth" });
      }
    });

    currentViewer = "comic";
  }

  // Test if file is accessible first
  async function testFileAccess() {
    try {
      showLoading("Checking file access...");
      const response = await fetch(`/api/file/${messageId}`, { method: "HEAD" });
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`File not accessible (${response.status}): ${errorText}`);
      }
      const contentType = response.headers.get('content-type');
      const contentLength = response.headers.get('content-length');
      console.log('File accessible:', {
        contentType,
        contentLength: contentLength ? `${contentLength} bytes` : 'unknown',
        status: response.status
      });

      return true;
    } catch (e) {
      console.error('File access test failed:', e);
      throw e;
    }
  }

  (async () => {
    if (viewerKind === "website") {
      if (!websiteSrc) {
        reportViewerError("Missing website URL");
        return;
      }
      initWebsiteViewer();
      return;
    }

    const fileInfo = await getFileInfo();
    const resolvedFilename = fileInfo?.filename || filename;
    const resolvedExt = String(fileInfo?.ext || extParam || inferExtFromFilename(resolvedFilename)).replace(/^\./, "").toLowerCase();
    document.getElementById("doc-title").textContent = resolvedFilename;

    if (resolvedExt === "pdf") {
      testFileAccess()
        .then(() => initPdfViewer())
        .catch((e) => {
          console.error("[PDF Viewer] Error:", e);
          reportViewerError(`Failed to load PDF: ${e.message}\n\nMessage ID: ${messageId}\nFilename: ${resolvedFilename}`);
        });
      return;
    }

    if (resolvedExt === "epub") {
      testFileAccess()
        .then(() => initEpubViewer())
        .catch((e) => {
          console.error("EPUB viewer error:", e);
          reportViewerError(`Failed to load EPUB: ${e.message}\n\nMessage ID: ${messageId}\nFilename: ${resolvedFilename}`);
        });
      return;
    }

    if (resolvedExt === "cbz" || resolvedExt === "cbr") {
      testFileAccess()
        .then(() => initComicViewer())
        .catch((e) => {
          console.error("Comic viewer error:", e);
          reportViewerError(`Failed to load comic: ${e.message}\n\nMessage ID: ${messageId}\nFilename: ${resolvedFilename}`);
        });
      return;
    }

    reportViewerError(`Unsupported file format: ${resolvedFilename}`);
  })();

  fullscreenBtn.addEventListener("click", async () => {
    try {
      await toggleFullscreen();
    } catch (e) {
      console.error("Viewer fullscreen failed:", e);
    }
  });
  closeBtn.addEventListener("click", closeViewer);

  document.addEventListener("fullscreenchange", updateFullscreenLabel);
  updateFullscreenLabel();
})();

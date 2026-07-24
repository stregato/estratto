(() => {
  console.log("=== VIEWER.JS v6 LOADED (STACKED PDF VIEWER) ===");

  const params = new URLSearchParams(window.location.search);
  const messageId = params.get("id");
  const filename = params.get("filename") || "Document";
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
  const loadingOverlay = document.getElementById("viewer-loading");
  const loadingText = document.getElementById("viewer-loading-text");

  const prevBtn = document.getElementById("prev-page");
  const nextBtn = document.getElementById("next-page");
  const pageInfo = document.getElementById("page-info");

  if (embedded) {
    backBtn.style.display = "none";
    viewerToolbar.style.height = "42px";
    viewerContainer.style.top = "42px";
  }

  let currentViewer = null;
  let currentPage = 1;
  let totalPages = 1;
  let saveProgressTimer = null;
  let pdfKeyHandler = null;

  // Detect file type from extension
  const ext = filename.split(".").pop().toLowerCase();

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

  function updateFullscreenLabel() {
    fullscreenBtn.textContent = document.fullscreenElement ? "Exit Fullscreen" : "Fullscreen";
  }

  function showLoading(message) {
    loadingText.textContent = message;
    loadingOverlay.style.display = "flex";
  }

  function hideLoading() {
    loadingOverlay.style.display = "none";
  }

  async function toggleFullscreen() {
    const target = embedded ? document.documentElement : viewerContainer;
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await target.requestFullscreen();
    }
    updateFullscreenLabel();
  }

  function closeViewer() {
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
    let pageCanvases = [];
    let suppressScrollSave = false;

    pdfViewer.style.display = "flex";
    showLoading("Loading PDF... 0%");
    prevBtn.style.display = "inline-block";
    nextBtn.style.display = "inline-block";
    pageInfo.style.display = "inline";
    zoomOutBtn.style.display = "inline-block";
    zoomInBtn.style.display = "inline-block";
    zoomInfo.style.display = "inline";

    currentPage = Math.max(1, Number(progress.current_page) || 1);
    const savedScrollRatio = Number(progress.scroll_position) || 0;

    function getPageAnchor(pageNumber) {
      return pageCanvases[pageNumber - 1] || null;
    }

    async function renderAllPages(restorePage = currentPage, restoreRatio = savedScrollRatio) {
      if (!pdfDoc) return;
      renderVersion += 1;
      const version = renderVersion;
      pdfViewer.innerHTML = "";
      pageCanvases = [];
      updateZoomInfo(currentScale);

      for (let i = 1; i <= totalPages; i++) {
        const page = await pdfDoc.getPage(i);
        if (version !== renderVersion) return;
        const viewport = page.getViewport({ scale: currentScale });
        const outputScale = window.devicePixelRatio || 1;
        const canvas = document.createElement("canvas");
        canvas.className = "pdf-page-canvas";
        canvas.dataset.page = String(i);
        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        pdfViewer.appendChild(canvas);
        pageCanvases.push(canvas);

        await page.render({
          canvasContext: canvas.getContext("2d"),
          viewport,
          transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null,
        }).promise;
      }

      currentPage = Math.max(1, Math.min(restorePage, totalPages));
      updatePageInfo();
      suppressScrollSave = true;
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
          suppressScrollSave = false;
        }, 150);
      });
    }

    function syncCurrentPageFromScroll() {
      if (!pageCanvases.length) return;
      const midpoint = container.scrollTop + container.clientHeight * 0.35;
      let nextPage = 1;
      for (let i = 0; i < pageCanvases.length; i++) {
        if (pageCanvases[i].offsetTop <= midpoint) nextPage = i + 1;
        else break;
      }
      currentPage = nextPage;
      updatePageInfo();
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

    pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.4.120/pdf.worker.min.js";
    const loadingTask = pdfjsLib.getDocument(`/api/file/${messageId}`);
    loadingTask.onProgress = (progressData) => {
      if (!progressData?.total) {
        showLoading("Loading PDF...");
        return;
      }
      const percent = Math.max(0, Math.min(100, Math.round((progressData.loaded / progressData.total) * 100)));
      showLoading(`Loading PDF... ${percent}%`);
    };
    pdfDoc = await loadingTask.promise;
    totalPages = pdfDoc.numPages || 1;
    if (currentPage > totalPages) currentPage = totalPages;
    showLoading("Rendering pages...");
    await renderAllPages(currentPage, savedScrollRatio);
    hideLoading();

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
      currentScale = Math.max(0.7, currentScale - 0.15);
      await renderAllPages(currentPage, 0);
    };

    zoomInBtn.onclick = async () => {
      if (currentScale >= 2.5) return;
      currentScale = Math.min(2.5, currentScale + 0.15);
      await renderAllPages(currentPage, 0);
    };

    container.addEventListener("scroll", () => {
      syncCurrentPageFromScroll();
    });

    container.addEventListener("wheel", async (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      if (e.deltaY < 0 && currentScale < 2.5) {
        currentScale = Math.min(2.5, currentScale + 0.1);
      } else if (e.deltaY > 0 && currentScale > 0.7) {
        currentScale = Math.max(0.7, currentScale - 0.1);
      } else {
        return;
      }
      await renderAllPages(currentPage, 0);
    }, { passive: false });

    pdfKeyHandler = async (e) => {
      if (e.key === "ArrowLeft" && currentPage > 1) {
        scrollToPage(currentPage - 1);
      } else if (e.key === "ArrowRight" && currentPage < totalPages) {
        scrollToPage(currentPage + 1);
      }
    };
    document.addEventListener("keydown", pdfKeyHandler);

    currentViewer = "pdf";
  }

  // EPUB Viewer
  async function initEpubViewer() {
    const container = document.getElementById("epub-viewer");
    container.style.display = "block";
    showLoading("Loading EPUB...");

    const book = ePub(`/api/file/${messageId}`);
    const rendition = book.renderTo("epub-viewer", {
      width: "100%",
      height: "100%",
      spread: "none",
    });

    // Load saved progress
    const progress = await api(`/api/progress/${messageId}`);
    const savedCfi = progress.scroll_position || null;

    if (savedCfi && savedCfi !== 0) {
      await rendition.display(savedCfi);
    } else {
      await rendition.display();
    }
    hideLoading();

    // Get total locations (approximate page count)
    book.ready.then(() => {
      return book.locations.generate(1024);
    }).then(() => {
      totalPages = book.locations.total || 1;
      updatePageInfo();
    });

    rendition.on("relocated", (location) => {
      if (book.locations && book.locations.total) {
        currentPage = location.start.location || 1;
        totalPages = book.locations.total;
        updatePageInfo();

        // Save CFI for precise position
        saveProgressTimer && clearTimeout(saveProgressTimer);
        saveProgressTimer = setTimeout(async () => {
          await api(`/api/progress/${messageId}`, {
            method: "POST",
            body: JSON.stringify({
              current_page: currentPage,
              total_pages: totalPages,
              scroll_position: location.start.cfi,
            }),
          });
        }, 1000);
      }
    });

    prevBtn.onclick = () => rendition.prev();
    nextBtn.onclick = () => rendition.next();

    // Keyboard navigation
    document.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") rendition.prev();
      else if (e.key === "ArrowRight") rendition.next();
    });

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
      const response = await fetch(`/api/file/${messageId}`);
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

      // Don't consume the response here, just verify it's accessible
      return true;
    } catch (e) {
      console.error('File access test failed:', e);
      throw e;
    }
  }

  // Initialize appropriate viewer based on file type
  if (ext === "pdf") {
    testFileAccess()
      .then(() => initPdfViewer())
      .catch((e) => {
        console.error('[PDF Viewer] Error:', e);
        hideLoading();
        alert(`Failed to load PDF: ${e.message}\n\nMessage ID: ${messageId}\nFilename: ${filename}`);
        window.location.href = "/";
      });
  } else if (ext === "epub") {
    testFileAccess()
      .then(() => initEpubViewer())
      .catch((e) => {
        console.error('EPUB viewer error:', e);
        hideLoading();
        alert(`Failed to load EPUB: ${e.message}\n\nMessage ID: ${messageId}\nFilename: ${filename}`);
        window.location.href = "/";
      });
  } else if (ext === "cbz" || ext === "cbr") {
    testFileAccess()
      .then(() => initComicViewer())
      .catch((e) => {
        console.error('Comic viewer error:', e);
        hideLoading();
        alert(`Failed to load comic: ${e.message}\n\nMessage ID: ${messageId}\nFilename: ${filename}`);
        window.location.href = "/";
      });
  } else {
    hideLoading();
    alert(`Unsupported file format: ${ext}`);
    window.location.href = "/";
  }

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

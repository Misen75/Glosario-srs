// Glosario SRS - Lógica cliente SPA / PWA
(() => {
  "use strict";

  // Estado global
  const state = {
    areas: [],
    categories: [],
    terms: [],
    dueCards: [],
    currentDueIndex: 0,
    isCardRevealed: false,
    stats: null,
  };

  // Zoom helpers
  const ZOOM_LABELS = {
    1: "Micro · Código",
    2: "Medio · Módulos",
    3: "Macro · Arquitectura",
    4: "Infraestructura · Operación",
  };

  // Elementos DOM
  const dom = {
    tabs: document.querySelectorAll(".tab"),
    views: document.querySelectorAll(".view"),
    dueBadge: document.getElementById("due-badge"),

    
    // Guía
    zoomLegend: document.getElementById("zoom-legend"),
    areasContainer: document.getElementById("areas"),
    
    // Compendio
    search: document.getElementById("search"),
    catFilter: document.getElementById("cat-filter"),
    termsBody: document.getElementById("terms-body"),
    resultCount: document.getElementById("result-count"),
    btnAdd: document.getElementById("btn-add"),
    btnPrint: document.getElementById("btn-print"),
    
    // Entrenamiento
    dueNum: document.getElementById("due-num"),
    dueLabel: document.getElementById("due-label"),
    reviewedToday: document.getElementById("reviewed-today"),
    trainArea: document.getElementById("train-area"),
    trainEmpty: document.getElementById("train-empty"),
    flashcard: document.getElementById("flashcard"),
    fcCategory: document.getElementById("fc-category"),
    fcHelp: document.getElementById("fc-help"),
    fcTerm: document.getElementById("fc-term"),
    fcHint: document.getElementById("fc-hint"),
    fcAnswer: document.getElementById("fc-answer"),
    fcDef: document.getElementById("fc-def"),
    fcAnalog: document.getElementById("fc-analog"),
    fcTradeoff: document.getElementById("fc-tradeoff"),
    ratingPanel: document.getElementById("rating"),
    rateButtons: document.querySelectorAll(".rate"),
    pv1: document.getElementById("pv-1"),
    pv2: document.getElementById("pv-2"),
    pv3: document.getElementById("pv-3"),
    
    // Diálogos
    dlgCat: document.getElementById("dlg-category"),
    dcZoom: document.getElementById("dc-zoom"),
    dcName: document.getElementById("dc-name"),
    dcArea: document.getElementById("dc-area"),
    dcDesc: document.getElementById("dc-desc"),
    
    dlgAdd: document.getElementById("dlg-add"),
    formAdd: document.getElementById("form-add"),
    addInput: document.getElementById("add-input"),
    addStatus: document.getElementById("add-status"),
    addCancel: document.getElementById("add-cancel"),
    addSubmit: document.getElementById("add-submit"),
    
    toast: document.getElementById("toast"),
  };

  // --- Utilidades ---
  const STORAGE_KEY_PIN = "glosario_srs_web_pin";

  function getSavedPin() {
    return localStorage.getItem(STORAGE_KEY_PIN) || "";
  }

  function savePin(pin) {
    if (pin) localStorage.setItem(STORAGE_KEY_PIN, pin.trim());
  }

  function clearSavedPin() {
    localStorage.removeItem(STORAGE_KEY_PIN);
  }

  function promptPin(errorMessage = "") {
    return new Promise((resolve) => {
      const dlg = document.getElementById("dlg-pin");
      const form = document.getElementById("form-pin");
      const input = document.getElementById("pin-input");
      const errEl = document.getElementById("pin-error");
      const cancelBtn = document.getElementById("pin-cancel");

      if (!dlg) {
        const fallback = window.prompt(errorMessage ? `${errorMessage}\nIngresa tu PIN:` : "Ingresa tu PIN de acceso:");
        if (fallback) savePin(fallback);
        resolve(fallback || null);
        return;
      }

      if (errorMessage) {
        errEl.textContent = errorMessage;
        errEl.style.display = "block";
      } else {
        errEl.style.display = "none";
      }

      input.value = getSavedPin();

      const onSubmit = (e) => {
        e.preventDefault();
        const pin = input.value.trim();
        savePin(pin);
        cleanup();
        dlg.close();
        resolve(pin);
      };

      const onCancel = () => {
        cleanup();
        dlg.close();
        resolve(null);
      };

      function cleanup() {
        form.removeEventListener("submit", onSubmit);
        cancelBtn.removeEventListener("click", onCancel);
      }

      form.addEventListener("submit", onSubmit);
      cancelBtn.addEventListener("click", onCancel);

      dlg.showModal();
      input.focus();
    });
  }

  let activePinPromise = null;

  async function ensurePin(errMsg = "") {
    let pin = getSavedPin();
    if (!pin) {
      if (!activePinPromise) {
        activePinPromise = promptPin(errMsg).finally(() => {
          activePinPromise = null;
        });
      }
      pin = await activePinPromise;
    }
    return pin;
  }

  async function fetchWithPin(url, options = {}) {
    let pin = await ensurePin();
    if (!pin) {
      throw new Error("Se requiere PIN para acceder al sistema.");
    }
    const headers = {
      ...(options.headers || {}),
      "X-Web-Pin": pin,
    };
    let res = await fetch(url, { ...options, headers });
    if (res.status === 401) {
      clearSavedPin();
      pin = await promptPin("PIN incorrecto. Ingresa tu WEB_ACCESS_PIN:");
      if (!pin) {
        throw new Error("PIN no autorizado.");
      }
      headers["X-Web-Pin"] = pin;
      res = await fetch(url, { ...options, headers });
    }
    return res;
  }

  function showToast(msg, duration = 3000) {
    dom.toast.textContent = msg;
    dom.toast.hidden = false;
    setTimeout(() => { dom.toast.hidden = true; }, duration);
  }


  function normalizeText(text) {
    return (text || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  // --- Pestañas y Vistas ---
  function switchView(viewName) {
    dom.tabs.forEach(tab => {
      const active = tab.dataset.view === viewName;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", active);
    });
    dom.views.forEach(view => {
      view.classList.toggle("active", view.id === `view-${viewName}`);
    });
    if (viewName === "train") {
      loadDueCards();
    }
  }

  dom.tabs.forEach(tab => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });

  // --- Carga de datos iniciales ---
  async function loadData() {
    try {
      const pin = await ensurePin();
      if (!pin) {
        showToast("Se requiere PIN para ver el glosario");
        return;
      }

      const [areasRes, termsRes, statsRes] = await Promise.all([
        fetchWithPin("/api/areas").then(r => r.json()),
        fetchWithPin("/api/terms").then(r => r.json()),
        fetchWithPin("/api/stats").then(r => r.json()),
      ]);
      state.areas = areasRes;
      state.terms = termsRes;
      state.stats = statsRes;
      
      // Flatten categories for fast lookup
      state.categories = [];
      state.areas.forEach(a => {
        a.categories.forEach(c => state.categories.push(c));
      });

      renderZoomLegend();
      renderGuide();
      populateCategoryFilter();
      renderTermsTable();
      updateStatsUI();
      await loadDueCards();
    } catch (err) {
      console.error("Error cargando datos:", err);
      showToast("Error conectando con el servidor");
    }
  }

  function updateStatsUI() {
    if (!state.stats) return;
    const due = state.stats.due_today;
    if (due > 0) {
      dom.dueBadge.textContent = due;
      dom.dueBadge.hidden = false;
    } else {
      dom.dueBadge.hidden = true;
    }
    dom.reviewedToday.textContent = `Repasados hoy: ${state.stats.reviewed_today}`;
  }

  // --- Vista 1: Guía de Áreas y Categorías ---
  function renderZoomLegend() {
    dom.zoomLegend.innerHTML = [1, 2, 3, 4]
      .map(lvl => `<span class="zoom-badge zoom-${lvl}">Zoom ${lvl}: ${ZOOM_LABELS[lvl]}</span>`)
      .join("");
  }

  function renderGuide() {
    dom.areasContainer.innerHTML = state.areas.map(area => {
      const catsHtml = area.categories.map(c => `
        <div class="cat-card" data-cat-id="${c.id}">
          <div class="cat-top">
            <span class="zoom-badge zoom-${c.zoom_level}">Zoom ${c.zoom_level}</span>
            <span class="cat-terms-preview">${c.term_count || 0} términos</span>
          </div>
          <div class="cat-name">${escapeHtml(c.name)}</div>
          <p class="cat-desc">${escapeHtml(c.description.substring(0, 110))}...</p>
        </div>
      `).join("");

      return `
        <div class="area-section">
          <div class="area-header">
            <span>${escapeHtml(area.name)}</span>
            <small style="color: #64748b; font-weight: normal;">${area.categories.length} categorías</small>
          </div>
          <div class="cat-grid">${catsHtml}</div>
        </div>
      `;
    }).join("");

    // Click handler en tarjetas de categoría
    dom.areasContainer.querySelectorAll(".cat-card").forEach(el => {
      el.addEventListener("click", () => {
        const catId = parseInt(el.dataset.catId, 10);
        showCategoryModal(catId);
      });
    });
  }

  function showCategoryModal(catId) {
    const cat = state.categories.find(c => c.id === catId);
    if (!cat) return;
    dom.dcZoom.textContent = `Zoom ${cat.zoom_level} · ${cat.zoom_label}`;
    dom.dcZoom.className = `zoom-pill zoom-${cat.zoom_level}`;
    dom.dcName.textContent = cat.name;
    dom.dcArea.textContent = `Área: ${cat.macro_area} (${cat.term_count || 0} términos en el glosario)`;
    dom.dcDesc.textContent = cat.description;
    dom.dlgCat.showModal();
  }

  // --- Vista 2: Compendio (Tabla) ---
  function populateCategoryFilter() {
    const current = dom.catFilter.value;
    dom.catFilter.innerHTML = '<option value="">Todas las categorías</option>' +
      state.categories.map(c => `<option value="${c.id}">${escapeHtml(c.name)} (${c.term_count || 0})</option>`).join("");
    dom.catFilter.value = current;
  }

  function renderTermsTable() {
    const query = normalizeText(dom.search.value.trim());
    const filterCatId = dom.catFilter.value ? parseInt(dom.catFilter.value, 10) : null;

    const filtered = state.terms.filter(t => {
      if (filterCatId && t.category_id !== filterCatId) return false;
      if (!query) return true;
      const haystack = normalizeText(`${t.term} ${t.category} ${t.technical_definition} ${t.daily_analogy} ${t.tradeoff_when_not_to_use || ""}`);
      return haystack.includes(query);
    });

    dom.resultCount.textContent = `Mostrando ${filtered.length} de ${state.terms.length} términos`;

    if (filtered.length === 0) {
      dom.termsBody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding: 24px; color: #64748b;">No se encontraron términos que coincidan con la búsqueda.</td></tr>`;
      return;
    }

    dom.termsBody.innerHTML = filtered.map(t => `
      <tr>
        <td class="cat">${escapeHtml(t.category)}</td>
        <td class="term">${escapeHtml(t.term)}</td>
        <td class="def">${escapeHtml(t.technical_definition)}</td>
        <td class="analog">${escapeHtml(t.daily_analogy)}</td>
        <td class="tradeoff">${escapeHtml(t.tradeoff_when_not_to_use || "—")}</td>
      </tr>
    `).join("");
  }

  dom.search.addEventListener("input", renderTermsTable);
  dom.catFilter.addEventListener("change", renderTermsTable);
  dom.btnPrint.addEventListener("click", () => window.print());

  // Agregar término rápido
  dom.btnAdd.addEventListener("click", () => {
    dom.addInput.value = "";
    dom.addStatus.className = "add-status";
    dom.addStatus.textContent = "";
    dom.addSubmit.disabled = false;
    dom.dlgAdd.showModal();
    dom.addInput.focus();
  });

  dom.addCancel.addEventListener("click", () => dom.dlgAdd.close());

  dom.formAdd.addEventListener("submit", async (e) => {
    e.preventDefault();
    const term = dom.addInput.value.trim();
    if (!term) return;

    dom.addSubmit.disabled = true;
    dom.addStatus.className = "add-status loading";
    dom.addStatus.textContent = "Verificando PIN de acceso...";

    const pin = await ensurePin();
    if (!pin) {
      dom.addStatus.className = "add-status error";
      dom.addStatus.textContent = "Operación cancelada: se requiere un PIN de acceso.";
      dom.addSubmit.disabled = false;
      return;
    }

    dom.addStatus.textContent = "Consultando a la IA y redactando ficha canónica...";

    try {
      const res = await fetchWithPin("/api/terms", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ term }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Error en el servidor");
      }

      dom.addStatus.className = "add-status success";
      if (data.created) {
        dom.addStatus.textContent = `¡Término "${data.card.term}" añadido con éxito!`;
        showToast(`Término "${data.card.term}" agregado al glosario.`);
      } else {
        dom.addStatus.textContent = `El término "${data.card.term}" ya existía en el glosario.`;
      }

      // Recargar datos en segundo plano
      await loadData();

      setTimeout(() => {
        dom.dlgAdd.close();
      }, 1200);
    } catch (err) {
      dom.addStatus.className = "add-status error";
      dom.addStatus.textContent = `Error: ${err.message}`;
      dom.addSubmit.disabled = false;
    }

  });

  // --- Vista 3: Flashcards SRS ---
  async function loadDueCards() {
    try {
      const res = await fetchWithPin("/api/review/due");
      if (!res.ok) return;
      const data = await res.json();
      state.dueCards = data.cards || [];
      state.currentDueIndex = 0;
      
      const count = data.count || state.dueCards.length;
      dom.dueNum.textContent = count;
      dom.dueLabel.textContent = count === 1 ? "término por repasar hoy" : "términos por repasar hoy";
      
      if (count > 0) {
        dom.dueBadge.textContent = count;
        dom.dueBadge.hidden = false;
        dom.trainEmpty.hidden = true;
        dom.trainArea.hidden = false;
        renderCurrentCard();
      } else {
        dom.dueBadge.hidden = true;
        dom.trainEmpty.hidden = false;
        dom.trainArea.hidden = true;
      }
    } catch (err) {
      console.error("Error cargando tarjetas due:", err);
    }
  }

  function renderCurrentCard() {
    const card = state.dueCards[state.currentDueIndex];
    if (!card) {
      loadDueCards();
      return;
    }

    state.isCardRevealed = false;
    dom.flashcard.classList.remove("revealed");
    dom.fcAnswer.hidden = true;
    dom.ratingPanel.hidden = true;
    dom.fcHint.hidden = false;

    dom.fcCategory.textContent = card.category;
    dom.fcTerm.textContent = card.term;
    dom.fcDef.textContent = card.technical_definition;
    dom.fcAnalog.textContent = card.daily_analogy;
    dom.fcTradeoff.textContent = card.tradeoff_when_not_to_use || "—";

    // Previsualización de intervalos en los botones
    if (card.preview) {
      dom.pv1.textContent = formatIntervalPreview(card.preview["1"]);
      dom.pv2.textContent = formatIntervalPreview(card.preview["2"]);
      dom.pv3.textContent = formatIntervalPreview(card.preview["3"]);
    } else {
      dom.pv1.textContent = "Repetir hoy";
      dom.pv2.textContent = "Corto plazo";
      dom.pv3.textContent = "Espaciar";
    }

    // Botón de ayuda de categoría
    dom.fcHelp.onclick = (e) => {
      e.stopPropagation();
      showCategoryModal(card.category_id);
    };
  }

  function formatIntervalPreview(days) {
    if (days === 0) return "Repetir hoy (0d)";
    if (days === 1) return "Mañana (1d)";
    return `En ${days} días`;
  }

  function revealCard() {
    if (state.isCardRevealed) return;
    state.isCardRevealed = true;
    dom.flashcard.classList.add("revealed");
    dom.fcAnswer.hidden = false;
    dom.fcHint.hidden = true;
    dom.ratingPanel.hidden = false;
  }

  dom.flashcard.addEventListener("click", () => {
    if (!state.isCardRevealed) {
      revealCard();
    }
  });

  async function submitRating(rating) {
    const card = state.dueCards[state.currentDueIndex];
    if (!card) return;

    try {
      const res = await fetchWithPin(`/api/review/${card.id}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ rating }),
      });

      if (!res.ok) {
        throw new Error("Error en la calificación");
      }

      const data = await res.json();
      showToast(`Guardado: ${rating === 1 ? "Olvidado" : rating === 2 ? "Dudé" : "Dominado"}`);
      
      // Si fue "Olvidado" (0 días), lo volvemos a encolar al final de la sesión de hoy
      if (rating === 1) {
        state.dueCards.push(card);
      }
      
      state.currentDueIndex++;
      if (state.currentDueIndex < state.dueCards.length) {
        renderCurrentCard();
      } else {
        await loadDueCards();
      }
      
      // Actualizar stats
      const statsRes = await fetchWithPin("/api/stats").then(r => r.json());
      state.stats = statsRes;
      updateStatsUI();
    } catch (err) {
      showToast("Error guardando calificación: " + err.message);
    }
  }


  dom.rateButtons.forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const rating = parseInt(btn.dataset.rating, 10);
      submitRating(rating);
    });
  });

  // Atajos de teclado para Flashcards
  window.addEventListener("keydown", (e) => {
    const activeView = document.querySelector(".view.active");
    if (!activeView || activeView.id !== "view-train") return;
    if (document.activeElement.tagName === "INPUT" || document.activeElement.tagName === "DIALOG") return;

    if (e.code === "Space" && !state.isCardRevealed) {
      e.preventDefault();
      revealCard();
    } else if (state.isCardRevealed) {
      if (e.key === "1") submitRating(1);
      if (e.key === "2") submitRating(2);
      if (e.key === "3") submitRating(3);
    }
  });

  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Service Worker para PWA
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(err => {
        console.warn("SW register fail:", err);
      });
    });
  }

  // Inicio
  loadData();
})();

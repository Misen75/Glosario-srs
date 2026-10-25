// Glosario SRS - Lógica cliente SPA / PWA (Versión 4.0 Canónica, 3D SRS, Command Palette & Grafo SVG)
(() => {
  "use strict";

  // Estado global
  const state = {
    areas: [],
    categories: [],
    terms: [],
    dueCards: [],
    currentDueIndex: 0,
    sessionHistory: [],
    isCardRevealed: false,
    revealStep: 0,
    stats: null,
    settings: {
      session_card_limit: 5,
      maturity_threshold_days: 21,
      initial_ease_factor: 2.5,
      focused_mode: true,
      theme: localStorage.getItem("glosario_srs_theme") || "dark",
      step_by_step_reveal: false,
      active_category_ids: [],
      excluded_term_ids: [],
    },
    users: [],
    bi: null,
    proposals: [],
    selectedArea: null,
    selectedZoom: null,
    selectedTermId: null,
    selectedCatId: null,
    collapsedCats: new Set(),
    // Command Palette state
    cmdFilter: "all",
    cmdActiveIndex: 0,
    cmdMatchedItems: [],
  };

  // Zoom helpers por defecto (disciplina computacional)
  const DEFAULT_ZOOM_LABELS = {
    1: "Micro · Código y Sintaxis",
    2: "Medio · Módulos y Componentes",
    3: "Macro · Arquitectura y Sistemas",
    4: "Infraestructura · Despliegue y Red",
  };

  // Elementos DOM
  const dom = {
    tabs: document.querySelectorAll(".tab"),
    views: document.querySelectorAll(".view"),
    dueBadge: document.getElementById("due-badge"),
    tabAdmin: document.getElementById("tab-admin"),
    btnHeaderAdd: document.getElementById("btn-header-add"),

    // Header 4.0 (Mastery Ring, Command Palette Trigger, Theme Toggle)
    headerMasteryPill: document.getElementById("header-mastery-pill"),
    headerRingFill: document.getElementById("header-ring-fill"),
    headerMasteryVal: document.getElementById("header-mastery-val"),
    btnCmdPalette: document.getElementById("btn-cmd-palette"),
    btnThemeToggle: document.getElementById("btn-theme-toggle"),
    themeToggleIco: document.getElementById("theme-toggle-ico"),

    // Explorador Taxonómico y Mazo (Master-Detail)
    explorerIntro: document.getElementById("explorer-intro"),
    deckSummaryLabel: document.getElementById("deck-summary-label"),
    btnCatsAll: document.getElementById("btn-cats-all"),
    btnSaveCats: document.getElementById("btn-save-cats"),
    areaFacets: document.getElementById("area-facets"),
    zoomLegend: document.getElementById("zoom-legend"),
    explorerSearch: document.getElementById("explorer-search"),
    areasContainer: document.getElementById("areas"),
    explorerDetail: document.getElementById("explorer-detail"),
    btnGotoExplorer: document.getElementById("btn-goto-explorer"),

    // Compendio (Tabla)
    search: document.getElementById("search"),
    catFilter: document.getElementById("cat-filter"),
    termsBody: document.getElementById("terms-body"),
    resultCount: document.getElementById("result-count"),
    btnAdd: document.getElementById("btn-add"),
    btnPrint: document.getElementById("btn-print"),

    // Entrenamiento (Flashcards SRS 3D + Session Timeline)
    dueNum: document.getElementById("due-num"),
    dueLabel: document.getElementById("due-label"),
    reviewedToday: document.getElementById("reviewed-today"),
    trainArea: document.getElementById("train-area"),
    trainEmpty: document.getElementById("train-empty"),
    sessionProgressWrap: document.getElementById("session-progress-wrap"),
    sessionProgressTitle: document.getElementById("session-progress-title"),
    sessionProgressStats: document.getElementById("session-progress-stats"),
    sessionProgressSegments: document.getElementById("session-progress-segments"),
    flashcard: document.getElementById("flashcard"),
    fcCategory: document.getElementById("fc-category"),
    fcZoomBadge: document.getElementById("fc-zoom-badge"),
    fcMaturityPill: document.getElementById("fc-maturity-pill"),
    fcHelp: document.getElementById("fc-help"),
    fcTerm: document.getElementById("fc-term"),
    fcFrontOntoPreview: document.getElementById("fc-front-onto-preview"),
    fcHint: document.getElementById("fc-hint"),
    fcBackCategory: document.getElementById("fc-back-category"),
    fcBackTerm: document.getElementById("fc-back-term"),
    fcFlipBack: document.getElementById("fc-flip-back"),
    fcAnswer: document.getElementById("fc-answer"),
    fcBlockDef: document.getElementById("fc-block-def"),
    fcBlockAnalog: document.getElementById("fc-block-analog"),
    fcBlockTradeoff: document.getElementById("fc-block-tradeoff"),
    fcBlockMeta: document.getElementById("fc-block-meta"),
    fcDef: document.getElementById("fc-def"),
    fcAnalog: document.getElementById("fc-analog"),
    fcTradeoff: document.getElementById("fc-tradeoff"),
    fcSource: document.getElementById("fc-source"),
    fcRelations: document.getElementById("fc-relations"),
    ratingPanel: document.getElementById("rating"),
    rateButtons: document.querySelectorAll(".rate"),
    pv1: document.getElementById("pv-1"),
    pv2: document.getElementById("pv-2"),
    pv3: document.getElementById("pv-3"),

    // Command Palette (Ctrl + K)
    dlgCmdPalette: document.getElementById("dlg-cmd-palette"),
    cmdSearchInput: document.getElementById("cmd-search-input"),
    cmdCloseBtn: document.getElementById("cmd-close-btn"),
    cmdFilterBar: document.getElementById("cmd-filter-bar"),
    cmdResultsList: document.getElementById("cmd-results-list"),

    // Configuración y Preferencias
    formSettings: document.getElementById("form-settings"),
    setSessionLimit: document.getElementById("set-session-limit"),
    setMaturityDays: document.getElementById("set-maturity-days"),
    setInitialEf: document.getElementById("set-initial-ef"),
    setFocusedMode: document.getElementById("set-focused-mode"),
    setTheme: document.getElementById("set-theme"),
    setStepReveal: document.getElementById("set-step-reveal"),
    btnSaveSettings: document.getElementById("btn-save-settings"),
    pinStatusLabel: document.getElementById("pin-status-label"),
    btnChangePin: document.getElementById("btn-change-pin"),
    btnLogoutPin: document.getElementById("btn-logout-pin"),
    studyCatsSelector: document.getElementById("study-categories-selector"),

    // Consola de Administración
    adminCountBadge: document.getElementById("admin-count-badge"),
    adminSearch: document.getElementById("admin-search"),
    adminCatFilter: document.getElementById("admin-cat-filter"),
    adminRecordsList: document.getElementById("admin-records-list"),
    adminUsersSection: document.getElementById("admin-users-section"),
    formAdminUser: document.getElementById("form-admin-user"),
    adminUsersList: document.getElementById("admin-users-list"),
    adminProposalsSection: document.getElementById("admin-proposals-section"),
    adminProposalsList: document.getElementById("admin-proposals-list"),
    btnRefreshProposals: document.getElementById("btn-refresh-proposals"),
    adminBiSection: document.getElementById("admin-bi-section"),
    btnRefreshBi: document.getElementById("btn-refresh-bi"),
    btnExportCsv: document.getElementById("btn-export-csv"),
    biDashboard: document.getElementById("bi-dashboard"),

    // Diálogos
    dlgCat: document.getElementById("dlg-category"),
    dcZoom: document.getElementById("dc-zoom"),
    dcName: document.getElementById("dc-name"),
    dcArea: document.getElementById("dc-area"),
    dcDesc: document.getElementById("dc-desc"),

    dlgAdd: document.getElementById("dlg-add"),
    formAdd: document.getElementById("form-add"),
    addInput: document.getElementById("add-input"),
    addScopeInput: document.getElementById("add-scope-input"),
    addStatus: document.getElementById("add-status"),
    addCancel: document.getElementById("add-cancel"),
    addSubmit: document.getElementById("add-submit"),

    dlgPropose: document.getElementById("dlg-propose"),
    formPropose: document.getElementById("form-propose"),
    propTermId: document.getElementById("prop-term-id"),
    propTermName: document.getElementById("prop-term-name"),
    propJustification: document.getElementById("prop-justification"),
    propSource: document.getElementById("prop-source"),
    propDef: document.getElementById("prop-def"),
    propAnalog: document.getElementById("prop-analog"),
    propLimits: document.getElementById("prop-limits"),
    propLimitsLabel: document.getElementById("prop-limits-label"),
    propStatus: document.getElementById("prop-status"),
    propCancel: document.getElementById("prop-cancel"),
    propSubmit: document.getElementById("prop-submit"),

    dlgPin: document.getElementById("dlg-pin"),
    formPin: document.getElementById("form-pin"),
    pinInput: document.getElementById("pin-input"),
    pinError: document.getElementById("pin-error"),
    pinCancel: document.getElementById("pin-cancel"),

    toast: document.getElementById("toast"),
  };

  // --- Manejo del PIN de autenticación local ---
  const PIN_KEY = "glosario_srs_web_pin";

  function getSavedPin() {
    return localStorage.getItem(PIN_KEY) || "";
  }

  function savePin(pin) {
    if (pin) {
      localStorage.setItem(PIN_KEY, pin);
    } else {
      localStorage.removeItem(PIN_KEY);
    }
  }

  function clearSavedPin() {
    localStorage.removeItem(PIN_KEY);
  }

  function requestPinModal(message = "") {
    return new Promise((resolve) => {
      if (!dom.dlgPin) {
        const fallback = prompt(message || "Ingresa tu PIN de acceso:");
        return resolve(fallback ? fallback.trim() : null);
      }
      dom.pinInput.value = "";
      dom.pinError.style.display = "none";
      dom.pinError.textContent = "";

      const cleanup = () => {
        dom.formPin.removeEventListener("submit", onSubmit);
        dom.pinCancel.removeEventListener("click", onCancel);
      };

      const onSubmit = async (e) => {
        e.preventDefault();
        const pin = dom.pinInput.value.trim();
        if (!pin) return;
        try {
          const res = await fetch("/api/auth/verify-pin", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pin }),
          });
          if (res.ok) {
            savePin(pin);
            dom.dlgPin.close();
            cleanup();
            resolve(pin);
          } else {
            const err = await res.json().catch(() => ({}));
            dom.pinError.textContent = err.detail || "PIN incorrecto.";
            dom.pinError.style.display = "block";
          }
        } catch {
          savePin(pin);
          dom.dlgPin.close();
          cleanup();
          resolve(pin);
        }
      };

      const onCancel = () => {
        dom.dlgPin.close();
        cleanup();
        resolve(null);
      };

      dom.formPin.addEventListener("submit", onSubmit);
      dom.pinCancel.addEventListener("click", onCancel);
      dom.dlgPin.showModal();
      dom.pinInput.focus();
    });
  }

  async function ensurePin() {
    let pin = getSavedPin();
    if (!pin) {
      pin = await requestPinModal("Se requiere un PIN de acceso:");
    }
    return pin;
  }

  async function fetchWithPin(url, options = {}) {
    const headers = new Headers(options.headers || {});
    const pin = getSavedPin();
    if (pin) {
      headers.set("X-Web-Pin", pin);
    }
    const res = await fetch(url, { ...options, headers });
    if (res.status === 401) {
      clearSavedPin();
      const newPin = await requestPinModal("Sesión expirada o PIN incorrecto. Ingresa tu PIN:");
      if (newPin) {
        const retryHeaders = new Headers(options.headers || {});
        retryHeaders.set("X-Web-Pin", newPin);
        return fetch(url, { ...options, headers: retryHeaders });
      }
    }
    return res;
  }

  async function fetchJsonWithPin(url, options = {}) {
    const res = await fetchWithPin(url, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Error HTTP ${res.status}`);
    }
    return data;
  }

  function showToast(message, durationMs = 3000) {
    if (!dom.toast) return;
    dom.toast.textContent = message;
    dom.toast.hidden = false;
    setTimeout(() => {
      dom.toast.hidden = true;
    }, durationMs);
  }

  function normalizeText(text) {
    return (text || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  // --- Tema Global (Dark / Light) ---
  function applyTheme(theme) {
    const mode = theme === "light" ? "light" : "dark";
    state.settings.theme = mode;
    document.documentElement.setAttribute("data-theme", mode);
    localStorage.setItem("glosario_srs_theme", mode);
    if (dom.themeToggleIco) {
      dom.themeToggleIco.textContent = mode === "dark" ? "☀️" : "🌙";
    }
    if (dom.setTheme) {
      dom.setTheme.value = mode;
    }
  }
  applyTheme(state.settings.theme);

  if (dom.btnThemeToggle) {
    dom.btnThemeToggle.addEventListener("click", async () => {
      const next = state.settings.theme === "dark" ? "light" : "dark";
      applyTheme(next);
      showToast(next === "dark" ? "🌙 Modo Oscuro (Obsidian) activado" : "☀️ Modo Claro activado", 2000);
      try {
        await fetchJsonWithPin("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ theme: next }),
        });
      } catch {
        // Guardado en localStorage de todos modos
      }
    });
  }

  function isUserAdmin() {
    if (state.stats && state.stats.current_user) {
      return Boolean(state.stats.current_user.is_admin);
    }
    return true; // Si no hay multi-tenant estricto configurado, opera en modo admin
  }

  function syncSettingsUI() {
    const s = state.settings;
    if (dom.setSessionLimit) dom.setSessionLimit.value = s.session_card_limit ?? 5;
    if (dom.setMaturityDays) dom.setMaturityDays.value = s.maturity_threshold_days ?? 21;
    if (dom.setInitialEf) dom.setInitialEf.value = s.initial_ease_factor ?? 2.5;
    if (dom.setFocusedMode) dom.setFocusedMode.value = String(s.focused_mode ?? true);
    if (dom.setTheme) dom.setTheme.value = s.theme || "dark";
    if (dom.setStepReveal) dom.setStepReveal.value = String(s.step_by_step_reveal ?? false);
    applyTheme(s.theme || "dark");
    if (dom.pinStatusLabel) {
      const hasPin = Boolean(getSavedPin());
      dom.pinStatusLabel.textContent = hasPin ? "✅ Configurado en este dispositivo" : "⚠️ No guardado";
      dom.pinStatusLabel.style.color = hasPin ? "#16a34a" : "#d97706";
    }
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
    } else if (state.terms.length === 0 || state.areas.length === 0) {
      loadData();
    } else if (viewName === "table") {
      renderTermsTable();
    } else if (viewName === "guide") {
      renderExplorer();
    } else if (viewName === "settings") {
      syncSettingsUI();
      renderStudyCategorySelector();
    } else if (viewName === "admin") {
      renderAdminRecords();
      loadAdminData();
    }
  }

  dom.tabs.forEach(tab => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });

  if (dom.btnGotoExplorer) {
    dom.btnGotoExplorer.addEventListener("click", () => switchView("guide"));
  }

  if (dom.headerMasteryPill) {
    dom.headerMasteryPill.style.cursor = "pointer";
    dom.headerMasteryPill.addEventListener("click", () => switchView("guide"));
  }

  // --- Carga de datos iniciales ---
  async function loadData() {
    try {
      const pin = await ensurePin();
      if (!pin) {
        showToast("Se requiere PIN para ver el glosario");
        return;
      }

      const statsRes = await fetchJsonWithPin("/api/stats");
      const [areasRes, termsRes] = await Promise.all([
        fetchJsonWithPin("/api/areas"),
        fetchJsonWithPin("/api/terms"),
      ]);
      state.areas = Array.isArray(areasRes) ? areasRes : [];
      state.terms = Array.isArray(termsRes) ? termsRes : [];
      state.stats = statsRes;
      if (statsRes && statsRes.settings) {
        const localTheme = localStorage.getItem("glosario_srs_theme");
        state.settings = {
          ...state.settings,
          ...statsRes.settings,
          theme: localTheme || statsRes.settings.theme || "dark",
        };
      }

      // Flatten categories for fast lookup
      state.categories = [];
      state.areas.forEach(a => {
        (a.categories || []).forEach(c => state.categories.push(c));
      });

      // Role check for Admin Tab
      if (dom.tabAdmin) {
        dom.tabAdmin.hidden = !isUserAdmin();
      }

      syncSettingsUI();
      renderExplorer();
      populateCategoryFilter();
      renderTermsTable();
      renderStudyCategorySelector();
      renderAdminRecords();
      updateStatsUI();
      await loadDueCards();
    } catch (err) {
      console.error("Error cargando datos:", err);
      showToast(err.message || "Error conectando con el servidor");
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
    const gm = state.stats.global_mastery;
    const gmPct = gm ? Math.min(100, Math.max(0, Number(gm.mastery_score || 0))) : 0;
    const gmText = gm ? ` · Maestría Global: ${gmPct.toFixed(1)}%` : "";
    dom.reviewedToday.textContent = `Repasados hoy: ${state.stats.reviewed_today}${gmText}`;

    // Header Mastery Ring SVG
    if (dom.headerRingFill) {
      dom.headerRingFill.setAttribute("stroke-dasharray", `${gmPct.toFixed(1)}, 100`);
    }
    if (dom.headerMasteryVal) {
      dom.headerMasteryVal.textContent = `${gmPct.toFixed(0)}%`;
    }
  }

  // =========================================================================
  // VISTA 1: EXPLORADOR TAXONÓMICO Y MAZO (MASTER-DETAIL) + GRAFO ONTOLÓGICO SVG
  // =========================================================================

  function getActiveZoomLabels() {
    if (state.selectedArea) {
      const areaObj = state.areas.find(a => a.name === state.selectedArea);
      if (areaObj && Array.isArray(areaObj.zoom_labels) && areaObj.zoom_labels.length === 4) {
        return {
          1: areaObj.zoom_labels[0],
          2: areaObj.zoom_labels[1],
          3: areaObj.zoom_labels[2],
          4: areaObj.zoom_labels[3],
        };
      }
    }
    return DEFAULT_ZOOM_LABELS;
  }

  function renderAreaFacets() {
    if (!dom.areaFacets) return;
    const allActive = !state.selectedArea;
    const pills = [
      `<button type="button" class="facet-pill ${allActive ? "active" : ""}" data-area="">🌐 Todas las Macro-Áreas</button>`,
      ...state.areas.map(a => {
        const active = state.selectedArea === a.name;
        return `<button type="button" class="facet-pill ${active ? "active" : ""}" data-area="${escapeHtml(a.name)}">${escapeHtml(a.name)}</button>`;
      })
    ];
    dom.areaFacets.innerHTML = pills.join("");

    dom.areaFacets.querySelectorAll(".facet-pill").forEach(btn => {
      btn.addEventListener("click", () => {
        const area = btn.dataset.area || null;
        state.selectedArea = area;
        renderAreaFacets();
        renderZoomLegend();
        renderExplorerTree();
      });
    });
  }

  function renderZoomLegend() {
    if (!dom.zoomLegend) return;
    const labels = getActiveZoomLabels();
    const allActive = state.selectedZoom === null;

    let html = `<button type="button" class="zoom-filter-btn ${allActive ? "active" : ""}" data-zoom="">Todos los Zooms</button>`;
    for (let lvl = 1; lvl <= 4; lvl++) {
      const active = state.selectedZoom === lvl;
      html += `<button type="button" class="zoom-filter-btn zoom-${lvl} ${active ? "active" : ""}" data-zoom="${lvl}">Zoom ${lvl}: ${escapeHtml(labels[lvl])}</button>`;
    }
    dom.zoomLegend.innerHTML = html;

    dom.zoomLegend.querySelectorAll(".zoom-filter-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const z = btn.dataset.zoom ? parseInt(btn.dataset.zoom, 10) : null;
        state.selectedZoom = z;
        renderZoomLegend();
        renderExplorerTree();
      });
    });
  }

  function updateDeckSummaryBar() {
    if (!dom.deckSummaryLabel) return;
    const activeCats = state.settings.active_category_ids || [];
    const excludedTerms = state.settings.excluded_term_ids || [];
    const totalCats = state.categories.length;

    let text = "";
    if (activeCats.length === 0 || activeCats.length === totalCats) {
      text = `Mazo activo: Todas las categorías (${totalCats})`;
    } else {
      text = `Mazo activo: ${activeCats.length} de ${totalCats} categorías seleccionadas`;
    }

    if (excludedTerms.length > 0) {
      text += ` · ⏸️ ${excludedTerms.length} término(s) excluidos`;
    }
    dom.deckSummaryLabel.textContent = text;
  }

  function renderMasteryBar(m) {
    if (!m || !m.total_terms) return "";
    const pct = Math.min(100, Math.max(0, Number(m.mastery_score || 0)));
    const barClass = m.is_canonically_mastered
      ? "mastery-fill-gold"
      : pct >= 70
      ? "mastery-fill-high"
      : pct > 0
      ? "mastery-fill-mid"
      : "mastery-fill-low";
    const dueChip = m.due_today > 0
      ? `<span class="mastery-due-pill">⏳ ${m.due_today} hoy</span>`
      : `<span class="mastery-clear-pill">✅ Al día</span>`;
    return `
      <div class="mastery-box">
        <div class="mastery-row">
          <span class="mastery-status">${escapeHtml(m.status_label || "")}</span>
          <span class="mastery-pct"><b>${pct.toFixed(0)}%</b> · ${m.mastered_count}/${m.total_terms} ≥21d ${dueChip}</span>
        </div>
        <div class="mastery-track" title="Maestría: ${pct.toFixed(1)}% (${m.mastered_count} de ${m.total_terms} términos con intervalo ≥ 21 días)">
          <div class="mastery-fill ${barClass}" style="width: ${pct}%;"></div>
        </div>
      </div>
    `;
  }

  function renderExplorer() {
    renderAreaFacets();
    renderZoomLegend();
    renderExplorerTree();
    updateDeckSummaryBar();
  }

  function renderExplorerTree() {
    if (!dom.areasContainer) return;
    const query = normalizeText(dom.explorerSearch ? dom.explorerSearch.value.trim() : "");
    const activeCatIds = state.settings.active_category_ids || [];
    const excludedTermIds = new Set(state.settings.excluded_term_ids || []);
    const allCatsActive = activeCatIds.length === 0;

    const filteredAreas = state.areas.filter(a => {
      if (state.selectedArea && a.name !== state.selectedArea) return false;
      return true;
    });

    let renderedAreasCount = 0;
    const areasHtml = filteredAreas.map(area => {
      const matchingCats = (area.categories || []).filter(c => {
        if (state.selectedZoom !== null && c.zoom_level !== state.selectedZoom) return false;
        if (!query) return true;
        const nameMatch = normalizeText(c.name).includes(query);
        const termsMatch = state.terms.some(t => t.category_id === c.id && (
          normalizeText(t.term).includes(query) ||
          normalizeText(t.technical_definition).includes(query)
        ));
        return nameMatch || termsMatch;
      });

      if (matchingCats.length === 0) return "";
      renderedAreasCount++;

      const catsHtml = matchingCats.map(c => {
        const isCatActive = allCatsActive || activeCatIds.includes(c.id);
        const isCatSelected = state.selectedCatId === c.id && state.selectedTermId === null;
        const isCollapsed = state.collapsedCats.has(c.id);

        const catTerms = state.terms.filter(t => t.category_id === c.id);
        const matchingTerms = catTerms.filter(t => {
          if (!query) return true;
          return normalizeText(`${t.term} ${t.technical_definition}`).includes(query);
        });

        const termsHtml = (!isCollapsed && matchingTerms.length > 0) ? `
          <div class="tree-terms-list">
            ${matchingTerms.map(t => {
              const isExcluded = excludedTermIds.has(t.id);
              const isSelected = state.selectedTermId === t.id;
              const srs = t.srs || {};
              const intervalText = srs.interval_days ? `${srs.interval_days}d` : "nuevo";
              const customBadge = t.is_customized ? `<span title="Personalizado en tu cuaderno (CoW)">✨</span>` : "";

              return `
                <div class="tree-term-row ${isSelected ? "selected" : ""} ${isExcluded ? "excluded" : ""}" data-term-id="${t.id}">
                  <label class="tree-check-wrap" title="${isExcluded ? "Excluido de tu mazo SRS" : "Incluido en tu mazo SRS"}">
                    <input type="checkbox" class="chk-term-include" data-term-id="${t.id}" ${isExcluded ? "" : "checked"}>
                  </label>
                  <button type="button" class="tree-term-btn" data-term-id="${t.id}">
                    <span class="tree-term-name">${customBadge} ${escapeHtml(t.term)}</span>
                    <span class="tree-term-meta">${intervalText}</span>
                  </button>
                </div>
              `;
            }).join("")}
          </div>
        ` : "";

        return `
          <div class="tree-cat-node" data-cat-id="${c.id}">
            <div class="tree-cat-header ${isCatSelected ? "selected" : ""}">
              <button type="button" class="tree-cat-toggle" data-cat-toggle="${c.id}" title="${isCollapsed ? "Desplegar términos" : "Plegar términos"}">
                <span class="tree-chevron">${isCollapsed ? "▶" : "▼"}</span>
              </button>
              <label class="tree-check-wrap" title="${isCatActive ? "Categoría activa en tu mazo" : "Categoría inactiva"}">
                <input type="checkbox" class="chk-cat-include" data-cat-id="${c.id}" ${isCatActive ? "checked" : ""}>
              </label>
              <button type="button" class="tree-cat-main" data-cat-select="${c.id}">
                <div class="tree-cat-top">
                  <span class="zoom-badge zoom-${c.zoom_level}">Z${c.zoom_level}</span>
                  <span class="tree-cat-title">${escapeHtml(c.name)}</span>
                </div>
                <div class="tree-cat-sub">
                  <span>${c.term_count || 0} térm.</span>
                  ${c.mastery ? `<span>· ${c.mastery.mastery_score.toFixed(0)}%</span>` : ""}
                </div>
              </button>
            </div>
            ${termsHtml}
          </div>
        `;
      }).join("");

      return `
        <div class="tree-area-group">
          <div class="tree-area-header">
            <span class="tree-area-name">📍 ${escapeHtml(area.name)}</span>
            <span class="tree-area-count">${matchingCats.length} cat.</span>
          </div>
          <div class="tree-cats-list">${catsHtml}</div>
        </div>
      `;
    }).join("");

    if (renderedAreasCount === 0) {
      dom.areasContainer.innerHTML = `<div class="admin-empty">No se encontraron ramas o términos que coincidan con los filtros.</div>`;
    } else {
      dom.areasContainer.innerHTML = areasHtml;
    }

    // Attach tree event listeners
    dom.areasContainer.querySelectorAll("button[data-cat-toggle]").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const catId = parseInt(btn.dataset.catToggle, 10);
        if (state.collapsedCats.has(catId)) {
          state.collapsedCats.delete(catId);
        } else {
          state.collapsedCats.add(catId);
        }
        renderExplorerTree();
      });
    });

    dom.areasContainer.querySelectorAll("button[data-cat-select]").forEach(btn => {
      btn.addEventListener("click", () => {
        const catId = parseInt(btn.dataset.catSelect, 10);
        state.selectedCatId = catId;
        state.selectedTermId = null;
        renderExplorerTree();
        const catObj = state.categories.find(c => c.id === catId);
        if (catObj) renderCatDetailPane(catObj);
      });
    });

    dom.areasContainer.querySelectorAll(".chk-cat-include").forEach(chk => {
      chk.addEventListener("change", (e) => {
        e.stopPropagation();
        const catId = parseInt(chk.dataset.catId, 10);
        toggleCategoryInDeck(catId, chk.checked);
      });
    });

    dom.areasContainer.querySelectorAll("button[data-term-id]").forEach(btn => {
      btn.addEventListener("click", () => {
        const termId = parseInt(btn.dataset.termId, 10);
        state.selectedTermId = termId;
        renderExplorerTree();
        const termObj = state.terms.find(t => t.id === termId);
        if (termObj) renderDetailPane(termObj);
      });
    });

    dom.areasContainer.querySelectorAll(".chk-term-include").forEach(chk => {
      chk.addEventListener("change", (e) => {
        e.stopPropagation();
        const termId = parseInt(chk.dataset.termId, 10);
        toggleTermExclusion(termId, !chk.checked);
      });
    });
  }

  function toggleCategoryInDeck(catId, included) {
    let active = [...(state.settings.active_category_ids || [])];
    const totalCats = state.categories.map(c => c.id);

    if (active.length === 0) {
      active = [...totalCats];
    }

    if (included) {
      if (!active.includes(catId)) active.push(catId);
    } else {
      active = active.filter(id => id !== catId);
    }

    if (active.length === totalCats.length) {
      active = [];
    }

    state.settings.active_category_ids = active;
    updateDeckSummaryBar();
  }

  function toggleTermExclusion(termId, excluded) {
    let excl = [...(state.settings.excluded_term_ids || [])];
    if (excluded) {
      if (!excl.includes(termId)) excl.push(termId);
    } else {
      excl = excl.filter(id => id !== termId);
    }
    state.settings.excluded_term_ids = excl;
    updateDeckSummaryBar();
  }

  async function persistDeckSelection(toastMsg = "🎯 Mazo de estudio actualizado") {
    try {
      const payload = {
        active_category_ids: state.settings.active_category_ids,
        excluded_term_ids: state.settings.excluded_term_ids,
      };
      await fetchJsonWithPin("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      showToast(toastMsg);
    } catch (err) {
      showToast("Error guardando mazo: " + err.message);
    }
  }

  // --- Visor de Detalle (Categoría) ---
  function renderCatDetailPane(cat) {
    if (!dom.explorerDetail) return;
    const catTerms = state.terms.filter(t => t.category_id === cat.id);
    const m = cat.mastery || {};
    const excludedTermIds = new Set(state.settings.excluded_term_ids || []);

    const termsCards = catTerms.map(t => {
      const isExcl = excludedTermIds.has(t.id);
      const srs = t.srs || {};
      return `
        <div class="detail-mini-term ${isExcl ? "excluded" : ""}" data-jump-term="${t.id}">
          <div class="detail-mini-top">
            <b>${escapeHtml(t.term)}</b>
            <span class="detail-mini-status">${srs.interval_days ? `${srs.interval_days}d` : "Nuevo"}</span>
          </div>
          <p>${escapeHtml((t.technical_definition || "").substring(0, 90))}…</p>
        </div>
      `;
    }).join("");

    dom.explorerDetail.innerHTML = `
      <article class="detail-card">
        <div class="detail-header">
          <div>
            <div class="detail-Swap-badges">
              <span class="chip">📍 ${escapeHtml(cat.macro_area)}</span>
              <span class="zoom-badge zoom-${cat.zoom_level}">Zoom ${cat.zoom_level} · ${escapeHtml(cat.zoom_label || `Zoom ${cat.zoom_level}`)}</span>
              <span class="chip">${catTerms.length} Términos</span>
            </div>
            <h2 class="detail-title">${escapeHtml(cat.name)}</h2>
          </div>
        </div>

        <p class="detail-cat-desc">${escapeHtml(cat.description)}</p>
        ${cat.simple_explanation ? `<div class="fc-block analog" style="margin-top:6px;"><p>💡 <b>Enfoque didáctico:</b> ${escapeHtml(cat.simple_explanation)}</p></div>` : ""}

        ${renderMasteryBar(m)}

        <div style="margin-top: 14px;">
          <h4 class="detail-subhead">Términos en esta Categoría (Toca uno para inspeccionar su Ficha Canónica):</h4>
          <div class="detail-cat-terms-grid" style="margin-top: 8px;">
            ${termsCards || '<p class="admin-empty">No hay términos registrados en esta categoría.</p>'}
          </div>
        </div>
      </article>
    `;

    dom.explorerDetail.querySelectorAll("[data-jump-term]").forEach(el => {
      el.addEventListener("click", () => {
        const tid = parseInt(el.dataset.jumpTerm, 10);
        state.selectedTermId = tid;
        renderExplorerTree();
        const t = state.terms.find(item => item.id === tid);
        if (t) renderDetailPane(t);
      });
    });
  }

  // --- Generador de Grafo Ontológico Interactivo en SVG (Fase 4) ---
  function truncateSvgText(str, maxLen = 22) {
    if (!str) return "";
    return str.length > maxLen ? str.substring(0, maxLen - 1) + "…" : str;
  }

  function findTermInCatalog(name) {
    if (!name) return null;
    const norm = normalizeText(name.trim());
    return state.terms.find(t => normalizeText(t.term) === norm) || null;
  }

  function buildOntoGraphSvg(term) {
    const parentName = (term.parent_term || "").trim();
    const rels = Array.isArray(term.related_terms)
      ? term.related_terms.filter(Boolean).slice(0, 5)
      : [];
    const scopes = Array.isArray(term.other_scopes) ? term.other_scopes.filter(Boolean) : [];

    if (!parentName && rels.length === 0 && scopes.length === 0) {
      return "";
    }

    const svgW = 680;
    const svgH = rels.length > 0 ? 255 : 175;
    const centerX = svgW / 2;
    const parentY = 40;
    const currentY = parentName ? 124 : 70;
    const childY = 212;

    let edgesSvg = "";
    let nodesSvg = "";

    // 1. Nodo Supraordinado (Padre)
    if (parentName) {
      const parentFound = findTermInCatalog(parentName);
      const pAttr = parentFound
        ? `data-onto-jump="${escapeHtml(parentName)}"`
        : `data-onto-capture="${escapeHtml(parentName)}"`;
      const pW = Math.max(150, Math.min(250, parentName.length * 8 + 36));
      const pX = centerX - pW / 2;

      edgesSvg += `
        <path class="onto-edge-path parent-edge" d="M ${centerX} ${parentY + 20} C ${centerX} ${(parentY + currentY) / 2}, ${centerX} ${(parentY + currentY) / 2}, ${centerX} ${currentY - 22}" />
      `;

      nodesSvg += `
        <g class="onto-svg-node node-parent" transform="translate(${pX}, ${parentY - 20})" ${pAttr}>
          <rect width="${pW}" height="40" />
          <text class="node-role" x="${pW / 2}" y="14" text-anchor="middle">▲ SUPRAORDINADO ${parentFound ? "· 🔗" : "· + CAPTURAR"}</text>
          <text x="${pW / 2}" y="30" text-anchor="middle">${escapeHtml(truncateSvgText(parentName, 28))}</text>
        </g>
      `;
    }

    // 2. Nodo Central (Concepto Actual)
    const curW = Math.max(175, Math.min(270, term.term.length * 8.5 + 42));
    const curX = centerX - curW / 2;
    nodesSvg += `
      <g class="onto-svg-node node-current" transform="translate(${curX}, ${currentY - 22})">
        <rect width="${curW}" height="44" />
        <text class="node-role" x="${curW / 2}" y="15" text-anchor="middle">★ CONCEPTO ACTUAL (ZOOM ${term.zoom_level || 1})</text>
        <text x="${curW / 2}" y="32" text-anchor="middle">${escapeHtml(truncateSvgText(term.term, 28))}</text>
      </g>
    `;

    // 3. Nodos Coordinados / Relacionados
    if (rels.length > 0) {
      const count = rels.length;
      const slotW = svgW / count;

      rels.forEach((rName, idx) => {
        const cx = slotW * idx + slotW / 2;
        const found = findTermInCatalog(rName);
        const nodeClass = found ? "node-related-linked" : "node-related-unlinked";
        const edgeClass = found ? "active-edge" : "";
        const actionAttr = found
          ? `data-onto-jump="${escapeHtml(rName)}"`
          : `data-onto-capture="${escapeHtml(rName)}"`;
        const roleLabel = found ? "🔗 EN CATÁLOGO" : "+ CAPTURAR CON IA";

        const nW = Math.max(116, Math.min(slotW - 12, 156));
        const nX = cx - nW / 2;
        const midY = (currentY + 22 + childY - 20) / 2;

        edgesSvg += `
          <path class="onto-edge-path ${edgeClass}" d="M ${centerX} ${currentY + 22} C ${centerX} ${midY}, ${cx} ${midY}, ${cx} ${childY - 20}" />
        `;

        nodesSvg += `
          <g class="onto-svg-node ${nodeClass}" transform="translate(${nX}, ${childY - 20})" ${actionAttr}>
            <rect width="${nW}" height="40" />
            <text class="node-role" x="${nW / 2}" y="14" text-anchor="middle">${roleLabel}</text>
            <text x="${nW / 2}" y="29" text-anchor="middle">${escapeHtml(truncateSvgText(rName, 18))}</text>
          </g>
        `;
      });
    }

    const scopesHtml = scopes.length > 0 ? `
      <div class="onto-pills-footer">
        <span class="onto-role-label">🌐 Polisemia (Otros Ámbitos):</span>
        ${scopes.map(s => `<span class="chip" style="font-size:11px;">${escapeHtml(s)}</span>`).join("")}
      </div>
    ` : "";

    return `
      <div class="onto-tree-box">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:10px;">
          <h3 style="margin:0;">🕸️ Topología del Conocimiento (Grafo Ontológico Interactivo)</h3>
          <span style="font-size:11px; color:var(--text-muted);">Toca un nodo verde para saltar o uno punteado para capturarlo</span>
        </div>
        <div class="onto-graph-canvas">
          <svg class="onto-svg-stage" viewBox="0 0 ${svgW} ${svgH}" role="img" aria-label="Grafo ontológico de ${escapeHtml(term.term)}">
            ${edgesSvg}
            ${nodesSvg}
          </svg>
        </div>
        ${scopesHtml}
      </div>
    `;
  }

  // --- Visor de Detalle (Término Canónico y Cuaderno Personal CoW) ---
  function renderDetailPane(term) {
    if (!dom.explorerDetail) return;
    const cat = state.categories.find(c => c.id === term.category_id) || {
      name: term.category,
      macro_area: term.macro_area,
      zoom_level: term.zoom_level || 1,
      zoom_label: `Zoom ${term.zoom_level || 1}`,
    };

    const srs = term.srs || {};
    const excludedSet = new Set(state.settings.excluded_term_ids || []);
    const isExcluded = excludedSet.has(term.id);

    // Dynamic adaptive limits
    const effLimits = term.effective_limits ?? term.tradeoff_when_not_to_use;
    const hasLimits = Boolean(effLimits && effLimits.trim() && effLimits.trim() !== "—" && effLimits.trim() !== "-");
    const limitsLabel = term.limits_label || "Compensaciones y Anti-patrones (Trade-offs)";

    // Primary Source
    const canonicalSource = term.canonical_source || "Sin fuente primaria formal registrada en el catálogo";

    // Personal Notebook
    const custom = term.customization || {};
    const isCustomized = Boolean(term.is_customized);

    const tradeoffBlockHtml = hasLimits ? `
      <div class="fc-block tradeoff">
        <h3>⚠️ ${escapeHtml(limitsLabel)}</h3>
        <p>${escapeHtml(effLimits)}</p>
      </div>
    ` : "";

    const ontoBlockHtml = buildOntoGraphSvg(term);

    const notebookHtml = `
      <div class="personal-notebook-box">
        <div class="notebook-header">
          <div>
            <div style="display:flex; align-items:center; gap:8px;">
              <h3>📓 Mi Cuaderno de Estudio (Personalización Privada)</h3>
              ${isCustomized ? '<span class="chip-customized">✨ Personalizado (CoW)</span>' : ''}
            </div>
            <p class="notebook-sub">Guarda tu propia analogía o notas personales sin alterar el Catálogo Canónico Global.</p>
          </div>
          ${isCustomized ? '<button type="button" class="btn btn-sm" id="btn-revert-custom" title="Restaurar ficha al estándar canónico">↺ Revertir a Canónico</button>' : ''}
        </div>
        <div class="notebook-form">
          <div class="dlg-field">
            <label for="custom-analogy-input">Mi Analogía Personal</label>
            <textarea id="custom-analogy-input" rows="2" placeholder="Escribe tu propia analogía o metáfora cotidiana mnemotécnica...">${escapeHtml(custom.custom_analogy || "")}</textarea>
          </div>
          <div class="dlg-field" style="margin-top: 6px;">
            <label for="custom-notes-input">Mis Apuntes de Estudio</label>
            <textarea id="custom-notes-input" rows="2" placeholder="Notas sobre proyectos, dudas resueltas o casos particulares...">${escapeHtml(custom.personal_notes || "")}</textarea>
          </div>
          <div class="notebook-actions">
            <button type="button" class="btn btn-sm btn-primary" id="btn-save-custom">💾 Guardar mi versión</button>
            <button type="button" class="btn btn-sm" id="btn-open-proposal" title="Si consideras que la versión canónica tiene un error, somete una propuesta con fuentes primarias">⚖️ Proponer mejora al Catálogo Canónico</button>
          </div>
        </div>
      </div>
    `;

    dom.explorerDetail.innerHTML = `
      <article class="detail-card">
        <div class="detail-header">
          <div>
            <div class="detail-Swap-badges">
              <span class="chip">📍 ${escapeHtml(term.macro_area)}</span>
              <span class="chip">${escapeHtml(cat.name)}</span>
              <span class="zoom-badge zoom-${cat.zoom_level}">Zoom ${cat.zoom_level}</span>
            </div>
            <h2 class="detail-title">${escapeHtml(term.term)}</h2>
          </div>
          <div>
            <button type="button" class="btn btn-sm ${isExcluded ? "btn-primary" : ""}" id="btn-toggle-term-deck">
              ${isExcluded ? "➕ Incluir en mi Mazo" : "⏸️ Excluir de mi Mazo"}
            </button>
          </div>
        </div>

        <div class="detail-srs-strip">
          <span><b>Intervalo:</b> ${srs.interval_days || 0} días</span>
          <span><b>Facilidad (EF):</b> ${(srs.ease_factor || 2.5).toFixed(2)}</span>
          <span><b>Repasos:</b> ${srs.repetitions || 0}</span>
          <span><b>Madurez:</b> ${srs.maturity_score ?? 0}% (${srs.maturity_stage || "En Aprendizaje"})</span>
          <span><b>Estado:</b> ${isExcluded ? "⏸️ Excluido" : (srs.due ? "⏳ Repaso Pendiente Hoy" : "✅ Al día")}</span>
        </div>

        <div class="fc-block" style="margin-top: 4px;">
          <h3>Definición Técnica Canónica</h3>
          <p>${escapeHtml(term.technical_definition)}</p>
        </div>

        <div class="fc-block analog">
          <h3>💡 Analogía Didáctica Canónica</h3>
          <p>${escapeHtml(term.daily_analogy)}</p>
        </div>

        ${tradeoffBlockHtml}

        <div class="fc-block source-block">
          <h3>📚 Fuente Primaria Canónica (Fuente Cero)</h3>
          <p class="canonical-source-text">${escapeHtml(canonicalSource)}</p>
        </div>

        ${ontoBlockHtml}

        ${notebookHtml}
      </article>
    `;

    // Button: Toggle inclusion in Deck
    const deckToggleBtn = document.getElementById("btn-toggle-term-deck");
    if (deckToggleBtn) {
      deckToggleBtn.addEventListener("click", async () => {
        const included = !isExcluded;
        toggleTermExclusion(term.id, included);
        renderExplorerTree();
        renderDetailPane(term);
        await persistDeckSelection(included ? `⏸️ "${term.term}" excluido de tu mazo` : `✅ "${term.term}" incluido en tu mazo`);
      });
    }

    // Button: Save Personal Customization (CoW)
    const saveCustomBtn = document.getElementById("btn-save-custom");
    if (saveCustomBtn) {
      saveCustomBtn.addEventListener("click", async () => {
        const ana = (document.getElementById("custom-analogy-input")?.value || "").trim();
        const notes = (document.getElementById("custom-notes-input")?.value || "").trim();
        try {
          saveCustomBtn.disabled = true;
          saveCustomBtn.textContent = "Guardando...";
          await fetchJsonWithPin(`/api/terms/${term.id}/customization`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              custom_analogy: ana,
              personal_notes: notes,
            }),
          });
          term.is_customized = Boolean(ana || notes);
          term.effective_analogy = ana || term.daily_analogy;
          term.personal_notes = notes;
          if (!term.customization) term.customization = {};
          term.customization.custom_analogy = ana;
          term.customization.personal_notes = notes;
          showToast("💾 Notas personales guardadas (0ms IA)");
          renderExplorerTree();
          renderDetailPane(term);
        } catch (err) {
          showToast("Error guardando personalización: " + err.message);
        } finally {
          if (saveCustomBtn) {
            saveCustomBtn.disabled = false;
            saveCustomBtn.textContent = "💾 Guardar mi versión";
          }
        }
      });
    }

    // Button: Revert to Canonical
    const revertCustomBtn = document.getElementById("btn-revert-custom");
    if (revertCustomBtn) {
      revertCustomBtn.addEventListener("click", async () => {
        if (!window.confirm("¿Restaurar esta ficha al contenido canónico global eliminando tus notas privadas?")) return;
        try {
          await fetchJsonWithPin(`/api/terms/${term.id}/customization`, { method: "DELETE" });
          term.is_customized = false;
          term.effective_analogy = term.daily_analogy;
          term.personal_notes = null;
          term.customization = null;
          showToast("↺ Ficha restaurada a la versión canónica");
          renderExplorerTree();
          renderDetailPane(term);
        } catch (err) {
          showToast("Error restaurando: " + err.message);
        }
      });
    }

    // Button: Open Canonical Proposal Dialog
    const openPropBtn = document.getElementById("btn-open-proposal");
    if (openPropBtn) {
      openPropBtn.addEventListener("click", () => {
        openProposalDialog(term);
      });
    }

    // SVG Ontological Graph: Jump to existing linked term
    dom.explorerDetail.querySelectorAll("[data-onto-jump]").forEach(node => {
      node.addEventListener("click", () => {
        const targetName = node.getAttribute("data-onto-jump") || "";
        const found = findTermInCatalog(targetName);
        if (found) {
          state.selectedTermId = found.id;
          renderExplorerTree();
          renderDetailPane(found);
          showToast(`🔗 Navegando a "${found.term}"`);
        }
      });
    });

    // SVG Ontological Graph: Capture unlinked term with AI
    dom.explorerDetail.querySelectorAll("[data-onto-capture]").forEach(node => {
      node.addEventListener("click", () => {
        const targetName = node.getAttribute("data-onto-capture") || "";
        openAddTermModal(targetName, term.macro_area || "");
      });
    });
  }

  // --- Botones de Mazo en Explorador ---
  if (dom.btnCatsAll) {
    dom.btnCatsAll.addEventListener("click", () => {
      state.settings.active_category_ids = [];
      state.settings.excluded_term_ids = [];
      renderExplorerTree();
      updateDeckSummaryBar();
      showToast("🌲 Todas las Macro-Áreas y Categorías marcadas activas");
    });
  }

  if (dom.btnSaveCats) {
    dom.btnSaveCats.addEventListener("click", async () => {
      dom.btnSaveCats.disabled = true;
      try {
        await persistDeckSelection("💾 Mazo de Estudio guardado y sincronizado");
        await loadData();
      } finally {
        dom.btnSaveCats.disabled = false;
      }
    });
  }

  if (dom.explorerSearch) {
    dom.explorerSearch.addEventListener("input", () => {
      renderExplorerTree();
    });
  }

  // =========================================================================
  // VISTA 2: COMPENDIO TABULAR
  // =========================================================================

  function populateCategoryFilter() {
    const optionsHtml = '<option value="">Todas las categorías</option>' +
      state.categories.map(c => `<option value="${c.id}">${escapeHtml(c.name)} (${c.term_count || 0})</option>`).join("");
    const current = dom.catFilter.value;
    dom.catFilter.innerHTML = optionsHtml;
    dom.catFilter.value = current;

    if (dom.adminCatFilter) {
      const adminCurrent = dom.adminCatFilter.value;
      dom.adminCatFilter.innerHTML = optionsHtml;
      dom.adminCatFilter.value = adminCurrent;
    }
  }

  function renderTermsTable() {
    const query = normalizeText(dom.search ? dom.search.value.trim() : "");
    const filterCatId = dom.catFilter.value ? parseInt(dom.catFilter.value, 10) : null;

    const filtered = state.terms.filter(t => {
      if (filterCatId && t.category_id !== filterCatId) return false;
      if (!query) return true;
      const scopesText = Array.isArray(t.other_scopes) ? t.other_scopes.join(" ") : "";
      const limitsText = t.effective_limits || t.tradeoff_when_not_to_use || "";
      const haystack = normalizeText(`${t.term} ${t.category} ${t.technical_definition} ${t.daily_analogy} ${limitsText} ${t.canonical_source || ""} ${scopesText}`);
      return haystack.includes(query);
    });

    if (dom.resultCount) {
      dom.resultCount.textContent = `Mostrando ${filtered.length} de ${state.terms.length} términos`;
    }

    if (filtered.length === 0) {
      dom.termsBody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding: 24px; color: var(--text-muted);">No se encontraron términos que coincidan con la búsqueda.</td></tr>`;
      return;
    }

    dom.termsBody.innerHTML = filtered.map(t => {
      const scopes = Array.isArray(t.other_scopes) && t.other_scopes.length > 0
        ? `<div style="margin-top:6px; font-weight:normal; font-size:10.5px; color:var(--text-muted);">🌐 <i>Otros ámbitos:</i> ${t.other_scopes.map(s => escapeHtml(s)).join(" · ")}</div>`
        : "";
      const sourceHtml = t.canonical_source
        ? `<div style="margin-top:4px; font-size:11px; color:var(--primary);">📚 <i>Fuente: ${escapeHtml(t.canonical_source)}</i></div>`
        : "";
      const customChip = t.is_customized ? `<span class="chip-customized" style="margin-left:4px;">✨ CoW</span>` : "";

      return `
      <tr>
        <td class="cat">${escapeHtml(t.category)}</td>
        <td class="term">${escapeHtml(t.term)}${customChip}${scopes}</td>
        <td class="def">${escapeHtml(t.effective_definition || t.technical_definition)}${sourceHtml}</td>
        <td class="analog">${escapeHtml(t.effective_analogy || t.daily_analogy)}</td>
        <td class="tradeoff">${escapeHtml(t.effective_limits || t.tradeoff_when_not_to_use || "—")}</td>
      </tr>
    `;
    }).join("");
  }

  if (dom.search) dom.search.addEventListener("input", renderTermsTable);
  if (dom.catFilter) dom.catFilter.addEventListener("change", renderTermsTable);
  if (dom.btnPrint) dom.btnPrint.addEventListener("click", () => window.print());

  // Agregar término rápido (permite pre-llenar desde el Grafo SVG o Command Palette)
  function openAddTermModal(prefillTerm = "", prefillScope = "") {
    dom.addInput.value = typeof prefillTerm === "string" ? prefillTerm : "";
    if (dom.addScopeInput) {
      dom.addScopeInput.value = typeof prefillScope === "string" ? prefillScope : "";
    }
    dom.addStatus.className = "add-status";
    dom.addStatus.textContent = "";
    dom.addSubmit.disabled = false;
    dom.dlgAdd.showModal();
    dom.addInput.focus();
  }

  if (dom.btnAdd) dom.btnAdd.addEventListener("click", () => openAddTermModal());
  if (dom.btnHeaderAdd) dom.btnHeaderAdd.addEventListener("click", () => openAddTermModal());
  if (dom.addCancel) dom.addCancel.addEventListener("click", () => dom.dlgAdd.close());

  dom.formAdd.addEventListener("submit", async (e) => {
    e.preventDefault();
    const term = dom.addInput.value.trim();
    const scope = dom.addScopeInput ? dom.addScopeInput.value.trim() : "";
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

    dom.addStatus.textContent = "Consultando fuentes primarias y redactando ficha canónica...";

    try {
      const res = await fetchWithPin("/api/terms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ term, scope: scope || null }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Error en el servidor");
      }

      dom.addStatus.className = "add-status success";
      const createdCount = Array.isArray(data.created_cards) ? data.created_cards.length : (data.created ? 1 : 0);
      if (data.created) {
        dom.addStatus.textContent = createdCount > 1
          ? `¡Término "${data.card.term}" añadido automáticamente en ${createdCount} ámbitos!`
          : `¡Término "${data.card.term}" añadido con éxito!`;
        showToast(`"${data.card.term}" registrado con respaldo en Fuentes Primarias.`);
      } else if (data.from_canonical_cache) {
        dom.addStatus.textContent = `⚡ "${data.card.term}" asignado desde el Catálogo Canónico ($0 IA).`;
        showToast(`"${data.card.term}" asignado desde el catálogo global.`);
      } else {
        dom.addStatus.textContent = `El término "${data.card.term}" ya existía en el glosario.`;
      }

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

  // =========================================================================
  // DIÁLOGO: PROPONER MEJORA AL CATÁLOGO CANÓNICO (ARBITRAJE)
  // =========================================================================

  function openProposalDialog(term) {
    if (!dom.dlgPropose) return;
    dom.propTermId.value = term.id;
    dom.propTermName.value = `${term.term} (${term.macro_area} › ${term.category})`;
    dom.propJustification.value = "";
    dom.propSource.value = term.canonical_source || "";
    dom.propDef.value = term.technical_definition || "";
    dom.propAnalog.value = term.daily_analogy || "";
    const effLim = term.effective_limits ?? term.tradeoff_when_not_to_use ?? "";
    dom.propLimits.value = (effLim === "—" || effLim === "-") ? "" : effLim;
    if (dom.propLimitsLabel) {
      dom.propLimitsLabel.textContent = `${term.limits_label || "Límites / Trade-offs"} Propuestos (Opcional)`;
    }
    if (dom.propStatus) {
      dom.propStatus.style.display = "none";
      dom.propStatus.textContent = "";
    }
    dom.dlgPropose.showModal();
    if (dom.propJustification) dom.propJustification.focus();
  }

  if (dom.formPropose) {
    dom.formPropose.addEventListener("submit", async (e) => {
      e.preventDefault();
      const termId = parseInt(dom.propTermId.value, 10);
      const just = (dom.propJustification.value || "").trim();
      const src = (dom.propSource.value || "").trim();
      const def = (dom.propDef.value || "").trim();
      const ana = (dom.propAnalog.value || "").trim();
      const lim = (dom.propLimits.value || "").trim();

      if (!just || just.length < 10) {
        dom.propStatus.style.display = "block";
        dom.propStatus.className = "add-status error";
        dom.propStatus.textContent = "Debes ingresar una justificación epistémica de al menos 10 caracteres.";
        return;
      }
      if (!src || src.length < 3) {
        dom.propStatus.style.display = "block";
        dom.propStatus.className = "add-status error";
        dom.propStatus.textContent = "Debes citar la fuente primaria oficial que respalda la corrección.";
        return;
      }

      try {
        dom.propSubmit.disabled = true;
        dom.propSubmit.textContent = "⚖️ Auditando con IA...";
        dom.propStatus.style.display = "block";
        dom.propStatus.className = "add-status loading";
        dom.propStatus.textContent = "Sometiendo propuesta al árbitro cognitivo y linter ontológico...";

        const res = await fetchJsonWithPin(`/api/terms/${termId}/propose-canonical`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            proposal_justification: just,
            proposal_source: src,
            custom_definition: def,
            custom_analogy: ana,
            custom_limits: lim,
          }),
        });

        const scoreMsg = res.arbitration_score != null ? `Score: ${Number(res.arbitration_score).toFixed(1)}/10` : "";
        const verdictMsg = res.arbitration_verdict ? `Veredicto: ${res.arbitration_verdict}` : "Sometido a revisión";
        dom.propStatus.className = "add-status success";
        dom.propStatus.textContent = `✅ Propuesta registrada exitosamente. ${scoreMsg} (${verdictMsg}).`;
        showToast("⚖️ Propuesta enviada a la mesa de arbitraje administrativo.");
        setTimeout(() => {
          dom.dlgPropose.close();
        }, 1400);
      } catch (err) {
        dom.propStatus.style.display = "block";
        dom.propStatus.className = "add-status error";
        dom.propStatus.textContent = "Error: " + err.message;
      } finally {
        dom.propSubmit.disabled = false;
        dom.propSubmit.textContent = "⚖️ Someter a Arbitraje";
      }
    });

    if (dom.propCancel) {
      dom.propCancel.addEventListener("click", () => {
        dom.dlgPropose.close();
      });
    }
  }

  // =========================================================================
  // VISTA 3: FLASHCARDS 3D SRS + BARRA TIMELINE ANIMADA DE TANDA (FASE 2)
  // =========================================================================

  function renderSessionProgress() {
    if (!dom.sessionProgressSegments) return;
    const total = state.dueCards.length;
    const currentIdx = state.currentDueIndex;
    const completed = Math.min(currentIdx, total);

    if (dom.sessionProgressStats) {
      dom.sessionProgressStats.textContent = `${completed} / ${total} completadas`;
    }
    if (dom.sessionProgressTitle) {
      if (state.activeFocus && state.activeFocus.category) {
        dom.sessionProgressTitle.textContent = `🎯 Tanda Enfocada · ${state.activeFocus.category}`;
      } else {
        dom.sessionProgressTitle.textContent = "Progreso de la Tanda Actual";
      }
    }

    const segs = [];
    for (let i = 0; i < total; i++) {
      let cls = "sp-seg";
      if (i < currentIdx) {
        const r = state.sessionHistory[i] || 3;
        cls += ` done-${r}`;
      } else if (i === currentIdx) {
        cls += " current";
      }
      const cardName = state.dueCards[i] ? state.dueCards[i].term : `Tarjeta ${i + 1}`;
      segs.push(`<div class="${cls}" title="${escapeHtml(cardName)}"></div>`);
    }
    dom.sessionProgressSegments.innerHTML = segs.join("");
  }

  async function loadDueCards() {
    try {
      const res = await fetchWithPin("/api/review/due");
      if (!res.ok) return;
      const data = await res.json();
      state.dueCards = data.cards || [];
      state.activeFocus = data.active_focus || null;
      state.currentDueIndex = 0;
      state.sessionHistory = [];

      const count = data.count || state.dueCards.length;
      dom.dueNum.textContent = count;
      if (state.activeFocus) {
        const mScore = typeof state.activeFocus.mastery_score === "number"
          ? ` · Maestría: ${state.activeFocus.mastery_score.toFixed(0)}%`
          : "";
        dom.dueLabel.textContent = `pendientes hoy · 🎯 Enfoque actual: ${state.activeFocus.category} (${state.activeFocus.category_due} hoy${mScore})`;
      } else {
        const modeTxt = state.settings.focused_mode === false ? " · 🌐 Modo Global Mixto" : "";
        dom.dueLabel.textContent = (count === 1 ? "término por repasar hoy" : "términos por repasar hoy") + modeTxt;
      }

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

    renderSessionProgress();

    state.isCardRevealed = false;
    state.revealStep = 0;
    dom.flashcard.classList.remove("revealed", "peek-front");
    dom.fcAnswer.hidden = true;
    if (dom.fcBlockDef) dom.fcBlockDef.hidden = false;
    if (dom.fcBlockAnalog) dom.fcBlockAnalog.hidden = false;
    if (dom.fcBlockTradeoff) dom.fcBlockTradeoff.hidden = false;
    if (dom.fcBlockMeta) dom.fcBlockMeta.hidden = true;
    dom.ratingPanel.hidden = true;
    dom.fcHint.hidden = false;
    dom.fcHint.innerHTML = state.settings.step_by_step_reveal
      ? `<span>Paso 1/3: Intenta explicarlo · toca o presiona <kbd>Espacio</kbd> para revelar Definición</span>`
      : `<span>Intenta explicarlo en voz alta · toca o presiona <kbd>Espacio</kbd> para girar 180°</span>`;

    // Front Face Metadata
    const catText = `${card.macro_area} · ${card.category}`;
    dom.fcCategory.textContent = catText;
    dom.fcTerm.textContent = card.term;

    const zoomLvl = card.zoom_level || 1;
    if (dom.fcZoomBadge) {
      dom.fcZoomBadge.textContent = `Zoom ${zoomLvl}`;
      dom.fcZoomBadge.className = `zoom-badge zoom-${zoomLvl}`;
    }

    if (dom.fcMaturityPill) {
      const srs = card.srs || {};
      const interval = srs.interval_days || 0;
      dom.fcMaturityPill.textContent = interval > 0 ? `⏱️ ${interval}d · EF ${(srs.ease_factor || 2.5).toFixed(1)}` : "🌱 Nuevo";
    }

    if (dom.fcFrontOntoPreview) {
      if (card.parent_term) {
        dom.fcFrontOntoPreview.innerHTML = `<span class="chip" style="font-size:11.5px;">▲ Supraordinado: <b>${escapeHtml(card.parent_term)}</b></span>`;
      } else {
        dom.fcFrontOntoPreview.innerHTML = "";
      }
    }

    // Back Face Header Sync
    if (dom.fcBackCategory) dom.fcBackCategory.textContent = catText;
    if (dom.fcBackTerm) dom.fcBackTerm.textContent = card.term;

    // Use customized student version if present, fallback to canonical
    dom.fcDef.textContent = card.effective_definition || card.technical_definition;
    dom.fcAnalog.textContent = card.effective_analogy || card.daily_analogy;

    // Adaptive limits
    const effLimits = card.effective_limits ?? card.tradeoff_when_not_to_use;
    const hasLimits = Boolean(effLimits && effLimits.trim() && effLimits.trim() !== "—" && effLimits.trim() !== "-");
    const limitsLabel = card.limits_label || "Compensaciones y Anti-patrones (Trade-offs)";

    if (dom.fcBlockTradeoff) {
      dom.fcBlockTradeoff.dataset.hasLimits = hasLimits ? "true" : "false";
      if (hasLimits) {
        const h3 = dom.fcBlockTradeoff.querySelector("h3");
        if (h3) h3.textContent = `⚠️ ${limitsLabel}`;
        dom.fcTradeoff.textContent = effLimits;
      } else {
        dom.fcBlockTradeoff.hidden = true;
      }
    }

    // Fuente Cero y Topología en el Reverso
    if (dom.fcSource) {
      dom.fcSource.textContent = card.canonical_source
        ? `📚 Fuente Cero: ${card.canonical_source}`
        : "📚 Sin fuente primaria formal registrada";
    }

    if (dom.fcRelations) {
      const rels = Array.isArray(card.related_terms) ? card.related_terms : [];
      const parent = card.parent_term ? `▲ Supraordinado: ${card.parent_term}` : "";
      const relText = rels.length > 0 ? `🔗 Vínculos: ${rels.join(" · ")}` : "";
      const parts = [parent, relText].filter(Boolean);
      dom.fcRelations.textContent = parts.length > 0 ? parts.join("   |   ") : "";
    }

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
    if (state.isCardRevealed) {
      dom.flashcard.classList.remove("peek-front");
      return;
    }
    const hasLimits = dom.fcBlockTradeoff && dom.fcBlockTradeoff.dataset.hasLimits === "true";

    if (!state.settings.step_by_step_reveal) {
      state.isCardRevealed = true;
      state.revealStep = 3;
      dom.flashcard.classList.add("revealed");
      dom.flashcard.classList.remove("peek-front");
      dom.fcAnswer.hidden = false;
      if (dom.fcBlockDef) dom.fcBlockDef.hidden = false;
      if (dom.fcBlockAnalog) dom.fcBlockAnalog.hidden = false;
      if (dom.fcBlockTradeoff) dom.fcBlockTradeoff.hidden = !hasLimits;
      if (dom.fcBlockMeta) dom.fcBlockMeta.hidden = false;
      dom.fcHint.hidden = true;
      dom.ratingPanel.hidden = false;
      return;
    }

    state.revealStep += 1;
    dom.fcAnswer.hidden = false;
    dom.flashcard.classList.add("revealed");
    dom.flashcard.classList.remove("peek-front");

    if (state.revealStep === 1) {
      if (dom.fcBlockDef) dom.fcBlockDef.hidden = false;
      if (dom.fcBlockAnalog) dom.fcBlockAnalog.hidden = true;
      if (dom.fcBlockTradeoff) dom.fcBlockTradeoff.hidden = true;
      if (dom.fcBlockMeta) dom.fcBlockMeta.hidden = true;
      showToast("Paso 1/3: Toca la tarjeta o Espacio para revelar la Analogía", 2000);
    } else if (state.revealStep === 2) {
      if (dom.fcBlockAnalog) dom.fcBlockAnalog.hidden = false;
      showToast("Paso 2/3: Toca de nuevo para revelar Límites y Fuente Canónica", 2000);
    } else {
      state.isCardRevealed = true;
      if (dom.fcBlockTradeoff) dom.fcBlockTradeoff.hidden = !hasLimits;
      if (dom.fcBlockMeta) dom.fcBlockMeta.hidden = false;
      dom.fcHint.hidden = true;
      dom.ratingPanel.hidden = false;
    }
  }

  if (dom.fcFlipBack) {
    dom.fcFlipBack.addEventListener("click", (e) => {
      e.stopPropagation();
      dom.flashcard.classList.toggle("peek-front");
    });
  }

  dom.flashcard.addEventListener("click", () => {
    if (dom.flashcard.classList.contains("peek-front")) {
      dom.flashcard.classList.remove("peek-front");
      return;
    }
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating }),
      });

      if (!res.ok) {
        throw new Error("Error en la calificación");
      }

      state.sessionHistory[state.currentDueIndex] = rating;
      const data = await res.json();

      if (data.active_focus && data.active_focus.category_id !== card.category_id) {
        showToast(`🏆 ¡Categoría "${card.category}" dominada! Pasando a "${data.active_focus.category}"`, 4500);
        await loadDueCards();
      } else {
        showToast(`Guardado: ${rating === 1 ? "Olvidado" : rating === 2 ? "Dudé" : "Dominado"}`);
        if (rating === 1) {
          state.dueCards.push(card);
        }
        state.currentDueIndex++;
        if (state.currentDueIndex < state.dueCards.length) {
          renderCurrentCard();
        } else {
          await loadDueCards();
        }
      }

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

  // =========================================================================
  // COMMAND PALETTE OMNIBAR (CTRL + K / CMD + K) - FASE 3
  // =========================================================================

  function openCommandPalette() {
    if (!dom.dlgCmdPalette) return;
    state.cmdFilter = "all";
    state.cmdActiveIndex = 0;
    if (dom.cmdFilterBar) {
      dom.cmdFilterBar.querySelectorAll(".cmd-filter-pill").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.cmdFilter === "all");
      });
    }
    if (dom.cmdSearchInput) {
      dom.cmdSearchInput.value = "";
    }
    renderCmdResults();
    if (!dom.dlgCmdPalette.open) {
      dom.dlgCmdPalette.showModal();
    }
    if (dom.cmdSearchInput) {
      dom.cmdSearchInput.focus();
    }
  }

  function closeCommandPalette() {
    if (dom.dlgCmdPalette && dom.dlgCmdPalette.open) {
      dom.dlgCmdPalette.close();
    }
  }

  function buildCommandItems(query) {
    const q = normalizeText(query);
    const filter = state.cmdFilter;
    const results = [];

    // 1. Acciones rápidas
    if (filter === "all" || filter === "actions") {
      const actions = [
        {
          type: "action",
          group: "Acciones Rápidas",
          icon: "🎯",
          title: "Ir a Entrenar SRS (Flashcards 3D)",
          sub: `${state.dueCards.length} tarjetas pendientes hoy`,
          badge: "Vista",
          run: () => switchView("train"),
        },
        {
          type: "action",
          group: "Acciones Rápidas",
          icon: "➕",
          title: "Capturar Nuevo Término con IA (Fuente Cero)",
          sub: query ? `Capturar "${query}" con rigor canónico` : "Abrir modal de captura de conceptos",
          badge: "Acción",
          run: () => openAddTermModal(query, ""),
        },
        {
          type: "action",
          group: "Acciones Rápidas",
          icon: "🧭",
          title: "Abrir Explorador Taxonómico y Grafo Ontológico",
          sub: `${state.terms.length} términos en ${state.categories.length} categorías`,
          badge: "Vista",
          run: () => switchView("guide"),
        },
        {
          type: "action",
          group: "Acciones Rápidas",
          icon: "📑",
          title: "Abrir Compendio Tabular Imprimible",
          sub: "Tabla comparativa completa y exportación PDF",
          badge: "Vista",
          run: () => switchView("table"),
        },
        {
          type: "action",
          group: "Acciones Rápidas",
          icon: state.settings.theme === "dark" ? "☀️" : "🌙",
          title: `Cambiar a Modo ${state.settings.theme === "dark" ? "Claro (Vercel Light)" : "Oscuro (Linear Obsidian)"}`,
          sub: "Alternar paleta visual instantáneamente",
          badge: "Tema",
          run: () => dom.btnThemeToggle && dom.btnThemeToggle.click(),
        },
        {
          type: "action",
          group: "Acciones Rápidas",
          icon: "⚙️",
          title: "Preferencias de Estudio y Parámetros SM-2",
          sub: "Ajustar límite de tanda diaria, umbral de madurez y PIN",
          badge: "Ajustes",
          run: () => switchView("settings"),
        },
      ];

      if (isUserAdmin()) {
        actions.push({
          type: "action",
          group: "Acciones Rápidas",
          icon: "🛠️",
          title: "Consola de Administración, Arbitraje y BI",
          sub: "Auditar propuestas canónicas, multi-tenant y métricas",
          badge: "Admin",
          run: () => switchView("admin"),
        });
      }

      actions.forEach(act => {
        if (!q || normalizeText(`${act.title} ${act.sub}`).includes(q)) {
          results.push(act);
        }
      });
    }

    // 2. Categorías y Ramas Taxonómicas
    if (filter === "all" || filter === "categories") {
      const matchedCats = state.categories.filter(c => {
        if (!q) return true;
        return normalizeText(`${c.name} ${c.macro_area} ${c.description || ""}`).includes(q);
      }).slice(0, filter === "categories" ? 20 : 5);

      matchedCats.forEach(c => {
        results.push({
          type: "category",
          group: "Categorías y Ramas",
          icon: "📂",
          title: c.name,
          sub: `📍 ${c.macro_area} · Zoom ${c.zoom_level} · ${c.term_count || 0} términos`,
          badge: `Z${c.zoom_level}`,
          run: () => {
            switchView("guide");
            state.selectedCatId = c.id;
            state.selectedTermId = null;
            renderExplorerTree();
            renderCatDetailPane(c);
          },
        });
      });
    }

    // 3. Términos Canónicos
    if (filter === "all" || filter === "terms") {
      const matchedTerms = state.terms.filter(t => {
        if (!q) return true;
        const relStr = Array.isArray(t.related_terms) ? t.related_terms.join(" ") : "";
        const hay = normalizeText(`${t.term} ${t.category} ${t.macro_area} ${t.technical_definition} ${t.canonical_source || ""} ${t.parent_term || ""} ${relStr}`);
        return hay.includes(q);
      }).slice(0, filter === "terms" ? 35 : 12);

      matchedTerms.forEach(t => {
        const srs = t.srs || {};
        results.push({
          type: "term",
          group: "Conceptos Canónicos",
          icon: t.is_customized ? "✨" : "📘",
          title: t.term,
          sub: `${t.macro_area} › ${t.category} · 📚 ${t.canonical_source || "Fuente Cero"}`,
          badge: srs.interval_days ? `${srs.interval_days}d` : "Nuevo",
          run: () => {
            switchView("guide");
            state.selectedTermId = t.id;
            renderExplorerTree();
            renderDetailPane(t);
          },
        });
      });
    }

    return results;
  }

  function renderCmdResults() {
    if (!dom.cmdResultsList) return;
    const rawQuery = dom.cmdSearchInput ? dom.cmdSearchInput.value.trim() : "";
    const items = buildCommandItems(rawQuery);
    state.cmdMatchedItems = items;

    if (state.cmdActiveIndex >= items.length) {
      state.cmdActiveIndex = Math.max(0, items.length - 1);
    }

    if (items.length === 0) {
      dom.cmdResultsList.innerHTML = `
        <div class="admin-empty" style="padding:28px 16px;">
          <p style="margin:0 0 10px;">No se encontraron resultados para "<b>${escapeHtml(rawQuery)}</b>".</p>
          <button type="button" class="btn btn-sm btn-primary" id="cmd-empty-capture">
            ➕ Capturar "${escapeHtml(rawQuery)}" con IA en el Catálogo Canónico
          </button>
        </div>
      `;
      const btnCap = document.getElementById("cmd-empty-capture");
      if (btnCap) {
        btnCap.addEventListener("click", () => {
          closeCommandPalette();
          openAddTermModal(rawQuery, "");
        });
      }
      return;
    }

    let lastGroup = null;
    let html = "";

    items.forEach((item, idx) => {
      if (item.group !== lastGroup) {
        lastGroup = item.group;
        html += `<div class="cmd-group-label">${escapeHtml(lastGroup)}</div>`;
      }
      const isActive = idx === state.cmdActiveIndex;
      html += `
        <div class="cmd-item ${isActive ? "active" : ""}" data-cmd-idx="${idx}" role="option" aria-selected="${isActive}">
          <div class="cmd-item-icon">${item.icon}</div>
          <div class="cmd-item-body">
            <div class="cmd-item-title">${escapeHtml(item.title)}</div>
            <div class="cmd-item-sub">${escapeHtml(item.sub)}</div>
          </div>
          <div class="cmd-item-right">
            <span class="chip" style="font-size:10.5px;">${escapeHtml(item.badge)}</span>
          </div>
        </div>
      `;
    });

    dom.cmdResultsList.innerHTML = html;

    dom.cmdResultsList.querySelectorAll(".cmd-item").forEach(el => {
      el.addEventListener("mouseenter", () => {
        const idx = parseInt(el.dataset.cmdIdx, 10);
        state.cmdActiveIndex = idx;
        dom.cmdResultsList.querySelectorAll(".cmd-item").forEach((row, rIdx) => {
          row.classList.toggle("active", rIdx === idx);
        });
      });
      el.addEventListener("click", () => {
        const idx = parseInt(el.dataset.cmdIdx, 10);
        const chosen = state.cmdMatchedItems[idx];
        if (chosen && typeof chosen.run === "function") {
          closeCommandPalette();
          chosen.run();
        }
      });
    });

    const activeRow = dom.cmdResultsList.querySelector(".cmd-item.active");
    if (activeRow) {
      activeRow.scrollIntoView({ block: "nearest" });
    }
  }

  if (dom.btnCmdPalette) {
    dom.btnCmdPalette.addEventListener("click", openCommandPalette);
  }
  if (dom.cmdCloseBtn) {
    dom.cmdCloseBtn.addEventListener("click", closeCommandPalette);
  }
  if (dom.dlgCmdPalette) {
    dom.dlgCmdPalette.addEventListener("click", (e) => {
      if (e.target === dom.dlgCmdPalette) {
        closeCommandPalette();
      }
    });
  }
  if (dom.cmdSearchInput) {
    dom.cmdSearchInput.addEventListener("input", () => {
      state.cmdActiveIndex = 0;
      renderCmdResults();
    });
    dom.cmdSearchInput.addEventListener("keydown", (e) => {
      const len = state.cmdMatchedItems.length;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (len > 0) {
          state.cmdActiveIndex = (state.cmdActiveIndex + 1) % len;
          renderCmdResults();
        }
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (len > 0) {
          state.cmdActiveIndex = (state.cmdActiveIndex - 1 + len) % len;
          renderCmdResults();
        }
      } else if (e.key === "Enter") {
        e.preventDefault();
        const chosen = state.cmdMatchedItems[state.cmdActiveIndex];
        if (chosen && typeof chosen.run === "function") {
          closeCommandPalette();
          chosen.run();
        }
      }
    });
  }
  if (dom.cmdFilterBar) {
    dom.cmdFilterBar.querySelectorAll(".cmd-filter-pill").forEach(btn => {
      btn.addEventListener("click", () => {
        state.cmdFilter = btn.dataset.cmdFilter || "all";
        state.cmdActiveIndex = 0;
        dom.cmdFilterBar.querySelectorAll(".cmd-filter-pill").forEach(b => {
          b.classList.toggle("active", b === btn);
        });
        renderCmdResults();
        if (dom.cmdSearchInput) dom.cmdSearchInput.focus();
      });
    });
  }

  // Atajos globales de teclado (Ctrl+K / Cmd+K y Controles SRS)
  window.addEventListener("keydown", (e) => {
    // Ctrl+K o Cmd+K abre/cierra Command Palette desde cualquier lugar
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (dom.dlgCmdPalette && dom.dlgCmdPalette.open) {
        closeCommandPalette();
      } else {
        openCommandPalette();
      }
      return;
    }

    // Si hay un diálogo modal abierto, no interceptar teclas de entrenamiento
    if (document.querySelector("dialog[open]")) return;

    const activeView = document.querySelector(".view.active");
    if (!activeView || activeView.id !== "view-train") return;
    if (document.activeElement.tagName === "INPUT" || document.activeElement.tagName === "TEXTAREA" || document.activeElement.tagName === "SELECT") return;

    if (e.code === "Space" && !state.isCardRevealed) {
      e.preventDefault();
      revealCard();
    } else if (state.isCardRevealed) {
      if (e.code === "Space") {
        e.preventDefault();
        dom.flashcard.classList.toggle("peek-front");
      } else if (e.key === "1") {
        submitRating(1);
      } else if (e.key === "2") {
        submitRating(2);
      } else if (e.key === "3") {
        submitRating(3);
      }
    }
  });

  // =========================================================================
  // VISTA 4: PREFERENCIAS DE ESTUDIO DEL USUARIO
  // =========================================================================

  if (dom.setTheme) {
    dom.setTheme.addEventListener("change", () => {
      applyTheme(dom.setTheme.value);
    });
  }

  if (dom.formSettings) {
    dom.formSettings.addEventListener("submit", async (e) => {
      e.preventDefault();
      const payload = {
        session_card_limit: parseInt(dom.setSessionLimit.value, 10) || 5,
        maturity_threshold_days: parseInt(dom.setMaturityDays.value, 10) || 21,
        initial_ease_factor: parseFloat(dom.setInitialEf.value) || 2.5,
        focused_mode: dom.setFocusedMode.value === "true",
        theme: dom.setTheme.value === "light" ? "light" : "dark",
        step_by_step_reveal: dom.setStepReveal.value === "true",
      };
      dom.btnSaveSettings.disabled = true;
      try {
        const updated = await fetchJsonWithPin("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        state.settings = { ...state.settings, ...updated };
        syncSettingsUI();
        showToast("✅ Configuración guardada y sincronizada en PostgreSQL");
        await loadData();
      } catch (err) {
        showToast("Error guardando configuración: " + err.message);
      } finally {
        dom.btnSaveSettings.disabled = false;
      }
    });
  }

  if (dom.btnChangePin) {
    dom.btnChangePin.addEventListener("click", async () => {
      const newPin = await requestPinModal("Ingresa el nuevo PIN de acceso para este dispositivo:");
      if (newPin) {
        syncSettingsUI();
        showToast("🔑 PIN actualizado en este dispositivo");
        await loadData();
      }
    });
  }

  if (dom.btnLogoutPin) {
    dom.btnLogoutPin.addEventListener("click", () => {
      clearSavedPin();
      syncSettingsUI();
      showToast("🚪 Sesión local cerrada. Se solicitará el PIN al recargar.");
    });
  }

  function renderStudyCategorySelector() {
    if (!dom.studyCatsSelector) return;
    const activeIds = Array.isArray(state.settings.active_category_ids) ? state.settings.active_category_ids : [];
    const allSelected = activeIds.length === 0;

    if (state.areas.length === 0) {
      dom.studyCatsSelector.innerHTML = `<div class="admin-empty">Aún no hay categorías registradas.</div>`;
      return;
    }

    dom.studyCatsSelector.innerHTML = state.areas.map(area => {
      const checks = (area.categories || []).map(c => {
        const checked = allSelected || activeIds.includes(c.id);
        return `
          <label class="study-cat-check">
            <input type="checkbox" class="chk-study-cat" value="${c.id}" ${checked ? "checked" : ""}>
            <span><b>${escapeHtml(c.name)}</b> <small>(${c.term_count || 0} térm · Zoom ${c.zoom_level})</small></span>
          </label>
        `;
      }).join("");
      return `
        <div class="study-area-group">
          <div class="study-area-title">📍 ${escapeHtml(area.name)}</div>
          <div class="study-cats-list">${checks}</div>
        </div>
      `;
    }).join("");

    dom.studyCatsSelector.querySelectorAll(".chk-study-cat").forEach(chk => {
      chk.addEventListener("change", () => {
        const boxes = Array.from(dom.studyCatsSelector.querySelectorAll(".chk-study-cat"));
        const checkedIds = boxes.filter(b => b.checked).map(b => parseInt(b.value, 10));
        state.settings.active_category_ids = (checkedIds.length === boxes.length || checkedIds.length === 0) ? [] : checkedIds;
        updateDeckSummaryBar();
      });
    });
  }

  // =========================================================================
  // VISTA 5: CONSOLA DE ADMINISTRACIÓN (CURADURÍA, MULTI-TENANT, PROPUESTAS Y BI)
  // =========================================================================

  function renderAdminRecords() {
    if (!dom.adminRecordsList) return;
    const query = normalizeText(dom.adminSearch ? dom.adminSearch.value.trim() : "");
    const filterCatId = dom.adminCatFilter && dom.adminCatFilter.value
      ? parseInt(dom.adminCatFilter.value, 10)
      : null;

    const filtered = state.terms.filter(t => {
      if (filterCatId && t.category_id !== filterCatId) return false;
      if (!query) return true;
      return normalizeText(`${t.term} ${t.category} ${t.macro_area} ${t.canonical_source || ""}`).includes(query);
    });

    if (dom.adminCountBadge) {
      dom.adminCountBadge.textContent = `${filtered.length} de ${state.terms.length} registros`;
    }

    if (filtered.length === 0) {
      dom.adminRecordsList.innerHTML = `<div class="admin-empty">No hay registros que coincidan con el filtro.</div>`;
      return;
    }

    dom.adminRecordsList.innerHTML = filtered.map(t => {
      const srs = t.srs || {};
      const mScore = srs.maturity_score ?? 0;
      const scopesCount = Array.isArray(t.other_scopes) ? t.other_scopes.length : 0;
      const srcText = t.canonical_source ? ` · 📚 ${t.canonical_source}` : "";
      return `
        <div class="admin-record-item" data-term-id="${t.id}">
          <div class="admin-record-info">
            <div class="admin-record-title">
              <b>${escapeHtml(t.term)}</b>
              <span class="admin-record-cat">${escapeHtml(t.macro_area)} · ${escapeHtml(t.category)}</span>
              <span class="admin-record-srs">Madurez: ${mScore}% (${srs.interval_days || 0}d · ${scopesCount} ámbitos${escapeHtml(srcText)})</span>
            </div>
            <div class="admin-record-def">${escapeHtml((t.technical_definition || "").substring(0, 140))}…</div>
          </div>
          <div class="admin-record-actions">
            <input type="text" class="admin-scope-input" placeholder="Ámbito opcional (ej. Derecho)" value="${escapeHtml(t.category)}" title="Ámbito objetivo para la IA">
            <button type="button" class="btn btn-sm btn-ai" data-action="reenrich" data-id="${t.id}">🤖 Reenviar a IA</button>
            <button type="button" class="btn btn-sm btn-danger" data-action="delete" data-id="${t.id}" data-name="${escapeHtml(t.term)}">🗑️ Eliminar</button>
          </div>
        </div>
      `;
    }).join("");

    dom.adminRecordsList.querySelectorAll("button[data-action='reenrich']").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = parseInt(btn.dataset.id, 10);
        const row = btn.closest(".admin-record-item");
        const scopeInput = row ? row.querySelector(".admin-scope-input") : null;
        const scope = scopeInput ? scopeInput.value.trim() : "";
        await handleReenrichTerm(id, scope, btn);
      });
    });

    dom.adminRecordsList.querySelectorAll("button[data-action='delete']").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = parseInt(btn.dataset.id, 10);
        const name = btn.dataset.name || `#${id}`;
        await handleDeleteTerm(id, name, btn);
      });
    });
  }

  async function handleReenrichTerm(termId, scope, btnEl) {
    const origText = btnEl.textContent;
    btnEl.disabled = true;
    btnEl.textContent = "⏳ Regenerando...";
    try {
      const res = await fetchJsonWithPin(`/api/terms/${termId}/reenrich`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: scope || null }),
      });
      showToast(`✨ Ficha de "${res.card.term}" regenerada con Fuentes Primarias`);
      await loadData();
    } catch (err) {
      showToast("Error regenerando con IA: " + err.message, 4000);
      btnEl.disabled = false;
      btnEl.textContent = origText;
    }
  }

  async function handleDeleteTerm(termId, termName, btnEl) {
    if (!window.confirm(`¿Estás seguro de eliminar el registro "${termName}" y su historial de repaso?`)) {
      return;
    }
    btnEl.disabled = true;
    btnEl.textContent = "⏳...";
    try {
      await fetchJsonWithPin(`/api/terms/${termId}`, {
        method: "DELETE",
      });
      showToast(`🗑️ Registro "${termName}" eliminado`);
      await loadData();
    } catch (err) {
      showToast("Error eliminando registro: " + err.message, 4000);
      btnEl.disabled = false;
      btnEl.textContent = "🗑️ Eliminar";
    }
  }

  if (dom.adminSearch) dom.adminSearch.addEventListener("input", renderAdminRecords);
  if (dom.adminCatFilter) dom.adminCatFilter.addEventListener("change", renderAdminRecords);

  async function loadAdminData() {
    const isAdmin = isUserAdmin();
    if (dom.adminUsersSection) dom.adminUsersSection.hidden = !isAdmin;
    if (dom.adminProposalsSection) dom.adminProposalsSection.hidden = !isAdmin;
    if (dom.adminBiSection) dom.adminBiSection.hidden = !isAdmin;
    if (!isAdmin) return;

    try {
      const [usersRes, biRes, proposalsRes] = await Promise.all([
        fetchJsonWithPin("/api/admin/users").catch(() => []),
        fetchJsonWithPin("/api/admin/bi").catch(() => null),
        fetchJsonWithPin("/api/admin/proposals").catch(() => []),
      ]);
      state.users = Array.isArray(usersRes) ? usersRes : [];
      state.bi = biRes;
      state.proposals = Array.isArray(proposalsRes) ? proposalsRes : [];
      renderAdminUsers();
      renderAdminProposals();
      renderBiDashboard();
    } catch (err) {
      console.warn("Error cargando paneles admin:", err);
    }
  }

  function renderAdminUsers() {
    if (!dom.adminUsersList) return;
    if (!state.users || state.users.length === 0) {
      dom.adminUsersList.innerHTML = `<div class="admin-empty">Solo Administrador Principal activo. Agrega usuarios arriba para habilitar multi-tenancy.</div>`;
      return;
    }
    dom.adminUsersList.innerHTML = state.users.map(u => {
      const statusBadge = u.is_approved ? "✅ Aprobado" : "⛔ Bloqueado";
      const delBtn = u.id !== 1
        ? `<button type="button" class="btn btn-sm btn-danger" data-del-user="${u.id}">🗑️ Eliminar</button>`
        : `<span class="admin-record-srs">Propietario</span>`;
      return `
        <div class="admin-record-item">
          <div class="admin-record-info">
            <div class="admin-record-title">
              <b>#${u.id} · ${escapeHtml(u.display_name)}</b>
              <span class="admin-record-cat">Rol: ${escapeHtml(u.role.toUpperCase())} · ${statusBadge}</span>
              <span class="admin-record-srs">Chat ID: ${escapeHtml(u.telegram_chat_id || "—")} · Cuota IA hoy: ${u.ai_calls_today}/${u.daily_ai_limit}</span>
            </div>
          </div>
          <div class="admin-record-actions">
            ${delBtn}
          </div>
        </div>
      `;
    }).join("");

    dom.adminUsersList.querySelectorAll("button[data-del-user]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const uid = parseInt(btn.dataset.delUser, 10);
        if (!window.confirm(`¿Eliminar al usuario #${uid} y su progreso individual?`)) return;
        try {
          await fetchJsonWithPin(`/api/admin/users/${uid}`, { method: "DELETE" });
          showToast(`Usuario #${uid} eliminado`);
          await loadAdminData();
        } catch (err) {
          showToast("Error eliminando usuario: " + err.message);
        }
      });
    });
  }

  if (dom.formAdminUser) {
    dom.formAdminUser.addEventListener("submit", async (e) => {
      e.preventDefault();
      const nameEl = document.getElementById("usr-name");
      const chatEl = document.getElementById("usr-chat-id");
      const pinEl = document.getElementById("usr-pin");
      const quotaEl = document.getElementById("usr-quota");
      const roleEl = document.getElementById("usr-role");
      const seedEl = document.getElementById("usr-seed");
      try {
        await fetchJsonWithPin("/api/admin/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: nameEl.value.trim(),
            telegram_chat_id: chatEl.value.trim() || null,
            web_pin: pinEl.value.trim() || null,
            daily_ai_limit: parseInt(quotaEl.value, 10) || 10,
            role: roleEl.value,
            is_approved: true,
            seed_all_terms: Boolean(seedEl && seedEl.checked),
          }),
        });
        nameEl.value = "";
        chatEl.value = "";
        pinEl.value = "";
        showToast("✅ Usuario guardado / aprobado correctamente");
        await loadAdminData();
      } catch (err) {
        showToast("Error guardando usuario: " + err.message);
      }
    });
  }

  // --- Cola de Propuestas y Arbitraje Canónico ---
  function renderAdminProposals() {
    if (!dom.adminProposalsList) return;
    if (!state.proposals || state.proposals.length === 0) {
      dom.adminProposalsList.innerHTML = `<div class="admin-empty">No hay propuestas pendientes de arbitraje canónico.</div>`;
      return;
    }

    dom.adminProposalsList.innerHTML = state.proposals.map(p => {
      const scoreBadge = p.arbitration_score != null
        ? `<span class="chip" style="font-weight:700;">Score IA: ${Number(p.arbitration_score).toFixed(1)}/10</span>`
        : `<span class="chip">Arbitraje en proceso</span>`;

      let verdictClass = "verdict-review";
      if (p.arbitration_verdict === "APPROVED_FOR_MERGE") verdictClass = "verdict-approved";
      if (p.arbitration_verdict === "REJECTED") verdictClass = "verdict-rejected";
      const verdictBadge = p.arbitration_verdict
        ? `<span class="verdict-badge ${verdictClass}">${escapeHtml(p.arbitration_verdict)}</span>`
        : "";

      return `
        <div class="proposal-item" data-proposal-term="${p.term_id}" data-proposal-user="${p.user_id}">
          <div class="proposal-top">
            <div>
              <div class="proposal-concept">${escapeHtml(p.term_name)}</div>
              <div class="proposal-meta">${escapeHtml(p.macro_area)} › ${escapeHtml(p.category_name)} · Propuesto por: <b>${escapeHtml(p.user_name)}</b></div>
            </div>
            <div style="display:flex; gap:6px; align-items:center;">
              ${scoreBadge}
              ${verdictBadge}
            </div>
          </div>

          <div class="proposal-diff-grid">
            <div class="diff-col canonical">
              <h4>Canónico Actual</h4>
              <p><b>Definición:</b> ${escapeHtml(p.canonical_definition)}</p>
              <p><b>Analogía:</b> ${escapeHtml(p.canonical_analogy)}</p>
              ${p.canonical_limits ? `<p><b>Límites:</b> ${escapeHtml(p.canonical_limits)}</p>` : ''}
              <p style="font-size:11.5px; color:var(--text-muted); margin-top:6px;"><i>Fuente: ${escapeHtml(p.canonical_source || 'Sin fuente')}</i></p>
            </div>
            <div class="diff-col proposed">
              <h4>Propuesta del Estudiante</h4>
              <p><b>Definición:</b> ${escapeHtml(p.proposed_definition || p.canonical_definition)}</p>
              <p><b>Analogía:</b> ${escapeHtml(p.proposed_analogy || p.canonical_analogy)}</p>
              ${p.proposed_limits ? `<p><b>Límites:</b> ${escapeHtml(p.proposed_limits)}</p>` : ''}
              <p style="font-size:11.5px; color:var(--success); margin-top:6px;"><b>Fuente citada:</b> ${escapeHtml(p.proposal_source || '—')}</p>
            </div>
          </div>

          <div class="proposal-reasoning">
            <b>Justificación del Estudiante:</b> ${escapeHtml(p.proposal_justification || '—')}
          </div>

          <div class="admin-record-actions" style="margin-top:8px;">
            <button type="button" class="btn btn-sm btn-primary" data-merge-prop="${p.term_id}" data-user="${p.user_id}">
              ✅ Aprobar y Fusionar (Merge Canónico)
            </button>
            <button type="button" class="btn btn-sm btn-danger" data-reject-prop="${p.term_id}" data-user="${p.user_id}">
              ❌ Rechazar Propuesta
            </button>
          </div>
        </div>
      `;
    }).join("");

    dom.adminProposalsList.querySelectorAll("button[data-merge-prop]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const tid = parseInt(btn.dataset.mergeProp, 10);
        const uid = parseInt(btn.dataset.user, 10);
        if (!window.confirm("¿Aprobar e incorporar formalmente esta propuesta al Catálogo Canónico Global?")) return;
        try {
          await fetchJsonWithPin(`/api/admin/proposals/${tid}/resolve`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: uid, action: "merge", admin_notes: "Aprobado por el Administrador" }),
          });
          showToast("✅ Propuesta fusionada al Catálogo Canónico");
          await loadData();
          await loadAdminData();
        } catch (err) {
          showToast("Error fusionando propuesta: " + err.message);
        }
      });
    });

    dom.adminProposalsList.querySelectorAll("button[data-reject-prop]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const tid = parseInt(btn.dataset.rejectProp, 10);
        const uid = parseInt(btn.dataset.user, 10);
        const reason = window.prompt("Motivo del rechazo epistémico (opcional):", "No cumple con el nivel de rigor requerido");
        if (reason === null) return;
        try {
          await fetchJsonWithPin(`/api/admin/proposals/${tid}/resolve`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: uid, action: "reject", admin_notes: reason }),
          });
          showToast("❌ Propuesta rechazada");
          await loadAdminData();
        } catch (err) {
          showToast("Error rechazando propuesta: " + err.message);
        }
      });
    });
  }

  if (dom.btnRefreshProposals) {
    dom.btnRefreshProposals.addEventListener("click", async () => {
      try {
        const res = await fetchJsonWithPin("/api/admin/proposals");
        state.proposals = Array.isArray(res) ? res : [];
        renderAdminProposals();
        showToast("🔄 Propuestas actualizadas");
      } catch (err) {
        showToast("Error cargando propuestas: " + err.message);
      }
    });
  }

  // --- Inteligencia de Negocios (BI) ---
  function renderBiDashboard() {
    if (!dom.biDashboard) return;
    const bi = state.bi;
    if (!bi) {
      dom.biDashboard.innerHTML = `<div class="admin-empty">Cargando métricas de Inteligencia de Negocios...</div>`;
      return;
    }

    const areasHtml = (bi.area_breakdown || []).map(a => `
      <tr>
        <td><b>${escapeHtml(a.macro_area)}</b></td>
        <td>${a.category_count || 0}</td>
        <td>${a.term_count || 0}</td>
        <td>${a.canonically_mastered_terms || 0}</td>
        <td>${a.avg_interval_days ?? 0}d (EF ${a.avg_ease_factor ?? 2.5})</td>
      </tr>
    `).join("") || `<tr><td colspan="5">Sin datos</td></tr>`;

    const hardestHtml = (bi.hardest_terms || []).slice(0, 8).map(t => `
      <tr>
        <td><b>${escapeHtml(t.term)}</b></td>
        <td>${escapeHtml(t.macro_area)} · ${escapeHtml(t.category_name)}</td>
        <td>${t.total_reviews || 0}</td>
        <td>${t.forgotten_count || 0} (${t.forgotten_rate_pct ?? 0}%)</td>
      </tr>
    `).join("") || `<tr><td colspan="4">Sin repasos registrados aún</td></tr>`;

    dom.biDashboard.innerHTML = `
      <div class="bi-grid">
        <div class="bi-box">
          <h4>📊 Rendimiento por Macro-Área</h4>
          <table class="bi-table">
            <thead><tr><th>Macro-Área</th><th>Cat.</th><th>Térm.</th><th>Maduros ≥21d</th><th>Promedio SRS</th></tr></thead>
            <tbody>${areasHtml}</tbody>
          </table>
        </div>
        <div class="bi-box">
          <h4>🔥 Conceptos con Mayor Tasa de Olvido (Puntos Ciegos)</h4>
          <table class="bi-table">
            <thead><tr><th>Término</th><th>Ámbito</th><th>Repasos</th><th>Olvidos (%)</th></tr></thead>
            <tbody>${hardestHtml}</tbody>
          </table>
        </div>
      </div>
    `;
  }

  if (dom.btnRefreshBi) {
    dom.btnRefreshBi.addEventListener("click", () => loadAdminData());
  }

  if (dom.btnExportCsv) {
    dom.btnExportCsv.addEventListener("click", async () => {
      try {
        const res = await fetchWithPin("/api/admin/bi/export-csv");
        if (!res.ok) throw new Error("No se pudo exportar el CSV");
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "glosario_srs_bi_export.csv";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        showToast("📥 Base de datos BI exportada en CSV (UTF-8 BOM)");
      } catch (err) {
        showToast("Error exportando CSV: " + err.message);
      }
    });
  }

  // --- Utilidades ---
  function showCategoryModal(catId) {
    const cat = state.categories.find(c => c.id === catId);
    if (!cat) return;
    dom.dcZoom.textContent = `Zoom ${cat.zoom_level} · ${cat.zoom_label || `Zoom ${cat.zoom_level}`}`;
    dom.dcZoom.className = `zoom-pill zoom-${cat.zoom_level}`;
    dom.dcName.textContent = cat.name;
    const m = cat.mastery;
    const mInfo = m ? ` · Maestría: ${m.mastery_score.toFixed(1)}% (${m.status_label})` : "";
    dom.dcArea.textContent = `Área: ${cat.macro_area} (${cat.term_count || 0} términos${mInfo})`;
    dom.dcDesc.textContent = cat.description;
    dom.dlgCat.showModal();
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Service Worker para PWA (con recarga automática al detectar nueva versión)
  if ("serviceWorker" in navigator) {
    let refreshing = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (!refreshing) {
        refreshing = true;
        window.location.reload();
      }
    });

    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").then(reg => {
        reg.update();
      }).catch(err => {
        console.warn("SW register fail:", err);
      });
    });
  }

  // Inicio
  loadData();
})();

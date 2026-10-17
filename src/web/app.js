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
    revealStep: 0,
    stats: null,
    settings: {
      session_card_limit: 5,
      maturity_threshold_days: 21,
      initial_ease_factor: 2.5,
      focused_mode: true,
      theme: localStorage.getItem("glosario_srs_theme") || "light",
      step_by_step_reveal: false,
    },
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
    fcBlockDef: document.getElementById("fc-block-def"),
    fcBlockAnalog: document.getElementById("fc-block-analog"),
    fcBlockTradeoff: document.getElementById("fc-block-tradeoff"),
    fcDef: document.getElementById("fc-def"),
    fcAnalog: document.getElementById("fc-analog"),
    fcTradeoff: document.getElementById("fc-tradeoff"),
    ratingPanel: document.getElementById("rating"),
    rateButtons: document.querySelectorAll(".rate"),
    pv1: document.getElementById("pv-1"),
    pv2: document.getElementById("pv-2"),
    pv3: document.getElementById("pv-3"),

    // Configuración y Administración
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
    adminCountBadge: document.getElementById("admin-count-badge"),
    adminSearch: document.getElementById("admin-search"),
    adminCatFilter: document.getElementById("admin-cat-filter"),
    adminRecordsList: document.getElementById("admin-records-list"),

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

      if (!dlg.open) {
        dlg.showModal();
      }
      input.focus();
    });
  }

  let activePinPromise = null;

  function requestPinModal(errMsg = "") {
    if (!activePinPromise) {
      activePinPromise = promptPin(errMsg).finally(() => {
        activePinPromise = null;
      });
    }
    return activePinPromise;
  }

  async function ensurePin(errMsg = "") {
    let pin = getSavedPin();
    if (!pin) {
      pin = await requestPinModal(errMsg);
    }
    return pin;
  }

  async function fetchWithPin(url, options = {}) {
    let usedPin = await ensurePin();
    if (!usedPin) {
      throw new Error("Se requiere PIN para acceder al sistema.");
    }
    const headers = {
      ...(options.headers || {}),
      "X-Web-Pin": usedPin,
    };
    let res = await fetch(url, { ...options, headers });
    if (res.status === 401) {
      const currentSaved = getSavedPin();
      let retryPin = (currentSaved && currentSaved !== usedPin) ? currentSaved : null;
      if (!retryPin) {
        clearSavedPin();
        retryPin = await requestPinModal("PIN incorrecto. Ingresa tu WEB_ACCESS_PIN:");
      }
      if (!retryPin) {
        throw new Error("PIN no autorizado.");
      }
      headers["X-Web-Pin"] = retryPin;
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

  function applyTheme(theme) {
    const mode = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", mode);
    localStorage.setItem("glosario_srs_theme", mode);
  }
  applyTheme(state.settings.theme);

  function syncSettingsUI() {
    const s = state.settings;
    if (dom.setSessionLimit) dom.setSessionLimit.value = s.session_card_limit ?? 5;
    if (dom.setMaturityDays) dom.setMaturityDays.value = s.maturity_threshold_days ?? 21;
    if (dom.setInitialEf) dom.setInitialEf.value = s.initial_ease_factor ?? 2.5;
    if (dom.setFocusedMode) dom.setFocusedMode.value = String(s.focused_mode ?? true);
    if (dom.setTheme) dom.setTheme.value = s.theme || "light";
    if (dom.setStepReveal) dom.setStepReveal.value = String(s.step_by_step_reveal ?? false);
    applyTheme(s.theme || "light");
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
    } else if (viewName === "settings") {
      syncSettingsUI();
      renderAdminRecords();
    }
  }

  dom.tabs.forEach(tab => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });

  async function fetchJsonWithPin(url, options = {}) {
    const res = await fetchWithPin(url, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Error HTTP ${res.status}`);
    }
    return data;
  }

  // --- Carga de datos iniciales ---
  async function loadData() {
    try {
      const pin = await ensurePin();
      if (!pin) {
        showToast("Se requiere PIN para ver el glosario");
        return;
      }

      // Primero validamos el PIN con la petición más ligera (/api/stats) para evitar colisiones paralelas de 401
      const statsRes = await fetchJsonWithPin("/api/stats");
      const [areasRes, termsRes] = await Promise.all([
        fetchJsonWithPin("/api/areas"),
        fetchJsonWithPin("/api/terms"),
      ]);
      state.areas = Array.isArray(areasRes) ? areasRes : [];
      state.terms = Array.isArray(termsRes) ? termsRes : [];
      state.stats = statsRes;
      if (statsRes && statsRes.settings) {
        state.settings = { ...state.settings, ...statsRes.settings };
      }
      
      // Flatten categories for fast lookup
      state.categories = [];
      state.areas.forEach(a => {
        (a.categories || []).forEach(c => state.categories.push(c));
      });

      syncSettingsUI();
      renderZoomLegend();
      renderGuide();
      populateCategoryFilter();
      renderTermsTable();
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
    const gmText = gm ? ` · Maestría Global: ${gm.mastery_score.toFixed(1)}%` : "";
    dom.reviewedToday.textContent = `Repasados hoy: ${state.stats.reviewed_today}${gmText}`;
  }

  // --- Vista 1: Guía de Áreas y Categorías ---
  function renderZoomLegend() {
    dom.zoomLegend.innerHTML = [1, 2, 3, 4]
      .map(lvl => `<span class="zoom-badge zoom-${lvl}">Zoom ${lvl}: ${ZOOM_LABELS[lvl]}</span>`)
      .join("");
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

  function renderGuide() {
    const activeCatId = state.stats && state.stats.active_focus ? state.stats.active_focus.category_id : null;
    dom.areasContainer.innerHTML = state.areas.map(area => {
      const am = area.mastery || null;
      const areaPct = am && am.total_terms ? `${am.mastery_score.toFixed(0)}% Maestría (${am.status_label})` : "";
      const catsHtml = area.categories.map(c => {
        const isFocus = activeCatId === c.id;
        const focusBadge = isFocus ? `<span class="focus-active-pill">🎯 Enfoque Activo</span>` : "";
        return `
        <div class="cat-card ${isFocus ? "cat-card-focus" : ""}" data-cat-id="${c.id}">
          <div>
            <div class="cat-top">
              <span class="zoom-badge zoom-${c.zoom_level}">Zoom ${c.zoom_level}</span>
              <span class="cat-terms-preview">${c.term_count || 0} términos</span>
            </div>
            ${focusBadge}
            <div class="cat-name">${escapeHtml(c.name)}</div>
            <p class="cat-desc">${escapeHtml(c.description.substring(0, 110))}...</p>
          </div>
          ${renderMasteryBar(c.mastery)}
        </div>
      `;
      }).join("");

      return `
        <div class="area-section">
          <div class="area-header">
            <span>${escapeHtml(area.name)}</span>
            <small style="color: #475569; font-weight: 600;">${areaPct ? `${escapeHtml(areaPct)} · ` : ""}${area.categories.length} categorías</small>
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
    const m = cat.mastery;
    const mInfo = m ? ` · Maestría: ${m.mastery_score.toFixed(1)}% (${m.status_label})` : "";
    dom.dcArea.textContent = `Área: ${cat.macro_area} (${cat.term_count || 0} términos${mInfo})`;
    dom.dcDesc.textContent = cat.description;
    dom.dlgCat.showModal();
  }

  // --- Vista 2: Compendio (Tabla) ---
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
    const query = normalizeText(dom.search.value.trim());
    const filterCatId = dom.catFilter.value ? parseInt(dom.catFilter.value, 10) : null;

    const filtered = state.terms.filter(t => {
      if (filterCatId && t.category_id !== filterCatId) return false;
      if (!query) return true;
      const scopesText = Array.isArray(t.other_scopes) ? t.other_scopes.join(" ") : "";
      const haystack = normalizeText(`${t.term} ${t.category} ${t.technical_definition} ${t.daily_analogy} ${t.tradeoff_when_not_to_use || ""} ${scopesText}`);
      return haystack.includes(query);
    });

    dom.resultCount.textContent = `Mostrando ${filtered.length} de ${state.terms.length} términos`;

    if (filtered.length === 0) {
      dom.termsBody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding: 24px; color: #64748b;">No se encontraron términos que coincidan con la búsqueda.</td></tr>`;
      return;
    }

    dom.termsBody.innerHTML = filtered.map(t => {
      const scopes = Array.isArray(t.other_scopes) && t.other_scopes.length > 0
        ? `<div style="margin-top:6px; font-weight:normal; font-size:10.5px; color:#64748b;">🌐 <i>Otros ámbitos:</i> ${t.other_scopes.map(s => escapeHtml(s)).join(" · ")}</div>`
        : "";
      return `
      <tr>
        <td class="cat">${escapeHtml(t.category)}</td>
        <td class="term">${escapeHtml(t.term)}${scopes}</td>
        <td class="def">${escapeHtml(t.technical_definition)}</td>
        <td class="analog">${escapeHtml(t.daily_analogy)}</td>
        <td class="tradeoff">${escapeHtml(t.tradeoff_when_not_to_use || "—")}</td>
      </tr>
    `;
    }).join("");
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
      state.activeFocus = data.active_focus || null;
      state.currentDueIndex = 0;
      
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

    state.isCardRevealed = false;
    state.revealStep = 0;
    dom.flashcard.classList.remove("revealed");
    dom.fcAnswer.hidden = true;
    if (dom.fcBlockDef) dom.fcBlockDef.hidden = false;
    if (dom.fcBlockAnalog) dom.fcBlockAnalog.hidden = false;
    if (dom.fcBlockTradeoff) dom.fcBlockTradeoff.hidden = false;
    dom.ratingPanel.hidden = true;
    dom.fcHint.hidden = false;
    dom.fcHint.textContent = state.settings.step_by_step_reveal
      ? "Paso 1/3: Intenta explicarlo · toca para revelar Definición Técnica"
      : "Intenta explicarlo en voz alta · toca para revelar";

    dom.fcCategory.textContent = `${card.macro_area} · ${card.category}`;
    dom.fcTerm.textContent = card.term;
    dom.fcDef.textContent = card.technical_definition;
    dom.fcAnalog.textContent = card.daily_analogy;
    const scopesSuffix = Array.isArray(card.other_scopes) && card.other_scopes.length > 0
      ? `\n\n🌐 Otros ámbitos de este término: ${card.other_scopes.join(" · ")}`
      : "";
    dom.fcTradeoff.textContent = (card.tradeoff_when_not_to_use || "—") + scopesSuffix;

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
    if (!state.settings.step_by_step_reveal) {
      state.isCardRevealed = true;
      state.revealStep = 3;
      dom.flashcard.classList.add("revealed");
      dom.fcAnswer.hidden = false;
      if (dom.fcBlockDef) dom.fcBlockDef.hidden = false;
      if (dom.fcBlockAnalog) dom.fcBlockAnalog.hidden = false;
      if (dom.fcBlockTradeoff) dom.fcBlockTradeoff.hidden = false;
      dom.fcHint.hidden = true;
      dom.ratingPanel.hidden = false;
      return;
    }

    state.revealStep += 1;
    dom.fcAnswer.hidden = false;
    if (state.revealStep === 1) {
      if (dom.fcBlockDef) dom.fcBlockDef.hidden = false;
      if (dom.fcBlockAnalog) dom.fcBlockAnalog.hidden = true;
      if (dom.fcBlockTradeoff) dom.fcBlockTradeoff.hidden = true;
      dom.fcHint.textContent = "Paso 2/3: Toca para revelar la Analogía Cotidiana";
    } else if (state.revealStep === 2) {
      if (dom.fcBlockAnalog) dom.fcBlockAnalog.hidden = false;
      dom.fcHint.textContent = "Paso 3/3: Toca para revelar el Trade-off y calificar";
    } else {
      state.isCardRevealed = true;
      dom.flashcard.classList.add("revealed");
      if (dom.fcBlockTradeoff) dom.fcBlockTradeoff.hidden = false;
      dom.fcHint.hidden = true;
      dom.ratingPanel.hidden = false;
    }
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
      if (data.active_focus && data.active_focus.category_id !== card.category_id) {
        showToast(`🏆 ¡Categoría "${card.category}" dominada! Pasando a "${data.active_focus.category}"`, 4500);
        await loadDueCards();
      } else {
        showToast(`Guardado: ${rating === 1 ? "Olvidado" : rating === 2 ? "Dudé" : "Dominado"}`);
        // Si fue "Olvidado" (0 días), lo volvemos a encolar al final de la sesión de esta categoría
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

  // --- Vista 4: Configuración y Gestión de Registros ---
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
        theme: dom.setTheme.value === "dark" ? "dark" : "light",
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
      const newPin = await requestPinModal("Ingresa el nuevo WEB_ACCESS_PIN para este dispositivo:");
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

  function renderAdminRecords() {
    if (!dom.adminRecordsList) return;
    const query = normalizeText(dom.adminSearch ? dom.adminSearch.value.trim() : "");
    const filterCatId = dom.adminCatFilter && dom.adminCatFilter.value
      ? parseInt(dom.adminCatFilter.value, 10)
      : null;

    const filtered = state.terms.filter(t => {
      if (filterCatId && t.category_id !== filterCatId) return false;
      if (!query) return true;
      return normalizeText(`${t.term} ${t.category} ${t.macro_area}`).includes(query);
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
      return `
        <div class="admin-record-item" data-term-id="${t.id}">
          <div class="admin-record-info">
            <div class="admin-record-title">
              <b>${escapeHtml(t.term)}</b>
              <span class="admin-record-cat">${escapeHtml(t.macro_area)} · ${escapeHtml(t.category)}</span>
              <span class="admin-record-srs">Madurez: ${mScore}% (${srs.interval_days || 0}d · ${scopesCount} ámbitos)</span>
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
      showToast(`✨ Ficha de "${res.card.term}" regenerada con la IA`);
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

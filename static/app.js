const IDLE_TIMEOUT_SECONDS = 90;

const state = {
    sessionId: null,
    user: null,
    startedAt: null,
    deadline: null,
    timerId: null,
};

const elements = {
    idleScreen: document.querySelector("#idleScreen"),
    terminalScreen: document.querySelector("#terminalScreen"),
    completeScreen: document.querySelector("#completeScreen"),
    scanForm: document.querySelector("#scanForm"),
    cardInput: document.querySelector("#cardInput"),
    scanMessage: document.querySelector("#scanMessage"),
    welcomeName: document.querySelector("#welcomeName"),
    sessionMeta: document.querySelector("#sessionMeta"),
    timeoutDisplay: document.querySelector("#timeoutDisplay"),
    endSessionButton: document.querySelector("#endSessionButton"),
    searchForm: document.querySelector("#searchForm"),
    searchInput: document.querySelector("#searchInput"),
    searchSummary: document.querySelector("#searchSummary"),
    results: document.querySelector("#results"),
    refreshEventsButton: document.querySelector("#refreshEventsButton"),
    events: document.querySelector("#events"),
    completeSummary: document.querySelector("#sessionSummary"),
    returnButton: document.querySelector("#returnButton"),
    locationModal: document.querySelector("#locationModal"),
    locationTitle: document.querySelector("#locationTitle"),
    locationText: document.querySelector("#locationText"),
    locationPart: document.querySelector("#locationPart"),
    closeLocationButton: document.querySelector("#closeLocationButton"),
    toast: document.querySelector("#toast"),
};

function showScreen(name) {
    elements.idleScreen.classList.toggle("hidden", name !== "idle");
    elements.terminalScreen.classList.toggle("hidden", name !== "terminal");
    elements.completeScreen.classList.toggle("hidden", name !== "complete");
}

async function apiFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
    }
    if (state.sessionId && !headers.has("X-Session-ID")) {
        headers.set("X-Session-ID", state.sessionId);
    }

    const response = await fetch(url, { ...options, headers });
    let body = {};
    try {
        body = await response.json();
    } catch (_) {
        body = {};
    }
    if (!response.ok) {
        throw new Error(body.detail || `Request failed (${response.status})`);
    }
    return body;
}

function formatDateTime(value) {
    return new Intl.DateTimeFormat(undefined, {
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
    }).format(new Date(value));
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function showToast(message) {
    elements.toast.textContent = message;
    elements.toast.classList.remove("hidden");
    clearTimeout(showToast.timeoutId);
    showToast.timeoutId = setTimeout(() => elements.toast.classList.add("hidden"), 3000);
}

function resetIdleTimer() {
    if (!state.sessionId) return;
    state.deadline = Date.now() + IDLE_TIMEOUT_SECONDS * 1000;
}

function startIdleTimer() {
    resetIdleTimer();
    clearInterval(state.timerId);
    state.timerId = setInterval(async () => {
        if (!state.sessionId || !state.deadline) return;
        const seconds = Math.max(0, Math.ceil((state.deadline - Date.now()) / 1000));
        const minutesPart = Math.floor(seconds / 60);
        const secondsPart = String(seconds % 60).padStart(2, "0");
        elements.timeoutDisplay.textContent = `${minutesPart}:${secondsPart}`;
        if (seconds === 0) {
            clearInterval(state.timerId);
            await endSession("INACTIVITY_TIMEOUT");
        }
    }, 250);
}

async function scanCard(cardIdentifier) {
    elements.scanMessage.textContent = "Checking authorization…";
    try {
        const data = await apiFetch("/api/auth/scan", {
            method: "POST",
            body: JSON.stringify({ card_identifier: cardIdentifier }),
        });
        state.sessionId = data.session_id;
        state.user = data.user;
        state.startedAt = data.started_at;
        elements.welcomeName.textContent = `Welcome, ${data.user.display_name}`;
        elements.sessionMeta.textContent = `${data.user.role} · Session started ${formatDateTime(data.started_at)}`;
        elements.scanMessage.textContent = "";
        elements.cardInput.value = "";
        showScreen("terminal");
        startIdleTimer();
        await searchItems("");
        await loadEvents();
        elements.searchInput.focus();
    } catch (error) {
        elements.scanMessage.textContent = error.message;
        elements.cardInput.select();
        await loadEvents();
    }
}

async function searchItems(query) {
    elements.searchSummary.textContent = "Searching inventory…";
    elements.results.innerHTML = "";
    try {
        const data = await apiFetch(`/api/items?q=${encodeURIComponent(query)}`);
        const count = data.items.length;
        elements.searchSummary.textContent = query
            ? `${count} result${count === 1 ? "" : "s"} for “${query}”`
            : `${count} active inventory items`;
        renderItems(data.items);
        if (query) await loadEvents();
    } catch (error) {
        elements.searchSummary.textContent = error.message;
        if (error.message.toLowerCase().includes("session")) returnToIdle();
    }
}

function stockLabel(item) {
    const labels = {
        adequate: "Stock adequate",
        low: "Low stock",
        out: "Out of stock",
        unknown: "Stock unknown",
    };
    return labels[item.stock_status] || "Stock unknown";
}

function renderItems(items) {
    if (!items.length) {
        elements.results.innerHTML = '<div class="empty-state">No matching inventory was found.</div>';
        return;
    }

    elements.results.innerHTML = items.map((item) => {
        const usageButton = item.inventory_type === "consumable"
            ? `<button class="button button-ghost" data-action="usage" data-item-id="${item.id}">Record probable usage</button>`
            : `<button class="button button-ghost" disabled title="Asset checkout comes later in Phase 1">Checkout coming next</button>`;
        const quantity = item.estimated_quantity == null
            ? "Quantity unknown"
            : `Estimated ${item.estimated_quantity} ${escapeHtml(item.unit)}`;
        return `
            <article class="item-card">
                <div class="item-top">
                    <div>
                        <h3>${escapeHtml(item.name)}</h3>
                        <div class="part-number">${escapeHtml(item.part_number)}</div>
                    </div>
                    <span class="pill ${escapeHtml(item.stock_status)}">${stockLabel(item)}</span>
                </div>
                <p class="item-description">${escapeHtml(item.description)}</p>
                <div class="item-meta">
                    <span class="pill ${escapeHtml(item.inventory_type)}">${escapeHtml(item.inventory_type)}</span>
                    <span class="pill">${escapeHtml(item.category)}</span>
                    <span class="pill">${quantity}</span>
                </div>
                <p class="location-inline">${escapeHtml(item.location)}</p>
                <div class="item-actions">
                    <button class="button button-primary" data-action="view" data-item-id="${item.id}">Show location</button>
                    ${usageButton}
                </div>
            </article>`;
    }).join("");
}

async function showItemLocation(itemId) {
    try {
        const data = await apiFetch(`/api/items/${itemId}/view`, { method: "POST" });
        const item = data.item;
        elements.locationTitle.textContent = item.name;
        elements.locationText.textContent = item.location;
        elements.locationPart.textContent = item.part_number;
        elements.locationModal.classList.remove("hidden");
        await loadEvents();
    } catch (error) {
        showToast(error.message);
    }
}

async function recordProbableUsage(itemId) {
    try {
        const data = await apiFetch(`/api/items/${itemId}/probable-usage`, { method: "POST" });
        showToast(`${data.item.name}: probable usage recorded`);
        await loadEvents();
    } catch (error) {
        showToast(error.message);
    }
}

async function loadEvents() {
    try {
        const data = await apiFetch("/api/events/recent?limit=30", {
            headers: { "X-Session-ID": "" },
        });
        if (!data.events.length) {
            elements.events.innerHTML = '<div class="empty-state">No events recorded yet.</div>';
            return;
        }
        elements.events.innerHTML = data.events.map((event) => {
            const subject = event.item_name || event.display_name || "Terminal";
            const details = event.details?.query
                ? `Query: ${event.details.query}`
                : event.details?.ending_reason
                    ? event.details.ending_reason
                    : event.part_number || "";
            return `
                <div class="event">
                    <strong>${escapeHtml(event.event_type.replaceAll("_", " "))}</strong>
                    <span>${escapeHtml(subject)}${details ? ` · ${escapeHtml(details)}` : ""}</span>
                    <span>${formatDateTime(event.timestamp)}</span>
                </div>`;
        }).join("");
    } catch (error) {
        elements.events.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    }
}

async function endSession(reason = "USER_SIGN_OUT") {
    if (!state.sessionId) return;
    const sessionId = state.sessionId;
    elements.endSessionButton.disabled = true;
    try {
        const data = await apiFetch(`/api/sessions/${sessionId}/end`, {
            method: "POST",
            body: JSON.stringify({ ending_reason: reason }),
        });
        const viewed = data.summary.locations_viewed;
        const usage = data.summary.probable_usage_records;
        elements.completeSummary.textContent = `${viewed} location${viewed === 1 ? "" : "s"} viewed · ${usage} probable usage record${usage === 1 ? "" : "s"}.`;
        clearSessionState();
        showScreen("complete");
        setTimeout(() => {
            if (!elements.completeScreen.classList.contains("hidden")) returnToIdle();
        }, 6000);
    } catch (error) {
        showToast(error.message);
        clearSessionState();
        returnToIdle();
    } finally {
        elements.endSessionButton.disabled = false;
    }
}

function clearSessionState() {
    clearInterval(state.timerId);
    state.sessionId = null;
    state.user = null;
    state.startedAt = null;
    state.deadline = null;
    state.timerId = null;
}

function returnToIdle() {
    clearSessionState();
    showScreen("idle");
    elements.searchInput.value = "";
    elements.results.innerHTML = "";
    elements.scanMessage.textContent = "";
    elements.cardInput.focus();
}

elements.scanForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const cardIdentifier = elements.cardInput.value.trim();
    if (!cardIdentifier) {
        elements.scanMessage.textContent = "Enter a card identifier.";
        return;
    }
    scanCard(cardIdentifier);
});

document.querySelectorAll("[data-card]").forEach((button) => {
    button.addEventListener("click", () => {
        elements.cardInput.value = button.dataset.card;
        scanCard(button.dataset.card);
    });
});

elements.searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    searchItems(elements.searchInput.value.trim());
});

elements.results.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    resetIdleTimer();
    const itemId = Number(button.dataset.itemId);
    if (button.dataset.action === "view") showItemLocation(itemId);
    if (button.dataset.action === "usage") recordProbableUsage(itemId);
});

elements.endSessionButton.addEventListener("click", () => endSession("USER_SIGN_OUT"));
elements.returnButton.addEventListener("click", returnToIdle);
elements.refreshEventsButton.addEventListener("click", loadEvents);
elements.closeLocationButton.addEventListener("click", () => elements.locationModal.classList.add("hidden"));
elements.locationModal.addEventListener("click", (event) => {
    if (event.target === elements.locationModal) elements.locationModal.classList.add("hidden");
});

["pointerdown", "keydown", "touchstart"].forEach((eventName) => {
    document.addEventListener(eventName, resetIdleTimer, { passive: true });
});

loadEvents();

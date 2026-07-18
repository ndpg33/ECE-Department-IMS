const IDLE_TIMEOUT_SECONDS = 90;

const state = {
    sessionId: null,
    user: null,
    startedAt: null,
    deadline: null,
    timerId: null,
    activeWorkspace: "inventory",
    pendingAssets: {
        checkout: null,
        return: null,
    },
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
    workspaceTabs: document.querySelectorAll("[data-workspace]"),
    inventoryWorkspace: document.querySelector("#inventoryWorkspace"),
    checkoutWorkspace: document.querySelector("#checkoutWorkspace"),
    returnWorkspace: document.querySelector("#returnWorkspace"),
    searchForm: document.querySelector("#searchForm"),
    searchInput: document.querySelector("#searchInput"),
    searchSummary: document.querySelector("#searchSummary"),
    results: document.querySelector("#results"),
    checkoutLookupForm: document.querySelector("#checkoutLookupForm"),
    checkoutTagInput: document.querySelector("#checkoutTagInput"),
    checkoutMessage: document.querySelector("#checkoutMessage"),
    checkoutResult: document.querySelector("#checkoutResult"),
    returnLookupForm: document.querySelector("#returnLookupForm"),
    returnTagInput: document.querySelector("#returnTagInput"),
    returnMessage: document.querySelector("#returnMessage"),
    returnResult: document.querySelector("#returnResult"),
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

function switchWorkspace(name, { focus = true } = {}) {
    state.activeWorkspace = name;
    elements.inventoryWorkspace.classList.toggle("hidden", name !== "inventory");
    elements.checkoutWorkspace.classList.toggle("hidden", name !== "checkout");
    elements.returnWorkspace.classList.toggle("hidden", name !== "return");
    elements.workspaceTabs.forEach((button) => {
        const isActive = button.dataset.workspace === name;
        button.classList.toggle("active", isActive);
        button.setAttribute("aria-current", isActive ? "page" : "false");
    });

    if (!focus) return;
    if (name === "inventory") elements.searchInput.focus();
    if (name === "checkout") elements.checkoutTagInput.focus();
    if (name === "return") elements.returnTagInput.focus();
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
    if (!value) return "—";
    return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
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
    showToast.timeoutId = setTimeout(() => elements.toast.classList.add("hidden"), 3200);
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

function clearAssetWorkflow(mode) {
    state.pendingAssets[mode] = null;
    const message = mode === "checkout" ? elements.checkoutMessage : elements.returnMessage;
    const result = mode === "checkout" ? elements.checkoutResult : elements.returnResult;
    message.textContent = "";
    message.className = "message";
    result.innerHTML = "";
}

function clearAllWorkspaces() {
    elements.searchInput.value = "";
    elements.results.innerHTML = "";
    elements.searchSummary.textContent = "";
    elements.checkoutTagInput.value = "";
    elements.returnTagInput.value = "";
    clearAssetWorkflow("checkout");
    clearAssetWorkflow("return");
    switchWorkspace("inventory", { focus: false });
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
        clearAllWorkspaces();
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
        const actionButton = item.inventory_type === "consumable"
            ? `<button class="button button-ghost" data-action="usage" data-item-id="${item.id}">Record probable usage</button>`
            : `<button class="button button-ghost" data-action="open-checkout">Scan individual asset</button>`;
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
                    ${actionButton}
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

function assetStatusLabel(status) {
    return status.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function assetStatusExplanation(asset, mode) {
    if (mode === "checkout") {
        if (asset.status === "AVAILABLE") return "This asset is available and may be checked out.";
        if (asset.status === "CHECKED_OUT") {
            return `Already checked out${asset.current_user ? ` to ${asset.current_user.display_name}` : ""}.`;
        }
        return `This asset cannot be checked out while its status is ${assetStatusLabel(asset.status)}.`;
    }

    if (asset.status === "CHECKED_OUT") return "This asset is currently checked out and may be returned.";
    if (asset.status === "AVAILABLE") return "This asset is already marked available.";
    return `This asset cannot be returned while its status is ${assetStatusLabel(asset.status)}.`;
}

function assetActionAllowed(asset, mode) {
    return mode === "checkout"
        ? asset.status === "AVAILABLE"
        : asset.status === "CHECKED_OUT";
}

function renderAsset(asset, mode, { successMessage = "" } = {}) {
    state.pendingAssets[mode] = asset;
    const result = mode === "checkout" ? elements.checkoutResult : elements.returnResult;
    const allowed = assetActionAllowed(asset, mode);
    const actionText = mode === "checkout" ? "Confirm checkout" : "Confirm return";
    const statusClass = asset.status.toLowerCase().replaceAll("_", "-");
    const borrowerRow = asset.current_user
        ? `<div><dt>Current borrower</dt><dd>${escapeHtml(asset.current_user.display_name)}</dd></div>`
        : "";
    const checkoutTimeRow = asset.checked_out_at
        ? `<div><dt>Checked out</dt><dd>${escapeHtml(formatDateTime(asset.checked_out_at))}</dd></div>`
        : "";
    const serialRow = asset.serial_number
        ? `<div><dt>Serial number</dt><dd>${escapeHtml(asset.serial_number)}</dd></div>`
        : "";
    const successBanner = successMessage
        ? `<div class="asset-success" role="status"><strong>✓ ${escapeHtml(successMessage)}</strong></div>`
        : "";

    result.innerHTML = `
        ${successBanner}
        <article class="asset-card">
            <div class="asset-card-header">
                <div>
                    <p class="eyebrow">${escapeHtml(asset.item.category)}</p>
                    <h3>${escapeHtml(asset.item.name)}</h3>
                    <div class="part-number">${escapeHtml(asset.asset_tag)} · ${escapeHtml(asset.item.part_number)}</div>
                </div>
                <span class="asset-status ${statusClass}">${escapeHtml(assetStatusLabel(asset.status))}</span>
            </div>
            <p class="item-description">${escapeHtml(asset.item.description)}</p>
            <dl class="asset-details">
                <div><dt>Stored location</dt><dd>${escapeHtml(asset.location)}</dd></div>
                ${serialRow}
                ${borrowerRow}
                ${checkoutTimeRow}
            </dl>
            ${asset.notes ? `<p class="asset-note"><strong>Note:</strong> ${escapeHtml(asset.notes)}</p>` : ""}
            <div class="asset-decision ${allowed ? "allowed" : "blocked"}">
                ${escapeHtml(assetStatusExplanation(asset, mode))}
            </div>
            <div class="asset-actions">
                <button type="button" class="button button-primary" data-asset-action="${mode}" data-asset-tag="${escapeHtml(asset.asset_tag)}" ${allowed ? "" : "disabled"}>${actionText}</button>
                <button type="button" class="button button-ghost" data-asset-action="clear" data-asset-mode="${mode}">Scan another asset</button>
            </div>
        </article>`;
}

async function lookupAsset(mode, rawTag) {
    const tag = rawTag.trim().toUpperCase();
    const input = mode === "checkout" ? elements.checkoutTagInput : elements.returnTagInput;
    const message = mode === "checkout" ? elements.checkoutMessage : elements.returnMessage;
    const result = mode === "checkout" ? elements.checkoutResult : elements.returnResult;

    if (!tag) {
        message.textContent = "Enter or scan an asset tag.";
        message.className = "message error-message";
        input.focus();
        return;
    }

    input.value = tag;
    message.textContent = "Looking up asset…";
    message.className = "message";
    result.innerHTML = "";
    state.pendingAssets[mode] = null;

    try {
        const data = await apiFetch(`/api/assets/${encodeURIComponent(tag)}`);
        message.textContent = "";
        renderAsset(data.asset, mode);
    } catch (error) {
        message.textContent = error.message;
        message.className = "message error-message";
        result.innerHTML = `
            <div class="asset-error-card">
                <strong>Asset could not be loaded</strong>
                <span>Check the tag and scan again.</span>
            </div>`;
        input.select();
    }
}

async function performAssetAction(mode, assetTag, button) {
    button.disabled = true;
    const message = mode === "checkout" ? elements.checkoutMessage : elements.returnMessage;
    message.textContent = mode === "checkout" ? "Completing checkout…" : "Completing return…";
    message.className = "message";

    try {
        const data = await apiFetch(`/api/assets/${encodeURIComponent(assetTag)}/${mode}`, {
            method: "POST",
        });
        const successMessage = mode === "checkout"
            ? `${data.asset.asset_tag} checked out to ${state.user.display_name}`
            : `${data.asset.asset_tag} returned and marked available`;
        message.textContent = "";
        renderAsset(data.asset, mode, { successMessage });
        showToast(successMessage);
        await loadEvents();
    } catch (error) {
        message.textContent = error.message;
        message.className = "message error-message";
        button.disabled = false;
        try {
            await lookupAsset(mode, assetTag);
        } catch (_) {
            // lookupAsset already renders its own error state.
        }
    }
}

function resetAssetEntry(mode) {
    clearAssetWorkflow(mode);
    const input = mode === "checkout" ? elements.checkoutTagInput : elements.returnTagInput;
    input.value = "";
    input.focus();
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
            const subject = event.asset_tag
                || event.item_name
                || event.display_name
                || "Terminal";
            let details = "";
            if (event.details?.query) details = `Query: ${event.details.query}`;
            else if (event.details?.ending_reason) details = event.details.ending_reason;
            else if (event.details?.original_borrower_name) details = `Returned for ${event.details.original_borrower_name}`;
            else if (event.item_name && event.asset_tag) details = event.item_name;
            else details = event.part_number || "";
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

function summaryPart(count, singular, plural = `${singular}s`) {
    return `${count} ${count === 1 ? singular : plural}`;
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
        const summary = data.summary;
        elements.completeSummary.textContent = [
            summaryPart(summary.locations_viewed, "location viewed", "locations viewed"),
            summaryPart(summary.probable_usage_records, "probable usage record"),
            summaryPart(summary.assets_checked_out, "asset checked out", "assets checked out"),
            summaryPart(summary.assets_returned, "asset returned", "assets returned"),
        ].join(" · ");
        clearSessionState();
        showScreen("complete");
        setTimeout(() => {
            if (!elements.completeScreen.classList.contains("hidden")) returnToIdle();
        }, 7000);
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
    state.pendingAssets.checkout = null;
    state.pendingAssets.return = null;
}

function returnToIdle() {
    clearSessionState();
    clearAllWorkspaces();
    showScreen("idle");
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

elements.workspaceTabs.forEach((button) => {
    button.addEventListener("click", () => {
        resetIdleTimer();
        switchWorkspace(button.dataset.workspace);
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
    if (button.dataset.action === "open-checkout") switchWorkspace("checkout");
});

elements.checkoutLookupForm.addEventListener("submit", (event) => {
    event.preventDefault();
    resetIdleTimer();
    lookupAsset("checkout", elements.checkoutTagInput.value);
});

elements.returnLookupForm.addEventListener("submit", (event) => {
    event.preventDefault();
    resetIdleTimer();
    lookupAsset("return", elements.returnTagInput.value);
});

document.querySelectorAll("[data-asset-demo]").forEach((button) => {
    button.addEventListener("click", () => {
        const mode = button.dataset.assetMode;
        const input = mode === "checkout" ? elements.checkoutTagInput : elements.returnTagInput;
        input.value = button.dataset.assetDemo;
        lookupAsset(mode, button.dataset.assetDemo);
    });
});

[elements.checkoutResult, elements.returnResult].forEach((container) => {
    container.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-asset-action]");
        if (!button) return;
        resetIdleTimer();
        const action = button.dataset.assetAction;
        if (action === "clear") {
            resetAssetEntry(button.dataset.assetMode);
            return;
        }
        performAssetAction(action, button.dataset.assetTag, button);
    });
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

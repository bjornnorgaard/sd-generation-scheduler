/**
 * Queue tab behaviour: polls the scheduler API, renders GschedCore's HTML into the tab,
 * routes button clicks to API calls, and keeps the tab title's job counter current.
 */
(function () {
    "use strict";

    const Core = globalThis.GschedCore;
    if (!Core) {
        console.error("[Generation Scheduler] gsched_core.js missing — load order broken?");
        return;
    }

    const ROOT_ID = "gsched-root";
    const FAST_MS = 1000; // Queue tab open or a job running
    const SLOW_MS = 4000; // otherwise, just enough to keep the tab counter fresh
    const QUEUE_BUTTON_IDS = ["txt2img_queue", "img2img_queue"];

    let lastHtml = null;
    let lastState = null;
    let timer = null;
    let inFlight = false;
    let started = false;

    // Display-only state (per viewer): which prompts are shown in full (see GschedCore.renderState).
    const view = { flipped: new Set(), expandAll: loadExpandAll() };

    function loadExpandAll() {
        try {
            return localStorage.getItem("gsched_expand_all") === "1";
        } catch (_) {
            return false;
        }
    }

    function saveExpandAll() {
        try {
            localStorage.setItem("gsched_expand_all", view.expandAll ? "1" : "0");
        } catch (_) {
            /* private mode etc.: the toggle just won't persist */
        }
    }
    let attachedId = null; // queue job whose live preview the txt2img / img2img gallery is showing

    function app() {
        return typeof gradioApp === "function" ? gradioApp() : document;
    }

    function getRoot() {
        return app().getElementById(ROOT_ID);
    }

    function isVisible(el) {
        return !!el && el.offsetParent !== null;
    }

    async function call({ method, path, body }) {
        const response = await fetch(path, {
            method,
            headers: { "X-Gsched": "1", "Content-Type": "application/json" },
            body: body ? JSON.stringify(body) : undefined,
            cache: "no-store",
        });
        let data = {};
        try {
            data = await response.json();
        } catch (_) {
            /* non-JSON error page */
        }
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
    }

    function ensureShell(root) {
        if (root.dataset.gschedReady === "1") return;
        root.innerHTML = '<div class="gsched-banner" hidden></div><div class="gsched-body"></div>';
        root.dataset.gschedReady = "1";
        root.addEventListener("click", onClick);
        lastHtml = null;
    }

    function showBanner(root, message) {
        const banner = root.querySelector(".gsched-banner");
        if (!banner) return;
        banner.hidden = !message;
        banner.textContent = message || "";
    }

    function updateTabLabel(state) {
        const label = Core.tabLabel(state);
        app()
            .querySelectorAll("#tabs button[role=tab], #tabs .tab-nav button")
            .forEach((button) => {
                const text = (button.textContent || "").trim();
                if ((text === "Queue" || /^Queue \(\d+\)$/.test(text)) && text !== label) {
                    button.textContent = label;
                }
            });
    }

    // -- State Manager compatibility ---------------------------------------------------------
    //
    // sd-webui-state-manager records the UI state when Generate is pressed and saves it when a new
    // image shows up in the gallery. Queue results arrive without Generate being pressed, so it
    // would find no state and pop up "No previous state found.". If it is installed we therefore
    // snapshot its state when Queue is pressed and hand it back when that job's images arrive.
    // Without State Manager none of this does anything.

    const stateFifo = []; // {tab, taskId, token} per job shown in a gallery, oldest first
    const snapshots = new Map(); // token -> Promise<state | null>
    const MAX_SNAPSHOTS = 100;
    const SKIP_WINDOW_MS = 5000;
    const SNAPSHOT_TIMEOUT_MS = 3000;
    let skipSaveUntil = 0;

    function stateManager() {
        const sm = window.stateManager;
        const usable = sm && typeof sm.getCurrentState === "function" && typeof sm.saveLastUsedState === "function";
        return usable ? sm : null;
    }

    /**
     * Called (and awaited) by the Queue button's click script; resolves to the token stored on the
     * job. Waits for State Manager's snapshot so it reflects the controls as they were at the
     * click, but never longer than SNAPSHOT_TIMEOUT_MS.
     */
    window.gschedNoteQueuePress = async function (tab) {
        const token = Core.makeToken();
        const sm = stateManager();
        if (!sm) return token;

        let snapshot = null;
        try {
            const timeout = new Promise((resolve) => setTimeout(() => resolve(null), SNAPSHOT_TIMEOUT_MS));
            snapshot = await Promise.race([Promise.resolve(sm.getCurrentState(tab)), timeout]);
        } catch (error) {
            console.warn("[Generation Scheduler] State Manager snapshot failed:", error);
        }
        snapshots.set(token, Promise.resolve(snapshot || null));
        while (snapshots.size > MAX_SNAPSHOTS) snapshots.delete(snapshots.keys().next().value);
        return token;
    };

    // Only when we have no snapshot for a delivered job (page reloaded, queued from another window)
    // is State Manager's save skipped — with a console warning instead of its alert. Any other
    // situation still reaches its original code, errors included.
    function installSaveGuard(sm) {
        if (sm.gschedGuard) return;
        const original = sm.saveLastUsedState;
        sm.saveLastUsedState = function () {
            if (!sm.lastUsedState && Date.now() < skipSaveUntil) {
                skipSaveUntil = 0;
                return undefined;
            }
            return original.apply(this, arguments);
        };
        sm.gschedGuard = true;
    }

    /** Called by the hidden restore event's follow-up, right after the job's images hit the gallery. */
    window.gschedDelivered = async function (tab) {
        const entry = Core.takeStateEntry(stateFifo, tab);
        const sm = stateManager();
        if (!entry || !sm) return [];
        installSaveGuard(sm);
        const snapshot = entry.token ? await snapshots.get(entry.token) : null;
        if (snapshot) {
            sm.lastUsedState = snapshot;
        } else {
            sm.lastUsedState = null; // never reuse the previous job's state for this one
            skipSaveUntil = Date.now() + SKIP_WINDOW_MS;
            console.warn(
                `[Generation Scheduler] No State Manager snapshot for ${entry.taskId} (queued before a page reload ` +
                    "or from another window); it won't be added to State Manager's history.",
            );
        }
        return [];
    };

    /** Is a manual Generate running in this tab? (Forge shows its Interrupt button then.) */
    function isGenerateBusy(tab) {
        const interrupt = app().getElementById(`${tab}_interrupt`);
        return !!interrupt && interrupt.style.display === "block";
    }

    /**
     * Show the running queue job in the tab's own gallery, the way a manual Generate does:
     * Forge's progress bar + live preview, Interrupt / Skip buttons, and — once the job is done —
     * its images (delivered by the hidden restore button, see wiring.py).
     */
    function showJobInGallery(tab, taskId, token) {
        const container = app().getElementById(`${tab}_gallery_container`);
        const gallery = app().getElementById(`${tab}_gallery`);
        if (!container || typeof requestProgress !== "function") return;

        attachedId = taskId;
        const setButtons = (show) => {
            if (typeof showSubmitButtons === "function") showSubmitButtons(tab, show);
        };
        setButtons(false);
        requestProgress(taskId, container, gallery, () => setButtons(true), null, 0);

        window.gschedRestoreId = window.gschedRestoreId || {};
        window.gschedRestoreId[tab] = taskId;
        const restore = app().getElementById(`${tab}_gsched_restore`);
        if (restore) {
            stateFifo.push({ tab, taskId, token });
            restore.click();
        }
    }

    function updateGalleryPreview(state) {
        const plan = Core.previewPlan(state, {
            attachedId,
            busyTabs: { txt2img: isGenerateBusy("txt2img"), img2img: isGenerateBusy("img2img") },
        });
        if (plan.clear) attachedId = null;
        if (plan.attach) showJobInGallery(plan.attach.tab, plan.attach.taskId, plan.attach.token);
    }

    function render(state) {
        const root = getRoot();
        if (!root) return;
        ensureShell(root);
        const html = Core.renderState(state, view);
        if (html !== lastHtml) {
            root.querySelector(".gsched-body").innerHTML = html;
            lastHtml = html;
        }
    }

    async function refresh() {
        if (inFlight) return;
        inFlight = true;
        const root = getRoot();
        try {
            lastState = await call({ method: "GET", path: `${Core.API}/state` });
            if (root) {
                render(lastState);
                showBanner(root, "");
            }
            updateTabLabel(lastState);
            updateGalleryPreview(lastState);
        } catch (error) {
            if (root) {
                ensureShell(root);
                showBanner(root, `Could not reach the queue: ${error.message}`);
            }
        } finally {
            inFlight = false;
        }
    }

    function schedule() {
        clearTimeout(timer);
        const busy = lastState && (lastState.running || (lastState.pending || []).length > 0);
        const delay = isVisible(getRoot()) || busy ? FAST_MS : SLOW_MS;
        timer = setTimeout(async () => {
            await refresh();
            schedule();
        }, delay);
    }

    /** Purely client-side actions (no request): prompt expand / collapse. Returns true if handled. */
    function toggleView(action, id) {
        if (action === "toggle-prompt") {
            const key = Number(id);
            if (view.flipped.has(key)) view.flipped.delete(key);
            else view.flipped.add(key);
        } else if (action === "toggle-all-prompts") {
            view.expandAll = !view.expandAll;
            view.flipped.clear();
            saveExpandAll();
        } else {
            return false;
        }
        if (lastState) render(lastState);
        return true;
    }

    async function onClick(event) {
        const button = event.target.closest("[data-gsched-action]");
        if (!button || button.disabled) return;
        if (toggleView(button.dataset.gschedAction, button.dataset.gschedId)) return;
        const request = Core.buildRequest(button.dataset.gschedAction, button.dataset.gschedId);
        if (!request) return;
        if (request.confirm && !window.confirm(request.confirm)) return;
        button.disabled = true;
        const root = getRoot();
        try {
            await call(request);
            if (root) showBanner(root, "");
        } catch (error) {
            if (root) showBanner(root, error.message);
        }
        await refresh();
    }

    function onQueueButtonClick(event) {
        const target = event.target.closest && event.target.closest("button");
        if (target && QUEUE_BUTTON_IDS.includes(target.id)) {
            // The job is saved server-side a moment after the click.
            setTimeout(refresh, 800);
            setTimeout(refresh, 2500);
        }
    }

    function overrideEnabled() {
        try {
            return typeof opts !== "undefined" && opts.gsched_override_ctrl_enter !== false;
        } catch (_) {
            return true;
        }
    }

    // Ctrl/Cmd + Enter queues instead of Forge's generate / interrupt-and-restart. Capture phase +
    // stopImmediatePropagation so Forge's own handler (on document, bubble phase) never sees it.
    function onKeyDown(event) {
        if (!Core.isQueueShortcut(event, overrideEnabled())) return;
        const content = typeof get_uiCurrentTabContent === "function" ? get_uiCurrentTabContent() : null;
        const button = content && content.querySelector("button[id$=_queue]");
        if (!button) return; // not on txt2img / img2img: leave the key alone
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!event.repeat) button.click(); // a held key must not flood the queue
    }

    window.addEventListener("keydown", onKeyDown, true);

    function start() {
        if (started) return;
        if (!getRoot()) return; // UI not built yet; onAfterUiUpdate will call again
        started = true;
        app().addEventListener("click", onQueueButtonClick, true);
        refresh().then(schedule);
    }

    if (typeof onUiLoaded === "function") onUiLoaded(start);
    if (typeof onAfterUiUpdate === "function") onAfterUiUpdate(start);
})();

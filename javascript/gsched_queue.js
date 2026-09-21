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

    function render(state) {
        const root = getRoot();
        if (!root) return;
        ensureShell(root);
        const html = Core.renderState(state);
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

    async function onClick(event) {
        const button = event.target.closest("[data-gsched-action]");
        if (!button || button.disabled) return;
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

// Loading feedback shared by every page:
//   0. Page skeleton    - a placeholder layout covers the page until it
//                         has loaded, then fades away.
//   1. Image skeletons  - images/videos shimmer until they have loaded.
//   2. Progress bar     - a thin bar at the top while the next page loads.
//   3. Busy buttons     - a form's submit button shows "Saving…" etc.
// Runs in <head> (not deferred) so the skeleton is on before first paint.
(() => {
    // ---------- 0. Page skeleton ----------

    const root = document.documentElement;
    const SKELETON_MIN_MS = 450;   // long enough to see, short enough not to annoy
    const SKELETON_MAX_MS = 5000;  // never hold the page longer than this

    // Without this class (e.g. JavaScript off) the skeleton is never shown.
    root.classList.add("is-page-loading");

    let skeletonHidden = false;

    function hideSkeleton() {
        if (skeletonHidden) {
            return;
        }
        skeletonHidden = true;
        root.classList.remove("is-page-loading");
        root.classList.add("is-page-ready");

        const skeleton = document.querySelector(".page-skeleton");
        if (skeleton) {
            skeleton.addEventListener("transitionend", () => skeleton.remove(), { once: true });
            // Fallback when transitions are off (reduced motion).
            setTimeout(() => skeleton.remove(), 600);
        }
    }

    window.addEventListener("load", () => {
        setTimeout(hideSkeleton, Math.max(0, SKELETON_MIN_MS - performance.now()));
    });
    setTimeout(hideSkeleton, SKELETON_MAX_MS);

    // ---------- 1. Image skeletons ----------

    const MEDIA = "img, video";

    function isReady(media) {
        if (media.tagName === "VIDEO") {
            return media.readyState >= 2; // HAVE_CURRENT_DATA
        }
        return media.complete && media.naturalWidth > 0;
    }

    function watch(media) {
        if (media.dataset.skeleton === "done" || isReady(media)) {
            return;
        }

        media.classList.add("is-loading");
        const finish = () => {
            media.classList.remove("is-loading");
            media.dataset.skeleton = "done";
        };
        const doneEvent = media.tagName === "VIDEO" ? "loadeddata" : "load";
        media.addEventListener(doneEvent, finish, { once: true });
        media.addEventListener("error", finish, { once: true });
    }

    function watchWithin(node) {
        if (node.nodeType !== Node.ELEMENT_NODE) {
            return;
        }
        if (node.matches(MEDIA)) {
            watch(node);
        }
        node.querySelectorAll(MEDIA).forEach(watch);
    }

    // ---------- 2. Progress bar ----------

    let bar = null;

    function startProgress() {
        if (!bar) {
            bar = document.createElement("div");
            bar.className = "page-progress";
            bar.setAttribute("aria-hidden", "true");
            document.body.append(bar);
        }
        // Restart from zero, then let the CSS ease it towards the end.
        bar.classList.remove("is-active");
        void bar.offsetWidth;
        bar.classList.add("is-active");
    }

    function stopProgress() {
        if (bar) {
            bar.classList.remove("is-active");
        }
    }

    // Only links that load a different page show the bar.
    function opensNewPage(link, event) {
        if (
            event.defaultPrevented ||
            event.button !== 0 ||
            event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
            link.target === "_blank" ||
            link.hasAttribute("download")
        ) {
            return false;
        }

        const url = new URL(link.href, location.href);
        if (url.origin !== location.origin || !/^https?:$/.test(url.protocol)) {
            return false;
        }

        // "#section" on the same page just scrolls.
        const samePage = url.pathname === location.pathname && url.search === location.search;
        return !(samePage && url.hash);
    }

    // ---------- 3. Busy buttons ----------

    function busyLabel(button) {
        if (button.dataset.busyLabel) {
            return button.dataset.busyLabel;
        }
        const text = button.textContent.trim().toLowerCase();
        if (text.includes("delete")) {
            return "Deleting…";
        }
        if (text.includes("send")) {
            return "Sending…";
        }
        if (text.includes("log in")) {
            return "Logging in…";
        }
        return "Saving…";
    }

    function setBusy(button) {
        button.dataset.originalHtml = button.innerHTML;
        // Keep the width so the layout doesn't jump.
        button.style.minWidth = `${button.offsetWidth}px`;
        button.innerHTML =
            `<span class="busy-spinner" aria-hidden="true"></span>${busyLabel(button)}`;
        button.classList.add("is-busy");
        button.setAttribute("aria-busy", "true");
        button.disabled = true;
    }

    function resetBusyButtons() {
        document.querySelectorAll("button.is-busy").forEach(button => {
            button.innerHTML = button.dataset.originalHtml;
            button.style.minWidth = "";
            button.classList.remove("is-busy");
            button.removeAttribute("aria-busy");
            button.disabled = false;
        });
    }

    // ---------- Wiring ----------

    document.addEventListener("DOMContentLoaded", () => {
        watchWithin(document.body);

        // Images added later (project viewer, upload previews).
        new MutationObserver(records => {
            records.forEach(record => record.addedNodes.forEach(watchWithin));
        }).observe(document.body, { childList: true, subtree: true });

        document.addEventListener("click", event => {
            const link = event.target.closest("a[href]");
            if (link && opensNewPage(link, event)) {
                startProgress();
            }
        });

        // Runs after inline handlers such as the delete confirm(), so a
        // cancelled confirm leaves everything as it was.
        document.addEventListener("submit", event => {
            if (event.defaultPrevented) {
                return;
            }
            const form = event.target;
            const button = event.submitter || form.querySelector('[type="submit"]');

            startProgress();
            if (button && button.tagName === "BUTTON") {
                // Disable after the browser has taken the form data.
                setTimeout(() => setBusy(button), 0);
            }
        });
    });

    // Coming back with the Back button can show the old page as it was left.
    window.addEventListener("pageshow", event => {
        if (event.persisted) {
            hideSkeleton();
            stopProgress();
            resetBusyButtons();
        }
    });
})();

// Live updates for the portfolio page.
//
// When the admin changes the profile, skills, projects or certificates,
// open portfolio pages update themselves without a reload:
//   * every few seconds while the tab is visible, the page asks
//     /content-version whether anything changed;
//   * the admin's own browser tabs hear about a save instantly through
//     a BroadcastChannel (see notifyContentChanged in dashboard.html).
// Only sections whose content changed are replaced; scroll position,
// the contact form and an open viewer are left alone.
(() => {
    const CHECK_EVERY_MS = 10000;
    const SECTIONS = ["home", "about", "projects", "certificates"];
    const versionMeta = document.querySelector('meta[name="content-version"]');
    if (!versionMeta) {
        return;
    }

    let checking = false;
    let pendingUpdate = false;

    function anyViewerOpen() {
        return Boolean(document.querySelector("dialog[open]"));
    }

    function showToast(text) {
        let toast = document.querySelector(".live-update-toast");
        if (!toast) {
            toast = document.createElement("div");
            toast.className = "live-update-toast";
            toast.setAttribute("role", "status");
            toast.setAttribute("aria-live", "polite");
            document.body.append(toast);
        }
        toast.textContent = text;
        toast.classList.remove("is-visible");
        void toast.offsetWidth;
        toast.classList.add("is-visible");
        clearTimeout(showToast.timer);
        showToast.timer = setTimeout(() => toast.classList.remove("is-visible"), 3200);
    }

    async function applyNewPage() {
        const response = await fetch("/", { cache: "no-store", credentials: "same-origin" });
        if (!response.ok) {
            return;
        }
        const fresh = new DOMParser().parseFromString(await response.text(), "text/html");

        let changed = 0;
        SECTIONS.forEach(id => {
            const current = document.getElementById(id);
            const incoming = fresh.getElementById(id);
            // Keep the element itself (the nav highlight watches it);
            // swap only its contents, and only if they differ.
            if (current && incoming && current.innerHTML !== incoming.innerHTML) {
                current.innerHTML = incoming.innerHTML;
                current.classList.add("just-updated");
                setTimeout(() => current.classList.remove("just-updated"), 1600);
                changed++;
            }
        });

        const data = document.getElementById("project-data");
        const freshData = fresh.getElementById("project-data");
        if (data && freshData) {
            data.textContent = freshData.textContent;
        }

        const freshVersion = fresh.querySelector('meta[name="content-version"]');
        if (freshVersion) {
            versionMeta.content = freshVersion.content;
        }

        if (changed) {
            document.dispatchEvent(new CustomEvent("portfolio:content-updated"));
            showToast("Portfolio updated");
        }
    }

    async function check() {
        if (checking || document.visibilityState !== "visible") {
            return;
        }
        if (anyViewerOpen()) {
            pendingUpdate = true;
            return;
        }

        checking = true;
        try {
            const response = await fetch("/content-version", { cache: "no-store" });
            if (response.ok) {
                const { version } = await response.json();
                if (version && version !== versionMeta.content) {
                    await applyNewPage();
                }
            }
        } catch (error) {
            // Offline or the server is busy: try again on the next check.
        } finally {
            checking = false;
        }
    }

    setInterval(check, CHECK_EVERY_MS);

    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
            check();
        }
    });

    // Apply an update that waited for a viewer to close.
    document.addEventListener("close", event => {
        if (event.target.tagName === "DIALOG" && pendingUpdate) {
            pendingUpdate = false;
            setTimeout(check, 400);
        }
    }, true);

    if ("BroadcastChannel" in window) {
        new BroadcastChannel("portfolio-content").addEventListener("message", check);
    }
})();

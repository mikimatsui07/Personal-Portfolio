// Live updates for the admin dashboard.
//
// New contact messages (and changes made in other admin tabs) appear
// without a reload:
//   * every 3 s while the dashboard is visible (15 s in a background tab)
//     it asks /dashboard-version whether anything changed;
//   * a message sent from this same browser is announced instantly
//     through a BroadcastChannel (index.html posts it).
// Only the parts that changed are replaced; new messages are highlighted,
// and a background tab shows the count in its title, e.g. "(2) Dashboard".
(() => {
    const VISIBLE_MS = 3000;
    const HIDDEN_MS = 15000;
    const PARTS = [".dashboard-stats", "#profile", "#projects", "#certificates", "#messages"];
    const versionMeta = document.querySelector('meta[name="dashboard-version"]');
    if (!versionMeta) {
        return;
    }

    const baseTitle = document.title;
    let unseen = 0;
    let timer = null;
    let checking = false;
    let stopped = false;

    function messageIds(root) {
        return new Set(
            Array.from(root.querySelectorAll(".message-card[data-message-id]"))
                .map(card => card.dataset.messageId)
        );
    }

    function updateTitle() {
        document.title = unseen ? `(${unseen}) ${baseTitle}` : baseTitle;
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
        showToast.timer = setTimeout(() => toast.classList.remove("is-visible"), 4000);
    }

    async function applyNewDashboard() {
        const response = await fetch("/dashboard", { cache: "no-store", credentials: "same-origin" });
        if (!response.ok || new URL(response.url).pathname !== "/dashboard") {
            stopped = true; // logged out
            return;
        }
        const fresh = new DOMParser().parseFromString(await response.text(), "text/html");
        const before = messageIds(document);

        PARTS.forEach(selector => {
            const current = document.querySelector(selector);
            const incoming = fresh.querySelector(selector);
            if (current && incoming && current.innerHTML !== incoming.innerHTML) {
                current.innerHTML = incoming.innerHTML;
            }
        });

        const freshVersion = fresh.querySelector('meta[name="dashboard-version"]');
        if (freshVersion) {
            versionMeta.content = freshVersion.content;
        }

        const added = Array.from(document.querySelectorAll(".message-card[data-message-id]"))
            .filter(card => !before.has(card.dataset.messageId));

        if (added.length) {
            added.forEach(card => card.classList.add("is-new-message"));
            showToast(
                added.length === 1
                    ? `New message from ${added[0].dataset.sender}`
                    : `${added.length} new messages`
            );
            if (document.visibilityState !== "visible") {
                unseen += added.length;
                updateTitle();
            }
        } else {
            showToast("Dashboard updated");
        }
    }

    async function check() {
        if (checking || stopped) {
            return;
        }
        checking = true;
        try {
            const response = await fetch("/dashboard-version", { cache: "no-store", credentials: "same-origin" });
            if (response.status === 401) {
                stopped = true;
            } else if (response.ok) {
                const { version } = await response.json();
                if (version && version !== versionMeta.content) {
                    await applyNewDashboard();
                }
            }
        } catch (error) {
            // Offline or the server is busy: try again on the next check.
        } finally {
            checking = false;
            schedule();
        }
    }

    function schedule() {
        clearTimeout(timer);
        if (!stopped) {
            timer = setTimeout(check, document.visibilityState === "visible" ? VISIBLE_MS : HIDDEN_MS);
        }
    }

    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
            unseen = 0;
            updateTitle();
            check();
        }
    });

    if ("BroadcastChannel" in window) {
        new BroadcastChannel("portfolio-messages").addEventListener("message", check);
    }

    schedule();
})();

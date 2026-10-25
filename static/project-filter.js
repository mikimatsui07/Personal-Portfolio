// Category filter above the projects on the home page.
// "All" shows every project; a category button shows only its projects.
// The choice is kept when live-update.js refreshes the projects.
(() => {
    let current = "all";

    function status() {
        let region = document.getElementById("project-filter-status");
        if (!region) {
            region = document.createElement("p");
            region.id = "project-filter-status";
            region.className = "visually-hidden";
            region.setAttribute("aria-live", "polite");
            const grid = document.getElementById("project-grid");
            if (grid) {
                grid.before(region);
            }
        }
        return region;
    }

    function apply(filter, announce) {
        const buttons = Array.from(document.querySelectorAll(".project-filter-button"));
        // A category that no longer has projects falls back to "All".
        if (!buttons.some(button => button.dataset.filter === filter)) {
            filter = "all";
        }
        current = filter;

        buttons.forEach(button => {
            button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
        });

        let shown = 0;
        document.querySelectorAll(".portfolio-project-card").forEach(card => {
            const match = filter === "all" || card.dataset.category === filter;
            if (match && card.hidden) {
                card.hidden = false;
                card.classList.remove("filter-in");
                void card.offsetWidth;
                card.classList.add("filter-in");
            } else if (!match) {
                card.hidden = true;
            }
            if (match) {
                shown++;
            }
        });

        if (announce) {
            const active = buttons.find(button => button.dataset.filter === filter);
            const name = filter === "all"
                ? ""
                : `${active.firstChild.textContent.trim()} `;
            status().textContent = `Showing ${shown} ${name}project${shown === 1 ? "" : "s"}`;
        }
    }

    document.addEventListener("click", event => {
        const button = event.target.closest(".project-filter-button");
        if (button) {
            apply(button.dataset.filter, true);
        }
    });

    document.addEventListener("portfolio:content-updated", () => apply(current, false));
})();

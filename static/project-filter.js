// Filters above the projects on the home page: one row of categories and
// one of technologies. A card shows when it matches both choices ("All"
// matches everything). The choices are kept when live-update.js refreshes
// the projects.
(() => {
    const chosen = { category: "all", technology: "all" };

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

    function buttonsOf(group) {
        return Array.from(document.querySelectorAll(
            `[data-filter-group="${group}"] .project-filter-button`
        ));
    }

    function labelOf(button) {
        return button.firstChild.textContent.trim();
    }

    function matches(card) {
        const technologies = (card.dataset.technologies || "").split(" ");
        return (chosen.category === "all" || card.dataset.category === chosen.category)
            && (chosen.technology === "all" || technologies.includes(chosen.technology));
    }

    function apply(announce) {
        Object.keys(chosen).forEach(group => {
            const buttons = buttonsOf(group);
            // A choice that no longer has projects falls back to "All".
            if (!buttons.some(button => button.dataset.filter === chosen[group])) {
                chosen[group] = "all";
            }
            buttons.forEach(button => {
                button.setAttribute("aria-pressed", String(button.dataset.filter === chosen[group]));
            });
        });

        let shown = 0;
        document.querySelectorAll(".portfolio-project-card").forEach(card => {
            const match = matches(card);
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

        const empty = document.getElementById("project-filter-empty");
        if (empty) {
            empty.hidden = shown > 0;
        }

        if (announce) {
            const names = Object.keys(chosen)
                .filter(group => chosen[group] !== "all")
                .map(group => labelOf(buttonsOf(group).find(button => button.dataset.filter === chosen[group])));
            status().textContent = `Showing ${shown} project${shown === 1 ? "" : "s"}` +
                (names.length ? `: ${names.join(", ")}` : "");
        }
    }

    document.addEventListener("click", event => {
        const button = event.target.closest(".project-filter-button");
        const group = button && button.closest("[data-filter-group]");
        if (group) {
            chosen[group.dataset.filterGroup] = button.dataset.filter;
            apply(true);
        } else if (event.target.closest(".project-filter-reset")) {
            chosen.category = "all";
            chosen.technology = "all";
            apply(true);
        }
    });

    document.addEventListener("portfolio:content-updated", () => apply(false));
})();

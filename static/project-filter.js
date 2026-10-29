// Filter buttons on the home page, for the projects and the certificates.
//
// A section marked data-filter-scope holds one or more rows of buttons
// (data-filter-group="category" or "technology") and the cards they
// filter (data-filter-item, with data-category / data-technologies).
// A card shows when it matches the choice in every row ("All" matches
// everything). The choices are kept when live-update.js refreshes the page.
(() => {
    // Section id -> {group: chosen key}
    const chosen = {};

    const matchers = {
        category: (card, key) => card.dataset.category === key,
        technology: (card, key) => (card.dataset.technologies || "").split(" ").includes(key)
    };

    function choicesOf(scope) {
        if (!chosen[scope.id]) {
            chosen[scope.id] = {};
        }
        return chosen[scope.id];
    }

    function status(scope) {
        const id = `${scope.id}-filter-status`;
        let region = document.getElementById(id);
        if (!region) {
            region = document.createElement("p");
            region.id = id;
            region.className = "visually-hidden";
            region.setAttribute("aria-live", "polite");
            const firstCard = scope.querySelector("[data-filter-item]");
            if (firstCard) {
                firstCard.parentElement.before(region);
            }
        }
        return region;
    }

    function labelOf(button) {
        return button.firstChild.textContent.trim();
    }

    function apply(scope, announce) {
        const choices = choicesOf(scope);
        const groups = Array.from(scope.querySelectorAll("[data-filter-group]"));

        groups.forEach(group => {
            const name = group.dataset.filterGroup;
            const buttons = Array.from(group.querySelectorAll(".project-filter-button"));
            // A choice that no longer has items falls back to "All".
            if (!buttons.some(button => button.dataset.filter === choices[name])) {
                choices[name] = "all";
            }
            buttons.forEach(button => {
                button.setAttribute("aria-pressed", String(button.dataset.filter === choices[name]));
            });
        });

        let shown = 0;
        scope.querySelectorAll("[data-filter-item]").forEach(card => {
            const match = groups.every(group => {
                const name = group.dataset.filterGroup;
                return choices[name] === "all" || matchers[name](card, choices[name]);
            });
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

        const empty = scope.querySelector(".project-filter-empty");
        if (empty) {
            empty.hidden = shown > 0;
        }

        if (announce) {
            const names = groups
                .filter(group => choices[group.dataset.filterGroup] !== "all")
                .map(group => labelOf(group.querySelector('.project-filter-button[aria-pressed="true"]')));
            const noun = scope.id === "certificates" ? "certificate" : "project";
            status(scope).textContent = `Showing ${shown} ${noun}${shown === 1 ? "" : "s"}` +
                (names.length ? `: ${names.join(", ")}` : "");
        }
    }

    document.addEventListener("click", event => {
        const scope = event.target.closest("[data-filter-scope]");
        if (!scope) {
            return;
        }
        const button = event.target.closest(".project-filter-button");
        const group = button && button.closest("[data-filter-group]");
        if (group) {
            choicesOf(scope)[group.dataset.filterGroup] = button.dataset.filter;
            apply(scope, true);
        } else if (event.target.closest(".project-filter-reset")) {
            chosen[scope.id] = {};
            apply(scope, true);
        }
    });

    document.addEventListener("portfolio:content-updated", () => {
        document.querySelectorAll("[data-filter-scope]").forEach(scope => apply(scope, false));
    });
})();

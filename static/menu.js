// Lets the CSS collapse menus only when this script is running,
// so the links stay visible if JavaScript fails to load.
document.documentElement.classList.add("has-menu-js");

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-menu-toggle]").forEach(toggle => {
        const container = toggle.closest("[data-menu]");
        const menu = document.getElementById(toggle.getAttribute("aria-controls"));
        if (!container || !menu) {
            return;
        }

        const setOpen = open => {
            container.classList.toggle("menu-open", open);
            toggle.setAttribute("aria-expanded", String(open));
            toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
        };

        toggle.addEventListener("click", () => {
            setOpen(!container.classList.contains("menu-open"));
        });

        menu.querySelectorAll("a").forEach(link => {
            link.addEventListener("click", () => setOpen(false));
        });

        document.addEventListener("keydown", event => {
            if (event.key === "Escape" && container.classList.contains("menu-open")) {
                setOpen(false);
                toggle.focus();
            }
        });

        document.addEventListener("click", event => {
            if (!container.contains(event.target)) {
                setOpen(false);
            }
        });
    });
});

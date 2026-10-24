// Light / dark / system theme. Runs in <head> so the theme is applied
// before the page paints (no white flash when dark mode is on).
(() => {
    const STORAGE_KEY = "portfolio-theme";
    const CHOICES = ["system", "light", "dark"];
    const root = document.documentElement;
    const systemDark = window.matchMedia("(prefers-color-scheme: dark)");

    function savedChoice() {
        try {
            const choice = localStorage.getItem(STORAGE_KEY);
            return CHOICES.includes(choice) ? choice : "system";
        } catch (error) {
            return "system";
        }
    }

    function resolve(choice) {
        if (choice === "system") {
            return systemDark.matches ? "dark" : "light";
        }
        return choice;
    }

    function syncSwitchers(choice) {
        document.querySelectorAll("[data-theme-option]").forEach(option => {
            option.setAttribute(
                "aria-pressed",
                String(option.dataset.themeOption === choice)
            );
        });
    }

    // Phone address-bar color. The <meta> tags already follow the device;
    // this keeps them matching when Light or Dark is picked on the site.
    const BAR_COLORS = { light: "#ffffff", dark: "#15111b" };

    function syncBarColor(theme) {
        document.querySelectorAll('meta[name="theme-color"]').forEach(meta => {
            if (!meta.dataset.deviceMedia) {
                meta.dataset.deviceMedia = meta.getAttribute("media") || "";
            }
            if (savedChoice() === "system") {
                meta.setAttribute("media", meta.dataset.deviceMedia);
                meta.setAttribute(
                    "content",
                    meta.dataset.deviceMedia.includes("dark") ? BAR_COLORS.dark : BAR_COLORS.light
                );
            } else {
                meta.removeAttribute("media");
                meta.setAttribute("content", BAR_COLORS[theme]);
            }
        });
    }

    // Tab icon: browsers do not redraw it when the theme changes, so swap
    // in the matching file. A new <link> element makes them pick it up.
    let shownIcon = null;

    function syncFavicon(theme) {
        const icon = document.querySelector('link[rel="icon"][type="image/svg+xml"]');
        if (!icon || shownIcon === theme) {
            return;
        }
        const folder = icon.href.slice(0, icon.href.lastIndexOf("/") + 1);
        const fresh = icon.cloneNode();
        fresh.href = `${folder}favicon-${theme}.svg`;
        icon.replaceWith(fresh);
        shownIcon = theme;
    }

    function applyChoice(choice) {
        root.dataset.theme = resolve(choice);
        root.dataset.themeChoice = choice;
        syncSwitchers(choice);
        syncBarColor(root.dataset.theme);
        syncFavicon(root.dataset.theme);
    }

    applyChoice(savedChoice());

    // "System" follows the device setting live, e.g. at sunset.
    systemDark.addEventListener("change", () => {
        if (savedChoice() === "system") {
            applyChoice("system");
        }
    });

    // A theme picked in another tab of this site applies here too.
    window.addEventListener("storage", event => {
        if (event.key === STORAGE_KEY || event.key === null) {
            applyChoice(savedChoice());
        }
    });

    document.addEventListener("DOMContentLoaded", () => {
        syncSwitchers(savedChoice());
        syncBarColor(root.dataset.theme);
        syncFavicon(root.dataset.theme);

        document.querySelectorAll("[data-theme-option]").forEach(option => {
            option.addEventListener("click", () => {
                const choice = option.dataset.themeOption;

                try {
                    if (choice === "system") {
                        localStorage.removeItem(STORAGE_KEY);
                    } else {
                        localStorage.setItem(STORAGE_KEY, choice);
                    }
                } catch (error) {
                    // Storage blocked: the theme still changes for this page.
                }

                applyChoice(choice);
            });
        });
    });
})();

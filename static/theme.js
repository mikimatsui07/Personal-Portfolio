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

    function applyChoice(choice) {
        root.dataset.theme = resolve(choice);
        root.dataset.themeChoice = choice;
        syncSwitchers(choice);
    }

    applyChoice(savedChoice());

    // "System" follows the device setting live, e.g. at sunset.
    systemDark.addEventListener("change", () => {
        if (savedChoice() === "system") {
            applyChoice("system");
        }
    });

    document.addEventListener("DOMContentLoaded", () => {
        syncSwitchers(savedChoice());

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

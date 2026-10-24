// "Leave without saving?" for the admin edit pages.
//
// A form marked data-unsaved-guard="your profile" remembers how it looked
// when the page opened. If it has changed, leaving through a link (Cancel,
// the sidebar, View Site, Logout) first asks in a dialog; reloading or
// closing the tab gets the browser's own warning. Saving never asks.
(() => {
    let form = null;
    let saved = null;
    let leaving = false;

    function snapshot() {
        return Array.from(new FormData(form).entries())
            .map(([name, value]) =>
                `${name}=${value instanceof File ? `${value.name}:${value.size}` : value}`
            )
            .join("&");
    }

    function hasChanges() {
        return Boolean(form) && saved !== null && !leaving && snapshot() !== saved;
    }

    function buildDialog() {
        const dialog = document.createElement("dialog");
        dialog.className = "leave-dialog";
        dialog.setAttribute("aria-labelledby", "leave-dialog-title");
        dialog.setAttribute("aria-describedby", "leave-dialog-text");
        dialog.innerHTML = `
            <div class="leave-dialog-icon" aria-hidden="true">!</div>
            <h2 id="leave-dialog-title">Leave without saving?</h2>
            <p id="leave-dialog-text"></p>
            <div class="leave-dialog-actions">
                <button type="button" class="leave-dialog-stay">Keep editing</button>
                <button type="button" class="leave-dialog-leave">Leave without saving</button>
            </div>`;
        document.body.append(dialog);
        return dialog;
    }

    function askToLeave(destination) {
        const dialog = document.querySelector(".leave-dialog") || buildDialog();
        dialog.querySelector("#leave-dialog-text").textContent =
            `Your changes to ${form.dataset.unsavedGuard} haven't been saved. ` +
            "If you leave now, they'll be lost.";

        const stay = dialog.querySelector(".leave-dialog-stay");
        const leave = dialog.querySelector(".leave-dialog-leave");

        stay.onclick = () => dialog.close();
        leave.onclick = () => {
            leaving = true;
            dialog.close();
            window.location.href = destination;
        };
        // Clicking the dimmed background keeps editing.
        dialog.onclick = event => {
            if (event.target === dialog) {
                dialog.close();
            }
        };

        dialog.showModal();
        stay.focus();
    }

    document.addEventListener("DOMContentLoaded", () => {
        form = document.querySelector("form[data-unsaved-guard]");
        if (!form) {
            return;
        }

        // Take the "before" picture once the page's own scripts have filled
        // in hidden fields (skills, technologies).
        const remember = () => { saved = snapshot(); };
        if (document.readyState === "complete") {
            setTimeout(remember, 0);
        } else {
            window.addEventListener("load", () => setTimeout(remember, 0));
        }

        document.addEventListener("click", event => {
            const link = event.target.closest("a[href]");
            if (
                !link ||
                event.defaultPrevented ||
                event.button !== 0 ||
                event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
                link.target === "_blank" ||
                link.getAttribute("href").startsWith("#") ||
                !hasChanges()
            ) {
                return;
            }
            event.preventDefault();
            // Runs before loading.js, so its progress bar does not start.
            event.stopImmediatePropagation();
            askToLeave(link.href);
        }, true);

        // Saving is not leaving. (If direct-upload.js took over the submit,
        // it calls unsavedGuard.allowLeave() once the files are uploaded.)
        form.addEventListener("submit", event => {
            if (!event.defaultPrevented) {
                leaving = true;
            }
        });

        window.addEventListener("beforeunload", event => {
            if (hasChanges()) {
                event.preventDefault();
                event.returnValue = "";
            }
        });

        // Coming back with the Back button: the form is editable again.
        window.addEventListener("pageshow", event => {
            if (event.persisted) {
                leaving = false;
            }
        });
    });

    // direct-upload.js submits the form itself after uploading.
    window.unsavedGuard = {
        allowLeave() {
            leaving = true;
        }
    };
})();

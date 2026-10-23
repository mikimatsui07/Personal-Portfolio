// Direct uploads to Supabase Storage (only loaded when Supabase is set up).
//
// Vercel rejects requests over 4.5 MB, so files never go through Flask:
// on submit, each chosen file is uploaded straight to Supabase with a
// one-time link from /upload-url, then the form is sent with just the
// stored file names ("<field>__direct" hidden inputs).
(() => {
    function fileInputs(form) {
        return Array.from(form.querySelectorAll('input[type="file"][name]'))
            .filter(input => !input.disabled && input.files.length);
    }

    function showError(form, message) {
        let box = form.parentElement.querySelector(".form-error");
        if (!box) {
            box = document.createElement("p");
            box.className = "form-error";
            box.setAttribute("role", "alert");
            form.parentElement.insertBefore(box, form);
        }
        box.textContent = message;
        box.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    function setButton(button, text) {
        if (!button) {
            return;
        }
        if (!button.dataset.idleHtml) {
            button.dataset.idleHtml = button.innerHTML;
            button.style.minWidth = `${button.offsetWidth}px`;
        }
        button.innerHTML = `<span class="busy-spinner" aria-hidden="true"></span>${text}`;
        button.classList.add("is-busy");
        button.setAttribute("aria-busy", "true");
        button.disabled = true;
    }

    function resetButton(button) {
        if (!button || !button.dataset.idleHtml) {
            return;
        }
        button.innerHTML = button.dataset.idleHtml;
        delete button.dataset.idleHtml;
        button.style.minWidth = "";
        button.classList.remove("is-busy");
        button.removeAttribute("aria-busy");
        button.disabled = false;
    }

    async function uploadOne(field, file) {
        const signResponse = await fetch("/upload-url", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ field, filename: file.name, size: file.size })
        });
        const signed = await signResponse.json().catch(() => ({}));
        if (!signResponse.ok) {
            throw new Error(signed.error || "The upload could not be started.");
        }

        const putResponse = await fetch(signed.upload_url, {
            method: "PUT",
            headers: {
                "Content-Type": file.type || "application/octet-stream",
                "x-upsert": "false"
            },
            body: file
        });
        if (!putResponse.ok) {
            throw new Error(`${file.name} could not be uploaded. Please try again.`);
        }

        return { stored_as: signed.stored_as, original: file.name };
    }

    document.addEventListener("submit", async event => {
        const form = event.target;
        if (event.defaultPrevented || !form.matches("form[enctype='multipart/form-data']")) {
            return;
        }

        const inputs = fileInputs(form);
        if (!inputs.length || form.dataset.directUploadDone) {
            return;
        }

        event.preventDefault();
        const button = event.submitter || form.querySelector('[type="submit"]');
        const jobs = inputs.flatMap(input =>
            Array.from(input.files).map(file => ({ input, file }))
        );

        try {
            const added = [];
            for (const [index, job] of jobs.entries()) {
                setButton(button, `Uploading ${index + 1} of ${jobs.length}…`);
                const result = await uploadOne(job.input.name, job.file);
                const hidden = document.createElement("input");
                hidden.type = "hidden";
                hidden.name = `${job.input.name}__direct`;
                hidden.value = JSON.stringify(result);
                added.push(hidden);
            }

            added.forEach(hidden => form.append(hidden));
            // Disabled inputs are not sent, so the big files stay out of the request.
            inputs.forEach(input => { input.disabled = true; });
            form.dataset.directUploadDone = "true";
            setButton(button, "Saving…");
            form.submit();
        } catch (error) {
            resetButton(button);
            showError(form, error.message);
        }
    // Capture phase: runs before loading.js's "Saving…" handler, which then
    // sees the event was taken over and leaves the button to this script.
    }, true);

    // Back button: make the form usable again.
    window.addEventListener("pageshow", event => {
        if (!event.persisted) {
            return;
        }
        document.querySelectorAll("form[data-direct-upload-done]").forEach(form => {
            delete form.dataset.directUploadDone;
            form.querySelectorAll('input[type="file"]').forEach(input => { input.disabled = false; });
            form.querySelectorAll('input[name$="__direct"]').forEach(hidden => hidden.remove());
        });
    });
})();

// Certificate viewer on the home page: the certificate photo on one side,
// and its dates, issuer, description, skills and credential on the other.
// Same layout and animation as the project viewer (project-viewer.js).
(() => {
    const viewer = document.getElementById("certificate-viewer");
    if (!viewer) {
        return;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const byId = id => document.getElementById(id);
    const stage = byId("cert-viewer-stage");
    const closeButton = byId("cert-viewer-close");
    let lastOpener = null;
    let closing = false;

    function certificates() {
        const data = byId("certificate-data");
        return data ? JSON.parse(data.textContent) : [];
    }

    function showSection(id, visible) {
        byId(id).hidden = !visible;
    }

    function fill(certificate) {
        // Photo
        if (certificate.photo) {
            const image = document.createElement("img");
            image.className = "viewer-item";
            image.src = certificate.photo;
            image.alt = `${certificate.title} certificate`;
            stage.replaceChildren(image);
        } else {
            const empty = document.createElement("div");
            empty.className = "viewer-item viewer-document";
            const label = document.createElement("span");
            label.className = "viewer-file-label";
            label.textContent = "CERTIFICATE";
            empty.append(label);
            stage.replaceChildren(empty);
        }

        // Dates: "Obtained …" plus "Valid until …" or an Expired tag.
        const dates = byId("cert-viewer-dates");
        dates.replaceChildren(`Obtained ${certificate.obtained}`);
        if (certificate.expires) {
            const status = document.createElement("span");
            status.className = `certificate-status${certificate.expired ? " is-expired" : ""}`;
            status.textContent = certificate.expired
                ? `Expired ${certificate.expires}`
                : `Valid until ${certificate.expires}`;
            dates.append(status);
        }

        const category = byId("cert-viewer-category");
        category.textContent = certificate.category || "";
        category.hidden = !certificate.category;

        byId("cert-viewer-title").textContent = certificate.title;

        const issuer = byId("cert-viewer-issuer");
        issuer.textContent = certificate.issuer ? `Issued by ${certificate.issuer}` : "";
        issuer.hidden = !certificate.issuer;

        byId("cert-viewer-description").textContent = certificate.description;
        showSection("cert-viewer-about", Boolean(certificate.description));

        byId("cert-viewer-skills").replaceChildren(...certificate.skills.map(skill => {
            const tag = document.createElement("span");
            tag.textContent = skill;
            return tag;
        }));
        showSection("cert-viewer-skills-section", certificate.skills.length > 0);

        const credentialId = byId("cert-viewer-credential-id");
        credentialId.replaceChildren();
        if (certificate.credentialId) {
            const label = document.createElement("span");
            label.textContent = "Credential ID ";
            const value = document.createElement("code");
            value.textContent = certificate.credentialId;
            credentialId.append(label, value);
        }
        credentialId.hidden = !certificate.credentialId;

        const link = byId("cert-viewer-link");
        link.hidden = !certificate.credentialUrl;
        if (certificate.credentialUrl) {
            link.href = certificate.credentialUrl;
        } else {
            link.removeAttribute("href");
        }
        showSection(
            "cert-viewer-credential",
            Boolean(certificate.credentialId || certificate.credentialUrl)
        );
    }

    function open(index, opener) {
        const certificate = certificates()[index];
        if (!certificate) {
            return;
        }
        lastOpener = opener;
        closing = false;
        fill(certificate);

        viewer.showModal();
        document.documentElement.classList.add("viewer-open");
        viewer.querySelector(".viewer-layout").scrollTop = 0;
        viewer.querySelector(".viewer-info").scrollTop = 0;
        closeButton.focus();
        requestAnimationFrame(() => viewer.classList.add("is-open"));
    }

    function close() {
        if (!viewer.open || closing) {
            return;
        }
        closing = true;
        viewer.classList.remove("is-open");

        const finish = () => {
            if (!closing) {
                return;
            }
            viewer.close();
            stage.replaceChildren();
            document.documentElement.classList.remove("viewer-open");
            closing = false;
            if (lastOpener) {
                lastOpener.focus({ preventScroll: true });
            }
        };

        if (reduceMotion.matches) {
            finish();
            return;
        }
        const onEnd = event => {
            if (event.target === viewer) {
                viewer.removeEventListener("transitionend", onEnd);
                finish();
            }
        };
        viewer.addEventListener("transitionend", onEnd);
        setTimeout(finish, 350);
    }

    // One listener for every card, so certificates added by a live update
    // open too. Links and "See more" keep their own behavior.
    document.addEventListener("click", event => {
        const card = event.target.closest(".certificate-card");
        if (!card || event.target.closest("a, .see-more-button")) {
            return;
        }
        open(Number(card.dataset.certificateIndex), card.querySelector(".certificate-open"));
    });

    closeButton.addEventListener("click", close);

    // Esc: animate out instead of closing instantly.
    viewer.addEventListener("cancel", event => {
        event.preventDefault();
        close();
    });

    // A click on the dimmed backdrop lands on the dialog element itself.
    viewer.addEventListener("click", event => {
        if (event.target === viewer) {
            close();
        }
    });
})();

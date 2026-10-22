// Project cards on the home page: "See more" for long descriptions and a
// viewer that shows a project's full description and every media file.
(() => {
    const dataElement = document.getElementById("project-data");
    const viewer = document.getElementById("project-viewer");
    if (!dataElement || !viewer) {
        return;
    }

    const projects = JSON.parse(dataElement.textContent);
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    // Runs `callback` once the element's own transition ends, with a timeout
    // in case no transition runs (nothing changed, or motion is reduced).
    function afterTransition(element, callback, fallbackMs) {
        let finished = false;
        let timer = null;

        const finish = event => {
            if (event && event.target !== element) {
                return;
            }
            if (finished) {
                return;
            }
            finished = true;
            element.removeEventListener("transitionend", finish);
            clearTimeout(timer);
            callback();
        };

        element.addEventListener("transitionend", finish);
        timer = setTimeout(finish, fallbackMs);
    }


    // =========================
    // SEE MORE / SEE LESS
    // =========================

    const descriptions = Array.from(
        document.querySelectorAll(".portfolio-project-description")
    );

    function seeMoreButtonFor(description) {
        return document.querySelector(`[aria-controls="${description.id}"]`);
    }

    function refreshSeeMore(description) {
        const button = seeMoreButtonFor(description);
        if (!button || description.classList.contains("is-expanded")) {
            return;
        }
        description.classList.add("is-clamped");
        button.hidden = description.scrollHeight <= description.clientHeight + 1;
    }

    function collapsedHeight(description) {
        return parseFloat(getComputedStyle(description).lineHeight) * 3;
    }

    function setExpanded(description, button, expand) {
        const startHeight = description.offsetHeight;

        button.setAttribute("aria-expanded", String(expand));
        button.textContent = expand ? "See less" : "See more";

        if (expand) {
            description.classList.remove("is-clamped");
            description.classList.add("is-expanded");
        }

        const finishCollapse = () => {
            description.classList.remove("is-expanded");
            description.classList.add("is-clamped");
        };

        if (reduceMotion.matches) {
            if (!expand) {
                finishCollapse();
            }
            return;
        }

        const endHeight = expand
            ? description.scrollHeight
            : collapsedHeight(description);

        description.style.maxHeight = `${startHeight}px`;
        void description.offsetHeight;
        description.style.maxHeight = `${endHeight}px`;

        afterTransition(description, () => {
            description.style.maxHeight = "";
            if (!expand) {
                finishCollapse();
            }
        }, 500);
    }

    descriptions.forEach(description => {
        const button = seeMoreButtonFor(description);
        if (!button) {
            return;
        }

        refreshSeeMore(description);

        button.addEventListener("click", () => {
            setExpanded(
                description,
                button,
                button.getAttribute("aria-expanded") !== "true"
            );
        });
    });

    let resizeTimer = null;
    window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => descriptions.forEach(refreshSeeMore), 150);
    });

    // Web fonts can change how many lines a description takes.
    window.addEventListener("load", () => descriptions.forEach(refreshSeeMore));


    // =========================
    // PROJECT VIEWER
    // =========================

    const stage = document.getElementById("viewer-stage");
    const thumbs = document.getElementById("viewer-thumbs");
    const counter = document.getElementById("viewer-counter");
    const prevButton = document.getElementById("viewer-prev");
    const nextButton = document.getElementById("viewer-next");
    const closeButton = document.getElementById("viewer-close");
    const titleElement = document.getElementById("viewer-title");
    const dateElement = document.getElementById("viewer-date");
    const descriptionElement = document.getElementById("viewer-description");
    const tagsElement = document.getElementById("viewer-tags");
    const linkElement = document.getElementById("viewer-link");

    let current = null;
    let currentIndex = 0;
    let lastOpener = null;
    let closing = false;

    function labelBox(text) {
        const box = document.createElement("span");
        box.className = "viewer-file-label";
        box.textContent = text;
        return box;
    }

    function buildMedia(media, position, forThumb) {
        if (media.kind === "image") {
            const image = document.createElement("img");
            image.src = media.url;
            image.alt = forThumb ? "" : `${current.title}, image ${position + 1}`;
            if (forThumb) {
                image.loading = "lazy";
            }
            return image;
        }

        if (media.kind === "video") {
            const video = document.createElement("video");
            video.playsInline = true;
            video.preload = "metadata";
            if (forThumb) {
                video.src = `${media.url}#t=0.1`;
                video.muted = true;
                video.tabIndex = -1;
            } else {
                video.src = media.url;
                video.controls = true;
            }
            return video;
        }

        const label = media.kind === "pdf" ? "PDF" : "FILE";
        if (forThumb) {
            return labelBox(label);
        }

        const documentCard = document.createElement("div");
        documentCard.className = "viewer-document";
        documentCard.appendChild(labelBox(label));

        const openLink = document.createElement("a");
        openLink.href = media.url;
        openLink.target = "_blank";
        openLink.rel = "noopener noreferrer";
        openLink.textContent = media.kind === "pdf" ? "Open PDF ↗" : "Open file ↗";
        documentCard.appendChild(openLink);
        return documentCard;
    }

    function showMedia(position, direction) {
        const gallery = current.gallery;

        if (!gallery.length) {
            const empty = document.createElement("div");
            empty.className = "viewer-item viewer-document";
            empty.appendChild(labelBox("PROJECT"));
            stage.replaceChildren(empty);
            return;
        }

        currentIndex = (position + gallery.length) % gallery.length;

        const item = buildMedia(gallery[currentIndex], currentIndex, false);
        item.classList.add("viewer-item");
        if (direction) {
            item.dataset.direction = direction;
        }
        stage.replaceChildren(item);

        counter.textContent = `${currentIndex + 1} / ${gallery.length}`;

        Array.from(thumbs.children).forEach((thumb, index) => {
            const isActive = index === currentIndex;
            thumb.classList.toggle("active", isActive);
            thumb.setAttribute("aria-current", String(isActive));
            if (isActive) {
                thumb.scrollIntoView({
                    block: "nearest",
                    inline: "nearest",
                    behavior: reduceMotion.matches ? "auto" : "smooth"
                });
            }
        });
    }

    function fillInfo() {
        titleElement.textContent = current.title;
        dateElement.textContent = current.date;
        descriptionElement.textContent = current.description;

        tagsElement.replaceChildren(...current.technologies.map(technology => {
            const tag = document.createElement("span");
            tag.textContent = technology;
            return tag;
        }));
        tagsElement.hidden = current.technologies.length === 0;

        linkElement.hidden = !current.link;
        if (current.link) {
            linkElement.href = current.link;
        } else {
            linkElement.removeAttribute("href");
        }

        thumbs.replaceChildren(...current.gallery.map((media, index) => {
            const thumb = document.createElement("button");
            thumb.type = "button";
            thumb.setAttribute("aria-label", `Show file ${index + 1} of ${current.gallery.length}`);
            thumb.appendChild(buildMedia(media, index, true));
            thumb.addEventListener("click", () => {
                if (index !== currentIndex) {
                    showMedia(index, index > currentIndex ? "next" : "prev");
                }
            });
            return thumb;
        }));
    }

    function openViewer(projectIndex, opener) {
        current = projects[projectIndex];
        if (!current) {
            return;
        }

        lastOpener = opener;
        closing = false;
        currentIndex = 0;

        viewer.classList.toggle("has-many", current.gallery.length > 1);
        fillInfo();
        showMedia(0);

        viewer.showModal();
        document.documentElement.classList.add("viewer-open");
        viewer.querySelector(".viewer-layout").scrollTop = 0;
        viewer.querySelector(".viewer-info").scrollTop = 0;
        closeButton.focus();

        requestAnimationFrame(() => viewer.classList.add("is-open"));
    }

    function closeViewer() {
        if (!viewer.open || closing) {
            return;
        }
        closing = true;
        viewer.classList.remove("is-open");

        const finish = () => {
            viewer.close();
            // Removing the media also stops any video that is playing.
            stage.replaceChildren();
            thumbs.replaceChildren();
            document.documentElement.classList.remove("viewer-open");
            closing = false;
            if (lastOpener) {
                lastOpener.focus({ preventScroll: true });
            }
        };

        if (reduceMotion.matches) {
            finish();
        } else {
            afterTransition(viewer, finish, 350);
        }
    }

    function step(amount) {
        if (current && current.gallery.length > 1) {
            showMedia(currentIndex + amount, amount > 0 ? "next" : "prev");
        }
    }

    document.querySelectorAll(".portfolio-project-card").forEach(card => {
        const projectIndex = Number(card.dataset.projectIndex);
        const openButton = card.querySelector(".portfolio-project-open");

        card.addEventListener("click", event => {
            // Links and "See more" keep their own behavior.
            if (event.target.closest("a, .see-more-button")) {
                return;
            }
            openViewer(projectIndex, openButton);
        });
    });

    prevButton.addEventListener("click", () => step(-1));
    nextButton.addEventListener("click", () => step(1));
    closeButton.addEventListener("click", closeViewer);

    // Esc: animate out instead of closing instantly.
    viewer.addEventListener("cancel", event => {
        event.preventDefault();
        closeViewer();
    });

    // A click on the dimmed backdrop lands on the dialog element itself.
    viewer.addEventListener("click", event => {
        if (event.target === viewer) {
            closeViewer();
        }
    });

    viewer.addEventListener("keydown", event => {
        if (event.target.tagName === "VIDEO") {
            return;
        }
        if (event.key === "ArrowLeft") {
            step(-1);
        } else if (event.key === "ArrowRight") {
            step(1);
        }
    });

    // Swipe left / right on touch screens.
    let touchStartX = null;
    stage.addEventListener("touchstart", event => {
        touchStartX = event.touches[0].clientX;
    }, { passive: true });
    stage.addEventListener("touchend", event => {
        if (touchStartX === null) {
            return;
        }
        const distance = event.changedTouches[0].clientX - touchStartX;
        touchStartX = null;
        if (Math.abs(distance) > 50) {
            step(distance < 0 ? 1 : -1);
        }
    });
})();

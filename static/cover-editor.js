// Cover framing on Add / Edit Project (templates/_cover_editor.html).
//
// The cover is the project's first file: the first current file that is
// not ticked "Remove", otherwise the first newly chosen file. Dragging the
// big frame moves the picture, the slider zooms, and every preview uses the
// same .cover-framed rules as the real cards, so what you see here is what
// visitors get. The values are sent as cover_x, cover_y and cover_zoom.
(() => {
    const DEFAULTS = { x: 50, y: 50, zoom: 1 };

    const clamp = (value, low, high) => Math.min(Math.max(value, low), high);
    const round = value => Math.round(value * 100) / 100;

    function kindOf(file) {
        if (file.type.startsWith("image/")) {
            return "image";
        }
        return file.type.startsWith("video/") ? "video" : "document";
    }

    function makeMedia(kind, url) {
        if (kind === "video") {
            const video = document.createElement("video");
            video.src = `${url}#t=0.1`;
            video.muted = true;
            video.playsInline = true;
            video.preload = "metadata";
            return video;
        }
        const image = document.createElement("img");
        image.src = url;
        image.alt = "";
        image.draggable = false;
        return image;
    }

    function setup(editor) {
        const input = document.querySelector(editor.dataset.mediaInput);
        const stage = editor.querySelector("[data-cover-stage]");
        const frames = [stage, ...editor.querySelectorAll("[data-cover-frame]")];
        const fieldX = editor.querySelector('input[name="cover_x"]');
        const fieldY = editor.querySelector('input[name="cover_y"]');
        const zoomField = editor.querySelector('input[name="cover_zoom"]');
        const zoomText = editor.querySelector("#cover-zoom-value");
        const status = editor.querySelector("[data-cover-status]");
        const title = document.getElementById("title");

        const number = (field, fallback) =>
            field.value.trim() !== "" && Number.isFinite(Number(field.value)) ? Number(field.value) : fallback;
        let state = {
            x: clamp(number(fieldX, DEFAULTS.x), 0, 100),
            y: clamp(number(fieldY, DEFAULTS.y), 0, 100),
            zoom: clamp(number(zoomField, DEFAULTS.zoom), 1, 3)
        };
        // Coming back to the cover the page opened with brings back its framing.
        const opening = { ...state };
        let openingKey;
        let shownKey;
        let objectUrl = null;
        let drag = null;

        function currentCover() {
            const kept = Array.from(document.querySelectorAll("[data-cover-url]"))
                .find(item => !item.querySelector('input[name="remove_media"]:checked'));
            if (kept) {
                return { key: kept.dataset.coverUrl, url: kept.dataset.coverUrl, kind: kept.dataset.coverKind };
            }
            const file = input && input.files[0];
            if (file) {
                return { key: `${file.name}|${file.size}|${file.lastModified}`, file, kind: kindOf(file) };
            }
            return null;
        }

        function apply() {
            frames.forEach(frame => {
                frame.style.setProperty("--cover-x", `${state.x}%`);
                frame.style.setProperty("--cover-y", `${state.y}%`);
                frame.style.setProperty("--cover-zoom", state.zoom);
            });
            fieldX.value = round(state.x);
            fieldY.value = round(state.y);
            zoomField.value = round(state.zoom);
            zoomText.textContent = `${state.zoom.toFixed(1)}×`;
        }

        function announce() {
            const across = state.x < 34 ? "left" : state.x > 66 ? "right" : "center";
            const down = state.y < 34 ? "top" : state.y > 66 ? "bottom" : "middle";
            status.textContent = `Showing the ${down} ${across} of the picture, zoom ${state.zoom.toFixed(1)} times.`;
        }

        function render() {
            const cover = currentCover();
            const key = cover ? cover.key : null;
            if (openingKey === undefined) {
                openingKey = key;
            } else if (key === shownKey) {
                return;
            } else {
                state = key === openingKey ? { ...opening } : { ...DEFAULTS };
            }
            shownKey = key;

            if (objectUrl) {
                URL.revokeObjectURL(objectUrl);
                objectUrl = null;
            }

            const framable = Boolean(cover) && (cover.kind === "image" || cover.kind === "video");
            editor.querySelector("[data-cover-body]").hidden = !framable;
            editor.querySelector("[data-cover-empty]").hidden = Boolean(cover);
            editor.querySelector("[data-cover-document]").hidden = !cover || framable;

            frames.forEach(frame => {
                const old = frame.querySelector("img, video");
                if (old) {
                    old.remove();
                }
            });
            if (framable) {
                const url = cover.file ? (objectUrl = URL.createObjectURL(cover.file)) : cover.url;
                frames.forEach(frame => frame.prepend(makeMedia(cover.kind, url)));
            }
            apply();
        }

        // How far the content moves per 1% of position, in pixels, given the
        // frame size, the picture's own size and the zoom (see .cover-framed).
        function span(frameSize, overflow) {
            return state.zoom * overflow + (state.zoom - 1) * frameSize;
        }

        stage.addEventListener("pointerdown", event => {
            if (event.button !== 0) {
                return;
            }
            stage.setPointerCapture(event.pointerId);
            drag = { x: event.clientX, y: event.clientY, startX: state.x, startY: state.y };
            stage.classList.add("is-dragging");
        });

        stage.addEventListener("pointermove", event => {
            const media = stage.querySelector("img, video");
            if (!drag || !media) {
                return;
            }
            const naturalWidth = media.naturalWidth || media.videoWidth;
            const naturalHeight = media.naturalHeight || media.videoHeight;
            if (!naturalWidth || !naturalHeight) {
                return;
            }
            const box = stage.getBoundingClientRect();
            const scale = Math.max(box.width / naturalWidth, box.height / naturalHeight);
            const spanX = span(box.width, naturalWidth * scale - box.width) / 100;
            const spanY = span(box.height, naturalHeight * scale - box.height) / 100;

            if (spanX > 0.01) {
                state.x = clamp(drag.startX - (event.clientX - drag.x) / spanX, 0, 100);
            }
            if (spanY > 0.01) {
                state.y = clamp(drag.startY - (event.clientY - drag.y) / spanY, 0, 100);
            }
            apply();
        });

        const endDrag = () => {
            if (drag) {
                drag = null;
                stage.classList.remove("is-dragging");
                announce();
            }
        };
        stage.addEventListener("pointerup", endDrag);
        stage.addEventListener("pointercancel", endDrag);

        stage.addEventListener("keydown", event => {
            const step = event.shiftKey ? 10 : 2;
            const moves = {
                ArrowLeft: ["x", step], ArrowRight: ["x", -step],
                ArrowUp: ["y", step], ArrowDown: ["y", -step]
            };
            if (moves[event.key]) {
                const [axis, change] = moves[event.key];
                state[axis] = clamp(state[axis] + change, 0, 100);
            } else if (event.key === "+" || event.key === "=") {
                state.zoom = clamp(state.zoom + 0.1, 1, 3);
            } else if (event.key === "-" || event.key === "_") {
                state.zoom = clamp(state.zoom - 0.1, 1, 3);
            } else {
                return;
            }
            event.preventDefault();
            apply();
            announce();
        });

        zoomField.addEventListener("input", () => {
            state.zoom = clamp(Number(zoomField.value), 1, 3);
            apply();
        });

        editor.querySelector("[data-cover-reset]").addEventListener("click", () => {
            state = { ...DEFAULTS };
            apply();
            announce();
        });

        // The previews carry the project's title, as the real card does.
        function showTitle() {
            const text = (title && title.value.trim()) || "Project title";
            editor.querySelectorAll("[data-cover-title]").forEach(line => {
                line.textContent = text;
            });
        }
        if (title) {
            title.addEventListener("input", showTitle);
        }
        showTitle();

        // A different cover: new files chosen or removed, or "Remove" ticked.
        if (input) {
            input.addEventListener("media-picker:change", render);
            input.addEventListener("change", render);
        }
        document.addEventListener("change", event => {
            if (event.target.matches('input[name="remove_media"]')) {
                render();
            }
        });

        render();
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("#cover-editor").forEach(setup);
    });
})();

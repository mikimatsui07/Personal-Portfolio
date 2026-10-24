// Chosen project media as removable preview tiles (Add / Edit Project).
//
// Markup: <input type="file" multiple data-media-picker="#tiles-id"
//          data-count="#count-text-id" data-max-files="10"
//          data-existing="3">   (data-existing: files the project already has)
//
// * Choosing files again adds to the list instead of replacing it.
// * Each tile has an "×" button that removes just that file.
// * The first file is marked as the cover when it will be the cover.
// The input's own file list is kept in sync, so the form (and
// direct-upload.js / unsaved-guard.js) always see exactly these files.
(() => {
    const units = ["B", "KB", "MB", "GB"];

    function readableSize(bytes) {
        let size = bytes;
        let unit = 0;
        while (size >= 1024 && unit < units.length - 1) {
            size /= 1024;
            unit++;
        }
        return `${size < 10 && unit ? size.toFixed(1) : Math.round(size)} ${units[unit]}`;
    }

    function fileKey(file) {
        return `${file.name}|${file.size}|${file.lastModified}`;
    }

    function setup(input) {
        const tiles = document.querySelector(input.dataset.mediaPicker);
        const countText = document.querySelector(input.dataset.count);
        const maxFiles = Number(input.dataset.maxFiles || 10);
        const existing = Number(input.dataset.existing || 0);
        const emptyText = countText ? countText.textContent.trim() : "";
        let files = [];
        let note = null;

        // Files the project keeps: existing ones minus those ticked for removal.
        function keptExisting() {
            const removed = document.querySelectorAll('input[name="remove_media"]:checked').length;
            return Math.max(existing - removed, 0);
        }

        function showNote(text) {
            if (!note) {
                note = document.createElement("p");
                note.className = "media-picker-note";
                note.setAttribute("role", "status");
                tiles.after(note);
            }
            note.textContent = text;
            note.hidden = !text;
        }

        function syncInput() {
            const transfer = new DataTransfer();
            files.forEach(file => transfer.items.add(file));
            input.files = transfer.files;
            // Let other scripts (e.g. the unsaved-changes check) notice.
            input.dispatchEvent(new Event("media-picker:change", { bubbles: true }));
        }

        function preview(file) {
            const box = document.createElement("span");
            box.className = "media-tile-preview";
            box.setAttribute("aria-hidden", "true");

            if (file.type.startsWith("image/")) {
                const image = document.createElement("img");
                image.src = URL.createObjectURL(file);
                image.alt = "";
                image.onload = () => URL.revokeObjectURL(image.src);
                box.append(image);
            } else if (file.type.startsWith("video/")) {
                const video = document.createElement("video");
                video.src = `${URL.createObjectURL(file)}#t=0.1`;
                video.muted = true;
                video.preload = "metadata";
                video.onloadeddata = () => URL.revokeObjectURL(video.src.split("#")[0]);
                box.append(video);
                box.classList.add("is-video");
            } else {
                box.textContent = file.name.toLowerCase().endsWith(".pdf") ? "PDF" : "FILE";
                box.classList.add("is-document");
            }
            return box;
        }

        function render() {
            const coverIsNew = keptExisting() === 0;

            tiles.replaceChildren(...files.map((file, index) => {
                const tile = document.createElement("div");
                tile.className = "media-tile";

                const info = document.createElement("span");
                info.className = "media-tile-info";

                const name = document.createElement("span");
                name.className = "media-tile-name";
                name.textContent = file.name;
                name.title = file.name;

                const meta = document.createElement("span");
                meta.className = "media-tile-meta";
                meta.textContent = readableSize(file.size);
                if (index === 0 && coverIsNew) {
                    const cover = document.createElement("span");
                    cover.className = "media-tile-cover";
                    cover.textContent = "Cover";
                    meta.prepend(cover);
                }
                info.append(name, meta);

                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "media-tile-remove";
                remove.setAttribute("aria-label", `Remove ${file.name}`);
                remove.title = "Remove";
                remove.textContent = "×";
                remove.addEventListener("click", () => {
                    files.splice(index, 1);
                    syncInput();
                    render();
                    showNote("");
                    // Keep keyboard focus nearby after the tile disappears.
                    const next = tiles.querySelectorAll(".media-tile-remove")[Math.min(index, files.length - 1)];
                    (next || input).focus();
                });

                tile.append(preview(file), info, remove);
                return tile;
            }));

            if (countText) {
                countText.textContent = files.length === 0
                    ? emptyText
                    : `${files.length} file${files.length === 1 ? "" : "s"} selected`;
            }
        }

        input.addEventListener("change", () => {
            const known = new Set(files.map(fileKey));
            const room = maxFiles - keptExisting() - files.length;
            const incoming = Array.from(input.files).filter(file => !known.has(fileKey(file)));
            const added = incoming.slice(0, Math.max(room, 0));

            files = files.concat(added);
            syncInput();
            render();

            showNote(
                incoming.length > added.length
                    ? `A project can have up to ${maxFiles} files, so ${incoming.length - added.length} ` +
                      `file${incoming.length - added.length === 1 ? " was" : "s were"} not added.`
                    : ""
            );
        });

        // Ticking "Remove" on current media changes which file is the cover.
        document.addEventListener("change", event => {
            if (event.target.matches('input[name="remove_media"]')) {
                render();
            }
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("input[type=file][data-media-picker]").forEach(setup);
    });
})();

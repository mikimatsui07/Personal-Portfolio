"""Mik Portfolio — Flask application, organised with classes.

How the four OOP principles are used:

* Abstraction   — ``Repository``, ``UploadValidator``, ``FormValidator`` and
                  ``PageView`` are abstract base classes. They describe *what*
                  a part does and leave the details to subclasses.
* Inheritance   — concrete classes reuse their base: every repository gets
                  ``fetch_all``/``find_by_id``/``count``/``delete_by_id`` from
                  ``Repository``; every admin page gets the login check from
                  ``AdminPageView``/``AdminActionView``.
* Polymorphism  — the same call does the right thing for each class:
                  ``validator.validate(...)`` for projects, profiles and
                  contact messages; ``view.render(...)`` uses each page's own
                  template; Flask calls ``get``/``post`` on whichever view
                  matches the URL.
* Encapsulation — state is kept behind underscore attributes and accessed
                  through methods/properties (the connection factory, the
                  upload folder, the "table already created" flags, the
                  services each view receives).
"""

import json
import os
import re
import secrets
import shutil
import uuid
from abc import ABC, abstractmethod
from datetime import date
from functools import wraps
from urllib.parse import quote, urlencode, urlparse

import requests
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for
)
from flask.views import MethodView
from psycopg import Error as DatabaseError
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from db import get_db_connection


# =========================================================
# AUTHENTICATION HELPERS
# =========================================================

def login_required(view_func):
    @wraps(view_func)
    def decorated_function(*args, **kwargs):
        if "admin_id" not in session:
            return redirect("/login")
        return view_func(*args, **kwargs)

    return decorated_function


class PasswordChecker:
    """Compares a stored password (hashed or plain) with a submitted one."""

    @staticmethod
    def matches(stored_password, submitted_password):
        if not stored_password:
            return False

        if check_password_hash(stored_password, submitted_password):
            return True

        if stored_password == submitted_password:
            return True

        return False


# =========================================================
# MEDIA FILES
# =========================================================

class MediaFile:
    """Knows what kind of media a file name points to."""

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov"}

    @classmethod
    def kind_of(cls, filename):
        extension = os.path.splitext(filename)[1].lower()
        if extension in cls.IMAGE_EXTENSIONS:
            return "image"
        if extension in cls.VIDEO_EXTENSIONS:
            return "video"
        if extension == ".pdf":
            return "pdf"
        return "file"


class StorageError(OSError):
    """A file could not be saved to, read from or removed from storage.

    It is an OSError, so the views' existing ``except OSError`` handling
    covers local disks and Supabase Storage alike.
    """


class FileStorage(ABC):
    """Where uploaded files live (abstraction).

    ``LocalStorage`` keeps them in static/uploads; ``SupabaseStorage``
    keeps them in a Supabase Storage bucket. Views only use the methods
    below, so they work the same with either (polymorphism).
    """

    # True when the browser may upload straight to the storage.
    supports_direct_upload = False

    @abstractmethod
    def write(self, filename, stream, content_type):
        """Store the bytes from ``stream`` under ``filename``."""

    @abstractmethod
    def remove(self, filename):
        """Delete ``filename``; missing files are ignored."""

    @abstractmethod
    def url(self, filename):
        """Public URL the browser uses to show ``filename``."""

    def size_of(self, filename):
        """Size in bytes of a stored file, or None if it does not exist."""
        return None

    def download_response(self, filename, save_as):
        """A response that makes the browser save ``filename`` as ``save_as``."""
        return redirect(self.url(filename))

    def save_all(self, validated):
        """Store validated uploads; on failure removes what was stored and re-raises.

        ``validated`` is a list of (IncomingFile, file name) pairs. Returns
        the stored file names, for ``discard`` if a later step fails.
        """
        saved = []
        try:
            for incoming, filename in validated:
                incoming.store(self, filename)
                saved.append(filename)
        except OSError:
            self.discard(saved)
            raise
        return saved

    def discard(self, filenames):
        """Best-effort removal of files that are no longer needed."""
        for filename in filenames:
            try:
                self.remove(filename)
            except OSError:
                pass


class LocalStorage(FileStorage):
    """Files in the app's static/uploads folder (local development).

    The folder is read from the app config on every use, so changing
    ``app.config["UPLOAD_FOLDER"]`` takes effect immediately.
    """

    def __init__(self, flask_app):
        self._app = flask_app

    @property
    def folder(self):
        return self._app.config["UPLOAD_FOLDER"]

    def _path(self, filename):
        upload_folder = os.path.realpath(self.folder)
        file_path = os.path.realpath(os.path.join(upload_folder, filename))
        if os.path.commonpath((upload_folder, file_path)) != upload_folder:
            return None
        return file_path

    def write(self, filename, stream, content_type):
        path = self._path(filename)
        if path is None:
            raise StorageError(f"Refused to write outside the upload folder: {filename}")
        os.makedirs(self.folder, exist_ok=True)
        with open(path, "wb") as target:
            shutil.copyfileobj(stream, target)

    def remove(self, filename):
        if not filename:
            return

        file_path = self._path(filename)
        if file_path is None:
            self._app.logger.warning(
                "Skipped deleting media outside upload directory: %s", filename
            )
            return

        if os.path.isfile(file_path):
            os.remove(file_path)

    def url(self, filename):
        return url_for("static", filename="uploads/" + filename)

    def download_response(self, filename, save_as):
        return send_from_directory(self.folder, filename, as_attachment=True, download_name=save_as)

    def size_of(self, filename):
        path = self._path(filename)
        if path is None or not os.path.isfile(path):
            return None
        return os.path.getsize(path)


class SupabaseStorage(FileStorage):
    """Files in a Supabase Storage bucket (used on Vercel).

    Talks to the Storage REST API with the service role key, which never
    leaves the server. The bucket is public-read, so images load straight
    from Supabase without going through Flask.
    """

    supports_direct_upload = True
    TIMEOUT = 30

    def __init__(self, project_url, service_key, bucket):
        self._base = project_url.rstrip("/") + "/storage/v1"
        self._bucket = bucket
        self._headers = {
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key
        }

    def _object_path(self, filename):
        return f"{self._bucket}/{quote(filename)}"

    def _request(self, method, path, **kwargs):
        headers = dict(self._headers, **kwargs.pop("headers", {}))
        try:
            return requests.request(
                method,
                f"{self._base}/{path}",
                headers=headers,
                timeout=self.TIMEOUT,
                **kwargs
            )
        except requests.RequestException as error:
            raise StorageError(f"Supabase Storage is unreachable: {error}") from error

    def write(self, filename, stream, content_type):
        response = self._request(
            "POST",
            f"object/{self._object_path(filename)}",
            data=stream.read(),
            headers={
                "Content-Type": content_type or "application/octet-stream",
                "x-upsert": "false"
            }
        )
        if response.status_code >= 300:
            raise StorageError(
                f"Supabase refused {filename}: {response.status_code} {response.text[:200]}"
            )

    def remove(self, filename):
        if not filename:
            return
        response = self._request("DELETE", f"object/{self._object_path(filename)}")
        # 400/404 mean the file is already gone, which is what we wanted.
        if response.status_code >= 300 and response.status_code not in (400, 404):
            raise StorageError(
                f"Supabase could not delete {filename}: {response.status_code}"
            )

    def url(self, filename):
        return f"{self._base}/object/public/{self._object_path(filename)}"

    def download_response(self, filename, save_as):
        # ?download= makes Supabase send the file as an attachment with this name.
        return redirect(f"{self.url(filename)}?{urlencode({'download': save_as})}")

    def size_of(self, filename):
        response = self._request("GET", f"object/info/{self._object_path(filename)}")
        if response.status_code != 200:
            return None
        info = response.json()
        size = info.get("size")
        if size is None:
            size = (info.get("metadata") or {}).get("size")
        return int(size) if size is not None else None

    def signed_upload_url(self, filename):
        """A one-time URL the browser can PUT this file to."""
        response = self._request(
            "POST", f"object/upload/sign/{self._object_path(filename)}"
        )
        if response.status_code != 200:
            raise StorageError(
                f"Supabase could not sign an upload: {response.status_code} {response.text[:200]}"
            )
        return self._base + response.json()["url"]


# =========================================================
# INCOMING FILES (abstraction + polymorphism)
# =========================================================

class IncomingFile(ABC):
    """A file submitted with a form, wherever its bytes currently are."""

    @property
    @abstractmethod
    def filename(self):
        """The name the file had on the visitor's computer."""

    @abstractmethod
    def size(self):
        """Size in bytes, or None if it cannot be read."""

    @abstractmethod
    def stored_name(self, extension):
        """The name to keep it under in storage."""

    @abstractmethod
    def store(self, storage, filename):
        """Make sure the file is in ``storage`` under ``filename``."""


class FormUpload(IncomingFile):
    """A file sent to Flask inside the form (local development, small files)."""

    def __init__(self, upload):
        self._upload = upload

    @property
    def filename(self):
        return self._upload.filename

    def size(self):
        try:
            self._upload.stream.seek(0, os.SEEK_END)
            size = self._upload.stream.tell()
            self._upload.stream.seek(0)
        except (AttributeError, OSError):
            return None
        return size

    def stored_name(self, extension):
        return f"{uuid.uuid4().hex}{extension}"

    def store(self, storage, filename):
        self._upload.stream.seek(0)
        storage.write(filename, self._upload.stream, self._upload.mimetype)


class DirectUpload(IncomingFile):
    """A file the browser already uploaded straight to Supabase Storage.

    This avoids Vercel's 4.5 MB request limit. Flask chose its storage
    name when it signed the upload, so ``store`` only checks it arrived.
    """

    def __init__(self, storage, stored_as, original_name):
        self._storage = storage
        self._stored_as = stored_as
        self._original_name = original_name

    @property
    def filename(self):
        return self._original_name

    def size(self):
        return self._storage.size_of(self._stored_as)

    def stored_name(self, extension):
        return self._stored_as

    def store(self, storage, filename):
        if storage.size_of(filename) is None:
            raise StorageError(f"The uploaded file is missing: {filename}")


class IncomingFiles:
    """Collects the files of one form field from the current request.

    Direct uploads arrive as hidden "<field>__direct" inputs holding the
    storage name Flask issued. Only names this session asked for are
    accepted, so a form cannot point at files it did not upload.
    """

    SESSION_KEY = "direct_uploads"
    MAX_REMEMBERED = 40

    def __init__(self, storage):
        self._storage = storage

    @staticmethod
    def _parse(raw):
        try:
            entry = json.loads(raw)
            return str(entry["stored_as"]), str(entry.get("original") or entry["stored_as"])
        except (ValueError, KeyError, TypeError):
            return None

    def remember(self, stored_as):
        issued = session.get(self.SESSION_KEY, [])
        session[self.SESSION_KEY] = (issued + [stored_as])[-self.MAX_REMEMBERED:]

    def forget(self, stored_names):
        issued = session.get(self.SESSION_KEY, [])
        session[self.SESSION_KEY] = [name for name in issued if name not in stored_names]

    def all(self, field):
        files = [
            FormUpload(upload)
            for upload in request.files.getlist(field)
            if upload and upload.filename
        ]

        issued = set(session.get(self.SESSION_KEY, []))
        for raw in request.form.getlist(f"{field}__direct"):
            parsed = self._parse(raw)
            if parsed and parsed[0] in issued:
                files.append(DirectUpload(self._storage, *parsed))

        return files

    def one(self, field):
        files = self.all(field)
        return files[0] if files else None

    def _direct_names(self):
        issued = set(session.get(self.SESSION_KEY, []))
        names = []
        for key, raw_values in request.form.lists():
            if key.endswith("__direct"):
                for raw in raw_values:
                    parsed = self._parse(raw)
                    if parsed and parsed[0] in issued:
                        names.append(parsed[0])
        return names

    def committed(self):
        """The request saved its direct uploads: stop tracking them."""
        names = self._direct_names()
        if names:
            self.forget(names)

    def discard_unused(self):
        """The request failed: delete the files the browser uploaded for it."""
        names = self._direct_names()
        if names:
            self._storage.discard(names)
            self.forget(names)


# =========================================================
# UPLOAD VALIDATION (abstraction + polymorphism)
# =========================================================

class UploadValidator(ABC):
    """Checks one uploaded file's type and size.

    Subclasses only say which extensions and size are allowed and
    what the error messages are; ``validate`` is shared.
    """

    @property
    @abstractmethod
    def extensions(self):
        """Allowed lowercase file extensions, e.g. {".png"}."""

    @property
    @abstractmethod
    def max_bytes(self):
        """Largest allowed file size in bytes."""

    @property
    @abstractmethod
    def type_error(self):
        """Message shown when the file type is not allowed."""

    @property
    @abstractmethod
    def size_error(self):
        """Message shown when the file is too large."""

    def check(self, filename, size):
        """Return (lowercase extension, None) or (None, error message)."""
        safe_name = secure_filename(filename or "")
        extension = os.path.splitext(safe_name)[1].lower()
        if not safe_name or extension not in self.extensions:
            return None, self.type_error

        if size is None:
            return None, "The selected file could not be read."

        if size > self.max_bytes:
            return None, self.size_error

        return extension, None

    def validate(self, upload):
        """Return (file name to store it under, None) or (None, error message).

        ``upload`` is an IncomingFile: sent with the form or already
        uploaded to storage by the browser.
        """
        if not upload or not upload.filename:
            return None, None

        extension, error = self.check(upload.filename, upload.size())
        if error:
            return None, error

        return upload.stored_name(extension), None


class ProjectMediaValidator(UploadValidator):
    """Images, videos and PDFs attached to a project."""

    EXTENSIONS = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".mp4", ".webm", ".mov", ".pdf"
    }
    MAX_BYTES = 16 * 1024 * 1024
    MAX_FILES = 10

    @property
    def extensions(self):
        return self.EXTENSIONS

    @property
    def max_bytes(self):
        return self.MAX_BYTES

    @property
    def type_error(self):
        return "Choose a PNG, JPG, GIF, WebP, MP4, WebM, MOV, or PDF file."

    @property
    def size_error(self):
        return "Media files must be 16 MB or smaller."

    def validate_many(self, uploads, existing_count=0):
        """Validate several project media files; returns [(upload, new name)], error."""
        uploads = [upload for upload in uploads if upload and upload.filename]

        if existing_count + len(uploads) > self.MAX_FILES:
            return [], f"A project can have up to {self.MAX_FILES} media files."

        validated = []
        for upload in uploads:
            filename, error = self.validate(upload)
            if error:
                return [], f"{upload.filename}: {error}"
            validated.append((upload, filename))

        return validated, None


class ProfilePictureValidator(UploadValidator):
    """The hero picture and the About picture."""

    EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    MAX_MB = 10

    def __init__(self, label="profile picture"):
        self._label = label

    @property
    def extensions(self):
        return self.EXTENSIONS

    @property
    def max_bytes(self):
        return self.MAX_MB * 1024 * 1024

    @property
    def type_error(self):
        return f"Choose a PNG, JPG, GIF, or WebP image for your {self._label}."

    @property
    def size_error(self):
        return f"Your {self._label} must be {self.MAX_MB} MB or smaller."


class CVValidator(UploadValidator):
    """The CV visitors can view and download (a PDF)."""

    MAX_MB = 10

    @property
    def extensions(self):
        return {".pdf"}

    @property
    def max_bytes(self):
        return self.MAX_MB * 1024 * 1024

    @property
    def type_error(self):
        return "Choose a PDF file for your CV."

    @property
    def size_error(self):
        return f"Your CV must be {self.MAX_MB} MB or smaller."


class CertificatePhotoValidator(ProfilePictureValidator):
    """Certificate photos follow the same image rules as profile pictures."""

    def __init__(self):
        super().__init__("certificate photo")


# =========================================================
# FORM VALIDATION (abstraction + polymorphism)
# =========================================================

class FormValidator(ABC):
    """Turns submitted form data into clean values."""

    @abstractmethod
    def validate(self, form):
        """Return (values, error message or None)."""


class ProjectCategories:
    """The fixed list of project categories, in display order.

    Each project has exactly one. The key is stored in the database;
    the label is what visitors and the admin see. To add a category,
    add a line here (existing projects keep theirs).

    A project in "other" may carry its own name (category_custom, e.g.
    "Photography"); it then shows and filters as that name.
    """

    CHOICES = (
        ("websites", "Websites & Apps"),
        ("digital-art", "Digital Art"),
        ("animation", "Animation"),
        ("graphic-design", "Graphic Design"),
        ("other", "Other"),
    )
    LABELS = dict(CHOICES)
    CUSTOM_MAX_LENGTH = 40

    @classmethod
    def is_valid(cls, key):
        return key in cls.LABELS

    @classmethod
    def label(cls, key):
        return cls.LABELS.get(key, cls.LABELS["other"])

    @staticmethod
    def _custom(project):
        if project.get("category") != "other":
            return ""
        return " ".join((project.get("category_custom") or "").split())

    @classmethod
    def display_label(cls, project):
        """What visitors see: the custom name for a named "Other"."""
        return cls._custom(project) or cls.label(project.get("category"))

    @classmethod
    def filter_key(cls, project):
        """Groups projects for the filter buttons (custom names ignore case)."""
        custom = cls._custom(project)
        if custom:
            return "other:" + re.sub(r"[^a-z0-9]+", "-", custom.lower()).strip("-")
        return project.get("category") if cls.is_valid(project.get("category")) else "other"

    @classmethod
    def in_use(cls, projects):
        """[(key, label, count)] for categories that have projects.

        Standard categories first (in CHOICES order), then custom names
        alphabetically, then the plain "Other".
        """
        groups = {}
        for project in projects:
            key = cls.filter_key(project)
            if key not in groups:
                groups[key] = [cls.display_label(project), 0]
            groups[key][1] += 1

        standard = [(key, groups[key][0], groups[key][1])
                    for key, _ in cls.CHOICES if key in groups and key != "other"]
        custom = sorted(
            ((key, label, count) for key, (label, count) in groups.items() if key.startswith("other:")),
            key=lambda group: group[1].lower()
        )
        plain_other = [("other", groups["other"][0], groups["other"][1])] if "other" in groups else []
        return standard + custom + plain_other


class ProjectTechnologies:
    """The technologies typed on projects ("HTML, CSS, PHP"), for the filter.

    Spellings that differ only in case or spacing ("php", "PHP ") count
    as one technology; the label is the first spelling seen.
    """

    @staticmethod
    def key(name):
        return re.sub(r"[^a-z0-9+#.]+", "-", name.lower()).strip("-")

    @classmethod
    def of(cls, project):
        """[(key, label)] of one project, without repeats."""
        seen = {}
        for name in (project.get("technologies") or "").split(","):
            name = " ".join(name.split())
            key = cls.key(name)
            if key and key not in seen:
                seen[key] = name
        return list(seen.items())

    @classmethod
    def keys(cls, project):
        """Space-separated keys, for the card's data-technologies."""
        return " ".join(key for key, _ in cls.of(project))

    @classmethod
    def in_use(cls, projects):
        """[(key, label, count)], most used first, then alphabetically."""
        groups = {}
        for project in projects:
            for key, label in cls.of(project):
                groups.setdefault(key, [label, 0])[1] += 1
        return sorted(
            ((key, label, count) for key, (label, count) in groups.items()),
            key=lambda group: (-group[2], group[1].lower())
        )


class CoverFraming:
    """How a project's cover sits in its card: a focus point and a zoom.

    The file itself is never cropped. The card shows it with
    object-position (cover_x, cover_y, in percent) and scale(cover_zoom),
    so the admin can reframe it at any time and the viewer still shows
    the whole picture.
    """

    DEFAULTS = {"cover_x": 50.0, "cover_y": 50.0, "cover_zoom": 1.0}
    LIMITS = {"cover_x": (0.0, 100.0), "cover_y": (0.0, 100.0), "cover_zoom": (1.0, 3.0)}

    @classmethod
    def from_form(cls, form):
        """Read the editor's values; anything missing or odd falls back to centered."""
        values = {}
        for field, default in cls.DEFAULTS.items():
            low, high = cls.LIMITS[field]
            try:
                number = float(form.get(field, default))
            except (TypeError, ValueError):
                number = default
            if number != number:  # NaN
                number = default
            values[field] = round(min(max(number, low), high), 2)
        return values

    @classmethod
    def of(cls, project):
        return {
            field: float(project.get(field) if project.get(field) is not None else default)
            for field, default in cls.DEFAULTS.items()
        }

    @classmethod
    def style(cls, project):
        """CSS variables for the cover's frame (see .cover-framed in style.css)."""
        framing = cls.of(project)
        return (
            f"--cover-x: {framing['cover_x']:g}%; "
            f"--cover-y: {framing['cover_y']:g}%; "
            f"--cover-zoom: {framing['cover_zoom']:g}"
        )


class ProjectFormValidator(FormValidator):

    def validate(self, form):
        title = (form.get("title") or "").strip()
        description = (form.get("description") or "").strip()
        technologies = (form.get("technologies") or "").strip()
        created_at = (form.get("created_at") or "").strip()
        project_link = (form.get("project_link") or "").strip()
        category = (form.get("category") or "").strip()
        category_custom = " ".join((form.get("category_custom") or "").split())

        if not title or not description or not created_at:
            return None, "Title, description, and project date are required."

        if not ProjectCategories.is_valid(category):
            return None, "Choose a category for the project."

        # Only an "Other" project keeps its own category name.
        if category != "other":
            category_custom = ""
        if len(category_custom) > ProjectCategories.CUSTOM_MAX_LENGTH:
            return None, f"The category name must be {ProjectCategories.CUSTOM_MAX_LENGTH} characters or fewer."

        if len(title) > 160 or len(description) > 5000:
            return None, "Title must be 160 characters or fewer and description 5,000 characters or fewer."

        if len(technologies) > 1000:
            return None, "Technologies must be 1,000 characters or fewer."

        if len(project_link) > 2048:
            return None, "Project links must be 2,048 characters or fewer."

        try:
            date.fromisoformat(created_at)
        except ValueError:
            return None, "Enter a valid project date."

        if project_link:
            parsed_link = urlparse(project_link)
            if parsed_link.scheme not in {"http", "https"} or not parsed_link.netloc:
                return None, "Enter a valid project link starting with http:// or https://."

        return {
            "title": title,
            "description": description,
            "technologies": technologies,
            "created_at": created_at,
            "project_link": project_link,
            "category": category,
            "category_custom": category_custom,
            **CoverFraming.from_form(form)
        }, None


class SkillParser:
    """Splits a comma-separated skill list, dropping blanks and duplicates."""

    @staticmethod
    def parse(raw_skills):
        skills = []
        seen = set()

        for skill in (raw_skills or "").split(","):
            skill = " ".join(skill.split())
            if skill and skill.lower() not in seen:
                seen.add(skill.lower())
                skills.append(skill)

        return skills


class ProfileFormValidator(FormValidator):
    DESCRIPTION_MAX_LENGTH = 3000
    SKILL_MAX_LENGTH = 40
    MAX_SKILLS = 30
    HERO_NAME_MAX_LENGTH = 40
    HERO_INTRO_MAX_LENGTH = 600
    FULL_NAME_MAX_LENGTH = 80

    def validate(self, form):
        values = {
            "hero_name": " ".join((form.get("hero_name") or "").split()),
            "full_name": " ".join((form.get("full_name") or "").split()),
            "hero_intro": (form.get("hero_intro") or "").strip(),
            "description": (form.get("description") or "").strip(),
            "skills": SkillParser.parse(form.get("skills"))
        }
        return values, self._first_error(**values)

    def _first_error(self, hero_name, full_name, hero_intro, description, skills):
        if not hero_name or not hero_intro:
            return "Please fill in the hero name and introduction."

        if len(hero_name) > self.HERO_NAME_MAX_LENGTH:
            return f"The hero name must be {self.HERO_NAME_MAX_LENGTH} characters or fewer."

        if not full_name:
            return "Please fill in your full name."

        if len(full_name) > self.FULL_NAME_MAX_LENGTH:
            return f"Your full name must be {self.FULL_NAME_MAX_LENGTH} characters or fewer."

        if len(hero_intro) > self.HERO_INTRO_MAX_LENGTH:
            return f"The hero introduction must be {self.HERO_INTRO_MAX_LENGTH} characters or fewer."

        if not description:
            return "Please write a short description about yourself."

        if len(description) > self.DESCRIPTION_MAX_LENGTH:
            return "Your description must be 3,000 characters or fewer."

        if len(skills) > self.MAX_SKILLS:
            return f"You can list up to {self.MAX_SKILLS} skills."

        if any(len(skill) > self.SKILL_MAX_LENGTH for skill in skills):
            return f"Each skill must be {self.SKILL_MAX_LENGTH} characters or fewer."

        return None


class CertificateFormValidator(FormValidator):
    TITLE_MAX_LENGTH = 160
    DESCRIPTION_MAX_LENGTH = 2000
    ISSUER_MAX_LENGTH = 160
    CREDENTIAL_ID_MAX_LENGTH = 120
    URL_MAX_LENGTH = 2048
    MAX_SKILLS = 20
    SKILL_MAX_LENGTH = 40

    def validate(self, form):
        title = (form.get("title") or "").strip()
        description = (form.get("description") or "").strip()
        obtained_at = (form.get("obtained_at") or "").strip()
        issuer = " ".join((form.get("issuer") or "").split())
        credential_id = (form.get("credential_id") or "").strip()
        credential_url = (form.get("credential_url") or "").strip()
        skills = SkillParser.parse(form.get("skills"))
        expires_at = (form.get("expires_at") or "").strip()

        if not title or not obtained_at:
            return None, "Title and date obtained are required."

        if len(title) > self.TITLE_MAX_LENGTH:
            return None, f"Title must be {self.TITLE_MAX_LENGTH} characters or fewer."

        if len(description) > self.DESCRIPTION_MAX_LENGTH:
            return None, "Description must be 2,000 characters or fewer."

        if len(issuer) > self.ISSUER_MAX_LENGTH:
            return None, f"Issued by must be {self.ISSUER_MAX_LENGTH} characters or fewer."

        if len(credential_id) > self.CREDENTIAL_ID_MAX_LENGTH:
            return None, f"Credential ID must be {self.CREDENTIAL_ID_MAX_LENGTH} characters or fewer."

        if len(credential_url) > self.URL_MAX_LENGTH:
            return None, "The verification link must be 2,048 characters or fewer."

        if credential_url:
            parsed_link = urlparse(credential_url)
            if parsed_link.scheme not in {"http", "https"} or not parsed_link.netloc:
                return None, "Enter a valid verification link starting with http:// or https://."

        if len(skills) > self.MAX_SKILLS:
            return None, f"You can list up to {self.MAX_SKILLS} skills."

        if any(len(skill) > self.SKILL_MAX_LENGTH for skill in skills):
            return None, f"Each skill must be {self.SKILL_MAX_LENGTH} characters or fewer."

        try:
            obtained = date.fromisoformat(obtained_at)
        except ValueError:
            return None, "Enter a valid date obtained."

        if expires_at:
            try:
                expires = date.fromisoformat(expires_at)
            except ValueError:
                return None, "Enter a valid expiration date, or leave it empty."
            if expires < obtained:
                return None, "The expiration date can't be before the date obtained."

        return {
            "title": title,
            "description": description,
            "obtained_at": obtained_at,
            "issuer": issuer,
            "credential_id": credential_id,
            "credential_url": credential_url,
            "skills": ", ".join(skills),
            "expires_at": expires_at or None
        }, None


class ContactFormValidator(FormValidator):
    EMAIL_PATTERN = r"[^@\s]+@[^@\s]+\.[^@\s]+"

    def validate(self, form):
        values = {
            "name": (form.get("name") or "").strip(),
            "email": (form.get("email") or "").strip(),
            "message": (form.get("message") or "").strip()
        }

        if not values["name"] or not values["email"] or not values["message"]:
            return values, "Please fill in your name, a valid email address, and a message."

        if not re.fullmatch(self.EMAIL_PATTERN, values["email"]):
            return values, "Please enter a valid email address."

        return values, None


# =========================================================
# REPOSITORIES — all SQL lives here (abstraction + inheritance)
# =========================================================

class Repository(ABC):
    """Base for one database table.

    Subclasses name their table; the common queries below then
    work for every table.
    """

    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    @property
    @abstractmethod
    def table_name(self):
        """Name of the table this repository reads and writes."""

    def _connect(self):
        return self._connection_factory()

    def fetch_all(self, cursor, newest_first=False):
        order = "DESC" if newest_first else "ASC"
        cursor.execute(
            f"""
            SELECT *
            FROM {self.table_name}
            ORDER BY id {order}
            """
        )
        return cursor.fetchall()

    def find_by_id(self, cursor, record_id):
        cursor.execute(
            f"""
            SELECT *
            FROM {self.table_name}
            WHERE id = %s
            """,
            (record_id,)
        )
        return cursor.fetchone()

    def count(self, cursor):
        cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM {self.table_name}
            """
        )
        return cursor.fetchone()["total"]

    def delete_by_id(self, cursor, record_id):
        """Delete one row; returns how many rows were removed."""
        cursor.execute(
            f"""
            DELETE FROM {self.table_name}
            WHERE id = %s
            """,
            (record_id,)
        )
        return cursor.rowcount


class AdminRepository(Repository):

    @property
    def table_name(self):
        return "admins"

    def find_by_username(self, username):
        username = (username or "").strip()
        if not username:
            return None

        connection = self._connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT * FROM admins WHERE username = %s LIMIT 1",
                (username,)
            )
            return cursor.fetchone()
        finally:
            cursor.close()
            connection.close()


class ProjectRepository(Repository):

    @property
    def table_name(self):
        return "projects"

    @staticmethod
    def _consistent_custom_name(cursor, name, exclude_id=None):
        """Reuse the spelling already saved for this "Other" name (any case).

        Typing "photography" after "Photography" exists saves "Photography",
        so tags, filter buttons and suggestions always agree.
        """
        if not name:
            return name
        cursor.execute(
            """
            SELECT category_custom
            FROM projects
            WHERE category = 'other'
              AND lower(category_custom) = lower(%s)
              AND id <> %s
            ORDER BY id
            LIMIT 1
            """,
            (name, exclude_id or 0)
        )
        row = cursor.fetchone()
        return row["category_custom"] if row else name

    def create(self, cursor, values):
        """Insert a project; returns its new id."""
        values = dict(values, category_custom=self._consistent_custom_name(
            cursor, values["category_custom"]
        ))
        cursor.execute(
            """
            INSERT INTO projects
            (title, description, technologies, project_link, created_at, category, category_custom,
             cover_x, cover_y, cover_zoom)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                values["title"],
                values["description"],
                values["technologies"],
                values["project_link"],
                values["created_at"],
                values["category"],
                values["category_custom"],
                values["cover_x"],
                values["cover_y"],
                values["cover_zoom"]
            )
        )
        return cursor.fetchone()["id"]

    def update(self, cursor, project_id, values):
        values = dict(values, category_custom=self._consistent_custom_name(
            cursor, values["category_custom"], exclude_id=project_id
        ))
        cursor.execute(
            """
            UPDATE projects
            SET title = %s,
                description = %s,
                technologies = %s,
                project_link = %s,
                created_at = %s,
                category = %s,
                category_custom = %s,
                cover_x = %s,
                cover_y = %s,
                cover_zoom = %s
            WHERE id = %s
            """,
            (
                values["title"],
                values["description"],
                values["technologies"],
                values["project_link"],
                values["created_at"],
                values["category"],
                values["category_custom"],
                values["cover_x"],
                values["cover_y"],
                values["cover_zoom"],
                project_id
            )
        )

    def custom_category_names(self):
        """Names already used for "Other" projects, for the form's suggestions."""
        connection = self._connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT DISTINCT ON (lower(category_custom)) category_custom AS name
                FROM projects
                WHERE category = 'other' AND category_custom <> ''
                ORDER BY lower(category_custom), id
                """
            )
            return [row["name"] for row in cursor.fetchall()]
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def to_viewer_data(project):
        """Everything the public project viewer needs, ready for JSON."""
        return {
            "id": project["id"],
            "title": project["title"],
            "description": project["description"],
            "date": str(project["created_at"]),
            "technologies": [
                technology.strip()
                for technology in (project["technologies"] or "").split(",")
                if technology.strip()
            ],
            "link": project["project_link"] or "",
            "category": ProjectCategories.display_label(project),
            "gallery": [
                {"url": media["url"], "kind": media["kind"]}
                for media in project["gallery"]
            ]
        }


class ProjectMediaRepository(Repository):
    """The gallery files of every project."""

    def __init__(self, connection_factory, storage):
        super().__init__(connection_factory)
        self._storage = storage

    @property
    def table_name(self):
        return "project_media"

    def load_galleries(self, connection, project_ids):
        """Return {project_id: [{"id", "filename", "kind"}, ...]} in display order."""
        galleries = {project_id: [] for project_id in project_ids}
        if not project_ids:
            return galleries

        cursor = connection.cursor()
        try:
            placeholders = ", ".join(["%s"] * len(project_ids))
            cursor.execute(
                f"""
                SELECT id, project_id, filename
                FROM project_media
                WHERE project_id IN ({placeholders})
                ORDER BY position, id
                """,
                tuple(project_ids)
            )
            for row in cursor.fetchall():
                galleries[row["project_id"]].append({
                    "id": row["id"],
                    "filename": row["filename"],
                    "kind": MediaFile.kind_of(row["filename"])
                })
        finally:
            cursor.close()

        return galleries

    def attach_galleries(self, connection, projects):
        """Give each project a "gallery" list of its media, each with a URL."""
        galleries = self.load_galleries(
            connection,
            [project["id"] for project in projects]
        )
        for project in projects:
            project["gallery"] = [
                dict(media, url=self._storage.url(media["filename"]))
                for media in galleries[project["id"]]
            ]

    def insert(self, cursor, project_id, filenames, start_position=0):
        for offset, filename in enumerate(filenames):
            cursor.execute(
                """
                INSERT INTO project_media (project_id, filename, position)
                VALUES (%s, %s, %s)
                """,
                (project_id, filename, start_position + offset)
            )

    def delete_many(self, cursor, project_id, media_ids):
        placeholders = ", ".join(["%s"] * len(media_ids))
        cursor.execute(
            f"""
            DELETE FROM project_media
            WHERE project_id = %s AND id IN ({placeholders})
            """,
            (project_id, *media_ids)
        )

    def next_position(self, cursor, project_id):
        cursor.execute(
            """
            SELECT COALESCE(MAX(position), -1) + 1 AS next_position
            FROM project_media
            WHERE project_id = %s
            """,
            (project_id,)
        )
        return cursor.fetchone()["next_position"]


class ProfileRepository(Repository):
    """The single row (id = 1) holding the hero and About content."""

    # Used to fill the profile table the first time it is created,
    # so the hero and About section look the same until they are edited.
    DEFAULTS = {
        "hero_name": "Miki",
        "full_name": "Miki G. Matsui",
        "hero_intro": (
            "A student, creative artist, and aspiring developer who enjoys "
            "creating digital art, websites, and other creative projects."
        ),
        "hero_picture": None,
        "description": (
            "I'm an information student interested in technology, programming, "
            "digital art, animation, and design.\n\n"
            "I enjoy learning new things and turning ideas into creative "
            "projects through design."
        ),
        "picture": None,
        "cv_file": None,
        "skills": "Digital Art, Programming, Speed typist, Graphic Design, Animation"
    }

    @property
    def table_name(self):
        return "profile"

    def load(self):
        connection = self._connect()
        cursor = connection.cursor()

        try:
            cursor.execute(
                """
                SELECT hero_name, full_name, hero_intro, hero_picture,
                       description, picture, skills, cv_file
                FROM profile
                WHERE id = 1
                """
            )
            row = cursor.fetchone() or self.DEFAULTS
        finally:
            cursor.close()
            connection.close()

        description = row["description"] or ""
        return {
            "hero_name": row["hero_name"] or self.DEFAULTS["hero_name"],
            "full_name": row["full_name"] or self.DEFAULTS["full_name"],
            "hero_intro": row["hero_intro"] or self.DEFAULTS["hero_intro"],
            "hero_picture": row["hero_picture"],
            "description": description,
            # Blank lines in the description start a new paragraph.
            "paragraphs": [
                paragraph.strip()
                for paragraph in re.split(r"\n\s*\n", description.replace("\r\n", "\n"))
                if paragraph.strip()
            ],
            "picture": row["picture"],
            "cv_file": row["cv_file"],
            "skills": SkillParser.parse(row["skills"])
        }

    def update(self, cursor, values, pictures):
        cursor.execute(
            """
            UPDATE profile
            SET hero_name = %s,
                full_name = %s,
                hero_intro = %s,
                hero_picture = %s,
                description = %s,
                picture = %s,
                skills = %s,
                cv_file = %s,
                updated_at = now() AT TIME ZONE 'Asia/Manila'
            WHERE id = 1
            """,
            (
                values["hero_name"],
                values["full_name"],
                values["hero_intro"],
                pictures["hero_picture"],
                values["description"],
                pictures["picture"],
                ", ".join(values["skills"]),
                pictures["cv_file"]
            )
        )


class CertificateRepository(Repository):
    """Certificates shown in the portfolio's Certificates section."""

    def __init__(self, connection_factory, storage):
        super().__init__(connection_factory)
        self._storage = storage

    @property
    def table_name(self):
        return "certificates"

    def fetch_latest(self, cursor):
        """All certificates, most recently obtained first."""
        cursor.execute(
            """
            SELECT *
            FROM certificates
            ORDER BY obtained_at DESC, id DESC
            """
        )
        return cursor.fetchall()

    def find(self, cursor, certificate_id):
        return self.find_by_id(cursor, certificate_id)

    # Columns written by create/update, besides the photo.
    FIELDS = ("title", "description", "obtained_at", "issuer",
              "credential_id", "credential_url", "skills", "expires_at")

    def create(self, cursor, values, photo):
        columns = self.FIELDS + ("photo",)
        cursor.execute(
            f"""
            INSERT INTO certificates ({", ".join(columns)})
            VALUES ({", ".join(["%s"] * len(columns))})
            """,
            [values[field] for field in self.FIELDS] + [photo]
        )

    def update(self, cursor, certificate_id, values, photo):
        assignments = ", ".join(f"{field} = %s" for field in self.FIELDS + ("photo",))
        cursor.execute(
            f"""
            UPDATE certificates
            SET {assignments}
            WHERE id = %s
            """,
            [values[field] for field in self.FIELDS] + [photo, certificate_id]
        )

    @staticmethod
    def _label(day):
        return f"{day:%B} {day.day}, {day.year}"

    def for_display(self, certificate):
        """Add the photo URL, readable dates and lists the templates use."""
        obtained = certificate["obtained_at"]
        expires = certificate.get("expires_at")
        return dict(
            certificate,
            photo_url=(
                self._storage.url(certificate["photo"])
                if certificate["photo"] else None
            ),
            obtained_iso=obtained.isoformat(),
            obtained_label=self._label(obtained),
            expires_iso=expires.isoformat() if expires else "",
            expires_label=self._label(expires) if expires else "",
            is_expired=bool(expires and expires < date.today()),
            skill_list=SkillParser.parse(certificate.get("skills"))
        )

    @staticmethod
    def to_viewer_data(certificate):
        """What the certificate viewer needs, ready for JSON (from for_display)."""
        return {
            "id": certificate["id"],
            "title": certificate["title"],
            "description": certificate["description"],
            "issuer": certificate.get("issuer") or "",
            "photo": certificate["photo_url"],
            "obtained": certificate["obtained_label"],
            "expires": certificate["expires_label"],
            "expired": certificate["is_expired"],
            "skills": certificate["skill_list"],
            "credentialId": certificate.get("credential_id") or "",
            "credentialUrl": certificate.get("credential_url") or ""
        }


class MessageRepository(Repository):

    @property
    def table_name(self):
        return "messages"

    def create(self, cursor, values):
        cursor.execute(
            """
            INSERT INTO messages (name, email, message)
            VALUES (%s, %s, %s)
            """,
            (values["name"], values["email"], values["message"])
        )


class ContentVersion:
    """A fingerprint of everything visitors see on the portfolio page.

    It changes whenever the profile, skills, projects, their media or the
    certificates change, so open pages can tell they are out of date.
    Messages and admin accounts are not part of it.
    """

    QUERY = """
        SELECT md5(concat_ws('|',
            (SELECT row_to_json(p)::text FROM profile p WHERE id = 1),
            (SELECT json_agg(x ORDER BY x.id)::text FROM projects x),
            (SELECT json_agg(x ORDER BY x.id)::text FROM project_media x),
            (SELECT json_agg(x ORDER BY x.id)::text FROM certificates x)
        )) AS version
    """

    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    # The dashboard also shows the messages, so its fingerprint adds them.
    DASHBOARD_QUERY = """
        SELECT md5(concat_ws('|',
            ({portfolio}),
            (SELECT json_agg(m ORDER BY m.id)::text FROM messages m)
        )) AS version
    """

    def read(self, cursor):
        cursor.execute(self.QUERY)
        return cursor.fetchone()["version"]

    def read_dashboard(self, cursor):
        portfolio = self.QUERY.replace("AS version", "")
        cursor.execute(self.DASHBOARD_QUERY.format(portfolio=portfolio))
        return cursor.fetchone()["version"]

    def current_dashboard(self):
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            return self.read_dashboard(cursor)
        finally:
            cursor.close()
            connection.close()

    def current(self):
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            return self.read(cursor)
        finally:
            cursor.close()
            connection.close()


# =========================================================
# SERVICES — one object the views receive (encapsulation)
# =========================================================

class AuthService:
    """Looks up an admin and checks their password."""

    def __init__(self, admins):
        self._admins = admins

    def authenticate(self, username, password):
        admin = self._admins.find_by_username(username)
        if admin and PasswordChecker.matches(admin.get("password"), password):
            return admin
        return None


class PortfolioServices:
    """Bundles the repositories, validators and storage the views use."""

    def __init__(self, flask_app, connection_factory, storage):
        self._connection_factory = connection_factory
        self.logger = flask_app.logger
        self.storage = storage
        self.incoming = IncomingFiles(storage)

        self.projects = ProjectRepository(connection_factory)
        self.media = ProjectMediaRepository(connection_factory, storage)
        self.profile = ProfileRepository(connection_factory)
        self.messages = MessageRepository(connection_factory)
        self.certificates = CertificateRepository(connection_factory, storage)
        self.auth = AuthService(AdminRepository(connection_factory))
        self.content_version = ContentVersion(connection_factory)

        self.project_form = ProjectFormValidator()
        self.profile_form = ProfileFormValidator()
        self.contact_form = ContactFormValidator()
        self.certificate_form = CertificateFormValidator()
        self.project_media = ProjectMediaValidator()
        self.hero_picture = ProfilePictureValidator("hero picture")
        self.about_picture = ProfilePictureValidator()
        self.certificate_photo = CertificatePhotoValidator()
        self.cv = CVValidator()

        # Form field name -> the validator its files must pass.
        self.upload_rules = {
            "media": self.project_media,
            "hero_picture": self.hero_picture,
            "picture": self.about_picture,
            "photo": self.certificate_photo,
            "cv_file": self.cv
        }

    def connect(self):
        return self._connection_factory()


# =========================================================
# VIEWS — one class per URL (inheritance + polymorphism)
# =========================================================

class BaseView(MethodView):
    """Every view receives the shared services when it is created."""

    def __init__(self, services):
        self._services = services


class PageView(BaseView, ABC):
    """A view that renders an HTML page."""

    @property
    @abstractmethod
    def template_name(self):
        """The Jinja template this page renders."""

    def render(self, status=None, **context):
        page = render_template(self.template_name, **context)
        if status is None:
            return page
        return page, status


class AdminPageView(PageView):
    """A page only a logged-in admin may open."""
    decorators = [login_required]

    def render(self, status=None, **context):
        # A form that failed keeps none of the files the browser uploaded
        # straight to storage for it; the admin chooses them again.
        if status is not None and status >= 400:
            self._services.incoming.discard_unused()
        return super().render(status, **context)

    def dispatch_request(self, *args, **kwargs):
        response = super().dispatch_request(*args, **kwargs)
        # A redirect after POST means the files were saved.
        if request.method == "POST" and getattr(response, "status_code", 200) in (301, 302, 303):
            self._services.incoming.committed()
        return response


class AdminActionView(BaseView):
    """A form action only a logged-in admin may send."""
    decorators = [login_required]


# ---------- Public site ----------

class HomeView(PageView):
    template_name = "index.html"

    def get(self):
        services = self._services
        connection = services.connect()
        cursor = connection.cursor()

        # Read first: if something changes while the page is being built,
        # the page holds the older fingerprint and live-update.js refreshes.
        content_version = services.content_version.read(cursor)
        projects = services.projects.fetch_all(cursor, newest_first=True)
        certificates = services.certificates.fetch_latest(cursor)

        cursor.close()
        services.media.attach_galleries(connection, projects)
        connection.close()

        # Everything the project viewer needs, sent to the page as JSON.
        project_data = [
            ProjectRepository.to_viewer_data(project) for project in projects
        ]

        shown_certificates = [
            services.certificates.for_display(certificate)
            for certificate in certificates
        ]

        return self.render(
            content_version=content_version,
            projects=projects,
            # Filter buttons: only categories that have projects.
            project_categories=ProjectCategories.in_use(projects),
            project_technologies=ProjectTechnologies.in_use(projects),
            project_data=project_data,
            certificates=shown_certificates,
            # Everything the certificate viewer needs, sent as JSON.
            certificate_data=[
                CertificateRepository.to_viewer_data(certificate)
                for certificate in shown_certificates
            ],
            profile=services.profile.load()
        )


class SendMessageView(BaseView):

    def post(self):
        services = self._services
        values, error = services.contact_form.validate(request.form)

        if error:
            flash(error, "error")
            return redirect("/#contact")

        connection = None
        cursor = None

        try:
            connection = services.connect()
            cursor = connection.cursor()
            services.messages.create(cursor, values)
            connection.commit()
        except DatabaseError:
            services.logger.exception("Could not save contact message")
            if connection is not None:
                connection.rollback()
            flash("Sorry, your message could not be sent. Please try again later.", "error")
        else:
            flash("Thanks for reaching out! Your message was sent successfully.", "success")
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

        return redirect("/#contact")


# ---------- Login / logout ----------

class LoginView(PageView):
    template_name = "login.html"

    def get(self):
        return self.render()

    def post(self):
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        admin = self._services.auth.authenticate(username, password)

        if admin:
            session["admin_id"] = admin["id"]
            session["username"] = admin["username"]
            return redirect("/dashboard")

        return self.render(error="Invalid username or password.")


class ContentVersionView(BaseView):
    """Tells open portfolio pages whether the content has changed."""

    def get(self):
        response = jsonify(version=self._services.content_version.current())
        response.headers["Cache-Control"] = "no-store"
        return response


class DashboardVersionView(BaseView):
    """Tells an open dashboard whether messages or content changed.

    Admin only (messages hold visitors' email addresses). Answers with
    JSON instead of a redirect when logged out, so the page can stop.
    """

    def get(self):
        if "admin_id" not in session:
            return jsonify(error="Not logged in."), 401
        response = jsonify(version=self._services.content_version.current_dashboard())
        response.headers["Cache-Control"] = "no-store"
        return response


class FaviconView(BaseView):
    """Browsers and link previews ask for /favicon.ico on their own."""

    def get(self):
        return send_from_directory(
            os.path.join(os.path.dirname(__file__), "static"),
            "favicon.png",
            mimetype="image/png",
            max_age=7 * 24 * 60 * 60
        )


class CVView(BaseView):
    """/cv opens the CV in the browser; /cv/download saves it.

    Stable addresses, so links keep working when the admin uploads a new CV.
    """

    def __init__(self, services, download=False):
        super().__init__(services)
        self._download = download

    def get(self):
        profile = self._services.profile.load()
        if not profile["cv_file"]:
            return redirect(url_for("home") + "#about")
        storage = self._services.storage
        if self._download:
            save_as = re.sub(r"[^A-Za-z0-9]+", "-", profile["full_name"]).strip("-") + "-CV.pdf"
            return storage.download_response(profile["cv_file"], save_as)
        return redirect(storage.url(profile["cv_file"]))


class LogoutView(BaseView):

    def get(self):
        session.clear()
        return redirect("/login")


# ---------- Dashboard ----------

class DashboardView(AdminPageView):
    template_name = "dashboard.html"

    def get(self):
        services = self._services
        connection = services.connect()
        cursor = connection.cursor()

        # Read first, like the home page: dashboard-live.js compares it.
        dashboard_version = services.content_version.read_dashboard(cursor)
        projects = services.projects.fetch_all(cursor, newest_first=True)
        message_count = services.messages.count(cursor)
        messages = services.messages.fetch_all(cursor)
        certificates = services.certificates.fetch_latest(cursor)

        cursor.close()
        services.media.attach_galleries(connection, projects)
        connection.close()

        return self.render(
            dashboard_version=dashboard_version,
            projects=projects,
            project_count=len(projects),
            message_count=message_count,
            messages=messages,
            certificates=[
                services.certificates.for_display(certificate)
                for certificate in certificates
            ],
            profile=services.profile.load()
        )


# ---------- Projects ----------

class AddProjectView(AdminPageView):
    template_name = "add_project.html"

    def get(self):
        return self.render(form={})

    def post(self):
        services = self._services
        values, error = services.project_form.validate(request.form)
        uploads, media_error = services.project_media.validate_many(
            services.incoming.all("media")
        )
        error = error or media_error

        if error:
            return self.render(400, error=error, form=request.form)

        try:
            saved_paths = services.storage.save_all(uploads)
        except OSError:
            services.logger.exception("Could not save uploaded project media")
            return self.render(
                500,
                error="The media files could not be saved. Please try again.",
                form=request.form
            )

        connection = None
        cursor = None

        try:
            connection = services.connect()
            cursor = connection.cursor()
            project_id = services.projects.create(cursor, values)
            services.media.insert(
                cursor,
                project_id,
                [filename for _, filename in uploads]
            )
            connection.commit()
        except DatabaseError:
            services.logger.exception("Could not create portfolio project")
            if connection is not None:
                connection.rollback()
            services.storage.discard(saved_paths)
            return self.render(
                500,
                error="The project could not be saved. Please try again.",
                form=request.form
            )
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

        flash("Project added successfully.", "success")
        return redirect("/dashboard#projects")


class EditProjectView(AdminPageView):
    template_name = "edit_project.html"

    def _open_project(self, project_id):
        """Return (connection, cursor, project with gallery) or None if missing."""
        services = self._services
        connection = services.connect()
        cursor = connection.cursor()

        project = services.projects.find_by_id(cursor, project_id)

        if not project:
            cursor.close()
            connection.close()
            return None

        services.media.attach_galleries(connection, [project])
        return connection, cursor, project

    def get(self, project_id):
        opened = self._open_project(project_id)
        if opened is None:
            return redirect("/dashboard")

        connection, cursor, project = opened
        cursor.close()
        connection.close()

        return self.render(project=project, form={})

    def post(self, project_id):
        opened = self._open_project(project_id)
        if opened is None:
            return redirect("/dashboard")

        services = self._services
        connection, cursor, project = opened
        gallery = project["gallery"]

        values, error = services.project_form.validate(request.form)
        remove_ids = {
            int(value)
            for value in request.form.getlist("remove_media")
            if value.isdigit()
        } & {media["id"] for media in gallery}
        uploads, media_error = services.project_media.validate_many(
            services.incoming.all("media"),
            existing_count=len(gallery) - len(remove_ids)
        )
        error = error or media_error

        if error:
            cursor.close()
            connection.close()
            return self.render(400, project=project, error=error, form=request.form)

        try:
            saved_paths = services.storage.save_all(uploads)
        except OSError:
            services.logger.exception("Could not save new project media")
            cursor.close()
            connection.close()
            return self.render(
                500,
                project=project,
                error="The media files could not be saved. Please try again.",
                form=request.form
            )

        removed_files = [
            media["filename"] for media in gallery if media["id"] in remove_ids
        ]

        try:
            services.projects.update(cursor, project_id, values)

            if remove_ids:
                services.media.delete_many(cursor, project_id, remove_ids)

            if uploads:
                services.media.insert(
                    cursor,
                    project_id,
                    [filename for _, filename in uploads],
                    services.media.next_position(cursor, project_id)
                )

            connection.commit()
        except DatabaseError:
            services.logger.exception("Could not update portfolio project %s", project_id)
            connection.rollback()
            services.storage.discard(saved_paths)
            cursor.close()
            connection.close()
            return self.render(
                500,
                project=project,
                error="The project could not be updated. Please try again.",
                form=request.form
            )

        cursor.close()
        connection.close()

        for filename in removed_files:
            try:
                services.storage.remove(filename)
            except OSError:
                services.logger.exception("Could not remove project media: %s", filename)

        flash("Project updated successfully.", "success")
        return redirect("/dashboard#projects")


class DeleteProjectView(AdminActionView):

    def post(self, project_id):
        services = self._services
        connection = services.connect()
        cursor = connection.cursor()

        project = services.projects.find_by_id(cursor, project_id)

        if not project:
            cursor.close()
            connection.close()
            flash("That project could not be found.", "error")
            return redirect("/dashboard")

        services.media.attach_galleries(connection, [project])

        try:
            # The project's project_media rows are removed by ON DELETE CASCADE.
            services.projects.delete_by_id(cursor, project_id)
            connection.commit()
        except DatabaseError:
            services.logger.exception("Could not delete portfolio project %s", project_id)
            connection.rollback()
            cursor.close()
            connection.close()
            flash("The project could not be deleted. Please try again.", "error")
            return redirect("/dashboard#projects")

        cursor.close()
        connection.close()

        failed_removals = 0
        for media in project["gallery"]:
            try:
                services.storage.remove(media["filename"])
            except OSError:
                services.logger.exception(
                    "Could not remove media of deleted project: %s", media["filename"]
                )
                failed_removals += 1

        if failed_removals:
            flash("Project deleted, but some of its media files could not be removed.", "warning")

        flash("Project deleted successfully.", "success")
        return redirect("/dashboard#projects")


# ---------- Profile ----------

class EditProfileView(AdminPageView):
    template_name = "edit_profile.html"

    def get(self):
        return self.render(profile=self._services.profile.load(), form={})

    def post(self):
        services = self._services
        profile = services.profile.load()

        values, error = services.profile_form.validate(request.form)
        uploads = {
            "hero_picture": services.incoming.one("hero_picture"),
            "picture": services.incoming.one("picture"),
            "cv_file": services.incoming.one("cv_file")
        }
        new_hero_picture, hero_picture_error = services.hero_picture.validate(
            uploads["hero_picture"]
        )
        new_picture, picture_error = services.about_picture.validate(
            uploads["picture"]
        )
        new_cv, cv_error = services.cv.validate(uploads["cv_file"])
        error = error or hero_picture_error or picture_error or cv_error

        if error:
            return self.render(400, profile=profile, error=error, form=request.form)

        # field name -> (new file name, file to keep when no new upload)
        pictures = {
            "hero_picture": (
                new_hero_picture,
                None if request.form.get("remove_hero_picture") == "on"
                else profile["hero_picture"]
            ),
            "picture": (
                new_picture,
                None if request.form.get("remove_picture") == "on"
                else profile["picture"]
            ),
            "cv_file": (
                new_cv,
                None if request.form.get("remove_cv_file") == "on"
                else profile["cv_file"]
            )
        }
        final_pictures = {
            field: new_name or kept_name
            for field, (new_name, kept_name) in pictures.items()
        }
        connection = None
        cursor = None

        try:
            saved_paths = services.storage.save_all([
                (uploads[field], new_name)
                for field, (new_name, _) in pictures.items()
                if new_name
            ])
        except OSError:
            services.logger.exception("Could not save profile pictures")
            return self.render(
                500,
                profile=profile,
                error="The file could not be saved. Please try again.",
                form=request.form
            )

        try:
            connection = services.connect()
            cursor = connection.cursor()
            services.profile.update(cursor, values, final_pictures)
            connection.commit()
        except DatabaseError:
            services.logger.exception("Could not update profile")
            if connection is not None:
                connection.rollback()
            services.storage.discard(saved_paths)
            return self.render(
                500,
                profile=profile,
                error="Your profile could not be saved. Please try again.",
                form=request.form
            )
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

        for field, final_name in final_pictures.items():
            old_name = profile[field]
            if old_name and old_name != final_name:
                try:
                    services.storage.remove(old_name)
                except OSError:
                    services.logger.exception("Could not remove old file: %s", old_name)

        flash("Profile updated successfully.", "success")
        return redirect("/dashboard")


# ---------- Certificates ----------

class AddCertificateView(AdminPageView):
    template_name = "add_certificate.html"

    def get(self):
        return self.render(form={})

    def post(self):
        services = self._services
        values, error = services.certificate_form.validate(request.form)
        upload = services.incoming.one("photo")
        photo, photo_error = services.certificate_photo.validate(upload)
        if not photo_error and not photo:
            photo_error = "Choose a photo of your certificate."
        error = error or photo_error

        if error:
            return self.render(400, error=error, form=request.form)

        try:
            saved_paths = services.storage.save_all([(upload, photo)])
        except OSError:
            services.logger.exception("Could not save certificate photo")
            return self.render(
                500,
                error="The photo could not be saved. Please try again.",
                form=request.form
            )

        connection = None
        cursor = None

        try:
            connection = services.connect()
            cursor = connection.cursor()
            services.certificates.create(cursor, values, photo)
            connection.commit()
        except DatabaseError:
            services.logger.exception("Could not create certificate")
            if connection is not None:
                connection.rollback()
            services.storage.discard(saved_paths)
            return self.render(
                500,
                error="The certificate could not be saved. Please try again.",
                form=request.form
            )
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

        flash("Certificate added successfully.", "success")
        return redirect("/dashboard#certificates")


class EditCertificateView(AdminPageView):
    template_name = "edit_certificate.html"

    def _find(self, certificate_id):
        connection = self._services.connect()
        cursor = connection.cursor()
        try:
            certificate = self._services.certificates.find(cursor, certificate_id)
        finally:
            cursor.close()
            connection.close()
        return certificate

    @staticmethod
    def _not_found():
        flash("That certificate could not be found.", "error")
        return redirect("/dashboard#certificates")

    def get(self, certificate_id):
        certificate = self._find(certificate_id)
        if not certificate:
            return self._not_found()

        return self.render(
            certificate=self._services.certificates.for_display(certificate),
            form={}
        )

    def post(self, certificate_id):
        certificate = self._find(certificate_id)
        if not certificate:
            return self._not_found()

        services = self._services
        shown = services.certificates.for_display(certificate)
        values, error = services.certificate_form.validate(request.form)
        upload = services.incoming.one("photo")
        new_photo, photo_error = services.certificate_photo.validate(upload)
        error = error or photo_error

        if error:
            return self.render(400, certificate=shown, error=error, form=request.form)

        saved_paths = []
        if new_photo:
            try:
                saved_paths = services.storage.save_all([(upload, new_photo)])
            except OSError:
                services.logger.exception("Could not save certificate photo")
                return self.render(
                    500,
                    certificate=shown,
                    error="The photo could not be saved. Please try again.",
                    form=request.form
                )

        connection = None
        cursor = None

        try:
            connection = services.connect()
            cursor = connection.cursor()
            services.certificates.update(
                cursor,
                certificate_id,
                values,
                new_photo or certificate["photo"]
            )
            connection.commit()
        except DatabaseError:
            services.logger.exception("Could not update certificate %s", certificate_id)
            if connection is not None:
                connection.rollback()
            services.storage.discard(saved_paths)
            return self.render(
                500,
                certificate=shown,
                error="The certificate could not be updated. Please try again.",
                form=request.form
            )
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

        # A new photo replaces the old file.
        if new_photo and certificate["photo"]:
            try:
                services.storage.remove(certificate["photo"])
            except OSError:
                services.logger.exception(
                    "Could not remove old certificate photo: %s", certificate["photo"]
                )

        flash("Certificate updated successfully.", "success")
        return redirect("/dashboard#certificates")


class DeleteCertificateView(AdminActionView):

    def post(self, certificate_id):
        services = self._services
        connection = services.connect()
        cursor = connection.cursor()

        certificate = services.certificates.find(cursor, certificate_id)

        if not certificate:
            cursor.close()
            connection.close()
            flash("That certificate could not be found.", "error")
            return redirect("/dashboard#certificates")

        try:
            services.certificates.delete_by_id(cursor, certificate_id)
            connection.commit()
        except DatabaseError:
            services.logger.exception("Could not delete certificate %s", certificate_id)
            connection.rollback()
            cursor.close()
            connection.close()
            flash("The certificate could not be deleted. Please try again.", "error")
            return redirect("/dashboard#certificates")

        cursor.close()
        connection.close()

        try:
            services.storage.remove(certificate["photo"])
        except OSError:
            services.logger.exception(
                "Could not remove photo of deleted certificate: %s", certificate["photo"]
            )
            flash("Certificate deleted, but its photo file could not be removed.", "warning")

        flash("Certificate deleted successfully.", "success")
        return redirect("/dashboard#certificates")


# ---------- Direct uploads ----------

class SignUploadView(AdminActionView):
    """Gives the browser a one-time URL to upload a file straight to storage.

    Used on Vercel, where a request to Flask can be at most 4.5 MB. The
    file is checked with the same validator its form field uses, and
    Flask picks the storage name, so the browser cannot overwrite files.
    """

    def post(self):
        services = self._services
        if not services.storage.supports_direct_upload:
            return jsonify(error="Direct uploads are not available."), 404

        data = request.get_json(silent=True) or {}
        validator = services.upload_rules.get(data.get("field"))
        if validator is None:
            return jsonify(error="Unknown upload field."), 400

        try:
            size = int(data.get("size"))
        except (TypeError, ValueError):
            size = None

        extension, error = validator.check(data.get("filename"), size)
        if error:
            return jsonify(error=f"{data.get('filename')}: {error}"), 400

        stored_as = f"{uuid.uuid4().hex}{extension}"
        try:
            upload_url = services.storage.signed_upload_url(stored_as)
        except OSError:
            services.logger.exception("Could not sign a direct upload")
            return jsonify(error="The upload could not be started. Please try again."), 502

        services.incoming.remember(stored_as)
        return jsonify(stored_as=stored_as, upload_url=upload_url)


# ---------- Messages ----------

class DeleteMessageView(AdminActionView):

    def post(self, message_id):
        services = self._services
        connection = services.connect()
        cursor = connection.cursor()

        try:
            deleted = services.messages.delete_by_id(cursor, message_id) > 0
            connection.commit()
        except DatabaseError:
            services.logger.exception("Could not delete contact message %s", message_id)
            connection.rollback()
            cursor.close()
            connection.close()
            flash("The message could not be deleted. Please try again.", "error")
            return redirect("/dashboard#messages")

        cursor.close()
        connection.close()

        if deleted:
            flash("Message deleted successfully.", "success")
        else:
            flash("That message could not be found.", "error")
        return redirect("/dashboard#messages")


# =========================================================
# APPLICATION
# =========================================================

class PortfolioApp:
    """Creates the Flask app, its settings, services and URL routes."""

    # (URL rule, view class, endpoint name used by url_for)
    ROUTES = (
        ("/", HomeView, "home"),
        ("/login", LoginView, "login"),
        ("/dashboard", DashboardView, "dashboard"),
        ("/add-project", AddProjectView, "add_project"),
        ("/edit-project/<int:project_id>", EditProjectView, "edit_project"),
        ("/edit-profile", EditProfileView, "edit_profile"),
        ("/delete-project/<int:project_id>", DeleteProjectView, "delete_project"),
        ("/add-certificate", AddCertificateView, "add_certificate"),
        ("/edit-certificate/<int:certificate_id>", EditCertificateView, "edit_certificate"),
        ("/delete-certificate/<int:certificate_id>", DeleteCertificateView, "delete_certificate"),
        ("/send-message", SendMessageView, "send_message"),
        ("/delete-message/<int:message_id>", DeleteMessageView, "delete_message"),
        ("/upload-url", SignUploadView, "upload_url"),
        ("/logout", LogoutView, "logout"),
        ("/cv", CVView, "cv"),
        ("/cv/download", CVView, "cv_download", {"download": True}),
        ("/favicon.ico", FaviconView, "favicon"),
        ("/content-version", ContentVersionView, "content_version"),
        ("/dashboard-version", DashboardVersionView, "dashboard_version"),
    )

    def __init__(self, connection_factory=get_db_connection, storage=None):
        self._flask = Flask(__name__)
        self._configure()
        storage = storage or self._choose_storage()
        self._services = PortfolioServices(self._flask, connection_factory, storage)
        self._register_template_helpers(storage)
        self._register_routes()

    @property
    def flask(self):
        return self._flask

    def _configure(self):
        flask_app = self._flask
        # Set FLASK_SECRET_KEY in production. The random fallback is only for
        # local use: logins reset whenever the server restarts.
        flask_app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
        flask_app.config["UPLOAD_FOLDER"] = os.path.join(
            os.path.dirname(__file__),
            "static",
            "uploads"
        )
        if os.getenv("VERCEL"):
            # Vercel serves the site over HTTPS only.
            flask_app.config["SESSION_COOKIE_SECURE"] = True

    def _choose_storage(self):
        """Supabase Storage when it is configured, else the local folder."""
        project_url = os.getenv("SUPABASE_URL")
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if project_url and service_key:
            return SupabaseStorage(
                project_url,
                service_key,
                os.getenv("SUPABASE_BUCKET", "portfolio-media")
            )
        return LocalStorage(self._flask)

    def _register_template_helpers(self, storage):
        # {{ media_url(name) }} works with either storage.
        self._flask.jinja_env.globals.update(
            media_url=storage.url,
            direct_uploads=storage.supports_direct_upload,
            # {{ category_label(project.category) }} and the form's choices.
            category_label=ProjectCategories.display_label,
            category_key=ProjectCategories.filter_key,
            # style="{{ cover_style(project) }}" on a .cover-framed box.
            cover_style=CoverFraming.style,
            # data-technologies="{{ technology_keys(project) }}" on a card.
            technology_keys=ProjectTechnologies.keys,
            category_choices=ProjectCategories.CHOICES,
            custom_category_names=self._services.projects.custom_category_names
        )

    def _register_routes(self):
        # A route may add a dict of extra arguments for its view.
        for rule, view_class, endpoint, *options in self.ROUTES:
            self._flask.add_url_rule(
                rule,
                view_func=view_class.as_view(endpoint, self._services, **(options[0] if options else {}))
            )

    def run(self, **options):
        self._flask.run(**options)


portfolio = PortfolioApp()
app = portfolio.flask


# =========================
# RUN FLASK
# =========================

if __name__ == "__main__":

    portfolio.run(
        debug=True
    )

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

import os
import re
import uuid
from abc import ABC, abstractmethod
from datetime import date
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for
)
from flask.views import MethodView
from mysql.connector import Error as MySQLError
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


class UploadStorage:
    """Saves and removes files in the upload folder.

    The folder is read from the app config on every use, so changing
    ``app.config["UPLOAD_FOLDER"]`` takes effect immediately.
    """

    def __init__(self, flask_app):
        self._app = flask_app

    @property
    def folder(self):
        return self._app.config["UPLOAD_FOLDER"]

    def path_for(self, filename):
        return os.path.join(self.folder, filename)

    def save_all(self, validated):
        """Save validated uploads; on failure removes what was saved and re-raises."""
        saved_paths = []
        try:
            for upload, filename in validated:
                path = self.path_for(filename)
                upload.save(path)
                saved_paths.append(path)
        except OSError:
            self.discard(saved_paths)
            raise
        return saved_paths

    @staticmethod
    def discard(paths):
        for path in paths:
            if os.path.exists(path):
                os.remove(path)

    def remove(self, filename):
        if not filename:
            return

        upload_folder = os.path.realpath(self.folder)
        file_path = os.path.realpath(os.path.join(upload_folder, filename))
        if os.path.commonpath((upload_folder, file_path)) != upload_folder:
            self._app.logger.warning(
                "Skipped deleting media outside upload directory: %s", filename
            )
            return

        if os.path.isfile(file_path):
            os.remove(file_path)


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

    def validate(self, upload):
        """Return (new unique file name, None) or (None, error message)."""
        if not upload or not upload.filename:
            return None, None

        safe_name = secure_filename(upload.filename)
        extension = os.path.splitext(safe_name)[1].lower()
        if not safe_name or extension not in self.extensions:
            return None, self.type_error

        try:
            upload.stream.seek(0, os.SEEK_END)
            size = upload.stream.tell()
            upload.stream.seek(0)
        except (AttributeError, OSError):
            return None, "The selected file could not be read."

        if size > self.max_bytes:
            return None, self.size_error

        return f"{uuid.uuid4().hex}{extension}", None


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


class ProjectFormValidator(FormValidator):

    def validate(self, form):
        title = (form.get("title") or "").strip()
        description = (form.get("description") or "").strip()
        technologies = (form.get("technologies") or "").strip()
        created_at = (form.get("created_at") or "").strip()
        project_link = (form.get("project_link") or "").strip()

        if not title or not description or not created_at:
            return None, "Title, description, and project date are required."

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
            "project_link": project_link
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

    def validate(self, form):
        values = {
            "hero_name": " ".join((form.get("hero_name") or "").split()),
            "hero_intro": (form.get("hero_intro") or "").strip(),
            "description": (form.get("description") or "").strip(),
            "skills": SkillParser.parse(form.get("skills"))
        }
        return values, self._first_error(**values)

    def _first_error(self, hero_name, hero_intro, description, skills):
        if not hero_name or not hero_intro:
            return "Please fill in the hero name and introduction."

        if len(hero_name) > self.HERO_NAME_MAX_LENGTH:
            return f"The hero name must be {self.HERO_NAME_MAX_LENGTH} characters or fewer."

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

    def validate(self, form):
        title = (form.get("title") or "").strip()
        description = (form.get("description") or "").strip()
        obtained_at = (form.get("obtained_at") or "").strip()

        if not title or not obtained_at:
            return None, "Title and date obtained are required."

        if len(title) > self.TITLE_MAX_LENGTH:
            return None, f"Title must be {self.TITLE_MAX_LENGTH} characters or fewer."

        if len(description) > self.DESCRIPTION_MAX_LENGTH:
            return None, "Description must be 2,000 characters or fewer."

        try:
            date.fromisoformat(obtained_at)
        except ValueError:
            return None, "Enter a valid date obtained."

        return {
            "title": title,
            "description": description,
            "obtained_at": obtained_at
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
    # Admin accounts may live in any of these tables.
    CANDIDATE_TABLES = ("admins", "admin", "users", "user")

    @property
    def table_name(self):
        return self.CANDIDATE_TABLES[0]

    def find_by_username(self, username):
        username = (username or "").strip()
        if not username:
            return None

        connection = self._connect()
        cursor = connection.cursor(dictionary=True)

        for table_name in self.CANDIDATE_TABLES:
            try:
                cursor.execute(
                    f"SELECT * FROM `{table_name}` WHERE username = %s LIMIT 1",
                    (username,)
                )
                admin = cursor.fetchone()
                if admin:
                    cursor.close()
                    connection.close()
                    return admin
            except Exception:
                continue

        cursor.close()
        connection.close()
        return None


class ProjectRepository(Repository):

    @property
    def table_name(self):
        return "projects"

    def create(self, cursor, values):
        """Insert a project; returns its new id."""
        cursor.execute(
            """
            INSERT INTO projects
            (title, description, technologies, project_link, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                values["title"],
                values["description"],
                values["technologies"],
                values["project_link"],
                values["created_at"]
            )
        )
        return cursor.lastrowid

    def update(self, cursor, project_id, values):
        cursor.execute(
            """
            UPDATE projects
            SET title = %s,
                description = %s,
                technologies = %s,
                project_link = %s,
                created_at = %s
            WHERE id = %s
            """,
            (
                values["title"],
                values["description"],
                values["technologies"],
                values["project_link"],
                values["created_at"],
                project_id
            )
        )

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
            "gallery": [
                {"url": media["url"], "kind": media["kind"]}
                for media in project["gallery"]
            ]
        }


class ProjectMediaRepository(Repository):
    """The gallery files of every project."""

    # Shared by all instances: the table only needs creating once per run.
    _schema_ready = False

    @property
    def table_name(self):
        return "project_media"

    def ensure_schema(self, connection):
        if ProjectMediaRepository._schema_ready:
            return

        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS project_media (
                    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    project_id INT NOT NULL,
                    filename VARCHAR(255) NOT NULL,
                    position INT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    KEY project_media_project (project_id),
                    CONSTRAINT fk_project_media_project
                        FOREIGN KEY (project_id) REFERENCES projects (id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
                """
            )

            # Projects used to hold a single file in projects.media. Move those
            # into the gallery table once, in one transaction so nothing is
            # copied twice if something fails halfway.
            connection.start_transaction()
            cursor.execute(
                """
                INSERT INTO project_media (project_id, filename, position)
                SELECT id, media, 0
                FROM projects
                WHERE media IS NOT NULL AND media <> ''
                """
            )
            cursor.execute("UPDATE projects SET media = NULL WHERE media IS NOT NULL")
            connection.commit()
        finally:
            cursor.close()

        ProjectMediaRepository._schema_ready = True

    def load_galleries(self, connection, project_ids):
        """Return {project_id: [{"id", "filename", "kind"}, ...]} in display order."""
        self.ensure_schema(connection)
        galleries = {project_id: [] for project_id in project_ids}
        if not project_ids:
            return galleries

        cursor = connection.cursor(dictionary=True)
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
                dict(
                    media,
                    url=url_for("static", filename="uploads/" + media["filename"])
                )
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
        "skills": "Digital Art, Programming, Speed typist, Graphic Design, Animation"
    }

    # Columns added after the profile table was first released; older
    # databases get them through ALTER TABLE in ensure_schema().
    ADDED_COLUMNS = {
        "hero_name": "VARCHAR(60) DEFAULT NULL",
        "hero_intro": "TEXT DEFAULT NULL",
        "hero_picture": "VARCHAR(255) DEFAULT NULL"
    }

    # Shared by all instances: the table only needs checking once per run.
    _schema_ready = False

    @property
    def table_name(self):
        return "profile"

    def ensure_schema(self, cursor):
        if ProfileRepository._schema_ready:
            return

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profile (
                id INT NOT NULL PRIMARY KEY,
                description TEXT NOT NULL,
                picture VARCHAR(255) DEFAULT NULL,
                skills TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL
                    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
            """
        )

        cursor.execute("SHOW COLUMNS FROM profile")
        existing_columns = {
            (column["Field"] if isinstance(column, dict) else column[0])
            for column in cursor.fetchall()
        }
        for column, definition in self.ADDED_COLUMNS.items():
            if column not in existing_columns:
                cursor.execute(f"ALTER TABLE profile ADD COLUMN `{column}` {definition}")

        cursor.execute(
            """
            INSERT IGNORE INTO profile (id, description, picture, skills)
            VALUES (1, %s, %s, %s)
            """,
            (
                self.DEFAULTS["description"],
                self.DEFAULTS["picture"],
                self.DEFAULTS["skills"]
            )
        )
        cursor.execute(
            """
            UPDATE profile
            SET hero_name = COALESCE(hero_name, %s),
                hero_intro = COALESCE(hero_intro, %s)
            WHERE id = 1
            """,
            (self.DEFAULTS["hero_name"], self.DEFAULTS["hero_intro"])
        )
        ProfileRepository._schema_ready = True

    def load(self):
        connection = self._connect()
        cursor = connection.cursor(dictionary=True)

        try:
            self.ensure_schema(cursor)
            cursor.execute(
                """
                SELECT hero_name, hero_intro, hero_picture,
                       description, picture, skills
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
            "skills": SkillParser.parse(row["skills"])
        }

    def update(self, cursor, values, pictures):
        cursor.execute(
            """
            UPDATE profile
            SET hero_name = %s,
                hero_intro = %s,
                hero_picture = %s,
                description = %s,
                picture = %s,
                skills = %s
            WHERE id = 1
            """,
            (
                values["hero_name"],
                values["hero_intro"],
                pictures["hero_picture"],
                values["description"],
                pictures["picture"],
                ", ".join(values["skills"])
            )
        )


class CertificateRepository(Repository):
    """Certificates shown in the portfolio's Certificates section."""

    # Shared by all instances: the table only needs creating once per run.
    _schema_ready = False

    @property
    def table_name(self):
        return "certificates"

    def ensure_schema(self, cursor):
        if CertificateRepository._schema_ready:
            return

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS certificates (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(160) NOT NULL,
                description TEXT NOT NULL,
                photo VARCHAR(255) DEFAULT NULL,
                obtained_at DATE NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
            """
        )
        CertificateRepository._schema_ready = True

    def fetch_latest(self, cursor):
        """All certificates, most recently obtained first."""
        self.ensure_schema(cursor)
        cursor.execute(
            """
            SELECT *
            FROM certificates
            ORDER BY obtained_at DESC, id DESC
            """
        )
        return cursor.fetchall()

    def find(self, cursor, certificate_id):
        self.ensure_schema(cursor)
        return self.find_by_id(cursor, certificate_id)

    def create(self, cursor, values, photo):
        self.ensure_schema(cursor)
        cursor.execute(
            """
            INSERT INTO certificates (title, description, photo, obtained_at)
            VALUES (%s, %s, %s, %s)
            """,
            (values["title"], values["description"], photo, values["obtained_at"])
        )

    def update(self, cursor, certificate_id, values, photo):
        cursor.execute(
            """
            UPDATE certificates
            SET title = %s,
                description = %s,
                photo = %s,
                obtained_at = %s
            WHERE id = %s
            """,
            (
                values["title"],
                values["description"],
                photo,
                values["obtained_at"],
                certificate_id
            )
        )

    @staticmethod
    def for_display(certificate):
        """Add the photo URL and readable dates the templates use."""
        obtained = certificate["obtained_at"]
        return dict(
            certificate,
            photo_url=(
                url_for("static", filename="uploads/" + certificate["photo"])
                if certificate["photo"] else None
            ),
            obtained_iso=obtained.isoformat(),
            obtained_label=f"{obtained:%B} {obtained.day}, {obtained.year}"
        )


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

    def __init__(self, flask_app, connection_factory):
        self._connection_factory = connection_factory
        self.logger = flask_app.logger
        self.storage = UploadStorage(flask_app)

        self.projects = ProjectRepository(connection_factory)
        self.media = ProjectMediaRepository(connection_factory)
        self.profile = ProfileRepository(connection_factory)
        self.messages = MessageRepository(connection_factory)
        self.certificates = CertificateRepository(connection_factory)
        self.auth = AuthService(AdminRepository(connection_factory))

        self.project_form = ProjectFormValidator()
        self.profile_form = ProfileFormValidator()
        self.contact_form = ContactFormValidator()
        self.certificate_form = CertificateFormValidator()
        self.project_media = ProjectMediaValidator()
        self.hero_picture = ProfilePictureValidator("hero picture")
        self.about_picture = ProfilePictureValidator()
        self.certificate_photo = CertificatePhotoValidator()

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


class AdminActionView(BaseView):
    """A form action only a logged-in admin may send."""
    decorators = [login_required]


# ---------- Public site ----------

class HomeView(PageView):
    template_name = "index.html"

    def get(self):
        services = self._services
        connection = services.connect()
        cursor = connection.cursor(dictionary=True)

        projects = services.projects.fetch_all(cursor, newest_first=True)
        certificates = services.certificates.fetch_latest(cursor)

        cursor.close()
        services.media.attach_galleries(connection, projects)
        connection.close()

        # Everything the project viewer needs, sent to the page as JSON.
        project_data = [
            ProjectRepository.to_viewer_data(project) for project in projects
        ]

        return self.render(
            projects=projects,
            project_data=project_data,
            certificates=[
                CertificateRepository.for_display(certificate)
                for certificate in certificates
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
        except MySQLError:
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
        cursor = connection.cursor(dictionary=True)

        projects = services.projects.fetch_all(cursor, newest_first=True)
        message_count = services.messages.count(cursor)
        messages = services.messages.fetch_all(cursor)
        certificates = services.certificates.fetch_latest(cursor)

        cursor.close()
        services.media.attach_galleries(connection, projects)
        connection.close()

        return self.render(
            projects=projects,
            project_count=len(projects),
            message_count=message_count,
            messages=messages,
            certificates=[
                CertificateRepository.for_display(certificate)
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
            request.files.getlist("media")
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
            services.media.ensure_schema(connection)
            cursor = connection.cursor()
            connection.start_transaction()
            project_id = services.projects.create(cursor, values)
            services.media.insert(
                cursor,
                project_id,
                [filename for _, filename in uploads]
            )
            connection.commit()
        except MySQLError:
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
        cursor = connection.cursor(dictionary=True)

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
            request.files.getlist("media"),
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
            connection.start_transaction()
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
        except MySQLError:
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
        cursor = connection.cursor(dictionary=True)

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
        except MySQLError:
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
        new_hero_picture, hero_picture_error = services.hero_picture.validate(
            request.files.get("hero_picture")
        )
        new_picture, picture_error = services.about_picture.validate(
            request.files.get("picture")
        )
        error = error or hero_picture_error or picture_error

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
            )
        }
        final_pictures = {
            field: new_name or kept_name
            for field, (new_name, kept_name) in pictures.items()
        }
        saved_paths = []
        connection = None
        cursor = None

        try:
            for field, (new_name, _) in pictures.items():
                if new_name:
                    path = services.storage.path_for(new_name)
                    request.files[field].save(path)
                    saved_paths.append(path)
        except OSError:
            services.logger.exception("Could not save profile pictures")
            for path in saved_paths:
                os.remove(path)
            return self.render(
                500,
                profile=profile,
                error="The picture could not be saved. Please try again.",
                form=request.form
            )

        try:
            connection = services.connect()
            cursor = connection.cursor()
            services.profile.update(cursor, values, final_pictures)
            connection.commit()
        except MySQLError:
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
                    services.logger.exception("Could not remove old picture: %s", old_name)

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
        upload = request.files.get("photo")
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
        except MySQLError:
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
        cursor = connection.cursor(dictionary=True)
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
            certificate=CertificateRepository.for_display(certificate),
            form={}
        )

    def post(self, certificate_id):
        certificate = self._find(certificate_id)
        if not certificate:
            return self._not_found()

        services = self._services
        shown = CertificateRepository.for_display(certificate)
        values, error = services.certificate_form.validate(request.form)
        upload = request.files.get("photo")
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
        except MySQLError:
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
        cursor = connection.cursor(dictionary=True)

        certificate = services.certificates.find(cursor, certificate_id)

        if not certificate:
            cursor.close()
            connection.close()
            flash("That certificate could not be found.", "error")
            return redirect("/dashboard#certificates")

        try:
            services.certificates.delete_by_id(cursor, certificate_id)
            connection.commit()
        except MySQLError:
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


# ---------- Messages ----------

class DeleteMessageView(AdminActionView):

    def post(self, message_id):
        services = self._services
        connection = services.connect()
        cursor = connection.cursor()

        try:
            deleted = services.messages.delete_by_id(cursor, message_id) > 0
            connection.commit()
        except MySQLError:
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
        ("/logout", LogoutView, "logout"),
    )

    def __init__(self, connection_factory=get_db_connection):
        self._flask = Flask(__name__)
        self._configure()
        self._services = PortfolioServices(self._flask, connection_factory)
        self._register_routes()

    @property
    def flask(self):
        return self._flask

    def _configure(self):
        flask_app = self._flask
        flask_app.secret_key = os.getenv("FLASK_SECRET_KEY", "MikiGajoMatsui2007")
        flask_app.config["UPLOAD_FOLDER"] = os.path.join(
            os.path.dirname(__file__),
            "static",
            "uploads"
        )
        os.makedirs(flask_app.config["UPLOAD_FOLDER"], exist_ok=True)

    def _register_routes(self):
        for rule, view_class, endpoint in self.ROUTES:
            self._flask.add_url_rule(
                rule,
                view_func=view_class.as_view(endpoint, self._services)
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

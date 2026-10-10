import os
import re
import uuid
from functools import wraps
from datetime import date
from urllib.parse import urlparse

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session
)
from mysql.connector import Error as MySQLError
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from db import get_db_connection


app = Flask(__name__)

app.secret_key = os.getenv("FLASK_SECRET_KEY", "MikiGajoMatsui2007")

app.config["UPLOAD_FOLDER"] = os.path.join(
    os.path.dirname(__file__),
    "static",
    "uploads"
)
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

PROJECT_MEDIA_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".mp4", ".webm", ".mov", ".pdf"
}
PROJECT_MEDIA_MAX_BYTES = 16 * 1024 * 1024


def login_required(view_func):
    @wraps(view_func)
    def decorated_function(*args, **kwargs):
        if "admin_id" not in session:
            return redirect("/login")
        return view_func(*args, **kwargs)

    return decorated_function


def get_admin_by_username(username):
    username = (username or "").strip()
    if not username:
        return None

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    tables = ["admins", "admin", "users", "user"]

    for table_name in tables:
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


def password_matches(stored_password, submitted_password):
    if not stored_password:
        return False

    if check_password_hash(stored_password, submitted_password):
        return True

    if stored_password == submitted_password:
        return True

    return False


def validate_project_form(form):
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


def validate_project_media(media):
    if not media or not media.filename:
        return None, None

    safe_name = secure_filename(media.filename)
    extension = os.path.splitext(safe_name)[1].lower()
    if not safe_name or extension not in PROJECT_MEDIA_EXTENSIONS:
        return None, "Choose a PNG, JPG, GIF, WebP, MP4, WebM, MOV, or PDF file."

    try:
        media.stream.seek(0, os.SEEK_END)
        size = media.stream.tell()
        media.stream.seek(0)
    except (AttributeError, OSError):
        return None, "The selected media file could not be read."

    if size > PROJECT_MEDIA_MAX_BYTES:
        return None, "Media files must be 16 MB or smaller."

    return f"{uuid.uuid4().hex}{extension}", None


def remove_project_media(filename):
    if not filename:
        return

    upload_folder = os.path.realpath(app.config["UPLOAD_FOLDER"])
    file_path = os.path.realpath(os.path.join(upload_folder, filename))
    if os.path.commonpath((upload_folder, file_path)) != upload_folder:
        app.logger.warning("Skipped deleting media outside upload directory: %s", filename)
        return

    if os.path.isfile(file_path):
        os.remove(file_path)


# =========================
# HOME
# =========================

@app.route("/")
def home():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT *
        FROM projects
        ORDER BY id DESC
        """
    )

    projects = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template("index.html", projects=projects)


# =========================
# LOGIN
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        admin = get_admin_by_username(username)

        if admin and password_matches(admin.get("password"), password):
            session["admin_id"] = admin["id"]
            session["username"] = admin["username"]
            return redirect("/dashboard")

        return render_template("login.html", error="Invalid username or password.")

    return render_template("login.html")


# =========================
# DASHBOARD
# =========================

@app.route("/dashboard")
@login_required
def dashboard():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT *
        FROM projects
        ORDER BY id DESC
        """
    )
    projects = cursor.fetchall()

    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM messages
        """
    )
    message_count = cursor.fetchone()["total"]

    cursor.execute(
        """
        SELECT *
        FROM messages
        ORDER BY id ASC
        """
    )
    messages = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "dashboard.html",
        projects=projects,
        project_count=len(projects),
        message_count=message_count,
        messages=messages
    )

# =========================
# ADD PROJECT
# =========================

@app.route("/add-project", methods=["GET", "POST"])
@login_required
def add_project():
    if request.method == "POST":
        values, error = validate_project_form(request.form)
        filename, media_error = validate_project_media(request.files.get("media"))
        error = error or media_error

        if error:
            return render_template(
                "add_project.html",
                error=error,
                form=request.form
            ), 400

        media = request.files.get("media")
        saved_path = None
        connection = None
        cursor = None

        if filename:
            saved_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            try:
                media.save(saved_path)
            except OSError:
                app.logger.exception("Could not save uploaded project media")
                return render_template(
                    "add_project.html",
                    error="The media file could not be saved. Please try again.",
                    form=request.form
                ), 500

        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO projects
                (title, description, technologies, media, project_link, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    values["title"],
                    values["description"],
                    values["technologies"],
                    filename,
                    values["project_link"],
                    values["created_at"]
                )
            )
            connection.commit()
        except MySQLError:
            app.logger.exception("Could not create portfolio project")
            if connection is not None:
                connection.rollback()
            if saved_path and os.path.exists(saved_path):
                os.remove(saved_path)
            return render_template(
                "add_project.html",
                error="The project could not be saved. Please try again.",
                form=request.form
            ), 500
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

        return redirect("/dashboard")

    return render_template("add_project.html", form={})

# =========================
# EDIT PROJECT
# =========================

@app.route("/edit-project/<int:project_id>", methods=["GET", "POST"])
@login_required
def edit_project(project_id):
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT *
        FROM projects
        WHERE id = %s
        """,
        (project_id,)
    )
    project = cursor.fetchone()

    if not project:
        cursor.close()
        connection.close()
        return redirect("/dashboard")

    if request.method == "POST":
        values, error = validate_project_form(request.form)
        media, media_error = validate_project_media(request.files.get("media"))
        error = error or media_error

        if error:
            cursor.close()
            connection.close()
            return render_template(
                "edit_project.html",
                project=project,
                error=error,
                form=request.form
            ), 400

        remove_media = request.form.get("remove_media") == "on"
        old_filename = project.get("media")
        new_filename = media or (None if remove_media else old_filename)
        saved_path = None

        if media:
            saved_path = os.path.join(app.config["UPLOAD_FOLDER"], media)
            try:
                request.files["media"].save(saved_path)
            except OSError:
                app.logger.exception("Could not save replacement project media")
                cursor.close()
                connection.close()
                return render_template(
                    "edit_project.html",
                    project=project,
                    error="The media file could not be saved. Please try again.",
                    form=request.form
                ), 500

        try:
            cursor.execute(
                """
                UPDATE projects
                SET title = %s,
                    description = %s,
                    technologies = %s,
                    project_link = %s,
                    created_at = %s,
                    media = %s
                WHERE id = %s
                """,
                (
                    values["title"],
                    values["description"],
                    values["technologies"],
                    values["project_link"],
                    values["created_at"],
                    new_filename,
                    project_id
                )
            )
            connection.commit()
        except MySQLError:
            app.logger.exception("Could not update portfolio project %s", project_id)
            connection.rollback()
            if saved_path and os.path.exists(saved_path):
                os.remove(saved_path)
            cursor.close()
            connection.close()
            return render_template(
                "edit_project.html",
                project=project,
                error="The project could not be updated. Please try again.",
                form=request.form
            ), 500

        cursor.close()
        connection.close()
        if old_filename and old_filename != new_filename:
            try:
                remove_project_media(old_filename)
            except OSError:
                app.logger.exception("Could not remove replaced project media: %s", old_filename)
        return redirect("/dashboard")

    cursor.close()
    connection.close()

    return render_template("edit_project.html", project=project, form={})

# =========================
# DELETE PROJECT
# =========================

@app.route("/delete-project/<int:project_id>", methods=["POST"])
@login_required
def delete_project(project_id):
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT *
        FROM projects
        WHERE id = %s
        """,
        (project_id,)
    )
    project = cursor.fetchone()

    if not project:
        cursor.close()
        connection.close()
        flash("That project could not be found.", "error")
        return redirect("/dashboard")

    try:
        cursor.execute(
            """
            DELETE FROM projects
            WHERE id = %s
            """,
            (project_id,)
        )
        connection.commit()
    except MySQLError:
        app.logger.exception("Could not delete portfolio project %s", project_id)
        connection.rollback()
        cursor.close()
        connection.close()
        flash("The project could not be deleted. Please try again.", "error")
        return redirect("/dashboard#projects")

    cursor.close()
    connection.close()

    try:
        remove_project_media(project.get("media"))
    except OSError:
        app.logger.exception("Project was deleted but its media file could not be removed")
        flash("Project deleted, but its media file could not be removed.", "warning")

    flash("Project deleted successfully.", "success")
    return redirect("/dashboard#projects")

# =========================
# SEND MESSAGE
# =========================

@app.route("/send-message", methods=["POST"])
def send_message():
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    message = (request.form.get("message") or "").strip()

    if not name or not email or not message:
        flash("Please fill in your name, a valid email address, and a message.", "error")
        return redirect("/#contact")

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        flash("Please enter a valid email address.", "error")
        return redirect("/#contact")

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO messages (name, email, message)
            VALUES (%s, %s, %s)
            """,
            (name, email, message)
        )

        connection.commit()
    except MySQLError:
        app.logger.exception("Could not save contact message")
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


# =========================
# DELETE MESSAGE
# =========================

@app.route("/delete-message/<int:message_id>", methods=["POST"])
@login_required
def delete_message(message_id):
    connection = get_db_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            DELETE FROM messages
            WHERE id = %s
            """,
            (message_id,)
        )
        deleted = cursor.rowcount > 0
        connection.commit()
    except MySQLError:
        app.logger.exception("Could not delete contact message %s", message_id)
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

# =========================
# LOGOUT
# =========================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        "/login"
    )


# =========================
# RUN FLASK
# =========================

if __name__ == "__main__":

    app.run(
        debug=True
    )
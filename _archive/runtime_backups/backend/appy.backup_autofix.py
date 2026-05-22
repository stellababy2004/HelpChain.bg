"""Deprecated legacy file. Do not run directly. Use app.py.

This module remains for archival reference only.
"""

import csv
import json
import logging
import math
import os
import sys
from datetime import datetime
from io import StringIO

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    # current_app,  # Премахнат за избягване на circular import
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import Babel, refresh
from flask_babel import gettext as _
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mail import Mail
from flask_migrate import Migrate
from flask_socketio import emit, join_room, leave_room
from flask_talisman import Talisman
from jinja2 import ChoiceLoader, FileSystemLoader
from sqlalchemy.exc import OperationalError

# All imports at the top
from werkzeug.utils import secure_filename

from admin_roles import admin_roles_bp
from backend.models_with_analytics import Task
from permissions import (
    require_admin_login,
    # initialize_default_roles_and_permissions,
    require_permission,
)
from backend.routes.notifications import notification_bp

# Import smart matching engine
from smart_matching import smart_matching_engine

from backend.extensions import db
from backend.models import (
    AdminUser,
    ChatMessage,
    ChatParticipant,
    ChatRoom,
    HelpRequest,
    RoleEnum,
    User,
    Volunteer,
)

# Sentry for error monitoring
# import sentry_sdk
# from sentry_sdk.integrations.flask import FlaskIntegration import random

# Add the backend directory to Python path for imports
backend_dir = os.path.dirname(__file__)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Настройка на logging преди всичко друго
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("helpchain.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger(__name__)


def initialize_default_admin():
    """
    Initialize default admin user if it doesn't exist
    """
    try:
        logger.info("Checking for existing admin user...")
        # Check if admin user exists
        admin_user = AdminUser.query.filter_by(username="admin").first()
        if admin_user:
            logger.info("Admin user already exists")
            return admin_user

        logger.info("Creating default admin user...")
        # Create admin user
        admin_user = AdminUser(
            username="admin",
            email="admin@helpchain.live",
        )
        admin_user.set_password(os.getenv("ADMIN_USER_PASSWORD", "test-password"))
        db.session.add(admin_user)
        db.session.commit()
        logger.info("Default admin user created successfully")
        return admin_user

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating default admin user: {e}", exc_info=True)
        return None


# Add the backend directory to Python path so we can import models
backend_dir = os.path.dirname(__file__)

sys.path.insert(0, backend_dir)

# Also add parent directory in case we need it
parent_dir = os.path.dirname(backend_dir)
sys.path.insert(0, parent_dir)

# Add helpchain_backend/src directory to Python path
helpchain_backend_dir = os.path.join(backend_dir, "helpchain-backend")
src_dir = os.path.join(helpchain_backend_dir, "src")
sys.path.insert(0, src_dir)

logger.debug(f"Python path: {sys.path[:3]}...")  # Debug print
logger.debug(f"Current directory: {os.getcwd()}")

logger.info("Starting HelpChain application...")
logger.info(f"Python version: {sys.version}")
logger.info(f"Working directory: {os.getcwd()}")
logger.info(f"Python path: {sys.path[:3]}...")

logger.info("Starting appy.py...")

#         def enable_2fa(self):
#             self.two_factor_enabled = True

#         def disable_2fa(self):
#             self.two_factor_enabled = False
#             self.totp_secret = None

#     mock_admin = MockAdminUser()
#     AdminUser = MockAdminUser  # Replace with mock

HAS_2FA = False
mock_admin = None

# Email 2FA settings
EMAIL_2FA_ENABLED = False  # Enable email-based 2FA for admin login
EMAIL_2FA_RECIPIENT = "contact@helpchain.live"  # Email to send 2FA codes to


def generate_email_2fa_code():
    """Generate a 6-digit 2FA code"""
    import random

    return str(random.randint(100000, 999999))


def send_email_2fa_code(code, ip_address, user_agent):
    """Send 2FA code via email"""
    try:
        logger.info(f"Attempting to send 2FA code to {EMAIL_2FA_RECIPIENT}")
        from flask_mail import Message

        msg = Message(
            subject="HelpChain - Код за верификация на администратор",
            recipients=[EMAIL_2FA_RECIPIENT],
            sender=app.config["MAIL_DEFAULT_SENDER"],
            body=f"""Здравейте,

Получен е опит за вход в администраторския панел на HelpChain.

Код за верификация: {code}

Детайли за достъпа:
- IP адрес: {ip_address}
- Браузър: {user_agent}
- Време: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

Ако това не сте вие, моля игнорирайте това съобщение.

С уважение,
HelpChain системата
""",
        )

        mail.send(msg)
        logger.info(f"Email 2FA code sent successfully to {EMAIL_2FA_RECIPIENT}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email 2FA code: {e}", exc_info=True)
        # Fallback: save to file
        try:
            logger.warning("Attempting fallback: saving email to file")
            with open("sent_emails.txt", "a", encoding="utf-8") as f:
                f.write(
                    f"Subject: HelpChain - Код за верификация на администратор\nTo: {EMAIL_2FA_RECIPIENT}\nFrom: {app.config['MAIL_DEFAULT_SENDER']}\n\nЗдравейте,\n\nПолучен е опит за вход в администраторския панел на HelpChain.\n\nКод за верификация: {code}\n\nДетайли за достъпа:\n- IP адрес: {ip_address}\n- Браузър: {user_agent}\n- Време: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nАко това не сте вие, моля игнорирайте това съобщение.\n\nС уважение,\nHelpChain системата\n\n{'=' * 50}\n"
                )
            logger.info("Email 2FA code saved to file as fallback")
            return True
        except Exception as file_e:
            logger.error(
                f"Failed to save email 2FA code to file: {file_e}", exc_info=True
            )
            return False


# Зареди environment variables от .env файла (от корена на проекта)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

# Създай папката instance ако не съществува
os.makedirs(os.path.join(os.path.dirname(__file__), "instance"), exist_ok=True)

# Задаваме явни папки за шаблони и статични файлове (адаптирай пътищата ако е нужно)
_templates = os.path.join(os.path.dirname(__file__), "templates")
_static = os.path.join(os.path.dirname(__file__), "static")

# Създаваме приложението с правилните пътища
app = Flask(__name__, template_folder=_templates, static_folder=_static)

# Задаваме SECRET_KEY за сесии и сигурност
app.config["SECRET_KEY"] = os.getenv(
    "SECRET_KEY", "dev-secret-key-change-in-production"
)

# Конфигурация за URL генерация извън контекста на заявка
# app.config["SERVER_NAME"] = os.getenv("SERVER_NAME", "localhost:3000")
app.config["PREFERRED_URL_SCHEME"] = os.getenv("PREFERRED_URL_SCHEME", "http")

# Initialize Sentry for error monitoring
# sentry_sdk.init(
#     dsn=os.getenv("SENTRY_DSN"),
#     integrations=[FlaskIntegration()],
#     traces_sample_rate=1.0,
#     environment="production" if not app.debug else "development",
# )

# Абсолютен път до базата за по-голяма сигурност
basedir = os.path.abspath(os.path.dirname(__file__))
# За production на Render, използвайме /tmp за SQLite база данни
if os.getenv("RENDER") == "true" or os.getenv("PRODUCTION") == "true":
    # В production на Render, използвайме временна директория
    db_path = "/tmp/volunteers.db"
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    logger.info(f"Production mode detected, using database path: {db_path}")
else:
    # Локално development - използвайме instance директория в root проекта
    instance_dir = os.path.join(os.path.dirname(basedir), "instance")
    os.makedirs(instance_dir, exist_ok=True)
    db_path = os.path.join(instance_dir, "volunteers.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    logger.info(f"Development mode, using database path: {db_path}")

logger.info(f"Database URI: {app.config['SQLALCHEMY_DATABASE_URI']}")  # Debug print
logger.debug(f"Database path: {app.config['SQLALCHEMY_DATABASE_URI']}")  # Debug print
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
migrate = Migrate(app, db)

# Езици
app.config["BABEL_DEFAULT_LOCALE"] = "bg"
app.config["BABEL_SUPPORTED_LOCALES"] = ["bg", "en"]
babel = Babel(app)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf"}


def allowed_file(filename):
    """Check if file extension is allowed"""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# Email configuration
app.config["MAIL_DEFAULT_SENDER"] = os.getenv(
    "MAIL_DEFAULT_SENDER", "noreply@helpchain.live"
)
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "True").lower() == "true"
app.config["MAIL_USE_SSL"] = os.getenv("MAIL_USE_SSL", "False").lower() == "true"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")

logger.info("Email configuration loaded")
logger.debug(f"MAIL_SERVER: {app.config.get('MAIL_SERVER')}")
logger.debug(f"MAIL_PORT: {app.config.get('MAIL_PORT')}")
logger.debug(f"MAIL_USE_TLS: {app.config.get('MAIL_USE_TLS')}")
logger.debug(f"MAIL_USERNAME: {app.config.get('MAIL_USERNAME')}")
logger.debug(f"MAIL_DEFAULT_SENDER: {app.config.get('MAIL_DEFAULT_SENDER')}")

# Initialize Mail early
mail = Mail(app)

# Security configurations
app.config["SESSION_COOKIE_SECURE"] = False  # Disabled for development
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  # Changed from Strict for development

# Upload folder configuration
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB limit

if not os.path.exists(app.config["UPLOAD_FOLDER"]):
    os.makedirs(app.config["UPLOAD_FOLDER"])

# Initialize security extensions
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",  # In production, use Redis
)

# Register blueprints after app is created

# Register analytics blueprint first to avoid import issues
from analytics_routes import analytics_bp

app.register_blueprint(analytics_bp)


# Test direct route
@app.route("/test_analytics")
def test_analytics():
    return "test analytics direct"


@app.route("/simple_test")
def simple_test():
    return "simple test works"


app.register_blueprint(admin_roles_bp, url_prefix="/admin/roles")

# Register notification blueprint
app.register_blueprint(notification_bp, url_prefix="/api/notifications")

# CSP configuration - ENFORCED MODE (after 24-48h telemetry)
csp = {
    "default-src": ["'self'"],
    "script-src": ["'self'", "'nonce-{{ nonce }}'"],
    "style-src": [
        "'self'",
        "'unsafe-inline'",
    ],  # Consider removing 'unsafe-inline' if possible
    "img-src": ["'self'", "data:", "https://helpchain.live"],
    "font-src": ["'self'"],
    "connect-src": ["'self'"],
    "frame-ancestors": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
}

# talisman = Talisman(
talisman = Talisman(
    app,
    content_security_policy=csp,  # ENFORCED - no longer report-only
    content_security_policy_report_uri="https://csp-report.helpchain.live/report",
    force_https=False,  # Disabled for development testing
    strict_transport_security=False,  # TEMPORARILY DISABLED FOR TESTING
    strict_transport_security_preload=True,
    strict_transport_security_include_subdomains=True,
    strict_transport_security_max_age=63072000,  # 2 years
    referrer_policy="strict-origin-when-cross-origin",
    permissions_policy={
        "camera": "()",
        "microphone": "()",
        "geolocation": "()",
        "payment": "()",
        "usb": "()",
        "magnetometer": "()",
        "accelerometer": "()",
        "gyroscope": "()",
        "ambient-light-sensor": "()",
        "autoplay": "()",
        "encrypted-media": "()",
        "fullscreen": "()",
        "picture-in-picture": "()",
    },
    feature_policy={},  # Deprecated, but keeping for compatibility
)

# csrf = CSRFProtect(app)  # Disabled for development testing

# CORS configuration - STRICT allowlist (no wildcards)
# cors = CORS(
#     app,
#     resources={
#         r"/api/*": {
#             "origins": [
#                 "https://helpchain.live",
#                 "https://www.helpchain.live",
#                 # Add staging if needed: "https://staging.helpchain.live"
#             ],
#             "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
#             "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
#             "supports_credentials": False,  # Never allow credentials for API
#             "max_age": 86400,  # Cache preflight for 24h
#         }
#     },
#     # Disable CORS for non-API routes (default deny)
# )

# Настройваме Jinja да търси шаблони в няколко възможни директории
_template_dirs = [
    os.path.join(os.path.dirname(__file__), "templates"),
    os.path.join(os.path.dirname(__file__), "HelpChain.bg", "backend", "templates"),
    os.path.join(os.path.dirname(__file__), "helpchain-backend", "src", "templates"),
]
_loaders = [FileSystemLoader(d) for d in _template_dirs if os.path.isdir(d)]
# добавяме текущия loader в края (ако има)
if _loaders:
    app.jinja_loader = ChoiceLoader(
        _loaders + ([app.jinja_loader] if getattr(app, "jinja_loader", None) else [])
    )


# Добавяме strftime филтър за Jinja2
@app.template_filter("strftime")
def strftime_filter(date, format="%Y-%m-%d %H:%M:%S"):
    if date is None:
        return ""
    return date.strftime(format)


# Добавяме strptime филтър за Jinja2
@app.template_filter("strptime")
def strptime_filter(date_string, format="%Y-%m-%dT%H:%M:%S.%f"):
    if date_string is None:
        return ""
    from datetime import datetime

    try:
        return datetime.strptime(date_string, format)
    except ValueError:
        return ""


@app.before_request
def log_request():
    pass


@app.route("/")
def index():
    # безопасно извличаме агрегати — ако моделът липсва или схемата не е съвместима, връщаме fallback
    try:
        volunteers_count = Volunteer.query.count() if "Volunteer" in globals() else 0
    except OperationalError:
        volunteers_count = 0
    except Exception:
        volunteers_count = 0

    try:
        HelpRequestModel = globals().get("HelpRequest")
        if HelpRequestModel is not None:
            requests_count = HelpRequestModel.query.count()
            open_requests = HelpRequestModel.query.filter(
                HelpRequestModel.status != "completed"
            ).count()
        else:
            requests_count = 0
            open_requests = 0
    except OperationalError:
        requests_count = 0
        open_requests = 0
    except Exception:
        requests_count = 0
        open_requests = 0

    if app.jinja_loader:
        return render_template(
            "index.html",
            volunteers_count=volunteers_count,
            requests_count=requests_count,
            open_requests=open_requests,
        )
    return (
        jsonify(
            {
                "volunteers_count": volunteers_count,
                "requests_count": requests_count,
                "open_requests": open_requests,
            }
        ),
        200,
    )


def initialize_default_admin():
    """Initialize default admin user if not exists"""
    try:
        admin_user = AdminUser.query.filter_by(username="admin").first()
        if not admin_user:
            admin_user = AdminUser(username="admin", email="admin@helpchain.live")
            admin_user.set_password("test-password")
            db.session.add(admin_user)
            db.session.commit()
            app.logger.info("Default admin user created")
        else:
            app.logger.info(f"Default admin user found: {admin_user.username}")
        return admin_user
    except Exception as e:
        app.logger.error(f"Error initializing default admin: {e}")
        db.session.rollback()
        return None


@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    logger.info("Admin login route called")
    logger.debug(
        f"Request method: {request.method}, EMAIL_2FA_ENABLED = {EMAIL_2FA_ENABLED}"
    )
    error = None
    if request.method == "POST":
        logger.info("Processing admin login POST request")
        username = request.form.get("username")
        password = request.form.get("password")

        logger.debug(f"Login attempt for username: {username}")

        # Initialize default admin if needed
        admin_user = initialize_default_admin()

        # Check credentials
        if (
            admin_user
            and username == admin_user.username
            and admin_user.check_password(password)
        ):
            logger.info(f"Admin login successful for {username}")
            # Check if 2FA is enabled
            if admin_user.two_factor_enabled:
                logger.info("2FA is enabled, redirecting to 2FA verification")
                session["pending_2fa"] = True
                session["pending_admin_id"] = admin_user.id
                return redirect(url_for("admin_2fa"))
            else:
                logger.info("No 2FA required, redirecting to dashboard")
                # Clear any volunteer session to prevent conflicts
                session.pop("volunteer_logged_in", None)
                session.pop("volunteer_id", None)
                session.pop("volunteer_name", None)
                # Set user session
                session["admin_logged_in"] = True
                session["admin_user_id"] = admin_user.id
                session["admin_username"] = admin_user.username
                session.permanent = True  # Make session persistent
                return redirect(url_for("admin_dashboard"))
        else:
            logger.warning(
                f"Failed login attempt for user: {username}, IP: {request.remote_addr}"
            )
            error = "Грешно потребителско име или парола!"
            # Log failed login attempt
            app.logger.warning(
                f"Failed login attempt for user: {username}, IP: {request.remote_addr}"
            )
    return render_template("admin_login.html", error=error)


@app.route("/logout")
def logout():
    return admin_logout()


@app.route("/admin_logout", methods=["POST"])
def admin_logout():
    session.pop("admin_logged_in", None)
    session.pop("admin_user_id", None)
    session.pop("admin_username", None)
    flash("Излезе от админ панела.", "info")
    return redirect(url_for("admin_login"))


@app.route("/admin_dashboard", endpoint="admin_dashboard")
@require_admin_login
def admin_dashboard():
    # Get real statistics from database
    try:
        # Check if HelpRequest model is available
        total_requests = HelpRequest.query.count()
        pending_requests = HelpRequest.query.filter(
            HelpRequest.status == "pending"
        ).count()
        completed_requests = HelpRequest.query.filter(
            HelpRequest.status == "completed"
        ).count()
        total_volunteers = Volunteer.query.count()
    except Exception as e:
        app.logger.error(f"Error fetching dashboard stats: {e}")
        total_requests = 0
        pending_requests = 0
        completed_requests = 0
        total_volunteers = 0

    requests = {
        "items": [
            {"id": 1, "name": "Мария", "status": "Активен"},
            {"id": 2, "name": "Георги", "status": "Завършен"},
        ]
    }
    logs_dict = {
        1: [{"status": "Активен", "changed_at": "2025-07-22"}],
        2: [{"status": "Завършен", "changed_at": "2025-07-21"}],
    }

    stats = {
        "total_requests": total_requests,
        "pending_requests": pending_requests,
        "completed_requests": completed_requests,
        "total_volunteers": total_volunteers,
    }

    # Get current admin user for template
    current_user = None
    if session.get("admin_user_id"):
        current_user = db.session.get(AdminUser, session.get("admin_user_id"))

    return render_template(
        "admin_dashboard.html",
        requests=requests,
        logs_dict=logs_dict,
        stats=stats,
        current_user=current_user,
    )


@app.route("/profile", methods=["GET", "POST"], endpoint="profile")
def admin_profile():
    # Check if admin is logged in manually
    if not session.get("admin_logged_in"):
        flash("Моля, влезте като администратор.", "error")
        return redirect(url_for("admin_login"))

    # Get current admin user
    admin_user = None
    if session.get("admin_user_id"):
        admin_user = db.session.get(AdminUser, session.get("admin_user_id"))

    if not admin_user:
        flash("Не сте логнат като администратор.", "error")
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()

        if not username or not email:
            flash("Всички полета са задължителни.", "error")
            return render_template("admin_profile.html", current_user=admin_user)

        # Check if username is already taken by another admin
        existing_admin = AdminUser.query.filter(
            AdminUser.username == username, AdminUser.id != admin_user.id
        ).first()

        if existing_admin:
            flash("Потребителското име вече е заето.", "error")
            return render_template("admin_profile.html", current_user=admin_user)

        # Check if email is already taken by another admin
        existing_email = AdminUser.query.filter(
            AdminUser.email == email, AdminUser.id != admin_user.id
        ).first()

        if existing_email:
            flash("Имейлът вече е зает.", "error")
            return render_template("admin_profile.html", current_user=admin_user)

        # Update admin user
        admin_user.username = username
        admin_user.email = email
        db.session.commit()

        flash("Профилът е обновен успешно.", "success")
        return redirect(url_for("admin_profile"))

    return render_template("admin_profile.html", current_user=admin_user)

    if request.method == "POST":
        # Handle profile updates
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()

        # Basic validation
        if not username or not email:
            flash("Потребителското име и имейлът са задължителни.", "error")
            return redirect(url_for("profile"))

        # Check if username is already taken by another admin
        existing_admin = AdminUser.query.filter(
            AdminUser.username == username, AdminUser.id != admin_user.id
        ).first()
        if existing_admin:
            flash("Това потребителско име вече е заето.", "error")
            return redirect(url_for("profile"))

        # Check if email is already taken by another admin
        existing_email = AdminUser.query.filter(
            AdminUser.email == email, AdminUser.id != admin_user.id
        ).first()
        if existing_email:
            flash("Този имейл вече е зает.", "error")
            return redirect(url_for("profile"))

        # Update admin user
        admin_user.username = username
        admin_user.email = email

        try:
            db.session.commit()
            session["admin_username"] = username
            flash("Профилът е обновен успешно.", "success")
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Error updating admin profile: {e}")
            flash("Грешка при обновяване на профила.", "error")

        return redirect(url_for("profile"))

    return render_template("admin_profile.html", current_user=admin_user)


@app.route("/admin_settings", methods=["GET", "POST"], endpoint="admin_settings")
@require_admin_login
def admin_settings():
    # Get current admin user
    admin_user = None
    if session.get("admin_user_id"):
        admin_user = db.session.get(AdminUser, session.get("admin_user_id"))

    if not admin_user:
        flash("Не сте логнат като администратор.", "error")
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        # Handle settings updates (placeholder for now)
        flash("Настройките са запазени успешно.", "success")
        return redirect(url_for("admin_settings"))

    return render_template("admin_settings.html", current_user=admin_user)


@app.route(
    "/notification_dashboard", methods=["GET"], endpoint="notification_dashboard"
)
@require_admin_login
def notification_dashboard():
    # Get current admin user
    admin_user = None
    if session.get("admin_user_id"):
        admin_user = db.session.get(AdminUser, session.get("admin_user_id"))

    if not admin_user:
        flash("Не сте логнат като администратор.", "error")
        return redirect(url_for("admin_login"))

    # Placeholder for notification dashboard
    notifications = [
        {
            "id": 1,
            "type": "new_volunteer",
            "message": "Нов доброволец се регистрира",
            "timestamp": "2024-01-15 10:30",
        },
        {
            "id": 2,
            "type": "new_request",
            "message": "Нова заявка за помощ",
            "timestamp": "2024-01-15 09:15",
        },
    ]

    return render_template(
        "notification_dashboard.html",
        current_user=admin_user,
        notifications=notifications,
    )


@app.route("/export_data", methods=["GET"], endpoint="export_data")
@require_admin_login
def export_data():
    # Get current admin user
    admin_user = None
    if session.get("admin_user_id"):
        admin_user = db.session.get(AdminUser, session.get("admin_user_id"))

    if not admin_user:
        flash("Не сте логнат като администратор.", "error")
        return redirect(url_for("admin_login"))

    return render_template("export_data.html", current_user=admin_user)


@app.route("/admin/email_2fa", methods=["GET", "POST"])
def admin_email_2fa():
    if not session.get("pending_email_2fa"):
        return redirect(url_for("admin_login"))

    # Check if code has expired
    if datetime.now().timestamp() > session.get("email_2fa_expires", 0):
        session.pop("pending_email_2fa", None)
        session.pop("email_2fa_code", None)
        session.pop("email_2fa_expires", None)
        flash("Кодът за верификация е изтекъл. Моля, опитайте отново.", "error")
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        entered_code = request.form.get("code", "").strip()

        # Check code
        if entered_code == session.get("email_2fa_code"):
            # Code is correct, complete login
            admin_user = initialize_default_admin()
            session["user_id"] = admin_user.id
            session["username"] = admin_user.username
            session.pop("pending_email_2fa", None)
            session.pop("email_2fa_code", None)
            session.pop("email_2fa_expires", None)
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Невалиден код за верификация.", "error")

    return render_template("admin_email_2fa.html")


@app.route("/admin/2fa", methods=["GET", "POST"])
def admin_2fa():
    if not session.get("pending_2fa"):
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        token = request.form.get("token", "").strip()

        # Get admin user
        admin_id = session.get("pending_admin_id")
        if not admin_id:
            flash("Сесията е изтекла. Моля, логнете се отново.", "error")
            return redirect(url_for("admin_login"))

        admin_user = db.session.get(AdminUser, admin_id)
        if not admin_user:
            flash("Потребителят не е намерен.", "error")
            return redirect(url_for("admin_login"))

        # Verify TOTP token
        if admin_user.verify_totp(token):
            # 2FA successful, complete login
            session["admin_logged_in"] = True
            session["admin_user_id"] = admin_user.id
            session["admin_username"] = admin_user.username
            session.pop("pending_2fa", None)
            session.pop("pending_admin_id", None)
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Невалиден код за верификация.", "error")

    return render_template("admin_2fa.html")


@app.route("/admin/2fa/setup", methods=["GET", "POST"])
@require_permission("admin_access", redirect_url="admin_login")
def admin_2fa_setup():
    # Get current admin user
    admin_user = None
    if session.get("admin_user_id"):
        admin_user = db.session.get(AdminUser, session.get("admin_user_id"))

    if not admin_user:
        flash("Не сте логнат като администратор.", "error")
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        token = request.form.get("token")
        if admin_user.verify_totp(token):
            admin_user.enable_2fa()
            db.session.commit()
            flash("2FA е активиран успешно!", "success")
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Невалиден код.", "error")

    uri = admin_user.get_totp_uri()
    return render_template("admin_2fa_setup.html", totp_uri=uri)


@app.route("/admin_volunteers", methods=["GET", "POST"])
@require_admin_login
def admin_volunteers():
    # Get filter parameters
    search = request.args.get("search", "")
    location_filter = request.args.get("location", "")
    sort_by = request.args.get("sort", "name")
    sort_order = request.args.get("order", "asc")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 25))

    # Build query
    query = Volunteer.query

    # Apply filters
    if search:
        query = query.filter(
            (Volunteer.name.ilike(f"%{search}%"))
            | (Volunteer.email.ilike(f"%{search}%"))
            | (Volunteer.phone.ilike(f"%{search}%"))
        )

    if location_filter:
        query = query.filter(Volunteer.location.ilike(f"%{location_filter}%"))

    # Apply sorting
    if sort_by == "name":
        query = query.order_by(
            Volunteer.name.asc() if sort_order == "asc" else Volunteer.name.desc()
        )
    elif sort_by == "location":
        query = query.order_by(
            Volunteer.location.asc()
            if sort_order == "asc"
            else Volunteer.location.desc()
        )
    elif sort_by == "created_at":
        query = query.order_by(
            Volunteer.created_at.asc()
            if sort_order == "asc"
            else Volunteer.created_at.desc()
        )
    else:
        query = query.order_by(Volunteer.id.asc())

    # Apply pagination
    total_volunteers = query.count()
    volunteers = query.offset((page - 1) * per_page).limit(per_page).all()

    # Calculate pagination info
    total_pages = (total_volunteers + per_page - 1) // per_page

    return render_template(
        "admin_volunteers.html",
        volunteers=volunteers,
        search=search,
        location_filter=location_filter,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        per_page=per_page,
        total_volunteers=total_volunteers,
        total_pages=total_pages,
    )


@app.route("/admin_volunteers/add", methods=["GET", "POST"])
@require_permission("manage_volunteers", redirect_url="admin_login")
def add_volunteer():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        location = request.form.get("location", "").strip()

        # Validate required fields
        errors = []
        if not name:
            errors.append("Името е задължително")
        if not email:
            errors.append("Имейлът е задължителен")
        if not phone:
            errors.append("Телефонът е задължителен")
        if not location:
            errors.append("Локацията е задължителна")

        # Basic email validation
        if email and "@" not in email:
            errors.append("Невалиден имейл адрес")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("add_volunteer.html")

        # Check if email already exists
        existing_volunteer = Volunteer.query.filter_by(email=email).first()
        if existing_volunteer:
            flash("Доброволец с този имейл вече съществува!", "error")
            return render_template("add_volunteer.html")

        try:
            volunteer = Volunteer(
                name=name, email=email, phone=phone, location=location
            )
            db.session.add(volunteer)
            db.session.commit()
            flash("Доброволецът е добавен успешно!", "success")
            return redirect(url_for("admin_volunteers"))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Error adding volunteer: {e}")
            flash("Грешка при добавяне на доброволец. Опитайте отново.", "error")
            return render_template("add_volunteer.html")

    return render_template("add_volunteer.html")


@app.route("/submit_request", methods=["GET", "POST"])
@limiter.limit("20 per minute; 200 per day")
def submit_request():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        category = request.form.get("category")
        location = request.form.get("location")
        problem = request.form.get("problem")
        captcha = request.form.get("captcha")
        file = request.files.get("file")

        if captcha != "7G5K":
            flash("Грешен код за защита!")
            return redirect(url_for("submit_request"))

        filename = None
        if file and file.filename:
            if not allowed_file(file.filename):
                flash("Позволени са само изображения и PDF!")
                return redirect(url_for("submit_request"))

            # Enhanced file validation
            allowed_mimes = {"image/png", "image/jpg", "image/jpeg", "application/pdf"}
            if file.mimetype not in allowed_mimes:
                flash("Невалиден тип файл!")
                return redirect(url_for("submit_request"))

            # Check file size (additional to MAX_CONTENT_LENGTH)
            file.seek(0, 2)  # Seek to end
            file_size = file.tell()
            file.seek(0)  # Reset to beginning
            if file_size > 5 * 1024 * 1024:  # 5MB
                flash("Файлът е твърде голям (макс. 5MB)!")
                return redirect(url_for("submit_request"))

            # Basic antivirus check (placeholder - integrate with real AV service)
            # TODO: Integrate with ClamAV or similar service
            # For now, just check for suspicious file signatures
            dangerous_signatures = [
                b"<script",
                b"<?php",
                b"<%",
                b"eval(",
                b"javascript:",
            ]
            file_content_start = file.read(1024)
            file.seek(0)  # Reset
            if any(sig in file_content_start.lower() for sig in dangerous_signatures):
                flash("Файлът съдържа подозрително съдържание!")
                return redirect(url_for("submit_request"))

            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(save_path)

        # Използваме подадените полета (логваме) за да не са "unused"
        request_data = {
            "name": name,
            "email": email[:3] + "***",  # Sanitize PII
            "category": category,
            "location": location,
            "problem": (
                problem[:50] + "..." if len(problem) > 50 else problem
            ),  # Truncate
            "filename": filename,
        }
        app.logger.info("submit_request received: %s", request_data)

        return render_template("submit_success.html")
    return render_template("submit_request.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/volunteer_register", methods=["GET", "POST"])
@limiter.limit("10 per minute; 50 per day")
def volunteer_register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        location = request.form.get("location")

        # Провери дали имейлът вече съществува
        existing_volunteer = Volunteer.query.filter_by(email=email).first()
        if existing_volunteer:
            flash("Този имейл вече е регистриран като доброволец.", "error")
            return redirect(url_for("volunteer_register"))

        try:
            volunteer = Volunteer(
                name=name, email=email, phone=phone, location=location
            )
            db.session.add(volunteer)
            db.session.commit()
            logger.info(
                "Volunteer added successfully: %s",
                {"name": name, "email": email[:3] + "***", "location": location},
            )
        except Exception as e:
            logger.error("Database error adding volunteer: %s", str(e))
            return f"Database error: {e}", 500

        # Изпрати имейл нотификация за нов доброволец
        try:
            from flask_mail import Message

            logger.debug(
                "Mail config - SERVER: %s, PORT: %s, USERNAME: %s, PASSWORD: %s",
                app.config.get("MAIL_SERVER"),
                app.config.get("MAIL_PORT"),
                app.config.get("MAIL_USERNAME"),
                "***" if app.config.get("MAIL_PASSWORD") else "None",
            )
            msg = Message(
                subject="Нов доброволец в HelpChain",
                recipients=["contact@helpchain.live"],
                sender=app.config["MAIL_DEFAULT_SENDER"],
                body=f"""Нов доброволец се е регистрирал:

Име: {name}
Имейл: {email}
Телефон: {phone}
Локация: {location}

Моля, свържете се с доброволеца за допълнителна информация.
""",
            )
            logger.debug(
                "Sending volunteer registration email to %s from %s",
                msg.recipients,
                msg.sender,
            )
            mail.send(msg)
            logger.info("Volunteer registration email sent successfully")
        except Exception as e:
            logger.error("Failed to send volunteer registration email: %s", str(e))
            # Fallback: записваме в файл
            try:
                with open("sent_emails.txt", "a", encoding="utf-8") as f:
                    f.write(
                        f"Subject: Нов доброволец в HelpChain\nTo: contact@helpchain.live\nFrom: {app.config['MAIL_DEFAULT_SENDER']}\n\nНов доброволец се е регистрирал:\n\nИме: {name}\nИмейл: {email}\nТелефон: {phone}\nЛокация: {location}\n\nМоля, свържете се с доброволеца за допълнителна информация.\n\n{'=' * 50}\n"
                    )
                logger.info("Volunteer registration email saved to file as fallback")
            except Exception as file_e:
                logger.error("Failed to save email to file: %s", str(file_e))

        app.logger.info(
            "Volunteer registered successfully: %s",
            {
                "name": name,
                "email": email[:3] + "***",
                "phone": phone[:3] + "***",
                "location": location,
            },
        )

        flash("Успешна регистрация! Ще се свържем с вас при нужда.")
        return redirect(url_for("volunteer_register"))
    return render_template("volunteer_register.html")


@app.route("/volunteer_login", methods=["GET", "POST"])
def volunteer_login():
    error = None
    if request.method == "POST":
        email = request.form.get("email")
        app.logger.info(f"POST request received. Email: '{email}'")
        app.logger.info(f"Request form data: {dict(request.form)}")

        # Check if volunteer exists with this email
        try:
            app.logger.info(f"Login attempt for email: {email}")
            volunteer = Volunteer.query.filter_by(email=email).first()
            app.logger.info(f"Volunteer found: {volunteer is not None}")
            if volunteer:
                app.logger.info(
                    f"Volunteer details: ID={volunteer.id}, Name={volunteer.name}, Email={volunteer.email}"
                )
                # Generate 6-digit access code
                import random

                access_code = str(random.randint(100000, 999999))
                app.logger.info(f"Generated access code: {access_code}")

                # Store in session with expiration (15 minutes)
                session["pending_volunteer_login"] = {
                    "email": email,
                    "volunteer_id": volunteer.id,
                    "access_code": access_code,
                    "expires": datetime.now().timestamp() + 900,  # 15 minutes
                }

                # Send email with access code
                try:
                    from flask_mail import Message

                    msg = Message(
                        subject="HelpChain - Код за достъп",
                        recipients=[email],
                        sender=app.config["MAIL_DEFAULT_SENDER"],
                        body=f"""Здравейте {volunteer.name},

Получен е опит за вход в доброволческия панел на HelpChain.

Вашият код за достъп: {access_code}

Кодът е валиден за 15 минути.

Ако това не сте вие, моля игнорирайте това съобщение.

С уважение,
HelpChain системата
""",
                    )
                    mail.send(msg)
                    app.logger.info(f"Access code sent to {email}")
                except Exception as e:
                    app.logger.error(f"Failed to send access code email: {e}")
                    # Fallback: save to file for development
                    try:
                        with open("sent_emails.txt", "a", encoding="utf-8") as f:
                            f.write(
                                f"Subject: HelpChain - Код за достъп\nTo: {email}\nFrom: {app.config['MAIL_DEFAULT_SENDER']}\n\nЗдравейте {volunteer.name},\n\nПолучен е опит за вход в доброволческия панел на HelpChain.\n\nВашият код за достъп: {access_code}\n\nКодът е валиден за 15 минути.\n\nАко това не сте вие, моля игнорирайте това съобщение.\n\nС уважение,\nHelpChain системата\n\n{'=' * 50}\n"
                            )
                        app.logger.info("Access code saved to file as fallback")
                    except Exception as file_e:
                        app.logger.error(f"Failed to save email to file: {file_e}")

                # Redirect to verification page
                app.logger.info("Redirecting to volunteer_verify_code")
                return redirect(url_for("volunteer_verify_code"))
            else:
                error = "Няма регистриран доброволец с този имейл!"
                app.logger.warning(f"No volunteer found with email: {email}")
        except Exception as e:
            error = f"Database error: {e}"
            app.logger.error(
                f"Database error during volunteer login: {e}", exc_info=True
            )
    return render_template("volunteer_login.html", error=error)


@app.route("/volunteer_verify_code", methods=["GET", "POST"])
def volunteer_verify_code():
    # Check if there's a pending login
    pending = session.get("pending_volunteer_login")
    if not pending:
        flash("Няма чакащ процес на вход. Моля, започнете отново.", "error")
        return redirect(url_for("volunteer_login"))

    # Check if code has expired
    if datetime.now().timestamp() > pending.get("expires", 0):
        session.pop("pending_volunteer_login", None)
        flash("Кодът за достъп е изтекъл. Моля, опитайте отново.", "error")
        return redirect(url_for("volunteer_login"))

    error = None
    if request.method == "POST":
        entered_code = request.form.get("code", "").strip()

        if entered_code == pending.get("access_code"):
            # Code is correct, complete login
            volunteer = db.session.get(Volunteer, pending["volunteer_id"])
            if volunteer:
                # Clear any admin session to prevent conflicts
                session.pop("admin_logged_in", None)
                session.pop("admin_user_id", None)
                session.pop("admin_username", None)
                # Set volunteer session
                session.permanent = True
                session["volunteer_logged_in"] = True
                session["volunteer_id"] = volunteer.id
                session["volunteer_name"] = volunteer.name
                # Clear pending login
                session.pop("pending_volunteer_login", None)

                app.logger.info(f"Volunteer {volunteer.name} logged in successfully")
                return redirect(url_for("volunteer_dashboard"))
            else:
                error = "Доброволецът не е намерен."
                session.pop("pending_volunteer_login", None)
        else:
            error = "Невалиден код за достъп."

    return render_template("volunteer_verify_code.html", error=error)


@app.route("/resend_volunteer_code", methods=["POST"])
def resend_volunteer_code():
    """Resend verification code to volunteer email"""
    try:
        # Check if there's a pending login
        pending = session.get("pending_volunteer_login")
        if not pending:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Няма чакащ процес на вход. Моля, започнете отново.",
                    }
                ),
                400,
            )

        # Check if code has expired
        if datetime.now().timestamp() > pending.get("expires", 0):
            session.pop("pending_volunteer_login", None)
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Кодът за достъп е изтекъл. Моля, опитайте отново.",
                    }
                ),
                400,
            )

        # Get volunteer
        volunteer = db.session.get(Volunteer, pending["volunteer_id"])
        if not volunteer:
            return (
                jsonify({"success": False, "message": "Доброволецът не е намерен."}),
                404,
            )

        # Generate new access code
        import random

        access_code = str(random.randint(100000, 999999))

        # Update session with new code and extended expiration
        session["pending_volunteer_login"]["access_code"] = access_code
        session["pending_volunteer_login"]["expires"] = (
            datetime.now().timestamp() + 900
        )  # 15 minutes

        # Send email with new access code
        try:
            from flask_mail import Message

            msg = Message(
                subject="HelpChain - Нов код за достъп",
                recipients=[volunteer.email],
                sender=app.config["MAIL_DEFAULT_SENDER"],
                body=f"""Здравейте {volunteer.name},

Получен е нов опит за вход в доброволческия панел на HelpChain.

Вашият нов код за достъп: {access_code}

Кодът е валиден за 15 минути.

Ако това не сте вие, моля игнорирайте това съобщение.

С уважение,
HelpChain системата
""",
            )
            mail.send(msg)
            app.logger.info(f"New access code sent to {volunteer.email}")
        except Exception as e:
            app.logger.error(f"Failed to send new access code email: {e}")
            # Fallback: save to file for development
            try:
                with open("sent_emails.txt", "a", encoding="utf-8") as f:
                    f.write(
                        f"Subject: HelpChain - Нов код за достъп\nTo: {volunteer.email}\nFrom: {app.config['MAIL_DEFAULT_SENDER']}\n\nЗдравейте {volunteer.name},\n\nПолучен е нов опит за вход в доброволческия панел на HelpChain.\n\nВашият нов код за достъп: {access_code}\n\nКодът е валиден за 15 минути.\n\nАко това не сте вие, моля игнорирайте това съобщение.\n\nС уважение,\nHelpChain системата\n\n{'=' * 50}\n"
                    )
                app.logger.info("New access code saved to file as fallback")
            except Exception as file_e:
                app.logger.error(f"Failed to save email to file: {file_e}")
                return (
                    jsonify(
                        {"success": False, "message": "Грешка при изпращане на имейл."}
                    ),
                    500,
                )

        return jsonify(
            {"success": True, "message": "Нов код е изпратен на вашия имейл."}
        )

    except Exception as e:
        app.logger.error(f"Error resending volunteer code: {e}")
        return (
            jsonify(
                {"success": False, "message": "Възникна грешка при изпращане на кода."}
            ),
            500,
        )


@app.route("/volunteer_dashboard")
def volunteer_dashboard():
    try:
        app.logger.info("Starting volunteer_dashboard function")
        # Check if volunteer is logged in
        if not session.get("volunteer_logged_in"):
            app.logger.info("Volunteer not logged in, redirecting to login")
            flash("Моля, влезте като доброволец.", "warning")
            return redirect(url_for("volunteer_login"))

        # Get current volunteer
        volunteer_id = session.get("volunteer_id")
        app.logger.info(f"Volunteer ID from session: {volunteer_id}")
        volunteer = db.session.get(Volunteer, volunteer_id) if volunteer_id else None
        app.logger.info(f"Volunteer object: {volunteer}")

        if not volunteer:
            app.logger.info("Volunteer not found, redirecting to login")
            flash("Нямате достъп до доброволческия панел", "error")
            return redirect(url_for("volunteer_login"))

        app.logger.info(f"Volunteer found: {volunteer.name} (id: {volunteer.id})")

        # Get real statistics from database
        try:
            from backend.models_with_analytics import Task, TaskPerformance

            # Completed tasks count
            completed_tasks = Task.query.filter_by(
                assigned_to=volunteer_id, status="completed"
            ).count()

            # Active tasks count (assigned but not completed)
            active_tasks_count = (
                Task.query.filter_by(assigned_to=volunteer_id)
                .filter(Task.status.in_(["assigned", "in_progress"]))
                .count()
            )

            # Average rating from performance records
            avg_rating_result = (
                db.session.query(db.func.avg(TaskPerformance.quality_rating))
                .filter_by(volunteer_id=volunteer_id)
                .scalar()
            )

            rating = round(avg_rating_result, 1) if avg_rating_result else 0.0

            # People helped count (unique tasks completed)
            people_helped = completed_tasks  # Approximation

            # Reviews count
            reviews_count = TaskPerformance.query.filter_by(
                volunteer_id=volunteer_id
            ).count()

        except Exception as e:
            app.logger.error(f"Error fetching volunteer stats: {e}")
            completed_tasks = 0
            active_tasks_count = 0
            rating = 0.0
            people_helped = 0
            reviews_count = 0

        # Get active tasks assigned to this volunteer
        try:
            # Get active tasks assigned to this volunteer
            active_tasks_query = (
                Task.query.filter_by(assigned_to=volunteer_id)
                .filter(Task.status.in_(["assigned", "in_progress"]))
                .order_by(Task.created_at.desc())
                .limit(5)
            )

            active_tasks = []
            for task in active_tasks_query:
                # Calculate progress based on status
                if task.status == "assigned":
                    progress = 10
                elif task.status == "in_progress":
                    progress = 50
                else:
                    progress = 0

                # Calculate time remaining (simplified)
                time_remaining = "Няма краен срок"
                if task.deadline:
                    from datetime import datetime

                    now = datetime.utcnow()
                    if task.deadline > now:
                        days_remaining = (task.deadline - now).days
                        if days_remaining == 0:
                            time_remaining = "Днес"
                        elif days_remaining == 1:
                            time_remaining = "1 ден"
                        elif days_remaining < 7:
                            time_remaining = f"{days_remaining} дни"
                        else:
                            time_remaining = f"{days_remaining // 7} седмици"
                    else:
                        time_remaining = "Просрочена"

                active_tasks.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "location": task.location_text or "Не е посочена локация",
                        "date": (
                            task.created_at.strftime("%Y-%m-%d")
                            if task.created_at
                            else "Няма дата"
                        ),
                        "time_remaining": time_remaining,
                        "description": task.description or "Няма описание",
                        "priority": task.priority or "medium",
                        "progress": progress,
                    }
                )

        except Exception as e:
            app.logger.error(f"Error fetching active tasks: {e}")
            active_tasks = []

        # Get available tasks count (open tasks not assigned)
        try:
            available_tasks_count = Task.query.filter_by(status="open").count()
        except Exception as e:
            app.logger.error(f"Error fetching available tasks count: {e}")
            available_tasks_count = 0

        # Get urgent tasks count (high priority open tasks)
        try:
            urgent_tasks_count = Task.query.filter_by(
                status="open", priority="urgent"
            ).count()
        except Exception as e:
            app.logger.error(f"Error fetching urgent tasks count: {e}")
            urgent_tasks_count = 0

        # Prepare stats dictionary
        stats = {
            "completed_tasks": completed_tasks,
            "active_tasks": active_tasks_count,
            "rating": rating,
            "people_helped": people_helped,
            "reviews": reviews_count,
        }

        # Get gamification data
        gamification = {
            "points": volunteer.points,
            "level": volunteer.level,
            "experience": volunteer.experience,
            "level_progress": (
                volunteer.get_level_progress()
                if hasattr(volunteer, "get_level_progress")
                else 0
            ),
            "next_level_exp": (
                (volunteer.level * 100) if hasattr(volunteer, "level") else 100
            ),  # Simple calculation
        }

        app.logger.info("Rendering template with volunteer data")
        return render_template(
            "volunteer_dashboard.html",
            current_user=volunteer,
            stats=stats,
            active_tasks=active_tasks,
            available_tasks=available_tasks_count,
            urgent_tasks=urgent_tasks_count,
            gamification=gamification,
        )

    except Exception as e:
        app.logger.error(f"Error loading volunteer dashboard: {e}", exc_info=True)
        flash("Възникна грешка при зареждането на панела", "error")
        return redirect(url_for("index"))


@app.route("/volunteer_logout")
def volunteer_logout():
    session.pop("volunteer_logged_in", None)
    session.pop("volunteer_id", None)
    session.pop("volunteer_name", None)
    session.pop("email", None)
    flash("Успешен изход", "info")
    return redirect(url_for("index"))


@app.route("/dashboard", endpoint="dashboard")
def dashboard():
    """General dashboard for logged in users"""
    # Check if user is logged in as volunteer
    if session.get("volunteer_logged_in"):
        return redirect(url_for("volunteer_dashboard"))
    # Check if admin is logged in
    elif session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))
    else:
        flash("Моля, влезте в системата", "warning")
        return redirect(url_for("index"))


# Additional volunteer routes for the dashboard functionality
@app.route("/my_tasks", methods=["GET"], endpoint="my_tasks")
def my_tasks():
    # TEMPORARY: Skip login check for testing
    # if not session.get("volunteer_logged_in"):
    #     flash("Моля, влезте като доброволец.", "warning")
    #     return redirect(url_for("volunteer_login"))
    # Placeholder for my tasks page
    return render_template("my_tasks.html")


@app.route("/available_tasks", methods=["GET"], endpoint="available_tasks")
def available_tasks():
    # Check if volunteer is logged in
    if not session.get("volunteer_logged_in"):
        flash("Моля, влезте като доброволец.", "warning")
        return redirect(url_for("volunteer_login"))
    # Placeholder for available tasks page
    return render_template("available_tasks.html")


@app.route("/update_volunteer_profile", methods=["POST"])
def update_volunteer_profile():
    if not session.get("volunteer_logged_in"):
        return jsonify({"success": False, "message": "Не сте логнати"}), 401

    volunteer_id = session.get("volunteer_id")
    volunteer = db.session.get(Volunteer, volunteer_id)
    if not volunteer:
        return jsonify({"success": False, "message": "Доброволецът не е намерен"}), 404

    try:
        # Update profile fields
        volunteer.name = request.form.get("name", volunteer.name)
        volunteer.phone = request.form.get("phone", volunteer.phone)
        volunteer.location = request.form.get("location", volunteer.location)
        volunteer.skills = request.form.get("skills", volunteer.skills)

        db.session.commit()
        return jsonify({"success": True, "message": "Профилът е обновен успешно"})
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating volunteer profile: {e}")
        return (
            jsonify({"success": False, "message": "Грешка при обновяване на профила"}),
            500,
        )


@app.route("/update_volunteer_settings", methods=["POST"])
def update_volunteer_settings():
    if not session.get("volunteer_logged_in"):
        return jsonify({"success": False, "message": "Не сте логнати"}), 401

    volunteer_id = session.get("volunteer_id")
    volunteer = db.session.get(Volunteer, volunteer_id)
    if not volunteer:
        return jsonify({"success": False, "message": "Доброволецът не е намерен"}), 404

    try:
        request.get_json()
        # Here you can save settings to volunteer model or separate settings table
        # For now, just return success
        return jsonify({"success": True, "message": "Настройките са запазени"})
    except Exception as e:
        app.logger.error(f"Error updating volunteer settings: {e}")
        return (
            jsonify(
                {"success": False, "message": "Грешка при запазване на настройките"}
            ),
            500,
        )


@app.route("/achievements", methods=["GET"], endpoint="achievements")
def achievements():
    # Check if volunteer is logged in
    if not session.get("volunteer_logged_in"):
        flash("Моля, влезте като доброволец.", "warning")
        return redirect(url_for("volunteer_login"))
    # Placeholder for achievements page
    return render_template("achievements.html")


@app.route("/volunteer_chat", methods=["GET"], endpoint="volunteer_chat")
def volunteer_chat():
    # Check if volunteer is logged in
    if not session.get("volunteer_logged_in"):
        flash("Моля, влезте като доброволец.", "warning")
        return redirect(url_for("volunteer_login"))
    # Placeholder for volunteer chat page
    return render_template("volunteer_chat.html")


@app.route("/volunteer_reports", methods=["GET"], endpoint="volunteer_reports")
def volunteer_reports():
    # Check if volunteer is logged in
    if not session.get("volunteer_logged_in"):
        flash("Моля, влезте като доброволец.", "warning")
        return redirect(url_for("volunteer_login"))
    # Placeholder for volunteer reports page
    return render_template("volunteer_reports.html")


@app.route("/volunteer_settings", methods=["GET", "POST"], endpoint="volunteer_profile")
def volunteer_settings():
    # Check if volunteer is logged in
    if not session.get("volunteer_logged_in"):
        flash("Моля, влезте като доброволец.", "warning")
        return redirect(url_for("volunteer_login"))
    # Placeholder for volunteer settings page
    return render_template("volunteer_settings.html")


@app.route("/admin_volunteers/edit/<int:id>", methods=["GET", "POST"])
@require_admin_login
def edit_volunteer(id):
    volunteer = Volunteer.query.get_or_404(id)
    if request.method == "POST":
        volunteer.name = request.form["name"]
        volunteer.email = request.form["email"]
        volunteer.phone = request.form["phone"]
        volunteer.location = request.form["location"]  # Добави и локацията тук
        db.session.commit()
        flash("Промените са запазени!", "success")
        return redirect(url_for("admin_volunteers"))
    return render_template("edit_volunteer.html", volunteer=volunteer)


@app.route("/admin_volunteers/delete/<int:id>", methods=["POST"])
@require_admin_login
def delete_volunteer(id):
    volunteer = Volunteer.query.get_or_404(id)
    db.session.delete(volunteer)
    db.session.commit()
    flash("Доброволецът е изтрит успешно!", "success")
    return redirect(url_for("admin_volunteers"))


@app.route("/admin_volunteers/<int:id>")
@require_admin_login
def volunteer_detail(id):
    volunteer = Volunteer.query.get_or_404(id)
    return render_template("volunteer_detail.html", volunteer=volunteer)


@app.route("/export_volunteers")
@require_admin_login
def export_volunteers():
    export_format = request.args.get("format", "csv")
    search = request.args.get("search", "")
    location_filter = request.args.get("location", "")

    # Build query with same filters as admin_volunteers
    query = Volunteer.query

    if search:
        query = query.filter(
            (Volunteer.name.ilike(f"%{search}%"))
            | (Volunteer.email.ilike(f"%{search}%"))
            | (Volunteer.phone.ilike(f"%{search}%"))
        )

    if location_filter:
        query = query.filter(Volunteer.location.ilike(f"%{location_filter}%"))

    volunteers = query.all()

    if export_format == "csv":
        return export_volunteers_csv(volunteers)
    elif export_format == "json":
        return export_volunteers_json(volunteers)
    elif export_format == "pdf":
        return export_volunteers_pdf(volunteers)
    else:
        return export_volunteers_csv(volunteers)


def export_volunteers_csv(volunteers):
    """Export volunteers to CSV format"""
    si = StringIO()
    cw = csv.writer(si)

    # Write header
    cw.writerow(
        [
            "ID",
            "Име",
            "Имейл",
            "Телефон",
            "Локация",
            "Умения",
            "Дата на регистрация",
            "Ширина",
            "Дължина",
        ]
    )

    # Write data
    for v in volunteers:
        cw.writerow(
            [
                v.id,
                v.name,
                v.email,
                v.phone,
                v.location or "",
                getattr(v, "skills", "") or "",
                v.created_at.strftime("%Y-%m-%d %H:%M:%S") if v.created_at else "",
                getattr(v, "latitude", "") or "",
                getattr(v, "longitude", "") or "",
            ]
        )

    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment;filename=volunteers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "Content-Type": "text/csv; charset=utf-8",
        },
    )


def export_volunteers_json(volunteers):
    """Export volunteers to JSON format"""
    volunteers_data = []
    for v in volunteers:
        volunteer_dict = {
            "id": v.id,
            "name": v.name,
            "email": v.email,
            "phone": v.phone,
            "location": v.location,
            "skills": getattr(v, "skills", ""),
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "latitude": getattr(v, "latitude", None),
            "longitude": getattr(v, "longitude", None),
        }
        volunteers_data.append(volunteer_dict)

    export_data = {
        "export_date": datetime.now().isoformat(),
        "total_volunteers": len(volunteers_data),
        "volunteers": volunteers_data,
    }

    return Response(
        json.dumps(export_data, ensure_ascii=False, indent=2),
        mimetype="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment;filename=volunteers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            "Content-Type": "application/json; charset=utf-8",
        },
    )


def export_volunteers_pdf(volunteers):
    """Export volunteers to PDF format"""
    try:
        from io import BytesIO

        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        elements = []

        # Styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=16,
            spaceAfter=30,
            alignment=1,  # Center alignment
        )

        # Title
        title = Paragraph("Списък с доброволци - HelpChain", title_style)
        elements.append(title)
        elements.append(Spacer(1, 12))

        # Export info
        info_text = f"Общо доброволци: {len(volunteers)} | Експортирано на: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        info_paragraph = Paragraph(info_text, styles["Normal"])
        elements.append(info_paragraph)
        elements.append(Spacer(1, 20))

        # Table data
        data = [["ID", "Име", "Имейл", "Телефон", "Локация", "Регистриран"]]

        for v in volunteers:
            data.append(
                [
                    str(v.id),
                    v.name,
                    v.email,
                    v.phone,
                    v.location or "",
                    v.created_at.strftime("%d.%m.%Y") if v.created_at else "",
                ]
            )

        # Create table
        table = Table(
            data,
            colWidths=[
                0.5 * inch,
                1.5 * inch,
                2 * inch,
                1.5 * inch,
                1.5 * inch,
                1 * inch,
            ],
        )

        # Table style
        table_style = TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 12),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                ("TEXTCOLOR", (0, 1), (-1, -1), colors.black),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 10),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ]
        )

        table.setStyle(table_style)
        elements.append(table)

        # Build PDF
        doc.build(elements)
        buffer.seek(0)

        return Response(
            buffer.getvalue(),
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f"attachment;filename=volunteers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            },
        )

    except ImportError:
        # Fallback to CSV if reportlab is not installed
        return export_volunteers_csv(volunteers)


@app.route("/faq")
def faq():
    return render_template("faq.html")


@app.route("/success_stories")
def success_stories():
    return render_template("success_stories.html")


@app.route("/feedback", methods=["GET", "POST"])
@limiter.limit("3 per minute; 10 per hour")  # Rate limit feedback submissions
def feedback():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        message = request.form.get("message")

        # Basic input validation
        if not all([name, email, message]) or len(message) < 10:
            flash("Моля, попълнете всички полета коректно!")
            return redirect(url_for("feedback"))

        # Log feedback with security tags
        app.logger.info(
            "[SECURITY:FEEDBACK] Feedback received from %s <%s>: %s",
            name,
            email,
            message[:100] + "..." if len(message) > 100 else message,
        )
        flash("Благодарим за обратната връзка!")
        return redirect(url_for("feedback"))
    return render_template("feedback.html")


@app.route("/category_help/<category>")
def category_help(category):
    """Показва доброволци по категория помощ"""
    # Дефинираме категориите и техните описания
    categories = {
        "food": {
            "name": "Храна",
            "icon": "fas fa-utensils",
            "color": "success",
        },
        "medical": {
            "name": "Медицинска помощ",
            "icon": "fas fa-medkit",
            "color": "danger",
        },
        "transport": {
            "name": "Транспорт",
            "icon": "fas fa-car",
            "color": "info",
        },
        "other": {
            "name": "Друго",
            "icon": "fas fa-hands-helping",
            "color": "secondary",
        },
    }

    if category not in categories:
        flash("Категорията не е намерена!")
        return redirect(url_for("index"))

    # Филтрираме доброволци които имат тази категория в skills
    # Търсим case-insensitive в skills полето
    volunteers = Volunteer.query.filter(Volunteer.skills.ilike(f"%{category}%")).all()

    # Ако няма доброволци, показваме съобщение
    no_volunteers = len(volunteers) == 0

    # Проверяваме дали потребителят е администратор
    is_admin = session.get("admin_logged_in", False)

    category_display = categories[category]["name"]

    return render_template(
        "category_help.html",
        category=category,
        category_info=categories[category],
        volunteers=volunteers,
        is_admin=is_admin,
        category_display=category_display,
        no_volunteers=no_volunteers,
    )


@app.route("/all_categories")
def all_categories():
    """Показва всички категории помощ"""
    categories = [
        {
            "slug": "medical",
            "name": "Спешна помощ",
            "icon": "fas fa-ambulance",
            "color": "danger",
            "description": "Медицинска помощ, спешни случаи",
        },
        {
            "slug": "transport",
            "name": "Транспорт",
            "icon": "fas fa-car",
            "color": "info",
            "description": "Превоз на хора и стоки",
        },
        {
            "slug": "household",
            "name": "Домакинска помощ",
            "icon": "fas fa-home",
            "color": "success",
            "description": "Почистване, ремонт, грижи за дома",
        },
        {
            "slug": "education",
            "name": "Образование",
            "icon": "fas fa-graduation-cap",
            "color": "warning",
            "description": "Помощ с уроци, консултации",
        },
        {
            "slug": "legal",
            "name": "Правна помощ",
            "icon": "fas fa-gavel",
            "color": "primary",
            "description": "Юридически консултации",
        },
        {
            "slug": "psychological",
            "name": "Психологическа помощ",
            "icon": "fas fa-brain",
            "color": "secondary",
            "description": "Подкрепа и консултации",
        },
    ]

    return render_template("all_categories.html", categories=categories)


@app.route("/chat")
def chat():
    """Main chat page - shows available chat rooms"""
    try:
        # Get public chat rooms
        public_rooms = (
            ChatRoom.query.filter_by(room_type="public", is_active=True)
            .order_by(ChatRoom.created_at.desc())
            .all()
        )

        # Get user's private rooms if logged in
        private_rooms = []
        user_info = {}

        # Check if user is logged in (volunteer or admin)
        if session.get("volunteer_logged_in"):
            volunteer = db.session.get(Volunteer, session.get("volunteer_id"))
            if volunteer:
                user_info = {
                    "type": "volunteer",
                    "id": volunteer.id,
                    "name": volunteer.name,
                }
                # Get rooms where volunteer is participant
                private_rooms = (
                    ChatRoom.query.join(ChatParticipant)
                    .filter(
                        ChatParticipant.volunteer_id == volunteer.id,
                        ChatRoom.room_type.in_(["private", "help_request"]),
                        ChatRoom.is_active,
                    )
                    .all()
                )
        elif session.get("user_id"):
            user = db.session.get(User, session.get("user_id"))
            if user:
                user_info = {"type": "admin", "id": user.id, "name": user.username}

        return render_template(
            "chat.html",
            public_rooms=public_rooms,
            private_rooms=private_rooms,
            user_info=user_info,
        )

    except Exception as e:
        app.logger.error(f"Error loading chat page: {e}")
        flash("Възникна грешка при зареждането на чата", "error")
        return redirect(url_for("index"))


@app.route("/chat/room/<int:room_id>")
def chat_room(room_id):
    """Chat room page"""
    try:
        room = ChatRoom.query.get_or_404(room_id)

        # Check permissions
        if room.room_type == "private":
            # Check if user has access to private room
            has_access = False
            if session.get("volunteer_logged_in"):
                volunteer_id = session.get("volunteer_id")
                participant = ChatParticipant.query.filter_by(
                    room_id=room_id, volunteer_id=volunteer_id
                ).first()
                has_access = participant is not None
            elif session.get("user_id"):
                # Admin has access to all rooms
                has_access = True

            if not has_access:
                flash("Нямате достъп до тази стая", "error")
                return redirect(url_for("chat"))

        # Get user info
        user_info = {}
        if session.get("volunteer_logged_in"):
            volunteer = db.session.get(Volunteer, session.get("volunteer_id"))
            if volunteer:
                user_info = {
                    "type": "volunteer",
                    "id": volunteer.id,
                    "name": volunteer.name,
                }
        elif session.get("user_id"):
            user = db.session.get(User, session.get("user_id"))
            if user:
                user_info = {"type": "admin", "id": user.id, "name": user.username}
        else:
            # Allow anonymous access to public rooms
            if room.room_type == "public":
                user_info = {"type": "guest", "id": 0, "name": "Гост"}
            else:
                flash("Трябва да сте логнати за достъп до тази стая", "error")
                return redirect(url_for("chat"))

        return render_template("chat_room.html", room=room, user_info=user_info)

    except Exception as e:
        app.logger.error(f"Error loading chat room {room_id}: {e}")
        flash("Възникна грешка при зареждането на стаята", "error")
        return redirect(url_for("chat"))


@app.route("/api/chat/rooms", methods=["GET"])
def api_get_chat_rooms():
    """API endpoint to get available chat rooms"""
    try:
        room_type = request.args.get("type", "public")

        if room_type == "public":
            rooms = (
                ChatRoom.query.filter_by(room_type="public", is_active=True)
                .order_by(ChatRoom.created_at.desc())
                .all()
            )
        else:
            # For private rooms, check user permissions
            rooms = []
            if session.get("volunteer_logged_in"):
                volunteer_id = session.get("volunteer_id")
                rooms = (
                    ChatRoom.query.join(ChatParticipant)
                    .filter(
                        ChatParticipant.volunteer_id == volunteer_id,
                        ChatRoom.room_type.in_(["private", "help_request"]),
                        ChatRoom.is_active,
                    )
                    .all()
                )
            elif session.get("user_id"):
                # Admin sees all rooms
                rooms = ChatRoom.query.filter_by(is_active=True).all()

        rooms_data = []
        for room in rooms:
            # Count online participants
            online_count = ChatParticipant.query.filter_by(
                room_id=room.id, is_online=True
            ).count()

            rooms_data.append(
                {
                    "id": room.id,
                    "name": room.name,
                    "description": room.description,
                    "room_type": room.room_type,
                    "created_at": room.created_at.isoformat(),
                    "online_count": online_count,
                }
            )

        return jsonify({"rooms": rooms_data})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat/room/<int:room_id>/messages", methods=["GET"])
def api_get_room_messages(room_id):
    """API endpoint to get room messages"""
    try:
        # Check permissions
        room = db.session.get(ChatRoom, room_id)
        if not room:
            return jsonify({"error": "Room not found"}), 404

        if room.room_type == "private":
            has_access = False
            if session.get("volunteer_logged_in"):
                volunteer_id = session.get("volunteer_id")
                participant = ChatParticipant.query.filter_by(
                    room_id=room_id, volunteer_id=volunteer_id
                ).first()
                has_access = participant is not None
            elif session.get("user_id"):
                has_access = True

            if not has_access:
                return jsonify({"error": "Access denied"}), 403

        # Get messages
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))

        messages = (
            ChatMessage.query.filter_by(room_id=room_id, is_deleted=False)
            .order_by(ChatMessage.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        messages_data = []
        for msg in reversed(messages):
            messages_data.append(
                {
                    "id": msg.id,
                    "sender_name": msg.sender_name,
                    "sender_type": msg.sender_type,
                    "content": msg.content,
                    "message_type": msg.message_type,
                    "file_url": msg.file_url,
                    "file_name": msg.file_name,
                    "file_size": msg.file_size,
                    "created_at": msg.created_at.isoformat(),
                    "reply_to": msg.reply_to_id,
                }
            )

        return jsonify({"messages": messages_data})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat/create_room", methods=["POST"])
def api_create_chat_room():
    """API endpoint to create a new chat room"""
    try:
        data = request.get_json()
        room_name = data.get("name", "").strip()
        room_type = data.get("type", "public")
        description = data.get("description", "").strip()

        if not room_name:
            return jsonify({"error": "Room name is required"}), 400

        # Check permissions for private rooms
        if room_type == "private" and not session.get("user_id"):
            return jsonify({"error": "Only admins can create private rooms"}), 403

        # Get creator info
        creator_id = None
        if session.get("user_id"):
            creator_id = session.get("user_id")

        # Create room
        room = ChatRoom(
            name=room_name,
            description=description,
            room_type=room_type,
            created_by=creator_id,
        )
        db.session.add(room)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "room": {
                    "id": room.id,
                    "name": room.name,
                    "description": room.description,
                    "room_type": room.room_type,
                    "created_at": room.created_at.isoformat(),
                },
            }
        )

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.context_processor
def inject_gettext():
    return dict(_=_)


@app.context_processor
def inject_get_locale():
    def get_locale():
        return request.cookies.get("language") or request.accept_languages.best_match(
            ["bg", "en"]
        )

    return dict(get_locale=get_locale)


@app.route("/set_language/<language>", methods=["POST"])
def set_language(language):
    if language in ["bg", "en"]:  # Поддържани езици
        session["language"] = language
        refresh()
    return redirect(request.referrer or "/")


# Debug logging за mail настройките
logger.debug("MAILTRAP_USERNAME: %s", os.getenv("MAILTRAP_USERNAME"))
logger.debug("MAILTRAP_PASSWORD: %s", "***" if os.getenv("MAILTRAP_PASSWORD") else None)
logger.debug("MAIL_SERVER: %s", os.getenv("MAIL_SERVER"))
logger.debug("MAIL_PORT: %s", os.getenv("MAIL_PORT"))
logger.debug("MAIL_USE_SSL: %s", os.getenv("MAIL_USE_SSL"))
logger.debug("MAIL_USE_TLS: %s", os.getenv("MAIL_USE_TLS"))
logger.debug("MAIL_USERNAME: %s", os.getenv("MAIL_USERNAME"))
logger.debug("MAIL_PASSWORD: %s", "***" if os.getenv("MAIL_PASSWORD") else None)

# Инициализирай Mail след като конфигурацията е заредена
# mail = Mail(app)  # Преместен по-горе

# Инициализирай SocketIO за видео чат функционалност
# socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)  # DISABLED FOR TESTING


# Video chat SocketIO event handlers - DISABLED FOR TESTING
# @socketio.on("join_room")
def handle_join_room(data):
    """Handle joining a video chat room"""
    room = data.get("room")
    user_type = data.get("user_type")  # 'volunteer', 'requester', or 'admin'
    user_id = data.get("user_id")
    user_name = data.get("user_name")

    if not room or not user_type or not user_id:
        emit("error", {"message": "Invalid room join data"})
        return

    join_room(room)
    emit(
        "user_joined",
        {
            "user_type": user_type,
            "user_id": user_id,
            "user_name": user_name,
            "room": room,
        },
        room=room,
        skip_sid=True,
    )  # Notify others in room

    logger.info("User %s (%s) joined room %s", user_name, user_type, room)


# @socketio.on("offer")
def handle_offer(data):
    """Handle WebRTC offer"""
    room = data.get("room")
    offer = data.get("offer")
    user_type = data.get("user_type")
    user_name = data.get("user_name")

    if not room or not offer:
        emit("error", {"message": "Invalid offer data"})
        return

    # Send offer to other participants in the room
    emit(
        "offer",
        {"offer": offer, "user_type": user_type, "user_name": user_name},
        room=room,
        skip_sid=True,
    )


# @socketio.on("answer")
def handle_answer(data):
    """Handle WebRTC answer"""
    room = data.get("room")
    answer = data.get("answer")
    user_type = data.get("user_type")
    user_name = data.get("user_name")

    if not room or not answer:
        emit("error", {"message": "Invalid answer data"})
        return

    # Send answer to other participants in the room
    emit(
        "answer",
        {"answer": answer, "user_type": user_type, "user_name": user_name},
        room=room,
        skip_sid=True,
    )


# @socketio.on("ice_candidate")
def handle_ice_candidate(data):
    """Handle ICE candidate exchange"""
    room = data.get("room")
    candidate = data.get("candidate")
    user_type = data.get("user_type")
    user_name = data.get("user_name")

    if not room or not candidate:
        emit("error", {"message": "Invalid ICE candidate data"})
        return

    # Send ICE candidate to other participants in the room
    emit(
        "ice_candidate",
        {"candidate": candidate, "user_type": user_type, "user_name": user_name},
        room=room,
        skip_sid=True,
    )


# @socketio.on("leave_room")
def handle_leave_room(data):
    """Handle leaving a video chat room"""
    room = data.get("room")
    user_type = data.get("user_type")
    user_id = data.get("user_id")
    user_name = data.get("user_name")

    if room:
        leave_room(room)
        emit(
            "user_left",
            {
                "user_type": user_type,
                "user_id": user_id,
                "user_name": user_name,
                "room": room,
            },
            room=room,
            skip_sid=True,
        )

        logger.info("User %s (%s) left room %s", user_name, user_type, room)


# @socketio.on("chat_message")
def handle_chat_message(data):
    """Handle text chat messages during video calls"""
    room = data.get("room")
    message = data.get("message")
    user_type = data.get("user_type")
    user_name = data.get("user_name")
    timestamp = data.get("timestamp")

    if not room or not message:
        emit("error", {"message": "Invalid chat message data"})
        return

    # Broadcast message to all participants in the room
    emit(
        "chat_message",
        {
            "message": message,
            "user_type": user_type,
            "user_name": user_name,
            "timestamp": timestamp,
        },
        room=room,
    )


# ===== CHAT SYSTEM SocketIO Event Handlers =====


# @socketio.on("join_chat_room")
def handle_join_chat_room(data):
    """Handle joining a chat room"""
    room_id = data.get("room_id")
    user_type = data.get("user_type")  # user, volunteer, admin, guest
    user_id = data.get("user_id")
    user_name = data.get("user_name")

    if not room_id or not user_type or not user_name:
        emit("error", {"message": "Invalid room join data"})
        return

    # Allow guest users with user_id = 0
    if user_type != "guest" and not user_id:
        emit("error", {"message": "Invalid room join data"})
        return

    try:
        with app.app_context():
            # Get or create chat room
            room = ChatRoom.query.filter_by(id=room_id).first()
            if not room:
                emit("error", {"message": "Chat room not found"})
                return

            # Check permissions for private rooms
            if room.room_type == "private" and user_type == "guest":
                emit("error", {"message": "Guests cannot access private rooms"})
                return

            # Check if user is already a participant
            participant = ChatParticipant.query.filter_by(
                room_id=room_id, participant_type=user_type, participant_name=user_name
            ).first()

            if not participant:
                # Add new participant
                participant = ChatParticipant(
                    room_id=room_id,
                    user_id=user_id if user_type == "user" else None,
                    volunteer_id=user_id if user_type == "volunteer" else None,
                    participant_type=user_type,
                    participant_name=user_name,
                    is_online=True,
                )
                db.session.add(participant)
                db.session.commit()
            else:
                # Update online status
                participant.is_online = True
                participant.last_seen = datetime.utcnow()
                db.session.commit()

            # Join SocketIO room
            join_room(f"chat_{room_id}")

            # Send recent messages (last 50)
            recent_messages = (
                ChatMessage.query.filter_by(room_id=room_id, is_deleted=False)
                .order_by(ChatMessage.created_at.desc())
                .limit(50)
                .all()
            )

            messages_data = []
            for msg in reversed(recent_messages):
                messages_data.append(
                    {
                        "id": msg.id,
                        "sender_name": msg.sender_name,
                        "sender_type": msg.sender_type,
                        "content": msg.content,
                        "message_type": msg.message_type,
                        "file_url": msg.file_url,
                        "file_name": msg.file_name,
                        "created_at": msg.created_at.isoformat(),
                        "reply_to": msg.reply_to_id,
                    }
                )

            # Send room info and messages to the user
            emit(
                "room_joined",
                {
                    "room_id": room_id,
                    "room_name": room.name,
                    "messages": messages_data,
                    "participants": get_room_participants(room_id),
                },
            )

            # Notify others in the room
            emit(
                "user_joined_chat",
                {"user_name": user_name, "user_type": user_type, "room_id": room_id},
                room=f"chat_{room_id}",
                skip_sid=True,
            )

            logger.info(f"User {user_name} ({user_type}) joined chat room {room_id}")

    except Exception as e:
        logger.error(f"Error joining chat room: {e}")
        emit("error", {"message": "Failed to join chat room"})


# @socketio.on("leave_chat_room")
def handle_leave_chat_room(data):
    """Handle leaving a chat room"""
    room_id = data.get("room_id")
    user_type = data.get("user_type")
    user_name = data.get("user_name")

    if not room_id or not user_type or not user_name:
        return

    try:
        with app.app_context():
            # Update participant status
            participant = ChatParticipant.query.filter_by(
                room_id=room_id, participant_type=user_type, participant_name=user_name
            ).first()

            if participant:
                participant.is_online = False
                participant.last_seen = datetime.utcnow()
                db.session.commit()

            # Leave SocketIO room
            leave_room(f"chat_{room_id}")

            # Notify others
            emit(
                "user_left_chat",
                {"user_name": user_name, "user_type": user_type, "room_id": room_id},
                room=f"chat_{room_id}",
                skip_sid=True,
            )

            logger.info(f"User {user_name} ({user_type}) left chat room {room_id}")

    except Exception as e:
        logger.error(f"Error leaving chat room: {e}")


# @socketio.on("send_chat_message")
def handle_send_chat_message(data):
    """Handle sending a chat message"""
    room_id = data.get("room_id")
    sender_type = data.get("sender_type")
    sender_name = data.get("sender_name")
    content = data.get("content")
    message_type = data.get("message_type", "text")
    reply_to_id = data.get("reply_to_id")

    if not room_id or not sender_type or not sender_name or not content:
        emit("error", {"message": "Invalid message data"})
        return

    try:
        with app.app_context():
            # Get sender ID
            sender_id = None
            if sender_type == "user":
                user = User.query.filter_by(username=sender_name).first()
                sender_id = user.id if user else None
            elif sender_type == "volunteer":
                volunteer = Volunteer.query.filter_by(name=sender_name).first()
                sender_id = volunteer.id if volunteer else None

            # Create message
            message = ChatMessage(
                room_id=room_id,
                sender_id=sender_id,
                sender_type=sender_type,
                sender_name=sender_name,
                content=content,
                message_type=message_type,
                reply_to_id=reply_to_id,
            )
            db.session.add(message)
            db.session.commit()

            # Prepare message data
            message_data = {
                "id": message.id,
                "sender_name": sender_name,
                "sender_type": sender_type,
                "content": content,
                "message_type": message_type,
                "created_at": message.created_at.isoformat(),
                "reply_to": reply_to_id,
            }

            # Broadcast to room
            emit("new_message", message_data, room=f"chat_{room_id}")

            logger.info(
                f"Message sent in room {room_id} by {sender_name} ({sender_type})"
            )

    except Exception as e:
        logger.error(f"Error sending chat message: {e}")
        emit("error", {"message": "Failed to send message"})


# @socketio.on("typing_start")
def handle_typing_start(data):
    """Handle typing indicator start"""
    room_id = data.get("room_id")
    user_name = data.get("user_name")
    user_type = data.get("user_type")

    if room_id and user_name and user_type:
        emit(
            "user_typing",
            {"user_name": user_name, "user_type": user_type, "is_typing": True},
            room=f"chat_{room_id}",
            skip_sid=True,
        )


# @socketio.on("typing_stop")
def handle_typing_stop(data):
    """Handle typing indicator stop"""
    room_id = data.get("room_id")
    user_name = data.get("user_name")
    user_type = data.get("user_type")

    if room_id and user_name and user_type:
        emit(
            "user_typing",
            {"user_name": user_name, "user_type": user_type, "is_typing": False},
            room=f"chat_{room_id}",
            skip_sid=True,
        )


# @socketio.on("upload_file")
def handle_file_upload(data):
    """Handle file upload in chat"""
    room_id = data.get("room_id")
    sender_type = data.get("sender_type")
    sender_name = data.get("sender_name")
    file_data = data.get("file_data")  # Base64 encoded file
    file_name = data.get("file_name")

    if not all([room_id, sender_type, sender_name, file_data, file_name]):
        emit("error", {"message": "Invalid file upload data"})
        return

    try:
        import base64
        import os

        from werkzeug.utils import secure_filename

        # Decode base64 file
        file_content = base64.b64decode(
            file_data.split(",")[1]
        )  # Remove data:image/... prefix

        # Generate secure filename
        secure_name = secure_filename(file_name)
        file_ext = os.path.splitext(secure_name)[1]

        # Create unique filename
        import uuid

        unique_filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join(
            app.config["UPLOAD_FOLDER"], "chat_files", unique_filename
        )

        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Save file
        with open(file_path, "wb") as f:
            f.write(file_content)

        # Get sender ID
        sender_id = None
        if sender_type == "user":
            user = User.query.filter_by(username=sender_name).first()
            sender_id = user.id if user else None
        elif sender_type == "volunteer":
            volunteer = Volunteer.query.filter_by(name=sender_name).first()
            sender_id = volunteer.id if volunteer else None

        # Create file message
        message = ChatMessage(
            room_id=room_id,
            sender_id=sender_id,
            sender_type=sender_type,
            sender_name=sender_name,
            content=f"Файл: {file_name}",
            message_type="file",
            file_url=f"/uploads/chat_files/{unique_filename}",
            file_name=file_name,
            file_size=len(file_content),
        )
        db.session.add(message)
        db.session.commit()

        # Prepare message data
        message_data = {
            "id": message.id,
            "sender_name": sender_name,
            "sender_type": sender_type,
            "content": message.content,
            "message_type": "file",
            "file_url": message.file_url,
            "file_name": file_name,
            "file_size": len(file_content),
            "created_at": message.created_at.isoformat(),
        }

        # Broadcast to room
        emit("new_message", message_data, room=f"chat_{room_id}")

        logger.info(f"File uploaded in room {room_id} by {sender_name}: {file_name}")

    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        emit("error", {"message": "Failed to upload file"})


def get_room_participants(room_id):
    """Get list of room participants"""
    try:
        participants = ChatParticipant.query.filter_by(room_id=room_id).all()
        return [
            {
                "name": p.participant_name,
                "type": p.participant_type,
                "is_online": p.is_online,
                "last_seen": p.last_seen.isoformat() if p.last_seen else None,
            }
            for p in participants
        ]
    except Exception:
        return []


@app.route("/.well-known/security.txt")
def security_txt():
    return Response(
        """Contact: mailto:security@helpchain.live
Expires: 2025-12-31T23:59:59.000Z
Preferred-Languages: bg, en
Canonical: https://helpchain.live/.well-known/security.txt
Policy: https://helpchain.live/privacy
""",
        mimetype="text/plain",
    )


@app.route("/admin", methods=["GET"])
def admin_panel():
    try:
        # безопасно извличаме агрегати — ако моделът липсва или схемата не е съвместима, връщаме fallback
        try:
            volunteers_count = (
                Volunteer.query.count() if "Volunteer" in globals() else 0
            )
        except OperationalError:
            volunteers_count = 0
        except Exception:
            volunteers_count = 0

        try:
            admins_count = (
                User.query.filter_by(role=RoleEnum.superadmin).count()
                if "User" in globals() and "RoleEnum" in globals()
                else 0
            )
        except OperationalError:
            admins_count = 0
        except Exception:
            admins_count = 0

        try:
            HelpRequestModel = globals().get("HelpRequest")
            if HelpRequestModel is not None:
                requests_count = HelpRequestModel.query.count()
            else:
                requests_count = 0
        except OperationalError:
            requests_count = 0
        except Exception:
            requests_count = 0

        active_sessions = 1  # Placeholder for active sessions

        return render_template(
            "admin.html",
            volunteers_count=volunteers_count,
            admins_count=admins_count,
            requests_count=requests_count,
            active_sessions=active_sessions,
        )
    except Exception as e:
        app.logger.error(f"Error loading admin template: {e}")
        # fallback прост HTML
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>Admin</title>"
            "<style>body{font-family:Arial,Helvetica,sans-serif;padding:1rem}h1{font-size:18px}</style>"
            "</head><body><h1>Admin panel (placeholder)</h1><p>Шаблонът admin.html не е намерен.</p></body></html>",
            200,
        )


# Добави mock за mail.send за тестване (симулира изпращане без реални SMTP заявки)
# from unittest.mock import patch

# Mock mail.send за всички изпращания на имейли
# mock_mail_send = patch.object(mail, 'send', side_effect=lambda msg: app.logger.info(f"Mocked email sent: {msg.subject} to {msg.recipients}")).start()


# Геолокационни API endpoints
@app.route("/api/volunteers/nearby", methods=["GET"])
def get_nearby_volunteers():
    try:
        lat = float(request.args.get("lat", 0))
        lng = float(request.args.get("lng", 0))
        radius_km = float(request.args.get("radius", 10))  # default 10km

        # Simple distance calculation using Haversine formula
        # For production, consider using PostGIS or similar

        def haversine_distance(lat1, lon1, lat2, lon2):
            R = 6371  # Earth radius in km
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lng - lon1)
            a = (
                math.sin(dlat / 2) ** 2
                + math.cos(math.radians(lat1))
                * math.cos(math.radians(lat2))
                * math.sin(dlon / 2) ** 2
            )
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            return R * c

        volunteers = Volunteer.query.filter(
            Volunteer.latitude.isnot(None), Volunteer.longitude.isnot(None)
        ).all()

        nearby = []
        for vol in volunteers:
            if vol.latitude and vol.longitude:
                distance = haversine_distance(lat, lng, vol.latitude, vol.longitude)
                if distance <= radius_km:
                    nearby.append(
                        {
                            "id": vol.id,
                            "name": vol.name,
                            "email": vol.email,
                            "phone": vol.phone,
                            "skills": vol.skills,
                            "location": vol.location,
                            "latitude": vol.latitude,
                            "longitude": vol.longitude,
                            "distance_km": round(distance, 2),
                        }
                    )

        # Sort by distance
        nearby.sort(key=lambda x: x["distance_km"])

        return (
            jsonify(
                {
                    "volunteers": nearby,
                    "count": len(nearby),
                    "search_location": {"lat": lat, "lng": lng},
                    "radius_km": radius_km,
                }
            ),
            200,
        )

    except ValueError:
        return jsonify({"error": "Invalid coordinates or radius"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/volunteers/<int:volunteer_id>/location", methods=["PUT"])
def update_volunteer_location(volunteer_id):
    try:
        data = request.get_json()
        lat = data.get("latitude")
        lng = data.get("longitude")
        location_text = data.get("location")  # Optional location text

        if lat is None or lng is None:
            return jsonify({"error": "latitude and longitude required"}), 400

        vol = db.session.get(Volunteer, volunteer_id)
        if not vol:
            return jsonify({"error": "Volunteer not found"}), 404

        vol.latitude = float(lat)
        vol.longitude = float(lng)
        if location_text is not None:
            vol.location = location_text.strip()
        db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "volunteer_id": volunteer_id,
                    "location": {
                        "lat": vol.latitude,
                        "lng": vol.longitude,
                        "text": vol.location,
                    },
                }
            ),
            200,
        )

    except ValueError:
        return jsonify({"error": "Invalid coordinates"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin_analytics", methods=["GET"])
# @require_admin_login
def admin_analytics():
    """Advanced Analytics Dashboard с real-time графики и прогнози"""
    try:
        from admin_analytics import AnalyticsEngine

        # Получаваме основни статистики
        dashboard_stats = AnalyticsEngine.get_dashboard_stats(days=30)

        # Геолокационни данни за карта
        geo_data = AnalyticsEngine.get_geo_data()

        # Трендове за последните 12 месеца
        trends_data = AnalyticsEngine.get_trends_data(months=12)

        # Прогнози за следващите 3 месеца
        predictions = AnalyticsEngine.get_predictions(months=3)

        # Live статистики
        live_stats = AnalyticsEngine.get_live_stats()

        # Категорийни статистики
        category_stats = AnalyticsEngine.get_category_stats()

        # Performance метрики
        performance_metrics = AnalyticsEngine.get_performance_metrics()

        return render_template(
            "admin_analytics.html",
            dashboard_stats=dashboard_stats,
            geo_data=geo_data,
            trends_data=trends_data,
            predictions=predictions,
            live_stats=live_stats,
            category_stats=category_stats,
            performance_metrics=performance_metrics,
        )

    except Exception as e:
        app.logger.error(f"Error loading analytics dashboard: {e}")
        flash("Възникна грешка при зареждането на аналитиката", "error")
        return redirect(url_for("admin_dashboard"))


@app.route("/api/analytics/live", methods=["GET"])
@require_admin_login
def api_analytics_live():
    """API endpoint за live analytics data"""
    try:
        from admin_analytics import AnalyticsEngine

        live_data = AnalyticsEngine.get_live_stats()
        return jsonify(live_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analytics/trends", methods=["GET"])
@require_admin_login
def api_analytics_trends():
    """API endpoint за trends data"""
    try:
        from admin_analytics import AnalyticsEngine

        months = int(request.args.get("months", 12))
        trends_data = AnalyticsEngine.get_trends_data(months=months)
        return jsonify(trends_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analytics/ml-insights", methods=["GET"])
@require_admin_login
def api_analytics_ml_insights():
    """API endpoint за ML insights данни"""
    try:
        from advanced_analytics import AdvancedAnalytics

        advanced_analytics = AdvancedAnalytics()
        ml_insights = advanced_analytics.generate_insights_report()

        return jsonify(
            {
                "success": True,
                "ml_insights": ml_insights,
                "timestamp": datetime.now().isoformat(),
            }
        )
    except Exception as e:
        app.logger.error(f"Error getting ML insights: {e}")
        return jsonify({"success": False, "error": str(e), "ml_insights": None}), 500


@app.route("/api/analytics/anomalies", methods=["GET"])
@require_admin_login
def api_analytics_anomalies():
    """API endpoint за аномалии в реално време"""
    try:
        from advanced_analytics import AdvancedAnalytics

        timeframe_days = int(request.args.get("timeframe", 7))
        advanced_analytics = AdvancedAnalytics()
        anomalies = advanced_analytics.detect_anomalies(timeframe_days=timeframe_days)

        return jsonify(
            {
                "success": True,
                "anomalies": anomalies,
                "timeframe_days": timeframe_days,
                "timestamp": datetime.now().isoformat(),
            }
        )
    except Exception as e:
        app.logger.error(f"Error detecting anomalies: {e}")
        return jsonify({"success": False, "error": str(e), "anomalies": []}), 500


# ===== SMART MATCHING SYSTEM API =====


@app.route("/api/tasks", methods=["GET"])
@require_admin_login
def api_get_tasks():
    """Получава списък със задачи"""
    try:
        from backend.models_with_analytics import Task

        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        status_filter = request.args.get("status")
        category_filter = request.args.get("category")

        query = Task.query

        if status_filter:
            query = query.filter_by(status=status_filter)
        if category_filter:
            query = query.filter_by(category=category_filter)

        tasks = query.order_by(Task.created_at.desc()).paginate(
            page=page, per_page=per_page
        )

        return jsonify(
            {
                "success": True,
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "description": t.description,
                        "category": t.category,
                        "priority": t.priority,
                        "status": t.status,
                        "location_required": t.location_required,
                        "location_text": t.location_text,
                        "estimated_hours": t.estimated_hours,
                        "deadline": t.deadline.isoformat() if t.deadline else None,
                        "assigned_to": t.assigned_to,
                        "volunteer_name": t.volunteer.name if t.volunteer else None,
                        "created_at": t.created_at.isoformat(),
                    }
                    for t in tasks.items
                ],
                "pagination": {
                    "page": tasks.page,
                    "per_page": tasks.per_page,
                    "total": tasks.total,
                    "pages": tasks.pages,
                    "has_next": tasks.has_next,
                    "has_prev": tasks.has_prev,
                },
            }
        )
    except Exception as e:
        app.logger.error(f"Error getting tasks: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tasks", methods=["POST"])
@require_admin_login
def api_create_task():
    """Създава нова задача"""
    try:
        from backend.models_with_analytics import Task

        data = request.get_json()

        task = Task(
            title=data["title"],
            description=data.get("description"),
            category=data.get("category"),
            priority=data.get("priority", "medium"),
            status="open",
            location_required=data.get("location_required", False),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
            location_text=data.get("location_text"),
            required_skills=(
                json.dumps(data.get("required_skills", []))
                if data.get("required_skills")
                else None
            ),
            preferred_skills=(
                json.dumps(data.get("preferred_skills", []))
                if data.get("preferred_skills")
                else None
            ),
            estimated_hours=data.get("estimated_hours"),
            deadline=(
                datetime.fromisoformat(data["deadline"])
                if data.get("deadline")
                else None
            ),
            created_by=session.get(
                "admin_logged_in"
            ),  # Use session instead of current_user
        )

        db.session.add(task)
        db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "task": {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "created_at": task.created_at.isoformat(),
                    },
                }
            ),
            201,
        )
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error creating task: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tasks/<int:task_id>", methods=["GET"])
@require_admin_login
def api_get_task(task_id):
    """Получава детайли за конкретна задача"""
    try:
        from backend.models_with_analytics import Task

        task = Task.query.get_or_404(task_id)

        return jsonify(
            {
                "success": True,
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "category": task.category,
                    "priority": task.priority,
                    "status": task.status,
                    "location_required": task.location_required,
                    "latitude": task.latitude,
                    "longitude": task.longitude,
                    "location_text": task.location_text,
                    "required_skills": (
                        json.loads(task.required_skills) if task.required_skills else []
                    ),
                    "preferred_skills": (
                        json.loads(task.preferred_skills)
                        if task.preferred_skills
                        else []
                    ),
                    "estimated_hours": task.estimated_hours,
                    "deadline": task.deadline.isoformat() if task.deadline else None,
                    "start_date": (
                        task.start_date.isoformat() if task.start_date else None
                    ),
                    "assigned_to": task.assigned_to,
                    "volunteer": (
                        {
                            "id": task.volunteer.id,
                            "name": task.volunteer.name,
                            "email": task.volunteer.email,
                        }
                        if task.volunteer
                        else None
                    ),
                    "assigned_at": (
                        task.assigned_at.isoformat() if task.assigned_at else None
                    ),
                    "completed_at": (
                        task.completed_at.isoformat() if task.completed_at else None
                    ),
                    "created_by": task.created_by,
                    "created_at": task.created_at.isoformat(),
                    "updated_at": task.updated_at.isoformat(),
                },
            }
        )
    except Exception as e:
        app.logger.error(f"Error getting task {task_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
@require_admin_login
def api_update_task(task_id):
    """Обновява задача"""
    try:
        from backend.models_with_analytics import Task

        task = Task.query.get_or_404(task_id)
        data = request.get_json()

        # Update fields
        for field in [
            "title",
            "description",
            "category",
            "priority",
            "status",
            "location_required",
            "latitude",
            "longitude",
            "location_text",
            "estimated_hours",
        ]:
            if field in data:
                setattr(task, field, data[field])

        if "deadline" in data:
            task.deadline = (
                datetime.fromisoformat(data["deadline"]) if data["deadline"] else None
            )

        if "required_skills" in data:
            task.required_skills = (
                json.dumps(data["required_skills"]) if data["required_skills"] else None
            )

        if "preferred_skills" in data:
            task.preferred_skills = (
                json.dumps(data["preferred_skills"])
                if data["preferred_skills"]
                else None
            )

        db.session.commit()

        return jsonify({"success": True, "message": "Task updated successfully"})
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating task {task_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tasks/<int:task_id>/assign/<int:volunteer_id>", methods=["POST"])
@require_admin_login
def api_assign_task(task_id, volunteer_id):
    """Ръчно разпределя задача на доброволец"""
    try:
        from backend.models_with_analytics import Task, TaskAssignment

        task = Task.query.get_or_404(task_id)
        volunteer = Volunteer.query.get_or_404(volunteer_id)

        # Update task
        task.assigned_to = volunteer_id
        task.assigned_at = datetime.utcnow()
        task.status = "assigned"

        # Create assignment record
        assignment = TaskAssignment(
            task_id=task_id,
            volunteer_id=volunteer_id,
            assigned_at=datetime.utcnow(),
            status="assigned",
            assigned_by="manual",
        )

        db.session.add(assignment)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": f"Task assigned to {volunteer.name}",
                "assignment_id": assignment.id,
            }
        )
    except Exception as e:
        db.session.rollback()
        app.logger.error(
            f"Error assigning task {task_id} to volunteer {volunteer_id}: {e}"
        )
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tasks/<int:task_id>/complete", methods=["POST"])
@require_admin_login
def api_complete_task(task_id):
    """Отбелязва задача като завършена"""
    try:
        from backend.models_with_analytics import Task, TaskPerformance

        task = Task.query.get_or_404(task_id)
        data = request.get_json() or {}

        task.status = "completed"
        task.completed_at = datetime.utcnow()

        # Create performance record if volunteer was assigned
        if task.assigned_to:
            performance = TaskPerformance(
                task_id=task_id,
                volunteer_id=task.assigned_to,
                time_taken_hours=data.get("time_taken_hours"),
                quality_rating=data.get("quality_rating"),
                timeliness_rating=data.get("timeliness_rating"),
                communication_rating=data.get("communication_rating"),
                task_completed=True,
                completion_notes=data.get("completion_notes"),
            )
            db.session.add(performance)

        db.session.commit()

        return jsonify({"success": True, "message": "Task marked as completed"})
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error completing task {task_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ===== SMART MATCHING API =====


@app.route("/api/tasks/<int:task_id>/matches", methods=["GET"])
@require_admin_login
def api_get_task_matches(task_id):
    """Намира най-добрите съпоставяния за задача"""
    try:
        from smart_matching import smart_matching_engine

        limit = int(request.args.get("limit", 5))
        matches = smart_matching_engine.find_best_matches(task_id, limit=limit)

        return jsonify(
            {
                "success": True,
                "task_id": task_id,
                "matches": [
                    {
                        "volunteer": {
                            "id": m["volunteer"].id,
                            "name": m["volunteer"].name,
                            "email": m["volunteer"].email,
                            "phone": m["volunteer"].phone,
                            "location": m["volunteer"].location,
                            "skills": m["volunteer"].skills,
                            "rating": getattr(m["volunteer"], "rating", None),
                        },
                        "scores": m["scores"],
                        "recommendation_reason": m["recommendation_reason"],
                    }
                    for m in matches
                ],
            }
        )
    except Exception as e:
        app.logger.error(f"Error getting task matches for {task_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tasks/<int:task_id>/auto-assign", methods=["POST"])
@require_admin_login
def api_auto_assign_task(task_id):
    """Автоматично разпределя задача на най-добрия кандидат"""
    try:
        from smart_matching import smart_matching_engine

        result = smart_matching_engine.auto_assign_task(task_id)

        if result:
            return jsonify(
                {
                    "success": True,
                    "message": f"Task auto-assigned to {result['volunteer'].name}",
                    "assignment": {
                        "task_id": result["task"].id,
                        "volunteer_id": result["volunteer"].id,
                        "volunteer_name": result["volunteer"].name,
                        "match_score": result["match_score"],
                        "assigned_at": result["assignment"].assigned_at.isoformat(),
                    },
                }
            )
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "No suitable volunteer found for auto-assignment",
                    }
                ),
                400,
            )
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error auto-assigning task {task_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/matching/analytics", methods=["GET"])
@require_admin_login
def api_matching_analytics():
    """Аналитика за ефективността на matching системата"""
    try:
        from smart_matching import smart_matching_engine

        analytics = smart_matching_engine.get_matching_analytics()

        return jsonify({"success": True, "analytics": analytics})
    except Exception as e:
        app.logger.error(f"Error getting matching analytics: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/volunteers/<int:volunteer_id>/task-recommendations", methods=["GET"])
def api_volunteer_task_recommendations(volunteer_id):
    """Препоръчва задачи за конкретен доброволец"""
    try:
        from backend.models_with_analytics import Task
        from smart_matching import smart_matching_engine

        # Вземи отворени задачи
        open_tasks = Task.query.filter_by(status="open").all()

        recommendations = []
        for task in open_tasks:
            match_score = smart_matching_engine._calculate_match_score(
                task, db.session.get(Volunteer, volunteer_id)
            )
            if match_score["overall"] > 40:  # Минимален threshold
                recommendations.append(
                    {
                        "task": {
                            "id": task.id,
                            "title": task.title,
                            "description": task.description,
                            "category": task.category,
                            "priority": task.priority,
                            "location_text": task.location_text,
                            "estimated_hours": task.estimated_hours,
                            "deadline": (
                                task.deadline.isoformat() if task.deadline else None
                            ),
                        },
                        "match_score": match_score["overall"],
                        "skill_match": match_score["skill_match"],
                        "location_match": match_score["location_match"],
                    }
                )

        # Сортирай по match score
        recommendations.sort(key=lambda x: x["match_score"], reverse=True)

        return jsonify(
            {
                "success": True,
                "volunteer_id": volunteer_id,
                "recommendations": recommendations[:10],  # Топ 10
            }
        )
    except Exception as e:
        app.logger.error(
            f"Error getting task recommendations for volunteer {volunteer_id}: {e}"
        )
        return jsonify({"success": False, "error": str(e)}), 500
    """Export analytics data"""
    try:
        from admin_analytics import AnalyticsEngine

        export_format = request.args.get("format", "json")
        data_type = request.args.get("type", "dashboard")

        if data_type == "dashboard":
            data = AnalyticsEngine.get_dashboard_stats()
        elif data_type == "trends":
            data = AnalyticsEngine.get_trends_data()
        elif data_type == "predictions":
            data = AnalyticsEngine.get_predictions()
        else:
            return jsonify({"error": "Internal Server Error"}), 500
            # ...existing code...valid data type"}), 400

        if export_format == "json":
            return Response(
                json.dumps(data, ensure_ascii=False, indent=2, default=str),
                mimetype="application/json",
                headers={
                    "Content-Disposition": f"attachment;filename=analytics_{data_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                },
            )
        elif export_format == "csv":
            # Convert to CSV format
            import csv
            from io import StringIO

            si = StringIO()
            cw = csv.writer(si)

            # Simple CSV export for basic stats
            if data_type == "dashboard":
                cw.writerow(["Metric", "Value"])
                for key, value in data.get("totals", {}).items():
                    cw.writerow([key, value])

            output = si.getvalue()
            return Response(
                output,
                mimetype="text/csv",
                headers={
                    "Content-Disposition": f"attachment;filename=analytics_{data_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                },
            )
        else:
            return jsonify({"error": "Unsupported format"}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/tasks", methods=["GET"])
def admin_tasks():
    """Admin dashboard for task management with smart matching"""
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    try:
        # Get all tasks with their assignments
        tasks = Task.query.options(
            db.joinedload(Task.volunteer), db.joinedload(Task.assignments)
        ).all()

        # Get smart matching analytics
        matching_stats = smart_matching_engine.get_matching_analytics()

        return render_template(
            "admin_tasks.html",
            tasks=tasks,
            matching_stats=matching_stats,
            title="Управление на задачи",
        )
    except Exception as e:
        logger.error("Error loading admin tasks: %s", str(e))
        flash("Грешка при зареждане на задачите", "error")
        return redirect(url_for("admin_dashboard"))


if __name__ == "__main__":
    logger.info("Starting HelpChain application...")
    try:
        with app.app_context():
            # Import AdminUser to ensure it's registered with SQLAlchemy
            from .models import AdminUser

            db.create_all()
        # Initialize default roles and permissions
        # initialize_default_roles_and_permissions()
        logger.info("Database created successfully and default roles initialized")
    except Exception as e:
        logger.error("Error creating database: %s", str(e))
        logger.warning("Continuing without database initialization...")

    logger.info("Starting server...")
    # Use PORT environment variable for production (Render), default to 5000 for development
    port = int(os.environ.get("PORT", 8000))
    logger.info("Server starting on port %d", port)

    try:
        app.run(debug=False, host="127.0.0.1", port=port)
    except Exception as e:
        logger.error("Error starting server: %s", str(e))
        import traceback

        traceback.print_exc()

# За да спреш mock-а в production, добави:
# mock_mail_send.stop()  # Премахни за реални имейли


import pathlib
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect
from alembic.config import Config
from alembic import command
from flask import Flask

try:
    # canonical db instance used by the app
    from backend.extensions import db as canonical_db
except Exception:
    canonical_db = None

try:
    from flask_migrate import Migrate
except Exception:
    Migrate = None


def test_alembic_upgrade_on_clean_sqlite():
    """Integration test: apply Alembic migrations to a fresh SQLite file DB.

    This test creates a temporary SQLite database file, configures Alembic to
    use it, runs `upgrade head` and asserts that at least some expected
    tables were created. This helps catch migration ordering or runtime
    errors in CI early.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[1]

    # Canonical Alembic root is repository-level migrations/
    alembic_ini = repo_root / "migrations" / "alembic.ini"
    assert alembic_ini.exists(), f"alembic.ini not found at {alembic_ini}"

    db_dir = repo_root / "backend" / "instance"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "alembic_test.db"
    if db_file.exists():
        db_file.unlink()
    db_file.touch()
    db_url = f"sqlite:///{db_file}"

    cfg = Config(str(alembic_ini))
    # Force the SQLAlchemy url to our temporary DB
    cfg.set_main_option("sqlalchemy.url", db_url)
    # Ensure Alembic uses the canonical repository migrations folder
    cfg.set_main_option("script_location", str(repo_root / "migrations"))

    # Run the migrations. Alembic's env.py expects a Flask `current_app` with
    # the Flask-Migrate extension registered. Create a minimal app context
    # and register the canonical `db` and `Migrate` so env.py can obtain the
    # engine and metadata.
    if canonical_db is None or Migrate is None:
        raise RuntimeError(
            "Required Flask extensions (backend.extensions or flask_migrate) not importable"
        )

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    # Ensure SQLite can be used from multiple connections in tests
    app.config.setdefault(
        "SQLALCHEMY_ENGINE_OPTIONS", {"connect_args": {"check_same_thread": False}}
    )

    # Initialize extensions and Flask-Migrate
    canonical_db.init_app(app)
    migrate = Migrate(app, canonical_db)

    # Run alembic upgrade within the app context so env.py can reference current_app
    with app.app_context():
        command.upgrade(cfg, "head")

    # Verify that tables were created
    engine = create_engine(db_url)
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    # Expect at least these core tables to be present after applying migrations
    assert len(tables) > 0, "No tables created by Alembic migrations"
    assert "structures" in tables, f"Expected 'structures' table not found in {tables}"


def test_enterprise_structure_migration_upgrade_and_downgrade():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    alembic_ini = repo_root / "migrations" / "alembic.ini"

    db_dir = repo_root / "backend" / "instance"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "alembic_enterprise_structure_test.db"
    if db_file.exists():
        db_file.unlink()
    db_file.touch()
    db_url = f"sqlite:///{db_file}"

    cfg = Config(str(alembic_ini))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(repo_root / "migrations"))

    if canonical_db is None or Migrate is None:
        raise RuntimeError(
            "Required Flask extensions (backend.extensions or flask_migrate) not importable"
        )

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config.setdefault(
        "SQLALCHEMY_ENGINE_OPTIONS", {"connect_args": {"check_same_thread": False}}
    )
    canonical_db.init_app(app)
    Migrate(app, canonical_db)

    enterprise_structure_columns = {
        "organization_type",
        "description",
        "legal_name",
        "registration_number",
        "website",
        "email",
        "phone",
        "emergency_phone",
        "opening_hours",
        "head_office",
        "departments_json",
        "territory",
        "capabilities_json",
        "languages_json",
        "priority_domains_json",
        "accepted_case_types_json",
        "required_documents_json",
        "supported_populations_json",
        "risk_level",
        "updated_at",
    }
    enterprise_service_columns = {
        "category",
        "availability",
        "capacity",
        "responsible_professionals_json",
        "opening_hours",
        "coverage",
        "updated_at",
    }

    with app.app_context():
        command.upgrade(cfg, "20260728_1500")
        engine = canonical_db.engine
        inspector = inspect(engine)
        assert "structure_contacts" in inspector.get_table_names()
        assert "structure_coverage_areas" in inspector.get_table_names()
        structure_columns = {col["name"] for col in inspector.get_columns("structures")}
        service_columns = {col["name"] for col in inspector.get_columns("structure_services")}
        assert enterprise_structure_columns <= structure_columns
        assert enterprise_service_columns <= service_columns

        command.downgrade(cfg, "20260728_0900")
        inspector = inspect(engine)
        assert "structure_contacts" not in inspector.get_table_names()
        assert "structure_coverage_areas" not in inspector.get_table_names()
        structure_columns = {col["name"] for col in inspector.get_columns("structures")}
        service_columns = {col["name"] for col in inspector.get_columns("structure_services")}
        assert enterprise_structure_columns.isdisjoint(structure_columns)
        assert enterprise_service_columns.isdisjoint(service_columns)

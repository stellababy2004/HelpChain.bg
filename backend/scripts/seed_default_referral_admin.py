from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_import_path() -> Path:
    this_file = Path(__file__).resolve()
    backend_dir = this_file.parents[1]
    repo_root = backend_dir.parent
    for candidate in (str(repo_root), str(backend_dir)):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    return repo_root


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _onboarding_state(admin) -> str:
    if getattr(admin, "onboarding_completed_at", None):
        return "completed"
    if getattr(admin, "onboarding_started_at", None):
        return str(getattr(admin, "onboarding_step", None) or "started")
    return str(getattr(admin, "onboarding_step", None) or "not_started")


def main() -> int:
    repo_root = _prepare_import_path()

    password = os.getenv("HC_DEFAULT_ADMIN_PASSWORD") or ""
    if not password:
        print("SEED_DEFAULT_REFERRAL_ADMIN: missing HC_DEFAULT_ADMIN_PASSWORD; no account created.")
        return 1

    from backend.helpchain_backend.src.app import create_app
    from backend.extensions import db
    from backend.models import AdminUser, Structure, canonical_role, utc_now
    from sqlalchemy.exc import OperationalError

    app = create_app()
    db_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI") or "").strip()
    if not db_uri.lower().startswith("sqlite:///"):
        print("SEED_DEFAULT_REFERRAL_ADMIN: refused non-SQLite target.")
        print("TARGET_DB=" + (db_uri or "<unset>"))
        return 2

    sqlite_path = Path(db_uri.replace("sqlite:///", "", 1)).resolve()
    allowed_roots = {
        repo_root.resolve(),
        (repo_root / "instance").resolve(),
        (repo_root / "backend" / "instance").resolve(),
    }
    if not any(root == sqlite_path or root in sqlite_path.parents for root in allowed_roots):
        print("SEED_DEFAULT_REFERRAL_ADMIN: refused SQLite target outside local workspace.")
        print("TARGET_DB=" + str(sqlite_path))
        return 2

    username = "default_admin"
    email = "default_admin@helpchain.local"
    role = "admin"

    with app.app_context():
        structure = db.session.get(Structure, 1)
        if structure is None:
            print("SEED_DEFAULT_REFERRAL_ADMIN: structure_id=1 not found; no account created.")
            return 3

        admin = AdminUser.query.filter_by(username=username).first()
        created = False
        if admin is None:
            admin = AdminUser(
                username=username,
                email=email,
                role=role,
                is_active=True,
                structure_id=structure.id,
            )
            created = True
            db.session.add(admin)
        else:
            admin.email = getattr(admin, "email", None) or email
            admin.role = role
            admin.is_active = True
            admin.structure_id = structure.id

        admin.set_password(password)

        if hasattr(admin, "must_change_password"):
            admin.must_change_password = False
        if hasattr(admin, "mfa_enabled") and _is_truthy(os.getenv("HC_LOCAL_DEV_ALLOW_MFA_DISABLE", "1")):
            admin.mfa_enabled = False
        if hasattr(admin, "totp_secret") and not bool(getattr(admin, "mfa_enabled", False)):
            admin.totp_secret = None
        if hasattr(admin, "onboarding_started_at") and getattr(admin, "onboarding_started_at", None) is None:
            admin.onboarding_started_at = utc_now()
        if hasattr(admin, "onboarding_completed_at"):
            admin.onboarding_completed_at = utc_now()
        if hasattr(admin, "onboarding_step"):
            admin.onboarding_step = "completed"

        try:
            db.session.commit()
        except OperationalError as exc:
            db.session.rollback()
            print("SEED_DEFAULT_REFERRAL_ADMIN: database write failed.")
            print("DETAIL=local SQLite target may be mounted read-only in the current execution context.")
            print("TARGET_DB=" + str(sqlite_path))
            print("ERROR_CLASS=" + exc.__class__.__name__)
            return 4

        rows = (
            db.session.query(AdminUser, Structure)
            .outerjoin(Structure, AdminUser.structure_id == Structure.id)
            .order_by(AdminUser.id.asc())
            .all()
        )

        print(
            "SEED_DEFAULT_REFERRAL_ADMIN: "
            + ("created" if created else "updated")
            + f" '{username}' on local SQLite dev DB."
        )
        print("TARGET_DB=" + str(sqlite_path))
        print("")
        print("Admin access matrix")
        print("username | role | structure | structure_id | active | global_access | onboarding_state | mfa_enabled")
        for row_admin, row_structure in rows:
            role_canon = canonical_role(getattr(row_admin, "role", None))
            structure_name = getattr(row_structure, "name", None) or ""
            structure_id = getattr(row_admin, "structure_id", None)
            global_access = "yes" if role_canon == "superadmin" and structure_id is None else "no"
            print(
                " | ".join(
                    [
                        str(getattr(row_admin, "username", "") or ""),
                        str(role_canon or ""),
                        str(structure_name),
                        str(structure_id or ""),
                        "active" if bool(getattr(row_admin, "is_active", False)) else "inactive",
                        global_access,
                        _onboarding_state(row_admin),
                        "yes" if bool(getattr(row_admin, "mfa_enabled", False)) else "no",
                    ]
                )
            )

        print("")
        print("Local referral happy-path")
        print("1. Login as ccas_boulogne_admin")
        print("2. Create partner request to Default")
        print("3. Login as default_admin")
        print("4. Accept partner request")
        print("5. Login as ccas_boulogne_admin")
        print("6. Send referral to Default")
        print("7. Login as default_admin")
        print("8. Accept / progress / complete referral")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

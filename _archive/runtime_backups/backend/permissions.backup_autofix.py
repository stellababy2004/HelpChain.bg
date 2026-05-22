"""
Permission-based access control decorators and utilities for HelpChain
"""

from functools import wraps

from flask import current_app, flash, redirect, session, url_for

from .extensions import db
from .models import Permission, PermissionEnum, Role, RolePermission, User, UserRole


def has_permission(permission_codename):
    """
    Check if current user has a specific permission

    Args:
        permission_codename (str): The codename of the permission to check

    Returns:
        bool: True if user has permission, False otherwise
    """
    if not session.get("user_id"):
        return False

    try:
        user = (
            db.session.get(User, session["user_id"]) if session.get("user_id") else None
        )
        if not user or not user.is_active:
            return False

        # Check if user has the permission through their roles
        user_roles = UserRole.query.filter_by(user_id=user.id).all()
        for user_role in user_roles:
            role_permissions = RolePermission.query.filter_by(
                role_id=user_role.role_id
            ).all()
            for role_perm in role_permissions:
                if role_perm.permission.codename == permission_codename:
                    return True

        return False
    except Exception as e:
        current_app.logger.error(f"Permission check error: {e}")
        return False


def has_any_permission(*permission_codenames):
    """
    Check if current user has any of the specified permissions

    Args:
        *permission_codenames: Variable number of permission codenames

    Returns:
        bool: True if user has any of the permissions, False otherwise
    """
    for perm in permission_codenames:
        if has_permission(perm):
            return True
    return False


def has_all_permissions(*permission_codenames):
    """
    Check if current user has all of the specified permissions

    Args:
        *permission_codenames: Variable number of permission codenames

    Returns:
        bool: True if user has all permissions, False otherwise
    """
    for perm in permission_codenames:
        if not has_permission(perm):
            return False
    return True


def require_permission(permission_codename, redirect_url="index"):
    """
    Decorator to require a specific permission for a route

    Args:
        permission_codename (str): The codename of the required permission
        redirect_url (str): URL to redirect to if permission denied

    Returns:
        function: Decorated function
    """

    def wrapper(f):
        @wraps(f)
        def wrapped_function(*args, **kwargs):
            if not has_permission(permission_codename):
                flash("Нямате достатъчни права за достъп до тази страница.", "danger")
                return redirect(url_for(redirect_url))
            return f(*args, **kwargs)

        return wrapped_function

    return wrapper


def require_any_permission(*permission_codenames, redirect_url="index"):
    """
    Decorator to require any of the specified permissions for a route

    Args:
        *permission_codenames: Variable number of permission codenames
        redirect_url (str): URL to redirect to if permission denied

    Returns:
        function: Decorated function
    """

    def wrapper(f):
        @wraps(f)
        def wrapped_function(*args, **kwargs):
            if not has_any_permission(*permission_codenames):
                flash("Нямате достатъчни права за достъп до тази страница.", "danger")
                return redirect(url_for(redirect_url))
            return f(*args, **kwargs)

        return wrapped_function

    return wrapper


def require_all_permissions(*permission_codenames, redirect_url="index"):
    """
    Decorator to require all of the specified permissions for a route

    Args:
        *permission_codenames: Variable number of permission codenames
        redirect_url (str): URL to redirect to if permission denied

    Returns:
        function: Decorated function
    """

    def wrapper(f):
        @wraps(f)
        def wrapped_function(*args, **kwargs):
            if not has_all_permissions(*permission_codenames):
                flash("Нямате достатъчни права за достъп до тази страница.", "danger")
                return redirect(url_for(redirect_url))
            return f(*args, **kwargs)

        return wrapped_function

    return wrapper


def require_login(redirect_url="login"):
    """
    Decorator to require user login

    Args:
        redirect_url (str): URL to redirect to if not logged in

    Returns:
        function: Decorated function
    """

    def wrapper(f):
        @wraps(f)
        def wrapped_function(*args, **kwargs):
            if "user_id" not in session:
                flash("Моля, влезте в системата.", "warning")
                return redirect(url_for(redirect_url))
            return f(*args, **kwargs)

        return wrapped_function

    return wrapper


def require_admin_login(redirect_url="admin_login"):
    """
    Decorator to require admin login with 2FA

    Args:
        redirect_url (str): URL to redirect to if not logged in

    Returns:
        function: Decorated function
    """

    def decorator(f):
        @wraps(f)
        def wrapped_function(*args, **kwargs):
            if not session.get("admin_logged_in"):
                flash("Моля, влезте като администратор.", "warning")
                return redirect(url_for(redirect_url))
            return f(*args, **kwargs)

        return wrapped_function

    # If called without arguments, return decorator with default redirect_url
    if callable(redirect_url):
        # This means @require_admin_login was used without parentheses
        f = redirect_url
        redirect_url = "admin_login"
        return decorator(f)
    else:
        # This means @require_admin_login("some_url") was used with arguments
        return decorator


def get_user_permissions(user_id=None):
    """
    Get all permissions for a user

    Args:
        user_id (int, optional): User ID. If None, uses current session user

    Returns:
        list: List of permission codenames
    """
    if user_id is None and "user_id" in session:
        user_id = session["user_id"]
    elif user_id is None:
        return []

    try:
        user_roles = UserRole.query.filter_by(user_id=user_id).all()
        permissions = set()

        for user_role in user_roles:
            role_permissions = RolePermission.query.filter_by(
                role_id=user_role.role_id
            ).all()
            for role_perm in role_permissions:
                permissions.add(role_perm.permission.codename)

        return list(permissions)
    except Exception as e:
        current_app.logger.error(f"Error getting user permissions: {e}")
        return []


def get_user_roles(user_id=None):
    """
    Get all roles for a user

    Args:
        user_id (int, optional): User ID. If None, uses current session user

    Returns:
        list: List of role names
    """
    if user_id is None and "user_id" in session:
        user_id = session["user_id"]
    elif user_id is None:
        return []

    try:
        user_roles = UserRole.query.filter_by(user_id=user_id).all()
        return [user_role.role.name for user_role in user_roles]
    except Exception as e:
        current_app.logger.error(f"Error getting user roles: {e}")
        return []


def initialize_default_roles_and_permissions():
    """
    Initialize default roles and permissions in the database
    This should be called during application setup
    """
    try:
        # Create default permissions
        default_permissions = [
            # User permissions
            {
                "name": "Преглед на профил",
                "codename": PermissionEnum.VIEW_PROFILE,
                "category": "user",
                "is_system_permission": True,
            },
            {
                "name": "Редактиране на профил",
                "codename": PermissionEnum.EDIT_PROFILE,
                "category": "user",
                "is_system_permission": True,
            },
            # Volunteer permissions
            {
                "name": "Преглед на доброволци",
                "codename": PermissionEnum.VIEW_VOLUNTEERS,
                "category": "volunteer",
                "is_system_permission": True,
            },
            {
                "name": "Управление на доброволци",
                "codename": PermissionEnum.MANAGE_VOLUNTEERS,
                "category": "volunteer",
                "is_system_permission": True,
            },
            {
                "name": "Преглед на заявки",
                "codename": PermissionEnum.VIEW_REQUESTS,
                "category": "volunteer",
                "is_system_permission": True,
            },
            {
                "name": "Управление на заявки",
                "codename": PermissionEnum.MANAGE_REQUESTS,
                "category": "volunteer",
                "is_system_permission": True,
            },
            {
                "name": "Използване на видео чат",
                "codename": PermissionEnum.USE_VIDEO_CHAT,
                "category": "volunteer",
                "is_system_permission": True,
            },
            # Moderator permissions
            {
                "name": "Модериране на съдържание",
                "codename": PermissionEnum.MODERATE_CONTENT,
                "category": "moderator",
                "is_system_permission": True,
            },
            {
                "name": "Преглед на аналитика",
                "codename": PermissionEnum.VIEW_ANALYTICS,
                "category": "moderator",
                "is_system_permission": True,
            },
            {
                "name": "Управление на категории",
                "codename": PermissionEnum.MANAGE_CATEGORIES,
                "category": "moderator",
                "is_system_permission": True,
            },
            # Admin permissions
            {
                "name": "Админ достъп",
                "codename": PermissionEnum.ADMIN_ACCESS,
                "category": "admin",
                "is_system_permission": True,
            },
            {
                "name": "Управление на потребители",
                "codename": PermissionEnum.MANAGE_USERS,
                "category": "admin",
                "is_system_permission": True,
            },
            {
                "name": "Управление на роли",
                "codename": PermissionEnum.MANAGE_ROLES,
                "category": "admin",
                "is_system_permission": True,
            },
            {
                "name": "Системни настройки",
                "codename": PermissionEnum.SYSTEM_SETTINGS,
                "category": "admin",
                "is_system_permission": True,
            },
            {
                "name": "Преглед на одит логове",
                "codename": PermissionEnum.VIEW_AUDIT_LOGS,
                "category": "admin",
                "is_system_permission": True,
            },
            # Super admin permissions
            {
                "name": "Супер админ",
                "codename": PermissionEnum.SUPER_ADMIN,
                "category": "superadmin",
                "is_system_permission": True,
            },
        ]

        for perm_data in default_permissions:
            if not Permission.query.filter_by(codename=perm_data["codename"]).first():
                permission = Permission(**perm_data)
                db.session.add(permission)

        # Create default roles
        default_roles = [
            {
                "name": "Потребител",
                "description": "Основен потребител с ограничени права",
                "is_system_role": True,
            },
            {
                "name": "Доброволец",
                "description": "Доброволец с права за управление на заявки",
                "is_system_role": True,
            },
            {
                "name": "Модератор",
                "description": "Модератор с права за модериране на съдържание",
                "is_system_role": True,
            },
            {
                "name": "Администратор",
                "description": "Администратор с пълни права за управление",
                "is_system_role": True,
            },
            {
                "name": "Супер администратор",
                "description": "Супер администратор с неограничени права",
                "is_system_role": True,
            },
        ]

        for role_data in default_roles:
            if not Role.query.filter_by(name=role_data["name"]).first():
                role = Role(**role_data)
                db.session.add(role)

        db.session.commit()

        # Assign permissions to roles
        assign_default_role_permissions()

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error initializing roles and permissions: {e}")


def assign_default_role_permissions():
    """
    Assign default permissions to default roles
    """
    try:
        # Get roles
        user_role = Role.query.filter_by(name="Потребител").first()
        volunteer_role = Role.query.filter_by(name="Доброволец").first()
        moderator_role = Role.query.filter_by(name="Модератор").first()
        admin_role = Role.query.filter_by(name="Администратор").first()
        superadmin_role = Role.query.filter_by(name="Супер администратор").first()

        # Get permissions
        permissions = {p.codename: p for p in Permission.query.all()}

        # User permissions
        if user_role:
            user_perms = [
                permissions.get(PermissionEnum.VIEW_PROFILE),
                permissions.get(PermissionEnum.EDIT_PROFILE),
            ]
            for perm in user_perms:
                if (
                    perm
                    and not RolePermission.query.filter_by(
                        role_id=user_role.id, permission_id=perm.id
                    ).first()
                ):
                    db.session.add(
                        RolePermission(role_id=user_role.id, permission_id=perm.id)
                    )

        # Volunteer permissions
        if volunteer_role:
            volunteer_perms = [
                permissions.get(PermissionEnum.VIEW_PROFILE),
                permissions.get(PermissionEnum.EDIT_PROFILE),
                permissions.get(PermissionEnum.VIEW_VOLUNTEERS),
                permissions.get(PermissionEnum.MANAGE_VOLUNTEERS),
                permissions.get(PermissionEnum.VIEW_REQUESTS),
                permissions.get(PermissionEnum.MANAGE_REQUESTS),
                permissions.get(PermissionEnum.USE_VIDEO_CHAT),
            ]
            for perm in volunteer_perms:
                if (
                    perm
                    and not RolePermission.query.filter_by(
                        role_id=volunteer_role.id, permission_id=perm.id
                    ).first()
                ):
                    db.session.add(
                        RolePermission(role_id=volunteer_role.id, permission_id=perm.id)
                    )

        # Moderator permissions
        if moderator_role:
            moderator_perms = [
                permissions.get(PermissionEnum.VIEW_PROFILE),
                permissions.get(PermissionEnum.EDIT_PROFILE),
                permissions.get(PermissionEnum.VIEW_VOLUNTEERS),
                permissions.get(PermissionEnum.MANAGE_VOLUNTEERS),
                permissions.get(PermissionEnum.VIEW_REQUESTS),
                permissions.get(PermissionEnum.MANAGE_REQUESTS),
                permissions.get(PermissionEnum.USE_VIDEO_CHAT),
                permissions.get(PermissionEnum.MODERATE_CONTENT),
                permissions.get(PermissionEnum.VIEW_ANALYTICS),
                permissions.get(PermissionEnum.MANAGE_CATEGORIES),
            ]
            for perm in moderator_perms:
                if (
                    perm
                    and not RolePermission.query.filter_by(
                        role_id=moderator_role.id, permission_id=perm.id
                    ).first()
                ):
                    db.session.add(
                        RolePermission(role_id=moderator_role.id, permission_id=perm.id)
                    )

        # Admin permissions
        if admin_role:
            admin_perms = [
                permissions.get(PermissionEnum.VIEW_PROFILE),
                permissions.get(PermissionEnum.EDIT_PROFILE),
                permissions.get(PermissionEnum.VIEW_VOLUNTEERS),
                permissions.get(PermissionEnum.MANAGE_VOLUNTEERS),
                permissions.get(PermissionEnum.VIEW_REQUESTS),
                permissions.get(PermissionEnum.MANAGE_REQUESTS),
                permissions.get(PermissionEnum.USE_VIDEO_CHAT),
                permissions.get(PermissionEnum.MODERATE_CONTENT),
                permissions.get(PermissionEnum.VIEW_ANALYTICS),
                permissions.get(PermissionEnum.MANAGE_CATEGORIES),
                permissions.get(PermissionEnum.ADMIN_ACCESS),
                permissions.get(PermissionEnum.MANAGE_USERS),
                permissions.get(PermissionEnum.MANAGE_ROLES),
                permissions.get(PermissionEnum.SYSTEM_SETTINGS),
                permissions.get(PermissionEnum.VIEW_AUDIT_LOGS),
            ]
            for perm in admin_perms:
                if (
                    perm
                    and not RolePermission.query.filter_by(
                        role_id=admin_role.id, permission_id=perm.id
                    ).first()
                ):
                    db.session.add(
                        RolePermission(role_id=admin_role.id, permission_id=perm.id)
                    )

        # Super admin permissions (all permissions)
        if superadmin_role:
            all_permissions = Permission.query.all()
            for perm in all_permissions:
                if not RolePermission.query.filter_by(
                    role_id=superadmin_role.id, permission_id=perm.id
                ).first():
                    db.session.add(
                        RolePermission(
                            role_id=superadmin_role.id, permission_id=perm.id
                        )
                    )

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error assigning default role permissions: {e}")


__all__ = [
    "has_permission",
    "has_any_permission",
    "has_all_permissions",
    "require_permission",
    "require_any_permission",
    "require_all_permissions",
    "require_login",
    "require_admin_login",
    "get_user_permissions",
    "get_user_roles",
    "initialize_default_roles_and_permissions",
    "assign_default_role_permissions",
]

"""
This file existed as a stray test helper that created its own
`SQLAlchemy()` instance which interfered with the canonical
`backend.extensions.db` singleton during tests. It is intentionally
left inert to avoid creating a second `SQLAlchemy()` object.

If you need a shim that re-exports the canonical `db`, create a
small module that does `from backend.extensions import db` instead.
"""

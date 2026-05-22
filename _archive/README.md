This folder holds non-runtime historical artifacts that were moved out of canonical runtime paths.

Buckets:
- `runtime_backups/`: obvious backup or proposal variants of app, route, template, CSS, JS, and migration-adjacent files.
- `legacy_snapshots/`: duplicate backup trees kept temporarily for reference and recovery.
- `scratch/`: stray scratch or malformed files that are not part of the canonical application runtime.

Canonical runtime entrypoint:
- `backend.helpchain_backend.src.app.create_app`

Archive policy:
- Files here are excluded from canonical runtime flows.
- They are retained temporarily for recovery, audit, or reference.
- Do not restore or re-import them into active runtime paths without an explicit review.

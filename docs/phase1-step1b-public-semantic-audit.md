# Phase 1 Step 1B - Public Semantic Audit

## Active canonical public surfaces

- `/` -> `home_new_slim.html` via `main.index`
- `/about` -> `about.html`
- `/pourquoi_helpchain` -> `pourquoi_helpchain.html`
- `/gouvernance` -> `gouvernance.html`
- `/securite` -> `securite.html`
- `/deploiement` -> `deploiement.html`
- `/offre` -> `offre.html`
- `/architecture` -> `architecture.html`
- `/privacy` and `/confidentialite` -> `privacy.html`
- `/terms` and `/conditions-utilisation` -> `terms.html`
- `/legal` and `/mentions-legales` -> `legal.html`

## Legacy or alternate public surfaces

- `templates/index.html`
  Future action: archive or convert to explicit legacy fallback later.
- `public/index.html`
  Future action: noindex or remove from deployment surface later if still exposed by hosting.
- `templates/home_new_slim_before_stats_panel_20260420_075505.html`
  Future action: archive later.

## Authority dilution risks

- `templates/index.html` looks like an older public homepage and can dilute homepage semantics if reused accidentally.
- `public/index.html` is a static preview surface and could be crawlable depending on deployment rules.
- timestamped or backup-style homepage remnants can confuse future edits and reintroduce old positioning language.

## Audit note on encoding

- Source audit on the priority authority templates found UTF-8 text stored correctly in the repo.
- The mojibake observed during terminal inspection came from console rendering, not from persistent corrupted source bytes on the audited templates.
- Public authority pages were still normalized during this pass by touching the key semantic blocks and metadata in the authoritative templates.

## Recommended next actions

- Keep `/` on `home_new_slim.html` as the only canonical homepage.
- Add explicit crawl policy or deployment exclusion for `public/index.html` if that file is exposed in production.
- Archive old homepage remnants once the public semantic layer is considered stable.

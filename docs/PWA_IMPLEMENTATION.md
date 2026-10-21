# PWA Implementation — Issue #70

## Overview

Makes the NSA Webservice an installable Progressive Web App (PWA) so field
FSOs can install it to their home screen, launch in a standalone window, load
faster from a cached app shell, and see a branded offline fallback.

**No new frontend framework. No build step.** Stays library-free and
self-hosted, matching the codebase's vanilla-JS philosophy.

## Configuration

| Flag | Key | Default | Convention |
|------|-----|---------|------------|
| PWA switch | `ENABLE_PWA` | `false` | opt-in (only `"true"` enables) |

Declared in `app/shared/config.py` (`_TABLE`), documented in `.env.example`.
Enforced by `tests/test_shared_config.py::test_env_example_keys_are_declared`.

## Files

| File | Purpose |
|------|---------|
| `app/shared/config.py` | `ENABLE_PWA` Setting in `_TABLE` |
| `app/__init__.py` | CSP `worker-src 'self'` added when flag is on |
| `app/health/routes.py` | `/manifest.json`, `/sw.js`, `/offline` routes (public, gated by flag) |
| `app/templates/base.html` | Conditional manifest link, theme-color, SW registration |
| `app/templates/offline.html` | Branded offline fallback page |
| `app/static/src/sw.js` | Root-scoped service worker source |
| `app/static/icons/` | 192px, 512px, maskable, favicon, Apple touch icons |
| `scripts/generate_pwa_icons.py` | Regenerates the icon set from Pillow |

## Rollout (staging → production)

1. Set `ENABLE_PWA=false` in `render.yaml` (both services) — **done**.
2. Deploy to staging, set `ENABLE_PWA=true` via Render dashboard.
3. Verify installability (Lighthouse / browser install prompt) on staging.
4. Once verified, set `ENABLE_PWA=true` on the production service.

## Security

- **CSP** includes `worker-src 'self'` only when the flag is on.
- The service worker precaches **only the anonymous app shell** (static assets,
  fonts, icons, login page). Authenticated routes and API responses are
  **network-only** — never cached — preventing cross-user data leakage on
  shared devices.
- HTTPS is already enforced in production (`force_https=True` via Talisman).

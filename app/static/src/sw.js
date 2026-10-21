/*
 * NSA Webservice — Progressive Web App service worker (issue #70).
 *
 * Cache strategy:
 *   - Static assets (CSS, JS, fonts, icons): cache-first, stale-while-revalidate.
 *   - Authenticated / dynamic pages + API responses: network-only (never cached).
 *   - Navigation requests (HTML): network-first, fallback to offline page.
 *
 * This SW is root-scoped ('/sw.js') so it controls the entire origin.  Only the
 * anonymous app shell is precached — authenticated HTML is never served from
 * cache, preventing one user's data leaking to another on a shared device.
 */

const CACHE_NAME = "nsa-pwa-v1";
const OFFLINE_URL = "/offline";

/* Resources that are safe to precache (anonymous, non-user-specific). */
const PRECACHE_URLS = [
    "/static/css/theme.css",
    "/static/vendor/fontawesome/css/all.min.css",
    "/static/vendor/fonts/gf.css",
    "/static/icons/pwa-192.png",
    "/static/icons/pwa-512.png",
    "/static/icons/pwa-192-maskable.png",
    "/static/icons/pwa-512-maskable.png",
    "/static/icons/pwa-32-favicon.png",
    "/static/icons/pwa-16-favicon.png",
    OFFLINE_URL,
    "/manifest.json",
];

/* URL prefixes that must always hit the network (never cached). */
const NETWORK_ONLY_PATHS = ["/api/", "/auth/", "/rag/query", "/rag/query/agent", "/diary/bulk"];

/*
 * Pre-cache the app shell on install.  Skip-waiting so the new SW activates
 * immediately.
 */
self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)));
    self.skipWaiting();
});

/* Remove old caches on activate. */
self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches
            .keys()
            .then((names) =>
                Promise.all(names.map((name) => name !== CACHE_NAME && caches.delete(name)))
            )
    );
    self.clients.claim();
});

/*
 * Fetch handler: network-first for authenticated/API, cache-first for static,
 * offline fallback for navigation.
 */
self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);
    const path = url.pathname;

    /* Never handle non-GET requests in the SW. */
    if (event.request.method !== "GET") return;

    /*
     * Network-only: authenticated routes and API endpoints.  These must
     * always come from the network so user data is never served stale from
     * cache (prevents cross-user data leakage on shared devices).
     */
    if (NETWORK_ONLY_PATHS.some((prefix) => path.startsWith(prefix))) {
        return;
    }

    /*
     * Cache-first for static assets: respond from cache immediately,
     * falling back to network, then to nothing (the cached resource).
     */
    if (path.startsWith("/static/")) {
        event.respondWith(
            caches.match(event.request).then((cached) => {
                if (cached) return cached;
                return fetch(event.request).then((response) => {
                    if (!response || response.status !== 200 || response.type === "opaque") {
                        return response;
                    }
                    const toCache = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, toCache));
                    return response;
                });
            })
        );
        return;
    }

    /*
     * Navigation requests (HTML pages): network-first with offline fallback.
     */
    if (event.request.mode === "navigate") {
        event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE_URL)));
        return;
    }
});

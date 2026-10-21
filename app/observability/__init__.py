"""Observability seam — error tracking (Sentry) and metrics (Prometheus).

Two tools, two different questions, deliberately kept apart:

* **Sentry** answers *"what broke, once, and with what stack trace?"* — a push
  destination for errors (and, later, traces).
* **Prometheus** answers *"how is the system behaving in aggregate?"* — pulled by
  a scraper, never sent anything.

Neither is the audit trail. :class:`~app.models.audit.RecordAudit` stays the
hash-chained record of truth (:doc:`../docs/OBSERVABILITY_SENTRY_PROMETHEUS_SPEC`
AUD-1); nothing in this package may be used as evidence or as history.

Both integrations are inert by default. With a blank ``SENTRY_DSN`` and
``METRICS_ENABLED`` unset (the declared defaults), the app performs no
observability I/O at all and spawns no threads — which is what lets the test
suite keep its no-network contract.

Nothing is re-exported here on purpose: importing this package must not drag
``sentry_sdk`` or ``prometheus_client`` into a context that wants only one of
them (or neither).
"""

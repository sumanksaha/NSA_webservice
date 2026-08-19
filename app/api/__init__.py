"""Shared FastAPI API dependencies (FASTAPI_IMPLEMENTATION_PLAN.md, Phase 2).

This package is imported by :mod:`asgi` routes to provide FastAPI-native
dependencies (DB session, config flags) that work *outside* a Flask app
context.
"""

"""Shared authentication helpers.

``admin_required`` lives here (not inside a blueprint) so that any
blueprint can gate admin-only views without importing another
blueprint's routes module.
"""

from __future__ import annotations

from functools import wraps

from flask import abort
from flask_login import current_user


def admin_required(view):
    """Restrict a view to authenticated admin users (403 for everyone else).

    Must be stacked below ``@login_required`` so unauthenticated visitors get
    the standard redirect to the login page instead of a bare 403.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped

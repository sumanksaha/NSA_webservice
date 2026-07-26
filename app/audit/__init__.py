from flask import Blueprint

audit_bp = Blueprint(
    'audit',
    __name__,
    template_folder='templates',
)

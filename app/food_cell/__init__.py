"""Food Cell module for NSA_webservice.

Generates a templated DO (Designated Officer) Intimation after an FSO saves
sample data, renders it to HTML + PDF, saves both, and forwards the
intimation to the Food Cell for action.

Blueprint prefix: `/food-cell`
"""

from flask import Blueprint

food_cell_bp = Blueprint(
    "food_cell",
    __name__,
    template_folder="templates",
    static_folder="static",
)

from app.food_cell import routes  # noqa: F401

"""Excel styling helpers for the billing module.

Extracted from :mod:`app.billing.billing_utils` to eliminate the three
copy-pasted styling blocks that appeared once for the *Samples* sheet and
again for the *Summary* sheet:

1. **Header cell** — bold font, centered, thin border, gray fill.
2. **Data-cell border** — thin border on all four sides.
3. **Column-width auto-adjust** — ``(max_content_len + 2) * 1.2``.

A fourth helper, :meth:`style_total_row`, covers the grand-total variant
(thin top/side borders + a *double* bottom border).

The public interface is four small methods. Callers construct one
``SheetStyler`` and delegate the styling blocks — no more duplicated
border/pattern/fill literals scattered across sheet loops.
"""

from __future__ import annotations

import contextlib

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# ---------------------------------------------------------------------------
# Shared style constants — openpyxl value objects are safe to share across
# cells (they are baked into each cell's style array on assignment).
# ---------------------------------------------------------------------------
_GRAY_FILL = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")
_THIN_BORDER = Border(
    top=Side(style="thin"),
    bottom=Side(style="thin"),
    left=Side(style="thin"),
    right=Side(style="thin"),
)


class SheetStyler:
    """Centralised Excel cell / worksheet styling for billing reports.

    A seam: :func:`generate_excel_report` delegates the three previously
    duplicated styling blocks to this class so the styling logic lives in
    one place, is independently testable, and can be reused by any future
    Excel export.
    """

    def style_header(self, cell) -> None:
        """Apply header styling: bold font, centered, thin border, gray fill."""
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        cell.border = _THIN_BORDER
        cell.fill = _GRAY_FILL

    def style_data_cell(
        self,
        cell,
        *,
        align: str = "left",
        number_format: str | None = None,
    ) -> None:
        """Apply data-cell styling: thin border, alignment, optional number format."""
        cell.border = _THIN_BORDER
        cell.alignment = Alignment(horizontal=align)
        if number_format:
            cell.number_format = number_format

    def style_total_row(
        self,
        cell,
        *,
        is_total_label: bool = False,
        number_format: str | None = None,
    ) -> None:
        """Apply grand-total row styling: bold, thin top/side, double bottom border.

        ``is_total_label`` left-aligns the label cell ("GRAND TOTAL");
        value cells are right-aligned.
        """
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="left" if is_total_label else "right")
        cell.border = Border(
            top=Side(style="thin"),
            bottom=Side(style="double"),
            left=Side(style="thin"),
            right=Side(style="thin"),
        )
        if number_format:
            cell.number_format = number_format

    def auto_adjust_widths(self, ws) -> None:
        """Auto-adjust column widths to fit cell content.

        Width = ``(longest cell value string length + 2) * 1.2``.
        Non-string / ``None`` cells are suppressed via ``contextlib.suppress``
        — mirroring the original ``billing_utils`` behaviour exactly.
        """
        for col in ws.columns:
            max_length = 0
            for cell in col:
                with contextlib.suppress(BaseException):
                    max_length = max(max_length, len(str(cell.value)))
            adjusted_width = (max_length + 2) * 1.2
            ws.column_dimensions[col[0].column_letter].width = adjusted_width

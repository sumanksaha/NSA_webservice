"""Email sender for Improvement Notices (u/s 32, FSS Act).

Uses per-FSO SMTP credentials stored in the ``fso`` table.  Each FSO
configures their own email account (Gmail, Outlook, government SMTP, etc.)
and emails are sent from their address.

Usage::

    from app.food_cell.email_sender import send_improvement_notice_email

    result = send_improvement_notice_email(
        fso_name="Soumitra Chatterjee",
        recipient_email="do@kmc.gov.in",
        subject="Improvement Notice — ABC Foods",
        html_body="<p>...</p>",
        docx_bytes=b"...",
        docx_filename="Improvement_Notice_1.docx",
    )
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from app.extensions import db
from app.models.inspection import FSO

logger = logging.getLogger(__name__)


@dataclass
class EmailResult:
    """Result of an email send attempt."""

    success: bool
    message: str = ""
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _get_fso_smtp_config(fso_name: str) -> dict[str, Any] | None:
    """Load SMTP configuration for an FSO.  Returns None if not configured."""
    fso = db.session.get(FSO, fso_name)
    if fso is None:
        return None
    if not fso.email or not fso.smtp_host:
        return None
    return {
        "email": fso.email,
        "smtp_host": fso.smtp_host,
        "smtp_port": fso.smtp_port or 587,
        "smtp_user": fso.smtp_user or fso.email,
        "smtp_password": fso.smtp_password or "",
        "smtp_use_tls": fso.smtp_use_tls if fso.smtp_use_tls is not None else True,
        "fso_name": fso.fso_name,
    }


def send_improvement_notice_email(
    *,
    fso_name: str,
    recipient_email: str,
    subject: str,
    html_body: str,
    docx_bytes: bytes | None = None,
    docx_filename: str = "Improvement_Notice.docx",
    text_body: str | None = None,
) -> EmailResult:
    """Send an Improvement Notice email with optional .docx attachment.

    Parameters
    ----------
    fso_name:
        The FSO whose SMTP credentials are used (and who is the sender).
    recipient_email:
        To-address (entered manually by the FSO).
    subject:
        Email subject line.
    html_body:
        Rich HTML body of the email.
    docx_bytes:
        Optional .docx attachment as raw bytes.
    docx_filename:
        Filename for the attachment.
    text_body:
        Optional plain-text fallback.  Derived from *html_body* if not given.
    """
    config = _get_fso_smtp_config(fso_name)
    if config is None:
        return EmailResult(
            success=False,
            error=(
                f"Email not configured for FSO '{fso_name}'. "
                "Please set SMTP credentials in the FSO admin panel."
            ),
        )

    if not recipient_email or "@" not in recipient_email:
        return EmailResult(success=False, error="Invalid recipient email address.")

    # Build the MIME message
    msg = MIMEMultipart("mixed")
    msg["From"] = f"{config['fso_name']} <{config['email']}>"
    msg["To"] = recipient_email
    msg["Subject"] = subject

    # HTML body part
    html_part = MIMEText(html_body, "html", "utf-8")
    msg.attach(html_part)

    # Plain-text fallback
    if text_body:
        text_part = MIMEText(text_body, "plain", "utf-8")
        msg.attach(text_part)

    # .docx attachment
    if docx_bytes:
        attachment = MIMEApplication(docx_bytes, _subtype="vnd.openxmlformats-officedocument.wordprocessingml.document")
        attachment.add_header(
            "Content-Disposition",
            "attachment",
            filename=docx_filename,
        )
        msg.attach(attachment)

    # Send via SMTP
    try:
        if config["smtp_use_tls"]:
            server = smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=30)
            server.ehlo()

        if config["smtp_user"] and config["smtp_password"]:
            server.login(config["smtp_user"], config["smtp_password"])

        server.sendmail(config["email"], [recipient_email], msg.as_string())
        server.quit()

        logger.info(
            "Improvement Notice email sent from %s to %s",
            config["email"],
            recipient_email,
        )
        return EmailResult(
            success=True,
            message=f"Email sent successfully from {config['email']} to {recipient_email}.",
            details={
                "from": config["email"],
                "to": recipient_email,
                "subject": subject,
                "has_attachment": docx_bytes is not None,
            },
        )

    except smtplib.SMTPAuthenticationError as exc:
        error_msg = (
            f"SMTP authentication failed for {config['email']}. "
            "Please check your SMTP username and password."
        )
        logger.error("SMTP auth error: %s", exc)
        return EmailResult(success=False, error=error_msg)

    except smtplib.SMTPConnectError as exc:
        error_msg = (
            f"Could not connect to SMTP server {config['smtp_host']}:{config['smtp_port']}. "
            "Please verify the server address and port."
        )
        logger.error("SMTP connect error: %s", exc)
        return EmailResult(success=False, error=error_msg)

    except smtplib.SMTPException as exc:
        error_msg = f"SMTP error: {exc}"
        logger.error("SMTP error: %s", exc)
        return EmailResult(success=False, error=error_msg)

    except Exception as exc:
        error_msg = f"Unexpected error sending email: {exc}"
        logger.exception("Email send failed")
        return EmailResult(success=False, error=error_msg)


__all__ = ["send_improvement_notice_email", "EmailResult"]

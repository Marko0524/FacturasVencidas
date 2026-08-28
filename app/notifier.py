"""Notification delivery.

Two interchangeable channels, both exposing the same ``send`` method so
``main.run`` never learns which one it got:

* ``LoggingNotifier`` -- simulates delivery with structured log lines. Default,
  needs no network, keeps the tests and the offline demo working.
* ``SmtpNotifier`` -- builds a real MIME message and hands it to an SMTP server.
  Pointed at Mailpit it delivers real mail to a local inbox; pointed at
  Microsoft 365 / SendGrid / Azure Communication Services it delivers to the
  real world. Same code, only the environment changes.

Swapping in a provider REST API instead means writing a third class with this
same ``send`` method -- no business rule changes.
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.invoice_service import (
    TYPE_OPERATIONS_ALERT,
    TYPE_REMINDER,
    Invoice,
    Notification,
)

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    """Raised when a notification could not be delivered.

    ``main._dispatch`` catches it, counts the error and moves on to the next
    invoice: one unreachable mailbox must not abort the batch.
    """


class LoggingNotifier:
    """Simulates sending notifications by writing structured log lines."""

    def __init__(self, operations_email: str) -> None:
        self._operations_email = operations_email

    def send(self, notification: Notification) -> None:
        """Dispatch a notification to the right channel."""
        if notification.type == TYPE_REMINDER:
            self.send_payment_reminder(notification.invoice)
        elif notification.type == TYPE_OPERATIONS_ALERT:
            self.send_operations_alert(notification.invoice, notification.days_overdue)
        else:
            raise ValueError(f"Unknown notification type: {notification.type}")

    def send_payment_reminder(self, invoice: Invoice) -> None:
        logger.info(
            'Payment reminder sent to=%s invoice=%s customer="%s" amount=%.2f currency=%s due_date=%s',
            invoice.customer_email,
            invoice.id,
            invoice.customer_name,
            invoice.amount,
            invoice.currency,
            invoice.due_date.isoformat(),
        )

    def send_operations_alert(self, invoice: Invoice, days_overdue: int) -> None:
        logger.warning(
            'Operations alert sent to=%s invoice=%s customer="%s" days_overdue=%d amount=%.2f currency=%s',
            self._operations_email,
            invoice.id,
            invoice.customer_name,
            days_overdue,
            invoice.amount,
            invoice.currency,
        )


class SmtpNotifier:
    """Sends notifications as real e-mail through an SMTP server.

    A fresh connection is opened per message. That is deliberate: the batch is a
    handful of invoices per run, and a short-lived connection cannot go stale
    between two slow sends.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        use_tls: bool = False,
        sender_email: str,
        sender_name: str = "",
        operations_email: str,
        timeout: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._sender = formataddr((sender_name, sender_email)) if sender_name else sender_email
        # Signs the customer-facing mail. Operations alerts are internal and
        # deliberately unsigned: they are machine output, not correspondence.
        self._signature = sender_name or "Departamento de Cobranza"
        self._operations_email = operations_email
        self._timeout = timeout

    def send(self, notification: Notification) -> None:
        """Dispatch a notification to the right channel."""
        if notification.type == TYPE_REMINDER:
            self.send_payment_reminder(notification.invoice, notification.days_overdue)
        elif notification.type == TYPE_OPERATIONS_ALERT:
            self.send_operations_alert(notification.invoice, notification.days_overdue)
        else:
            raise ValueError(f"Unknown notification type: {notification.type}")

    def send_payment_reminder(self, invoice: Invoice, days_overdue: int) -> None:
        message = self._build_message(
            to_address=invoice.customer_email,
            subject=f"Recordatorio de pago - Factura {invoice.id}",
            text_body=_reminder_text(invoice, days_overdue, self._signature),
            html_body=_reminder_html(invoice, days_overdue, self._signature),
        )
        self._deliver(message, invoice.id, TYPE_REMINDER)

    def send_operations_alert(self, invoice: Invoice, days_overdue: int) -> None:
        message = self._build_message(
            to_address=self._operations_email,
            subject=f"[ALERTA] Factura {invoice.id} con {days_overdue} dias de atraso",
            text_body=_alert_text(invoice, days_overdue),
            html_body=_alert_html(invoice, days_overdue),
        )
        message["X-Priority"] = "1"
        self._deliver(message, invoice.id, TYPE_OPERATIONS_ALERT)

    def _build_message(
        self, *, to_address: str, subject: str, text_body: str, html_body: str
    ) -> EmailMessage:
        """Build a ``multipart/alternative`` message: HTML, with text as fallback.

        Order matters. ``set_content`` first and ``add_alternative`` second is
        what puts the plain text before the HTML in the MIME tree, which is how
        a client that cannot render HTML — or a reader who turned it off — still
        gets a complete, readable message instead of markup soup.
        """
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to_address
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain="recordatorio-de-pagos.local")
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        return message

    def _deliver(self, message: EmailMessage, invoice_id: str, notification_type: str) -> None:
        """Open a connection, send, close. Any SMTP failure becomes ``NotificationError``."""
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                if self._use_tls:
                    # The explicit context is not decoration: smtplib's default
                    # (``ssl._create_stdlib_context``) leaves check_hostname off
                    # and verify_mode at CERT_NONE, so the password would travel
                    # through a tunnel nobody authenticated.
                    smtp.starttls(context=ssl.create_default_context())
                if self._username:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise NotificationError(
                f"smtp_delivery_failed invoice={invoice_id} type={notification_type}: {exc}"
            ) from exc

        logger.info(
            "Email sent via=smtp to=%s invoice=%s type=%s subject=%r",
            message["To"],
            invoice_id,
            notification_type,
            message["Subject"],
        )


MONTHS_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _format_date(value: date) -> str:
    """``3 de agosto de 2026``. Built by hand: ``strftime`` would depend on the
    machine's locale, and a job that runs on a server must not."""
    return f"{value.day} de {MONTHS_ES[value.month - 1]} de {value.year}"


def _format_amount(invoice: Invoice) -> str:
    return f"${invoice.amount:,.2f} {invoice.currency}"


def _plural_days(days: int) -> str:
    return "1 día" if days == 1 else f"{days} días"


def _text_rows(rows: list[tuple[str, str]]) -> str:
    """Align ``label: value`` pairs into a column that survives a monospace view."""
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"    {label.ljust(width)}   {value}" for label, value in rows)


def _reminder_text(invoice: Invoice, days_overdue: int, signature: str) -> str:
    rows = _text_rows(
        [
            ("Factura", invoice.id),
            ("Importe", _format_amount(invoice)),
            ("Fecha de vencimiento", _format_date(invoice.due_date)),
            ("Días transcurridos", _plural_days(days_overdue)),
        ]
    )
    return (
        f"Apreciable {invoice.customer_name}:\n\n"
        "Por este medio le informamos, de manera atenta, que a la fecha se encuentra\n"
        f"pendiente de pago la factura {invoice.id}, con vencimiento el "
        f"{_format_date(invoice.due_date)}.\n\n"
        f"{rows}\n\n"
        "Le agradeceremos cubrir el importe correspondiente a la brevedad. En caso de\n"
        "que el pago ya se hubiera realizado, le pedimos hacer caso omiso del presente\n"
        "aviso o bien compartirnos el comprobante para actualizar el estado de su cuenta.\n\n"
        "Quedamos a sus órdenes para cualquier aclaración.\n\n"
        "Atentamente,\n"
        f"Departamento de Cobranza\n{signature}\n"
    )


def _alert_text(invoice: Invoice, days_overdue: int) -> str:
    rows = _text_rows(
        [
            ("Factura", invoice.id),
            ("Cliente", invoice.customer_name),
            ("Contacto", invoice.customer_email),
            ("Importe", _format_amount(invoice)),
            ("Fecha de vencimiento", _format_date(invoice.due_date)),
            ("Días de atraso", _plural_days(days_overdue)),
        ]
    )
    return (
        "ALERTA DE CARTERA VENCIDA\n\n"
        f"La factura {invoice.id} rebasó el umbral de atraso definido para el\n"
        "escalamiento a Operaciones y requiere seguimiento.\n\n"
        f"{rows}\n\n"
        "El recordatorio de pago correspondiente ya fue enviado al cliente.\n\n"
        "--\n"
        "Notificación automática del proceso de recordatorio de pagos.\n"
    )


# --- HTML part ---------------------------------------------------------------
#
# Written for e-mail clients, not for browsers: tables instead of flexbox, styles
# inline instead of a stylesheet, and no external resource of any kind. Outlook
# discards <style> blocks, Gmail strips <head>, and a remote image would be
# blocked by default and leak a read receipt. Colours are stated explicitly so a
# client forcing dark mode cannot invert half the message and leave the rest.

FONT_STACK = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

COLOR_PAGE = "#eef1f5"
COLOR_CARD = "#ffffff"
COLOR_BORDER = "#dfe3e8"
COLOR_RULE = "#edf0f3"
COLOR_TITLE = "#1a2b3c"
COLOR_TEXT = "#33475b"
COLOR_LABEL = "#6b7a8c"
COLOR_FOOTER_BG = "#f7f9fb"
COLOR_FOOTER_TEXT = "#8697a8"

ACCENT_REMINDER = "#1f4e79"
ACCENT_ALERT = "#b45309"


def _html_rows(rows: list[tuple[str, str]], accent: str, emphasise: str | None = None) -> str:
    """Render the invoice facts as a ledger-style table.

    ``emphasise`` names the one row that should read as the headline figure.
    """
    cells = []
    for index, (label, value) in enumerate(rows):
        last = index == len(rows) - 1
        border = "" if last else f"border-bottom:1px solid {COLOR_RULE};"
        highlighted = label == emphasise
        value_style = (
            f"font-size:18px;font-weight:700;color:{accent};"
            if highlighted
            else f"font-size:15px;font-weight:600;color:{COLOR_TITLE};"
        )
        cells.append(
            f'<tr>'
            f'<td style="padding:11px 0;{border}font-family:{FONT_STACK};font-size:13px;'
            f'color:{COLOR_LABEL};">{html.escape(label)}</td>'
            f'<td align="right" style="padding:11px 0;{border}font-family:{FONT_STACK};'
            f'{value_style}">{html.escape(value)}</td>'
            f'</tr>'
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
        + "".join(cells)
        + "</table>"
    )


def _html_document(
    *,
    preheader: str,
    accent: str,
    eyebrow: str,
    title: str,
    paragraphs: list[str],
    rows_html: str,
    closing: list[str],
    footer: str,
) -> str:
    """Wrap the pieces in the card layout shared by both notification types."""
    body_paragraphs = "".join(
        f'<p style="margin:0 0 14px;font-family:{FONT_STACK};font-size:15px;'
        f'line-height:1.65;color:{COLOR_TEXT};">{text}</p>'
        for text in paragraphs
    )
    closing_paragraphs = "".join(
        f'<p style="margin:0 0 14px;font-family:{FONT_STACK};font-size:15px;'
        f'line-height:1.65;color:{COLOR_TEXT};">{text}</p>'
        for text in closing
    )
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{html.escape(title)}</title>
</head>
<body style="margin:0;padding:0;background-color:{COLOR_PAGE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
{html.escape(preheader)}
</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="background-color:{COLOR_PAGE};">
<tr>
<td align="center" style="padding:32px 16px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"
       style="max-width:600px;width:100%;background-color:{COLOR_CARD};
              border:1px solid {COLOR_BORDER};border-radius:6px;">
<tr><td style="height:4px;background-color:{accent};
               border-radius:6px 6px 0 0;font-size:0;line-height:0;">&nbsp;</td></tr>
<tr>
<td style="padding:32px 36px 8px;">
<p style="margin:0 0 6px;font-family:{FONT_STACK};font-size:11px;font-weight:700;
          letter-spacing:0.9px;text-transform:uppercase;color:{accent};">
{html.escape(eyebrow)}</p>
<h1 style="margin:0 0 22px;font-family:{FONT_STACK};font-size:21px;font-weight:600;
           line-height:1.35;color:{COLOR_TITLE};">{html.escape(title)}</h1>
{body_paragraphs}
</td>
</tr>
<tr><td style="padding:6px 36px 20px;">{rows_html}</td></tr>
<tr><td style="padding:0 36px 30px;">{closing_paragraphs}</td></tr>
<tr>
<td style="padding:18px 36px;background-color:{COLOR_FOOTER_BG};
           border-top:1px solid {COLOR_BORDER};border-radius:0 0 6px 6px;">
<p style="margin:0;font-family:{FONT_STACK};font-size:12px;line-height:1.5;
          color:{COLOR_FOOTER_TEXT};">{footer}</p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>
"""


def _reminder_html(invoice: Invoice, days_overdue: int, signature: str) -> str:
    name = html.escape(invoice.customer_name)
    invoice_id = html.escape(invoice.id)
    due = html.escape(_format_date(invoice.due_date))
    rows = _html_rows(
        [
            ("Factura", invoice.id),
            ("Fecha de vencimiento", _format_date(invoice.due_date)),
            ("Días transcurridos", _plural_days(days_overdue)),
            ("Importe pendiente", _format_amount(invoice)),
        ],
        ACCENT_REMINDER,
        emphasise="Importe pendiente",
    )
    return _html_document(
        preheader=(
            f"Factura {invoice.id} por {_format_amount(invoice)}, "
            f"vencida el {_format_date(invoice.due_date)}."
        ),
        accent=ACCENT_REMINDER,
        eyebrow="Recordatorio de pago",
        title=f"Factura {invoice_id} pendiente de pago",
        paragraphs=[
            f"Apreciable <strong>{name}</strong>:",
            "Por este medio le informamos, de manera atenta, que a la fecha se encuentra "
            f"pendiente de pago la factura <strong>{invoice_id}</strong>, con vencimiento "
            f"el {due}.",
        ],
        rows_html=rows,
        closing=[
            "Le agradeceremos cubrir el importe correspondiente a la brevedad. "
            "En caso de que el pago ya se hubiera realizado, le pedimos hacer caso omiso "
            "del presente aviso o bien compartirnos el comprobante para actualizar el "
            "estado de su cuenta.",
            "Quedamos a sus órdenes para cualquier aclaración.",
            f"Atentamente,<br><strong>Departamento de Cobranza</strong><br>"
            f"{html.escape(signature)}",
        ],
        footer=(
            "Este mensaje se generó automáticamente a partir del estado de su cuenta. "
            "Si considera que se trata de un error, responda a este correo."
        ),
    )


def _alert_html(invoice: Invoice, days_overdue: int) -> str:
    invoice_id = html.escape(invoice.id)
    rows = _html_rows(
        [
            ("Factura", invoice.id),
            ("Cliente", invoice.customer_name),
            ("Contacto", invoice.customer_email),
            ("Fecha de vencimiento", _format_date(invoice.due_date)),
            ("Importe", _format_amount(invoice)),
            ("Días de atraso", _plural_days(days_overdue)),
        ],
        ACCENT_ALERT,
        emphasise="Días de atraso",
    )
    return _html_document(
        preheader=(
            f"{invoice.id} — {_plural_days(days_overdue)} de atraso, "
            f"{_format_amount(invoice)}."
        ),
        accent=ACCENT_ALERT,
        eyebrow="Alerta de cartera vencida",
        title=f"La factura {invoice_id} requiere seguimiento",
        paragraphs=[
            f"La factura <strong>{invoice_id}</strong> rebasó el umbral de atraso "
            "definido para el escalamiento a Operaciones.",
        ],
        rows_html=rows,
        closing=["El recordatorio de pago correspondiente ya fue enviado al cliente."],
        footer="Notificación automática del proceso de recordatorio de pagos.",
    )

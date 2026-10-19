# app/utils/email_service.py
import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import List, Optional

from app.core.config import SMTP_FROM_NAME, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_TIMEOUT, SMTP_USER

logger = logging.getLogger(__name__)


def enviar_correo_con_adjunto(
    destinatarios: List[str],
    asunto: str,
    cuerpo_html: str,
    archivo_bytes: Optional[bytes] = None,
    nombre_archivo: Optional[str] = None,
) -> bool:
    """Envía un correo HTML (con adjunto opcional). Devuelve True si se envió."""
    if not destinatarios:
        return False

    msg = MIMEMultipart("mixed")
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_USER))
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = asunto.replace("\r", " ").replace("\n", " ")
    msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))

    if archivo_bytes and nombre_archivo:
        adjunto = MIMEApplication(archivo_bytes, Name=nombre_archivo)
        adjunto["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
        msg.attach(adjunto)

    try:
        # Puerto 465 = SSL directo; 587 = STARTTLS
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
            server.starttls()
        with server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, destinatarios, msg.as_string())
        logger.info("Correo '%s' enviado a %d destinatario(s).", asunto, len(destinatarios))
        return True
    except Exception:
        logger.exception("Fallo al enviar el correo '%s'", asunto)
        return False

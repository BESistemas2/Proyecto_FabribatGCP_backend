import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

# Cargar variables de entorno
SMTP_HOST = os.getenv("SMTP_HOST", "mail.fabribat.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465)) # Puerto 465 requiere SSL Directo
SMTP_USER = os.getenv("SMTP_USER", "conciliacion@fabribat.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Conciliación Fabribat")


def enviar_correo_con_adjunto(
    destinatarios: List[str],
    asunto: str,
    cuerpo_html: str,
    archivo_bytes: Optional[bytes] = None,
    nombre_archivo: Optional[str] = None
)-> bool:

    if not destinatarios:
        return False
    
    """Servicio genérico para envío de correos con archivos adjuntos."""
    msg = MIMEMultipart("mixed")
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_USER}>"
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = asunto

    # Agregar el cuerpo HTML
    parte_html = MIMEText(cuerpo_html, "html", "utf-8")
    msg.attach(parte_html)

# Agregar el archivo adjunto (Excel .xlsx)
    if archivo_bytes and nombre_archivo:
        parte_adjunto = MIMEApplication(archivo_bytes, Name=nombre_archivo)
        parte_adjunto["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
        msg.attach(parte_adjunto)

    try:
        # IMPORTANTE: Usar SMTP_SSL para el puerto 465
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, destinatarios, msg.as_string())
        print(f"Correo enviado exitosamente a {len(destinatarios)} destinatarios.")
        return True
        
    except Exception as e:
        print(f"Fallo al enviar el correo SMTP: {str(e)}")
        return False
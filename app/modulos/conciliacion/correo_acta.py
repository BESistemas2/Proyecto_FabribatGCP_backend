# app/modulos/conciliacion/correo_acta.py
"""Correo del informe de conciliación (envío manual desde Actas y envío automático del Auto-Match)."""
import html
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import EMAIL_DOMINIOS_PERMITIDOS
from app.modulos.conciliacion.services.reporte_excel import generar_excel_informe_conciliacion

logger = logging.getLogger(__name__)

# Roles que reciben automáticamente los informes del Auto-Match programado
ROLES_DESTINATARIOS_AUTOMATICOS = ("RECEPTOR_MAIL", "CONTABILIDAD")

_ROJO = "#C8102E"   # rojo corporativo (versión sobria para correo)


def _e(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def dinero(v) -> str:
    return "Sin dato" if v is None else f"${v:,.2f}"


def nombre_archivo(datos: dict) -> str:
    limpio = lambda s: "".join(c if c.isalnum() else "_" for c in str(s)).strip("_")
    return f"Informe_Conciliacion_{limpio(datos.get('banco'))}_{limpio(datos.get('periodo_nombre'))}.xlsx"


def dominio_permitido(email: str) -> bool:
    return email.split("@")[-1].lower() in EMAIL_DOMINIOS_PERMITIDOS


def destinatarios_automaticos(db: Session) -> List[str]:
    """Usuarios activos con rol RECEPTOR_MAIL o CONTABILIDAD (solo dominios permitidos)."""
    filas = db.execute(text("""
        SELECT LOWER(email) FROM identidad.usuarios
         WHERE activo = true AND rol = ANY(:roles)
    """), {"roles": list(ROLES_DESTINATARIOS_AUTOMATICOS)}).scalars().all()
    return sorted({e for e in filas if e and dominio_permitido(e)})


def _plantilla(titulo: str, cuerpo: str) -> str:
    return f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;color:#333;margin:0;">
<div style="max-width:600px;margin:0 auto;border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;">
<div style="height:4px;background:linear-gradient(90deg,{_ROJO},#FBB804);"></div>
<div style="padding:20px;">
<h2 style="color:{_ROJO};margin-top:0;">{titulo}</h2>
{cuerpo}
<p style="font-size:11px;color:#777;border-top:1px solid #eee;padding-top:10px;margin-bottom:0;">
Mensaje generado automáticamente por Fabribat · Conciliación Bancaria.</p>
</div></div></body></html>"""


def _tabla(filas: Iterable[Tuple[str, str]]) -> str:
    cuerpo = "".join(
        f'<tr><td style="padding:8px;border-bottom:1px solid #ddd;"><b>{k}:</b></td>'
        f'<td style="padding:8px;border-bottom:1px solid #ddd;font-family:monospace;">{v}</td></tr>'
        for k, v in filas)
    return f'<table style="border-collapse:collapse;width:100%;font-size:13px;margin:20px 0;">{cuerpo}</table>'


def construir_correo_acta(datos: Dict[str, Any], intro: str, comentario: Optional[str] = None,
                          filas_extra: Iterable[Tuple[str, str]] = ()) -> Dict[str, Any]:
    """Asunto, HTML y adjunto Excel del informe de un acta. `intro` es texto plano (se escapa)."""
    filas = [
        ("Empresa", _e(datos["empresa"])),
        ("Banco", _e(datos["banco"])),
        ("Periodo", _e(datos["periodo_nombre"])),
        *filas_extra,
        ("Saldo final según extracto", dinero(datos.get("saldo_extracto_reportado"))),
        ("Movimiento neto extracto", dinero(datos.get("neto_banco"))),
        ("Movimiento neto libros", dinero(datos.get("neto_libros"))),
        ("Diferencia no explicada", dinero(datos.get("diferencia_no_explicada") or 0)),
    ]
    obs = (f'<div style="background:#f7f3ee;padding:12px;border-left:4px solid {_ROJO};font-size:13px;margin-bottom:20px;">'
           f'<b>Observaciones:</b> {_e(comentario)}</div>') if comentario else ""
    cuerpo = f"<p>{_e(intro)}</p>{_tabla(filas)}{obs}"
    return {
        "asunto": f"Informe de Conciliación Bancaria - {datos['banco']} ({datos['periodo_nombre']})",
        "cuerpo_html": _plantilla("Informe de Conciliación Bancaria", cuerpo),
        "archivo_bytes": generar_excel_informe_conciliacion(datos).getvalue(),
        "nombre_archivo": nombre_archivo(datos),
    }


def construir_correo_error(banco: str, cuenta: str, resumen: str) -> Dict[str, Any]:
    cuerpo = (f"<p>El Auto-Match programado de <b>{_e(banco)}</b> ({_e(cuenta)}) terminó con errores. "
              f"Revise la cuenta en la pantalla Cuentas o ejecute el Auto-Match manualmente desde el Workbench.</p>"
              f'<pre style="white-space:pre-wrap;background:#f7f3ee;padding:12px;font-size:12px;">{_e(resumen)}</pre>')
    return {"asunto": f"[Error] Auto-Match - {banco} ({cuenta})",
            "cuerpo_html": _plantilla("Auto-Match con errores", cuerpo)}

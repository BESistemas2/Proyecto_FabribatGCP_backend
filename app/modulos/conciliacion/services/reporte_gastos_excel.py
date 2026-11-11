"""Excel "Gastos bancarios" (ver GastosBancariosService.resumen): insumo para el asiento mensual."""
import io
from datetime import date
from typing import Any, Dict

from openpyxl import Workbook
from openpyxl.styles import Alignment

from app.modulos.conciliacion.services.reporte_excel import (
    FMT_MONEDA, _F_BOLD, _F_HEADER, _F_NORMAL, _F_SECCION, _F_SUB, _F_TITULO, _FILL, _texto_seguro,
)

FMT_FECHA = "DD/MM/YYYY"
MESES = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre")
_DER = Alignment(horizontal="right")


def _nombre_mes(mes: str) -> str:
    return f"{MESES[int(mes[5:]) - 1]} {mes[:4]}"


def generar_excel_gastos_bancarios(d: Dict[str, Any]) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Gastos bancarios"
    for col, ancho in zip("ABCDE", (13, 22, 52, 14, 16)):
        ws.column_dimensions[col].width = ancho

    ws["A1"] = "Gastos bancarios"
    ws["A1"].font = _F_TITULO
    ws["A2"] = f"{d['banco']} · Cuenta {d['numero_cuenta']} · BC {d['codigo_bc']}"
    ws["A2"].font = _F_SUB
    ws["A3"] = f"Del {d['fecha_inicio']:%d/%m/%Y} al {d['fecha_fin']:%d/%m/%Y}"
    ws["A3"].font = _F_SUB
    fila = 5

    if not d["meses"]:
        ws.cell(fila, 1, "No hay gastos bancarios en el periodo.").font = _F_NORMAL

    for m in d["meses"]:
        ws.cell(fila, 1, f"{_nombre_mes(m['mes'])} · {m['estado']}").font = _F_SECCION
        fila += 1
        ws.cell(fila, 1, "Fecha del asiento").font = _F_BOLD
        c = ws.cell(fila, 2, m["fecha_fin_mes"])
        c.number_format, c.font = FMT_FECHA, _F_NORMAL
        ws.cell(fila, 3, "Total a registrar").font = _F_BOLD
        c = ws.cell(fila, 5, float(m["total"]))
        c.number_format, c.font = FMT_MONEDA, _F_BOLD
        fila += 1
        for k, a in enumerate(m["asientos"]):
            ws.cell(fila, 1, "Asiento de BC" if k == 0 else "Ajuste en BC").font = _F_BOLD
            ws.cell(fila, 2, _texto_seguro(a["document_no"])).font = _F_NORMAL
            ws.cell(fila, 3, _texto_seguro(f"{a['fecha']:%d/%m/%Y} · {a['descripcion']}")).font = _F_NORMAL
            c = ws.cell(fila, 5, float(a["monto"]))
            c.number_format, c.font = FMT_MONEDA, _F_NORMAL
            fila += 1

        fila += 1
        ws.cell(fila, 1, "Resumen por tipo").font = _F_BOLD
        fila += 1
        for cat in m["categorias"]:
            ws.cell(fila, 3, cat["categoria"]).font = _F_NORMAL
            c = ws.cell(fila, 5, float(cat["monto"]))
            c.number_format, c.font = FMT_MONEDA, _F_NORMAL
            fila += 1

        fila += 1
        for col, titulo in enumerate(("Fecha", "Referencia", "Concepto", "Tipo", "Monto"), 1):
            c = ws.cell(fila, col, titulo)
            c.font, c.fill = _F_HEADER, _FILL
            if col == 5:
                c.alignment = _DER
        fila += 1
        for mov in m["movimientos"]:
            c = ws.cell(fila, 1, mov["fecha"])
            c.number_format, c.font = FMT_FECHA, _F_NORMAL
            ws.cell(fila, 2, _texto_seguro(mov["referencia"])).font = _F_NORMAL
            ws.cell(fila, 3, _texto_seguro(mov["concepto"])).font = _F_NORMAL
            ws.cell(fila, 4, mov["categoria"]).font = _F_NORMAL
            c = ws.cell(fila, 5, float(mov["monto"]))
            c.number_format, c.font = FMT_MONEDA, _F_NORMAL
            fila += 1
        ws.cell(fila, 4, f"Total ({m['cantidad']})").font = _F_BOLD
        c = ws.cell(fila, 5, float(m["total"]))
        c.number_format, c.font = FMT_MONEDA, _F_BOLD
        fila += 3

    ws.freeze_panes = "A5"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0
    salida = io.BytesIO()
    wb.save(salida)
    salida.seek(0)
    return salida


def nombre_archivo_gastos(d: Dict[str, Any]) -> str:
    limpio = lambda s: "".join(ch if ch.isalnum() else "_" for ch in str(s)).strip("_")
    return (f"Gastos_Bancarios_{limpio(d['banco'])}_{limpio(d['codigo_bc'])}_"
            f"{d['fecha_inicio']:%Y%m%d}_{d['fecha_fin']:%Y%m%d}.xlsx")

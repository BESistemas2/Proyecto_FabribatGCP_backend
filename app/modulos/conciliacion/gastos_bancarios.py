# app/modulos/conciliacion/gastos_bancarios.py
"""
Gastos bancarios: reconocer comisiones, tarifas e IVA cobrados por el banco.

Los patrones viven en conciliacion.patrones_gasto_bancario (editables por ADMIN).
La comparación se hace sobre el concepto del movimiento en mayúsculas, sin tildes
y con espacios colapsados. Solo los débitos se consideran gasto bancario.
"""
from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.modulos.bancos.etl import normalizar_texto
from app.modulos.conciliacion import motor
from app.modulos.conciliacion.models import PatronGastoBancario
from app.modulos.conciliacion.repository import ConciliacionRepository

TIPOS_COINCIDENCIA = ("EMPIEZA", "CONTIENE", "REGEX")


@dataclass(frozen=True)
class Patron:
    banco: str
    patron: str
    tipo: str
    categoria: str

    def coincide(self, concepto_normalizado: str) -> bool:
        if self.tipo == "REGEX":
            try:
                return re.search(self.patron, concepto_normalizado) is not None
            except re.error:
                return False
        p = normalizar_texto(self.patron)
        return concepto_normalizado.startswith(p) if self.tipo == "EMPIEZA" else p in concepto_normalizado


def patrones_para_banco(patrones: Iterable[Patron], banco_cuenta: str) -> List[Patron]:
    """Patrones cuyo `banco` aparece en el nombre del banco de la cuenta."""
    nombre = normalizar_texto(banco_cuenta)
    return [p for p in patrones if normalizar_texto(p.banco) in nombre]


def clasificar(concepto: Optional[str], monto, patrones: Iterable[Patron]) -> Optional[str]:
    """Categoría del gasto bancario (Comisión, Tarifa, IVA...) o None. Solo débitos."""
    if not concepto or monto is None or monto >= 0:
        return None
    texto = normalizar_texto(concepto)
    for p in patrones:
        if p.coincide(texto):
            return p.categoria
    return None


def cargar_patrones(db: Session, banco_cuenta: str) -> List[Patron]:
    filas = (db.query(PatronGastoBancario)
             .filter(PatronGastoBancario.activo.is_(True))
             .order_by(PatronGastoBancario.id_patron).all())
    return patrones_para_banco((Patron(f.banco, f.patron, f.tipo_coincidencia, f.categoria) for f in filas),
                               banco_cuenta)


def validar_patron(patron: str, tipo: str) -> Optional[str]:
    """Mensaje de error o None."""
    if tipo not in TIPOS_COINCIDENCIA:
        return f"Tipo de coincidencia inválido: use {', '.join(TIPOS_COINCIDENCIA)}."
    if not (patron or "").strip():
        return "El patrón no puede estar vacío."
    if tipo == "REGEX":
        try:
            re.compile(patron)
        except re.error as e:
            return f"Expresión regular inválida: {e}"
    return None


# ---------------------------------------------------------------------------
# Vista mensual (insumo para el asiento) y catálogo
# ---------------------------------------------------------------------------
ESTADO_ESPERANDO = "Esperando asiento"
ESTADO_CONCILIADO = "Conciliado"
ESTADO_REVISAR = "Revisar"   # hay gastos conciliados y otros nuevos sin asiento en el mismo mes


def _dec(v: Any) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


class GastosBancariosService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = ConciliacionRepository(db)

    def resumen(self, id_cuenta: UUID, f_ini: date, f_fin: date) -> Dict[str, Any]:
        """
        Gastos bancarios por mes: detalle, subtotales por categoría, total y asiento (si ya se cruzó).
        Siempre usa meses completos: el asiento se registra por el total del mes.
        """
        if f_ini > f_fin:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "La fecha inicial es posterior a la final.")
        f_ini = f_ini.replace(day=1)
        f_fin = f_fin.replace(day=monthrange(f_fin.year, f_fin.month)[1])
        cuenta = self.repo.obtener_cuenta_bancaria(id_cuenta)
        if not cuenta:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Cuenta bancaria no encontrada.")
        patrones = cargar_patrones(self.db, cuenta.banco_nombre)

        movs: List[Dict[str, Any]] = []
        for m in self.repo.movimientos_banco_pendientes(id_cuenta, f_ini, f_fin):
            cat = clasificar(m.concepto, m.monto, patrones)
            if cat:
                movs.append({"id_movimiento": str(m.id_movimiento), "fecha": m.fecha_transaccion,
                             "referencia": m.numero_referencia or "", "concepto": m.concepto or "",
                             "categoria": cat, "monto": _dec(m.monto), "conciliado": False})
        conc_banco, conc_erp = self.repo.listar_gastos_bancarios_conciliados(id_cuenta, f_ini, f_fin)
        for r in conc_banco:
            movs.append({"id_movimiento": str(r["id_movimiento"]), "fecha": r["fecha_transaccion"],
                         "referencia": r["numero_referencia"] or "", "concepto": r["concepto"] or "",
                         "categoria": clasificar(r["concepto"], r["monto"], patrones) or "Gasto bancario",
                         "monto": _dec(r["monto"]), "conciliado": True})
        asientos = {f"{r['posting_date']:%Y-%m}": r for r in conc_erp}

        meses: Dict[str, List[Dict[str, Any]]] = {}
        for m in sorted(movs, key=lambda x: (x["fecha"], x["referencia"])):
            meses.setdefault(f"{m['fecha']:%Y-%m}", []).append(m)

        salida = []
        for mes, lista in sorted(meses.items()):
            anio, num = int(mes[:4]), int(mes[5:])
            fin = date(anio, num, monthrange(anio, num)[1])
            pendientes = [m for m in lista if not m["conciliado"]]
            conciliados = [m for m in lista if m["conciliado"]]
            categorias: Dict[str, Decimal] = {}
            for m in lista:
                categorias[m["categoria"]] = categorias.get(m["categoria"], Decimal("0.00")) + m["monto"]
            asiento = asientos.get(mes)
            estado = (ESTADO_REVISAR if pendientes and conciliados
                      else ESTADO_CONCILIADO if conciliados else ESTADO_ESPERANDO)
            salida.append({
                "mes": mes,
                "fecha_fin_mes": fin,
                "fecha_asiento_desde": date(anio, num, motor.DIA_INICIO_ASIENTO_GASTOS),
                "estado": estado,
                "total": sum((m["monto"] for m in lista), Decimal("0.00")),
                "total_pendiente": sum((m["monto"] for m in pendientes), Decimal("0.00")),
                "cantidad": len(lista),
                "categorias": [{"categoria": k, "monto": v} for k, v in sorted(categorias.items())],
                "movimientos": lista,
                "asiento": ({"document_no": asiento["document_no"], "fecha": asiento["posting_date"],
                             "descripcion": asiento["description"] or "", "monto": _dec(asiento["monto"]),
                             "entry_no_bc": asiento["entry_no_bc"]} if asiento else None),
            })
        return {
            "id_cuenta": str(id_cuenta),
            "banco": cuenta.banco_nombre,
            "numero_cuenta": cuenta.numero_cuenta or "",
            "codigo_bc": cuenta.codigo_banco_bc,
            "fecha_inicio": f_ini,
            "fecha_fin": f_fin,
            "tiene_patrones": bool(patrones),
            "meses": salida,
        }

    # --- Catálogo de patrones -------------------------------------------------
    def listar_patrones(self) -> List[PatronGastoBancario]:
        return (self.db.query(PatronGastoBancario)
                .order_by(PatronGastoBancario.banco, PatronGastoBancario.id_patron).all())

    def guardar_patron(self, datos: Dict[str, Any], usuario: str, id_patron: Optional[int] = None) -> PatronGastoBancario:
        datos = {**datos, "banco": normalizar_texto(datos["banco"]),
                 "patron": datos["patron"].strip() if datos["tipo_coincidencia"] == "REGEX"
                 else normalizar_texto(datos["patron"])}
        error = validar_patron(datos["patron"], datos["tipo_coincidencia"])
        if error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, error)
        if id_patron is None:
            p = PatronGastoBancario(creado_por=usuario, **datos)
            self.db.add(p)
        else:
            p = self.db.get(PatronGastoBancario, id_patron)
            if not p:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Patrón no encontrado.")
            for k, v in datos.items():
                setattr(p, k, v)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe ese patrón para el banco.")
        self.db.refresh(p)
        return p

    def eliminar_patron(self, id_patron: int) -> None:
        p = self.db.get(PatronGastoBancario, id_patron)
        if not p:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Patrón no encontrado.")
        self.db.delete(p)
        self.db.commit()

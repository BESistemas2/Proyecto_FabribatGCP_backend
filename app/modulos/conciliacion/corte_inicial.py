# app/modulos/conciliacion/corte_inicial.py
"""
Corte inicial de una cuenta: los saldos de la última acta cerrada fuera del sistema y sus
partidas en tránsito. Es el punto de partida del cuadre por saldos:

    saldo según libros = saldo del banco − cheques no cobrados + notas de débito no registradas
                         − notas de crédito no registradas + pendientes por acreditar
                         − pendientes por debitar

Solo se guarda si cuadra (diferencia 0). Se bloquea cuando ya hay un acta cerrada en el
sistema posterior a la fecha del corte.
"""
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.modulos.bancos.models import CuentaBancaria
from app.modulos.conciliacion.models import Acta, CorteInicial, PartidaInicial
from app.modulos.conciliacion.schemas import CorteInicialIn

# Signo con que cada categoría ajusta el saldo del banco para llegar al de libros
SIGNO = {
    "CHEQUE_NO_COBRADO": -1,
    "NOTA_DEBITO_NO_REGISTRADA": 1,
    "NOTA_CREDITO_NO_REGISTRADA": -1,
    "PENDIENTE_ACREDITAR": 1,
    "PENDIENTE_DEBITAR": -1,
}
NOMBRE = {
    "CHEQUE_NO_COBRADO": "Cheques girados y no cobrados",
    "NOTA_DEBITO_NO_REGISTRADA": "Notas de débito no registradas",
    "NOTA_CREDITO_NO_REGISTRADA": "Notas de crédito no registradas",
    "PENDIENTE_ACREDITAR": "Valores pendientes por acreditar",
    "PENDIENTE_DEBITAR": "Valores pendientes por debitar",
}
CERO = Decimal("0.00")


def _es(v) -> str:
    """287.078,92"""
    return f"{Decimal(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def saldo_libros_calculado(saldo_banco: Decimal, partidas) -> Decimal:
    total = Decimal(saldo_banco)
    for p in partidas:
        total += SIGNO[p.categoria] * Decimal(p.valor)
    return total.quantize(Decimal("0.01"))


def _motivo_bloqueo(db: Session, corte: Optional[CorteInicial]) -> Optional[str]:
    if not corte:
        return None
    acta = (db.query(Acta)
            .filter(Acta.id_cuenta == corte.id_cuenta, Acta.estado == "CERRADO",
                    Acta.periodo > f"{corte.fecha_corte:%Y-%m}")
            .order_by(Acta.periodo).first())
    return f"Ya hay un acta cerrada posterior al corte ({acta.periodo})." if acta else None


def _salida(db: Session, corte: CorteInicial) -> Dict[str, Any]:
    calc = saldo_libros_calculado(corte.saldo_banco, corte.partidas)
    motivo = _motivo_bloqueo(db, corte)
    return {
        "id_corte": corte.id_corte, "id_cuenta": corte.id_cuenta, "fecha_corte": corte.fecha_corte,
        "saldo_banco": corte.saldo_banco, "saldo_libros": corte.saldo_libros, "observacion": corte.observacion,
        "partidas": [{"id_partida": p.id_partida, "categoria": p.categoria, "fecha": p.fecha, "detalle": p.detalle,
                      "referencia": p.referencia, "valor": p.valor, "observacion": p.observacion,
                      "estado": p.estado} for p in corte.partidas],
        "saldo_libros_calculado": calc, "diferencia": (calc - Decimal(corte.saldo_libros)).quantize(Decimal("0.01")),
        "bloqueado": motivo is not None, "motivo_bloqueo": motivo,
        "creado_por": corte.creado_por, "actualizado_por": corte.actualizado_por,
        "updated_at": corte.updated_at.isoformat() if corte.updated_at else None,
    }


def obtener(db: Session, id_cuenta: UUID) -> Optional[Dict[str, Any]]:
    corte = db.query(CorteInicial).filter(CorteInicial.id_cuenta == id_cuenta).first()
    return _salida(db, corte) if corte else None


def guardar(db: Session, id_cuenta: UUID, datos: CorteInicialIn, usuario: str) -> Dict[str, Any]:
    if not db.get(CuentaBancaria, id_cuenta):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cuenta bancaria no encontrada.")
    calc = saldo_libros_calculado(datos.saldo_banco, datos.partidas)
    diferencia = calc - Decimal(datos.saldo_libros)
    if diferencia != 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"El corte no cuadra: el saldo del banco más los ajustes da {_es(calc)} y el saldo de libros es "
            f"{_es(datos.saldo_libros)} (diferencia {_es(diferencia)}).")
    for p in datos.partidas:
        if p.fecha > datos.fecha_corte:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"La partida «{p.detalle}» tiene fecha posterior al corte.")

    corte = db.query(CorteInicial).filter(CorteInicial.id_cuenta == id_cuenta).with_for_update().first()
    motivo = _motivo_bloqueo(db, corte)
    if motivo:
        raise HTTPException(status.HTTP_409_CONFLICT, f"El corte inicial ya no se puede modificar. {motivo}")
    if not corte:
        corte = CorteInicial(id_cuenta=id_cuenta, creado_por=usuario)
        db.add(corte)
    else:
        corte.actualizado_por = usuario
    corte.fecha_corte, corte.saldo_banco, corte.saldo_libros = datos.fecha_corte, datos.saldo_banco, datos.saldo_libros
    corte.observacion = (datos.observacion or "").strip() or None
    corte.partidas = [PartidaInicial(categoria=p.categoria, fecha=p.fecha, detalle=p.detalle.strip(),
                                     referencia=(p.referencia or "").strip() or None, valor=p.valor,
                                     observacion=(p.observacion or "").strip() or None)
                      for p in datos.partidas]
    db.commit()
    db.refresh(corte)
    return _salida(db, corte)

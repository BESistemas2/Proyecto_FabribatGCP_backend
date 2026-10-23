# app/modulos/conciliacion/diagnostico.py
"""
Diagnóstico de cruces para el reporte "Detalle de cruces" (funciones puras, sin BD).

- referencia_coincidente: qué referencia une un movimiento del banco con un asiento de BC.
- diagnosticar_pendientes: por qué un movimiento (banco o BC) no cruzó, con las mismas
  ventanas y la misma normalización que usa el motor. Solo lee: no concilia nada.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from app.modulos.conciliacion import motor
from app.modulos.conciliacion.motor import MovBanco, MovErp, VENTANA_DIAS, VENTANA_REF_ADICIONAL

SIN_REFERENCIA = "— (por monto y fecha)"

NOMBRES_REGLA = {
    motor.REGLA_1: ("Referencia + monto exacto", "Misma referencia y monto, ±3 días."),
    motor.REGLA_1_GYE: ("Referencia REF2 + monto exacto", "Banco Guayaquil: cruce por REF2 sin referencia principal."),
    motor.REGLA_1N: ("Consolidado 1:N por referencia",
                     "Varios asientos de BC de la misma referencia suman el monto del banco, ±3 días."),
    motor.REGLA_2: ("Monto + fecha", "Mismo monto, ±3 días, un único candidato posible."),
    motor.REGLA_3: ("Suma de asientos", "La suma de 2 o 3 asientos de BC iguala el monto del banco (solución única)."),
    motor.REGLA_REF_ADICIONAL: ("Referencia adicional del banco",
                                "Débitos: REF/REF2/REF3 del banco = documento externo de BC, mismo monto, ±15 días."),
    motor.REGLA_REVERSO: ("Reverso en Business Central",
                          "Asientos con el mismo documento y fecha que se anulan (+X / −X). No cruzan con el banco."),
}


def nombre_regla(codigo: str | None) -> str:
    if not codigo:
        return "Manual"
    return NOMBRES_REGLA.get(codigo, (codigo, ""))[0]


def _dinero(v) -> str:
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def _ref(a, b) -> str:
    comunes = sorted(a.referencias & b.referencias, key=lambda r: (-len(r), r))
    return comunes[0] if comunes else ""


def _dias(b: MovBanco, e: MovErp) -> int:
    return (e.fecha - b.fecha).days


def referencia_coincidente(b: MovBanco, e: MovErp, regla: str | None) -> str:
    """Referencia normalizada que comparten el banco y BC (la que usó el motor, si hubo)."""
    if regla == motor.REGLA_REF_ADICIONAL and e.documento_externo in b.referencias_adicionales:
        return e.documento_externo
    comunes = sorted(b.referencias & e.referencias, key=lambda r: (-len(r), r))
    return comunes[0] if comunes else SIN_REFERENCIA


# ---------------------------------------------------------------------------
# Pendientes
# ---------------------------------------------------------------------------

def _indice_refs(movs: Iterable) -> Dict[str, List]:
    idx: Dict[str, List] = defaultdict(list)
    for m in movs:
        for r in m.referencias:
            idx[r].append(m)
    return idx


def _indice_monto(movs: Iterable) -> Dict[int, List]:
    idx: Dict[int, List] = defaultdict(list)
    for m in movs:
        idx[motor._centavos(m.monto)].append(m)
    return idx


def _mismo_signo(a, b) -> bool:
    return (a.monto > 0) == (b.monto > 0)


def _diag_banco(b: MovBanco, erp_ref, erp_monto, erp_conc_ref, banco_monto) -> str:
    cb = motor._centavos(b.monto)

    # 1. Referencia encontrada en asientos de BC pendientes
    con_ref = {e.id: e for r in b.referencias for e in erp_ref.get(r, ())}.values()
    if con_ref:
        con_ref = sorted(con_ref, key=lambda e: (abs(_dias(b, e)), str(e.id)))
        mismo_monto = [e for e in con_ref if motor._centavos(e.monto) == cb]
        if mismo_monto:
            e = mismo_monto[0]
            return (f"Referencia {_ref(b, e)} y monto coinciden con BC {e.documento or '(sin doc.)'} del {e.fecha:%d/%m/%Y}, "
                    f"pero a {abs(_dias(b, e))} días (ventana ±{VENTANA_DIAS}).")
        opuestos = [e for e in con_ref if not _mismo_signo(b, e)]
        if opuestos and len(opuestos) == len(con_ref):
            e = opuestos[0]
            return (f"Referencia {_ref(b, e)} encontrada en BC {e.documento or '(sin doc.)'} pero con signo contrario "
                    f"({_dinero(e.monto)}).")
        mismos = [e for e in con_ref if _mismo_signo(b, e)]
        if len(mismos) == 1:
            e = mismos[0]
            return (f"Referencia {_ref(b, e)} encontrada en BC {e.documento or '(sin doc.)'} con otro monto "
                    f"({_dinero(e.monto)}; banco {_dinero(b.monto)}).")
        en_ventana = [e for e in mismos if abs(_dias(b, e)) <= VENTANA_DIAS]
        suma = sum(e.monto for e in en_ventana)
        return (f"{len(mismos)} asientos de BC con la referencia {_ref(b, mismos[0])} ({len(en_ventana)} en ±{VENTANA_DIAS} días, "
                f"suman {_dinero(suma)}); no igualan el monto del banco ({_dinero(b.monto)}).")

    # 2. La referencia está en un asiento ya conciliado con otro movimiento
    ya = {e.id: e for r in b.referencias for e in erp_conc_ref.get(r, ())}.values()
    if ya:
        e = sorted(ya, key=lambda e: str(e.id))[0]
        return (f"La referencia {_ref(b, e)} está en BC {e.documento or '(sin doc.)'}, ya conciliado con otro "
                f"movimiento del banco.")

    # 3. Sin referencia: por monto
    mismo_monto = [e for e in erp_monto.get(cb, ())]
    en_ventana = [e for e in mismo_monto if abs(_dias(b, e)) <= VENTANA_DIAS]
    if len(en_ventana) > 1:
        return (f"Ambiguo: {len(en_ventana)} asientos de BC con el mismo monto en ±{VENTANA_DIAS} días; "
                f"sin referencia que los distinga.")
    if len(en_ventana) == 1:
        e = en_ventana[0]
        rivales = [o for o in banco_monto.get(cb, ()) if o.id != b.id and abs(_dias(o, e)) <= VENTANA_DIAS]
        if rivales:
            return (f"Ambiguo: otro movimiento del banco del mismo monto compite por BC "
                    f"{e.documento or '(sin doc.)'} del {e.fecha:%d/%m/%Y}.")
        return f"Candidato BC {e.documento or '(sin doc.)'} del {e.fecha:%d/%m/%Y}: candidato único por monto; aún no se ejecuta el cruce con estos datos."
    if mismo_monto:
        e = min(mismo_monto, key=lambda e: (abs(_dias(b, e)), str(e.id)))
        extra = ""
        if b.monto < 0 and b.referencias_adicionales and abs(_dias(b, e)) <= VENTANA_REF_ADICIONAL:
            extra = " La REF adicional del banco no coincide con su documento externo."
        return (f"Mismo monto en BC {e.documento or '(sin doc.)'} del {e.fecha:%d/%m/%Y}, a {abs(_dias(b, e))} días: "
                f"fuera de la ventana y sin referencia común.{extra}")
    return "Sin candidato en BC: ninguna referencia ni monto coincidente en el periodo."


def _diag_erp(e: MovErp, banco_ref, banco_monto, banco_conc_ref, erp_monto) -> str:
    ce = motor._centavos(e.monto)
    doc = e.documento or "(sin doc.)"

    con_ref = {b.id: b for r in e.referencias for b in banco_ref.get(r, ())}.values()
    if con_ref:
        con_ref = sorted(con_ref, key=lambda b: (abs(_dias(b, e)), str(b.id)))
        mismo_monto = [b for b in con_ref if motor._centavos(b.monto) == ce]
        if mismo_monto:
            b = mismo_monto[0]
            return (f"Referencia {_ref(b, e)} y monto coinciden con el banco del {b.fecha:%d/%m/%Y}, pero a "
                    f"{abs(_dias(b, e))} días (ventana ±{VENTANA_DIAS}).")
        mismos = [b for b in con_ref if _mismo_signo(b, e)]
        if not mismos:
            b = con_ref[0]
            return f"Referencia {_ref(b, e)} encontrada en el banco del {b.fecha:%d/%m/%Y} pero con signo contrario ({_dinero(b.monto)})."
        b = mismos[0]
        return (f"Referencia {_ref(b, e)} encontrada en el banco del {b.fecha:%d/%m/%Y} con otro monto "
                f"({_dinero(b.monto)}; BC {_dinero(e.monto)}). Puede ser parte de un cruce 1:N incompleto.")

    ya = {b.id: b for r in e.referencias for b in banco_conc_ref.get(r, ())}.values()
    if ya:
        b = sorted(ya, key=lambda b: str(b.id))[0]
        return (f"La referencia {_ref(b, e)} está en un movimiento del banco del {b.fecha:%d/%m/%Y} ya conciliado "
                f"con otro(s) asiento(s).")

    mismo_monto = list(banco_monto.get(ce, ()))
    en_ventana = [b for b in mismo_monto if abs(_dias(b, e)) <= VENTANA_DIAS]
    if len(en_ventana) > 1:
        return (f"Ambiguo: {len(en_ventana)} movimientos del banco con el mismo monto en ±{VENTANA_DIAS} días; "
                f"sin referencia que los distinga.")
    if len(en_ventana) == 1:
        b = en_ventana[0]
        rivales = [o for o in erp_monto.get(ce, ()) if o.id != e.id and abs(_dias(b, o)) <= VENTANA_DIAS]
        if rivales:
            return (f"Ambiguo: otro asiento de BC del mismo monto compite por el movimiento del banco "
                    f"del {b.fecha:%d/%m/%Y}.")
        return f"Candidato en el banco del {b.fecha:%d/%m/%Y}: candidato único por monto; aún no se ejecuta el cruce con estos datos."
    if mismo_monto:
        b = min(mismo_monto, key=lambda b: (abs(_dias(b, e)), str(b.id)))
        return (f"Mismo monto en el banco del {b.fecha:%d/%m/%Y}, a {abs(_dias(b, e))} días: "
                f"fuera de la ventana y sin referencia común.")
    return (f"Sin contrapartida en el banco: el extracto del periodo no tiene ese monto ni la referencia {doc}. "
            f"Posible partida en tránsito.")


def diagnosticar_pendientes(
    banco_pend: Iterable[MovBanco], erp_pend: Iterable[MovErp],
    banco_conc: Iterable[MovBanco] = (), erp_conc: Iterable[MovErp] = (),
) -> Tuple[Dict[object, str], Dict[object, str]]:
    """Observación (texto) por id para cada pendiente del banco y de BC."""
    banco_pend, erp_pend = list(banco_pend), list(erp_pend)
    erp_ref, erp_monto = _indice_refs(erp_pend), _indice_monto(erp_pend)
    banco_ref, banco_monto = _indice_refs(banco_pend), _indice_monto(banco_pend)
    erp_conc_ref, banco_conc_ref = _indice_refs(erp_conc), _indice_refs(banco_conc)

    obs_banco = {b.id: _diag_banco(b, erp_ref, erp_monto, erp_conc_ref, banco_monto) for b in banco_pend}
    obs_erp = {e.id: _diag_erp(e, banco_ref, banco_monto, banco_conc_ref, erp_monto) for e in erp_pend}
    return obs_banco, obs_erp


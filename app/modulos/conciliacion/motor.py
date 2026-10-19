# app/modulos/conciliacion/motor.py
"""
Motor de cruce banco <-> Business Central (funciones puras, sin BD).

Convención de signo en ambos lados: positivo = entra dinero, negativo = sale.
Un movimiento solo cruza con otro(s) del MISMO signo y por el monto exacto.

Reglas, en orden de prioridad:
  R1  Referencia + monto exacto + fecha (±VENTANA_DIAS)          -> 1 a 1
  R1N Referencia + suma exacta de N asientos de BC + fecha        -> 1 a N
  R2  Monto exacto + fecha, sin ambigüedad (un solo candidato)   -> 1 a 1  (parcial)
  R3  Suma de 2 o 3 asientos de BC en la ventana de fechas        -> 1 a N  (parcial)
"""
from __future__ import annotations

import itertools
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Set

VENTANA_DIAS = 3
MAX_CANDIDATOS_COMBINATORIA = 25
MAX_TAMANO_COMBINACION = 3

REGLA_1 = "REGLA_1_EXACTA"
REGLA_1_GYE = "REGLA_1_EXACTA_REF2_GYE"
REGLA_1N = "REGLA_2_1_A_N_REF"
REGLA_2 = "REGLA_2_PARCIAL"
REGLA_3 = "REGLA_4_SUBCONJUNTO_COMBINATORIO"

ESTADO_EXACTO = "CONCILIADO"
ESTADO_PARCIAL = "CONCILIADO_PARCIAL"

_REFS_VACIAS = {"", "0", "SN", "SR", "NULL", "NONE", "NAN"}


def normalizar_referencia(ref: object) -> str:
    """Mayúsculas, solo letras/dígitos y sin ceros a la izquierda ('No.0068745486' -> 'NO68745486')."""
    s = re.sub(r"[^A-Z0-9]", "", str(ref or "").upper())
    s = s.lstrip("0")
    return "" if s in _REFS_VACIAS else s


def referencias_de_texto(*valores: object) -> Set[str]:
    """Referencia completa normalizada + cada bloque numérico de 5 o más dígitos que contenga."""
    refs: Set[str] = set()
    for v in valores:
        texto = str(v or "")
        r = normalizar_referencia(texto)
        if r and len(r) <= 40:
            refs.add(r)
        for num in re.findall(r"\d{5,}", texto):
            n = normalizar_referencia(num)
            if n:
                refs.add(n)
    return refs


def referencias_banco(numero_referencia: object, info_adicional: object) -> Set[str]:
    """
    Referencias candidatas de un movimiento bancario: el nº de documento y los campos
    REF / REF2 / REF3 que el ETL guarda en info_adicional ("REF2: 123 / OF: MATRIZ").
    """
    valores = [numero_referencia]
    for parte in str(info_adicional or "").split(" / "):
        clave, _, valor = parte.partition(":")
        if clave.strip().upper() in ("REF", "REF2", "REF3"):
            valores.append(valor.strip())
    return referencias_de_texto(*valores)


@dataclass
class MovBanco:
    id: object
    fecha: date
    monto: Decimal
    referencias: Set[str]
    referencia_principal: str = ""


@dataclass
class MovErp:
    id: object
    fecha: date
    monto: Decimal
    referencias: Set[str]


@dataclass
class Cruce:
    id_banco: object
    ids_erp: List[object]
    regla: str
    estado: str


@dataclass
class ResultadoMotor:
    cruces: List[Cruce] = field(default_factory=list)

    @property
    def ids_banco(self) -> Set[object]:
        return {c.id_banco for c in self.cruces}

    @property
    def ids_erp(self) -> Set[object]:
        return {i for c in self.cruces for i in c.ids_erp}


def _centavos(monto: Decimal) -> int:
    return int((Decimal(monto) * 100).to_integral_value())


def _en_ventana(a: date, b: date) -> bool:
    return abs((a - b).days) <= VENTANA_DIAS


def _pasada(movs_banco: Iterable[MovBanco], movs_erp: Iterable[MovErp], es_banco_guayaquil: bool = False) -> ResultadoMotor:
    banco = sorted(movs_banco, key=lambda m: (m.fecha, str(m.id)))
    erp = list(movs_erp)

    # Índices construidos una sola vez (antes se recorría todo el ERP por cada movimiento)
    por_monto: Dict[int, List[MovErp]] = defaultdict(list)
    por_ref: Dict[str, List[MovErp]] = defaultdict(list)
    for e in erp:
        por_monto[_centavos(e.monto)].append(e)
        for r in e.referencias:
            por_ref[r].append(e)

    usados: Set[object] = set()
    resultado = ResultadoMotor()
    pendientes: List[MovBanco] = []

    def libres(lista: Iterable[MovErp]) -> List[MovErp]:
        return [e for e in lista if e.id not in usados]

    def cercano(b: MovBanco, candidatos: List[MovErp]) -> Optional[MovErp]:
        validos = [e for e in candidatos if _en_ventana(b.fecha, e.fecha)]
        return min(validos, key=lambda e: (abs((b.fecha - e.fecha).days), str(e.id))) if validos else None

    def registrar(b: MovBanco, es: List[MovErp], regla: str, estado: str) -> None:
        for e in es:
            usados.add(e.id)
        resultado.cruces.append(Cruce(b.id, [e.id for e in es], regla, estado))

    # --- R1 y R1N: por referencia ----------------------------------------------
    for b in banco:
        cb = _centavos(b.monto)
        candidatos = {e.id: e for r in b.referencias for e in por_ref.get(r, ())}
        candidatos = [e for e in libres(candidatos.values()) if _en_ventana(b.fecha, e.fecha)]

        mismo_monto = [e for e in candidatos if _centavos(e.monto) == cb]
        elegido = cercano(b, mismo_monto)
        if elegido:
            regla = REGLA_1_GYE if (es_banco_guayaquil and not normalizar_referencia(b.referencia_principal)) else REGLA_1
            registrar(b, [elegido], regla, ESTADO_EXACTO)
            continue

        # 1 a N: todos los asientos de una misma referencia suman el monto del banco
        hecho = False
        for r in sorted(b.referencias):
            grupo = [e for e in candidatos if r in e.referencias and (e.monto > 0) == (b.monto > 0)]
            if len(grupo) > 1 and sum(_centavos(e.monto) for e in grupo) == cb:
                registrar(b, grupo, REGLA_1N, ESTADO_EXACTO)
                hecho = True
                break
        if not hecho:
            pendientes.append(b)

    # --- R2: monto exacto + fecha, solo si el cruce NO es ambiguo -------------------
    # Si hay varios movimientos con el mismo monto en la ventana (p. ej. comisiones de
    # $0.48 o transferencias de $60.00), se deja pendiente para revisión manual:
    # es preferible un pendiente a un cruce equivocado.
    banco_por_monto: Dict[int, List[MovBanco]] = defaultdict(list)
    for b in pendientes:
        banco_por_monto[_centavos(b.monto)].append(b)
    restantes: List[MovBanco] = []
    for b in pendientes:
        cb = _centavos(b.monto)
        opciones = [e for e in libres(por_monto.get(cb, ())) if _en_ventana(b.fecha, e.fecha)]
        rivales = [o for o in banco_por_monto[cb] if o is not b and o.id not in resultado.ids_banco
                   and any(_en_ventana(o.fecha, e.fecha) for e in opciones)]
        if len(opciones) == 1 and not rivales:
            registrar(b, opciones, REGLA_2, ESTADO_PARCIAL)
        else:
            restantes.append(b)

    # --- R3: combinación de 2..3 asientos del mismo signo ---------------------------
    erp_por_fecha = sorted(erp, key=lambda e: e.fecha)
    for b in restantes:
        cb = _centavos(b.monto)
        candidatos = [
            e for e in erp_por_fecha
            if e.id not in usados and _en_ventana(b.fecha, e.fecha)
            and (e.monto > 0) == (b.monto > 0) and abs(_centavos(e.monto)) < abs(cb)
        ]
        candidatos.sort(key=lambda e: (abs((b.fecha - e.fecha).days), str(e.id)))

        # Se buscan TODAS las combinaciones (pares sobre toda la ventana, tríos entre los
        # más cercanos). Solo se cruza si hay exactamente una: si hay varias, es ambiguo.
        soluciones: List[List[MovErp]] = []
        por_valor: Dict[int, List[MovErp]] = defaultdict(list)
        for e in candidatos:
            for p in por_valor.get(cb - _centavos(e.monto), ()):
                soluciones.append([p, e])
            if len(soluciones) > 1:
                break
            por_valor[_centavos(e.monto)].append(e)
        if not soluciones and MAX_TAMANO_COMBINACION >= 3:
            for combo in itertools.combinations(candidatos[:MAX_CANDIDATOS_COMBINATORIA], 3):
                if sum(_centavos(e.monto) for e in combo) == cb:
                    soluciones.append(list(combo))
                    if len(soluciones) > 1:
                        break
        encontrado = soluciones[0] if len(soluciones) == 1 else None
        if encontrado:
            registrar(b, encontrado, REGLA_3, ESTADO_PARCIAL)

    return resultado


def ejecutar_motor(movs_banco: Iterable[MovBanco], movs_erp: Iterable[MovErp],
                   es_banco_guayaquil: bool = False, max_pasadas: int = 5) -> ResultadoMotor:
    """
    Repite el cruce sobre lo que queda pendiente hasta que no haya cruces nuevos:
    un cruce puede eliminar la ambigüedad de otro. Así, volver a ejecutar no encuentra nada más.
    """
    banco, erp = list(movs_banco), list(movs_erp)
    total = ResultadoMotor()
    for _ in range(max_pasadas):
        usados_b, usados_e = total.ids_banco, total.ids_erp
        r = _pasada([b for b in banco if b.id not in usados_b], [e for e in erp if e.id not in usados_e],
                    es_banco_guayaquil)
        if not r.cruces:
            break
        total.cruces.extend(r.cruces)
    return total


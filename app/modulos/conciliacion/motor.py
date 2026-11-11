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
  R5  (al final) Débitos: REF o REF2 de la info adicional del banco = N° de documento
      externo de BC, monto exacto, ventana amplia (±VENTANA_REF_ADICIONAL días)  -> 1 a 1
      (REF3 no se usa: en Banco Guayaquil trae textos con números que no identifican el pago)

Antes del cruce con el banco:
  RV  Reversos en BC: asientos con el mismo N° de documento y la misma fecha cuyos montos
      se anulan (+X y -X). No cruzan con el banco: se marcan conciliados entre sí.
  RVB Reversos en el banco (solo Banco Guayaquil): un débito y un crédito del mismo día, con el
      mismo número en REF/REF2 ("REF: 200017439353" y "REF: Reverso - 200017439353") y monto
      opuesto exacto. Se concilian entre sí, sin BC (p. ej. un pago de servicios reversado con
      su comisión e IVA).
  GB  Gastos bancarios: los débitos del banco clasificados como comisión, tarifa o IVA del
      banco (catálogo de patrones por banco). Primero se intenta R1/R1N (si BC ya registró el
      gasto con su referencia, cruza ahí y no se cuenta dos veces). Lo que queda se agrupa por
      mes y cruza con UN asiento de BC por el total, registrado entre el día
      DIA_INICIO_ASIENTO_GASTOS y el fin de ese mes, o con la factura del banco más hasta
      MAX_AJUSTES_GASTOS notas de crédito/ajustes pequeños en esos días que sumen el total. Nunca pasan por R2, R3 ni R5, que cruzan
      solo por monto.                                                    -> N a 1
"""
from __future__ import annotations

import itertools
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Set, Tuple

VENTANA_DIAS = 7
# Pagos: el banco debita y el asiento en BC puede registrarse varios días después
VENTANA_REF_ADICIONAL = 15
MAX_CANDIDATOS_COMBINATORIA = 25
MAX_TAMANO_COMBINACION = 3

REGLA_1 = "REGLA_1_EXACTA"
REGLA_1_GYE = "REGLA_1_EXACTA_REF2_GYE"
REGLA_1N = "REGLA_2_1_A_N_REF"
REGLA_2 = "REGLA_2_PARCIAL"
REGLA_3 = "REGLA_4_SUBCONJUNTO_COMBINATORIO"
REGLA_REF_ADICIONAL = "REGLA_5_REF_ADICIONAL"
REGLA_REVERSO = "REGLA_REVERSO_BC"
REGLA_REVERSO_BANCO = "REGLA_REVERSO_BANCO"
REGLA_GASTOS_BANCARIOS = "REGLA_GASTOS_BANCARIOS"

# El asiento mensual de gastos bancarios se registra en los últimos días del mes
DIA_INICIO_ASIENTO_GASTOS = 25
# La factura del banco puede venir con notas de crédito o ajustes pequeños (p. ej. comisiones
# que el banco reversó pero facturó): se aceptan hasta 2 ajustes de hasta este % del total.
MAX_AJUSTES_GASTOS = 2
PORCENTAJE_AJUSTE_GASTOS = Decimal("0.05")

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


CAMPOS_REF = ("REF", "REF2", "REF3")
CAMPOS_REF_R5 = ("REF", "REF2")   # R5 y reversos del banco: REF3 no identifica el pago


def _valores_ref_adicional(info_adicional: object, campos: Tuple[str, ...] = CAMPOS_REF) -> List[str]:
    """Valores de los campos REF / REF2 / REF3 que el ETL guarda en info_adicional."""
    valores = []
    for parte in str(info_adicional or "").split(" / "):
        clave, _, valor = parte.partition(":")
        if clave.strip().upper() in campos and valor.strip():
            valores.append(valor.strip())
    return valores


def referencias_banco(numero_referencia: object, info_adicional: object) -> Set[str]:
    """
    Referencias candidatas de un movimiento bancario: el nº de documento y los campos
    REF / REF2 / REF3 que el ETL guarda en info_adicional ("REF2: 123 / OF: MATRIZ").
    """
    return referencias_de_texto(numero_referencia, *_valores_ref_adicional(info_adicional))


def referencias_adicionales(info_adicional: object) -> Set[str]:
    """Referencias adicionales para la R5: REF y REF2, normalizadas y completas (sin REF3)."""
    return {r for r in (normalizar_referencia(v) for v in _valores_ref_adicional(info_adicional, CAMPOS_REF_R5)) if r}


def claves_reverso_banco(info_adicional: object) -> Set[str]:
    """
    Números (5+ dígitos) de REF/REF2 que identifican un pago y su reverso en el banco:
    "REF: 200017439353" y "REF: Reverso -  200017439353" -> {"200017439353"}.
    """
    claves: Set[str] = set()
    for v in _valores_ref_adicional(info_adicional, CAMPOS_REF_R5):
        for num in re.findall(r"\d{5,}", v):
            n = normalizar_referencia(num)
            if n:
                claves.add(n)
    return claves


@dataclass
class MovBanco:
    id: object
    fecha: date
    monto: Decimal
    referencias: Set[str]
    referencia_principal: str = ""
    referencias_adicionales: Set[str] = field(default_factory=set)
    claves_reverso: Set[str] = field(default_factory=set)   # reversos dentro del banco (Guayaquil)
    gasto_bancario: bool = False                             # comisión/tarifa/IVA del banco (regla GB)


@dataclass
class MovErp:
    id: object
    fecha: date
    monto: Decimal
    referencias: Set[str]
    documento: str = ""            # N° de documento de BC (para detectar reversos)
    documento_externo: str = ""    # N° de documento externo normalizado (regla R5)


@dataclass
class Cruce:
    id_banco: object
    ids_erp: List[object]
    regla: str
    estado: str


@dataclass
class GrupoGastos:
    """Gastos bancarios de un mes conciliados contra su asiento mensual (N a 1, o N a factura + ajustes)."""
    mes: str                 # 'AAAA-MM'
    ids_banco: List[object]
    ids_erp: List[object]
    total: Decimal


@dataclass
class ResultadoMotor:
    cruces: List[Cruce] = field(default_factory=list)
    # Pares de asientos de BC que se anulan entre sí (id_cargo, id_abono)
    reversos: List[Tuple[object, object]] = field(default_factory=list)
    # Pares de movimientos del banco que se anulan entre sí (id_debito, id_credito)
    reversos_banco: List[Tuple[object, object]] = field(default_factory=list)
    # Gastos bancarios del mes cruzados con su asiento mensual
    gastos: List[GrupoGastos] = field(default_factory=list)

    @property
    def ids_banco(self) -> Set[object]:
        return ({c.id_banco for c in self.cruces} | {i for par in self.reversos_banco for i in par}
                | {i for g in self.gastos for i in g.ids_banco})

    @property
    def ids_erp(self) -> Set[object]:
        return ({i for c in self.cruces for i in c.ids_erp} | {i for par in self.reversos for i in par}
                | {i for g in self.gastos for i in g.ids_erp})


def _centavos(monto: Decimal) -> int:
    return int((Decimal(monto) * 100).to_integral_value())


def _en_ventana(a: date, b: date) -> bool:
    return abs((a - b).days) <= VENTANA_DIAS


def _pasada(movs_banco: Iterable[MovBanco], movs_erp: Iterable[MovErp], es_banco_guayaquil: bool = False,
            solo_referencia: bool = False) -> ResultadoMotor:
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

        # 1 a N: todos los asientos de una misma referencia suman el monto del banco.
        # Se admiten signos distintos: p. ej. un débito de $17.77 y un ajuste por
        # aproximación de +$0.01 con la misma referencia cuadran un débito de $17.76.
        hecho = False
        for r in sorted(b.referencias):
            grupo = [e for e in candidatos if r in e.referencias]
            if len(grupo) > 1 and sum(_centavos(e.monto) for e in grupo) == cb:
                registrar(b, grupo, REGLA_1N, ESTADO_EXACTO)
                hecho = True
                break
        if not hecho:
            pendientes.append(b)
    if solo_referencia:
        return resultado

    # --- R2: monto exacto + fecha ---------------------------------------------------
    # a) Mismo monto y MISMA fecha en ambos lados: se emparejan en el orden en que
    #    aparecen (p. ej. tres comisiones de $0.48 del mismo día contra tres asientos
    #    de $0.48 de ese día). Son movimientos indistinguibles; cualquier par es válido.
    # b) Con fechas distintas dentro de la ventana, solo se cruza si hay un único
    #    candidato; si hay varios, queda pendiente (preferible a un cruce equivocado).
    banco_mismo_dia: Dict[Tuple[int, date], List[MovBanco]] = defaultdict(list)
    for b in pendientes:
        banco_mismo_dia[(_centavos(b.monto), b.fecha)].append(b)
    for (cb, fecha), grupo_banco in banco_mismo_dia.items():
        grupo_erp = [e for e in libres(por_monto.get(cb, ())) if e.fecha == fecha]
        for b, e in zip(grupo_banco, grupo_erp):
            registrar(b, [e], REGLA_2, ESTADO_PARCIAL)
    pendientes = [b for b in pendientes if b.id not in resultado.ids_banco]

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


def detectar_reversos(movs_erp: Iterable[MovErp]) -> List[Tuple[object, object]]:
    """
    Asientos de BC con el mismo N° de documento y la misma fecha que se anulan entre sí
    (+X y -X). Si hay varios del mismo monto se emparejan uno a uno; los que sobran siguen
    pendientes (p. ej. un pago registrado dos veces y reversado una).
    """
    grupos: Dict[tuple, List[MovErp]] = defaultdict(list)
    for e in movs_erp:
        doc = normalizar_referencia(e.documento)
        if doc and e.monto:
            grupos[(doc, e.fecha, abs(_centavos(e.monto)))].append(e)
    pares: List[Tuple[object, object]] = []
    for grupo in grupos.values():
        cargos = sorted((e for e in grupo if e.monto < 0), key=lambda e: str(e.id))
        abonos = sorted((e for e in grupo if e.monto > 0), key=lambda e: str(e.id))
        pares.extend((c.id, a.id) for c, a in zip(cargos, abonos))
    return pares


def detectar_reversos_banco(movs_banco: Iterable[MovBanco]) -> List[Tuple[object, object]]:
    """
    Pagos reversados por el banco: un crédito que anula a un débito del mismo día, con el
    mismo número en REF/REF2 y el monto exacto con signo contrario. Cada movimiento se usa
    una sola vez; si hay dos débitos iguales (pago repetido) se empareja el primero y el
    otro sigue pendiente para cruzar con BC.
    """
    debitos: Dict[tuple, List[MovBanco]] = defaultdict(list)
    for b in movs_banco:
        if b.monto < 0:
            for k in b.claves_reverso:
                debitos[(b.fecha, k, -_centavos(b.monto))].append(b)
    usados: Set[object] = set()
    pares: List[Tuple[object, object]] = []
    creditos = sorted((b for b in movs_banco if b.monto > 0 and b.claves_reverso), key=lambda b: str(b.id))
    for c in creditos:
        candidatos = {d.id: d for k in sorted(c.claves_reverso)
                      for d in debitos.get((c.fecha, k, _centavos(c.monto)), ()) if d.id not in usados}
        if candidatos:
            d = candidatos[min(candidatos, key=str)]
            usados.update((d.id, c.id))
            pares.append((d.id, c.id))
    return pares


def _pasada_ref_adicional(movs_banco: Iterable[MovBanco], movs_erp: Iterable[MovErp]) -> List[Cruce]:
    """
    R5: débitos del banco cuya REF o REF2 (info adicional) es el N° de documento externo
    de un asiento de BC con el mismo monto, dentro de ±VENTANA_REF_ADICIONAL días.
    Solo cruza si hay un único candidato (y ese candidato no lo reclama otro débito).
    """
    por_ext: Dict[str, List[MovErp]] = defaultdict(list)
    for e in movs_erp:
        if e.documento_externo and e.monto < 0:
            por_ext[e.documento_externo].append(e)
    propuestas: Dict[object, List[MovBanco]] = defaultdict(list)
    elegido: Dict[object, MovErp] = {}
    for b in movs_banco:
        if b.monto >= 0 or not b.referencias_adicionales:
            continue
        cb = _centavos(b.monto)
        candidatos = {e.id: e for r in b.referencias_adicionales for e in por_ext.get(r, ())
                      if _centavos(e.monto) == cb and abs((b.fecha - e.fecha).days) <= VENTANA_REF_ADICIONAL}
        if len(candidatos) == 1:
            e = next(iter(candidatos.values()))
            elegido[b.id] = e
            propuestas[e.id].append(b)
    return [Cruce(bs[0].id, [eid], REGLA_REF_ADICIONAL, ESTADO_EXACTO)
            for eid, bs in propuestas.items() if len(bs) == 1]


def _fin_de_mes(fecha: date) -> date:
    siguiente = date(fecha.year + (fecha.month == 12), fecha.month % 12 + 1, 1)
    return date.fromordinal(siguiente.toordinal() - 1)


def es_gasto_bancario(b: MovBanco) -> bool:
    """Solo los débitos clasificados como gasto bancario entran a la regla GB."""
    return b.gasto_bancario and b.monto < 0


def totales_gastos_por_mes(movs_banco: Iterable[MovBanco]) -> Dict[str, Tuple[Decimal, List[MovBanco]]]:
    """{'AAAA-MM': (total, movimientos)} de los gastos bancarios recibidos."""
    meses: Dict[str, List[MovBanco]] = defaultdict(list)
    for b in movs_banco:
        if es_gasto_bancario(b):
            meses[f"{b.fecha:%Y-%m}"].append(b)
    return {m: (sum((b.monto for b in bs), Decimal("0")), bs) for m, bs in meses.items()}


def _asientos_de_gastos(total: Decimal, candidatos: List[MovErp]) -> Optional[List[MovErp]]:
    """
    Asientos de BC que explican el total de gastos del mes:
      1. un asiento por el total exacto, o
      2. la factura del banco (mismo signo que el total) más 1 o 2 ajustes pequeños de cualquier
         signo (|ajuste| <= PORCENTAJE_AJUSTE_GASTOS del total, mínimo $1) que suman el total.
    Solo devuelve una solución si es única; si hay varias, el grupo queda pendiente.
    """
    ct = _centavos(total)
    exactos = [e for e in candidatos if _centavos(e.monto) == ct]
    if exactos:
        return exactos[:1] if len(exactos) == 1 else None
    tope = max(abs(ct) * PORCENTAJE_AJUSTE_GASTOS, Decimal(100))
    ajustes = [e for e in candidatos if 0 < abs(_centavos(e.monto)) <= tope]
    facturas = [e for e in candidatos if (e.monto < 0) == (total < 0) and abs(_centavos(e.monto)) > tope]
    soluciones: List[List[MovErp]] = []
    for n in range(1, MAX_AJUSTES_GASTOS + 1):
        for f in facturas:
            for combo in itertools.combinations([a for a in ajustes if a.id != f.id], n):
                if _centavos(f.monto) + sum(_centavos(a.monto) for a in combo) == ct:
                    soluciones.append([f, *combo])
                    if len(soluciones) > 1:
                        return None
        if soluciones:
            return soluciones[0]
    return None


def detectar_gastos_bancarios(movs_banco: Iterable[MovBanco], movs_erp: Iterable[MovErp]) -> List[GrupoGastos]:
    """
    GB: los gastos bancarios de cada mes cruzan con su asiento mensual (ver _asientos_de_gastos),
    registrado entre el día DIA_INICIO_ASIENTO_GASTOS y el fin de ese mes. Si hay más de una
    combinación posible, el grupo queda pendiente (ambiguo).
    """
    erp = list(movs_erp)
    usados: Set[object] = set()
    grupos: List[GrupoGastos] = []
    for mes, (total, bs) in sorted(totales_gastos_por_mes(movs_banco).items()):
        fin = _fin_de_mes(bs[0].fecha)
        desde = date(fin.year, fin.month, DIA_INICIO_ASIENTO_GASTOS)
        candidatos = sorted((e for e in erp if e.id not in usados and desde <= e.fecha <= fin and e.monto),
                            key=lambda e: (e.fecha, str(e.id)))
        asientos = _asientos_de_gastos(total, candidatos)
        if asientos:
            usados.update(e.id for e in asientos)
            grupos.append(GrupoGastos(mes, [b.id for b in sorted(bs, key=lambda b: (b.fecha, str(b.id)))],
                                      [e.id for e in asientos], total))
    return grupos


def ejecutar_motor(movs_banco: Iterable[MovBanco], movs_erp: Iterable[MovErp],
                   es_banco_guayaquil: bool = False, max_pasadas: int = 5) -> ResultadoMotor:
    """
    1. Reversos dentro de BC y, en Banco Guayaquil, dentro del banco (se anulan entre sí).
    2. Gastos bancarios: R1/R1N por referencia y luego el total del mes contra su asiento
       mensual (GB). Los que aún no tienen asiento quedan pendientes y no pasan por R2-R5.
    3. Repite el cruce R1-R3 sobre lo pendiente hasta que no haya cruces nuevos:
       un cruce puede eliminar la ambigüedad de otro. Así, volver a ejecutar no encuentra nada más.
    4. R5 (referencia adicional, ventana amplia) sobre lo que aún queda.
    """
    banco, erp = list(movs_banco), list(movs_erp)
    total = ResultadoMotor(reversos=detectar_reversos(erp),
                           reversos_banco=detectar_reversos_banco(banco) if es_banco_guayaquil else [])
    usados_b, usados_e = total.ids_banco, total.ids_erp
    gastos = [b for b in banco if es_gasto_bancario(b) and b.id not in usados_b]
    if gastos:
        por_ref = _pasada(gastos, [e for e in erp if e.id not in usados_e], es_banco_guayaquil, solo_referencia=True)
        total.cruces.extend(por_ref.cruces)
        usados_b, usados_e = total.ids_banco, total.ids_erp
        total.gastos = detectar_gastos_bancarios([b for b in gastos if b.id not in usados_b],
                                                 [e for e in erp if e.id not in usados_e])
    banco = [b for b in banco if not es_gasto_bancario(b)]
    for _ in range(max_pasadas):
        usados_b, usados_e = total.ids_banco, total.ids_erp
        r = _pasada([b for b in banco if b.id not in usados_b], [e for e in erp if e.id not in usados_e],
                    es_banco_guayaquil)
        if not r.cruces:
            break
        total.cruces.extend(r.cruces)
    usados_b, usados_e = total.ids_banco, total.ids_erp
    total.cruces.extend(_pasada_ref_adicional([b for b in banco if b.id not in usados_b],
                                              [e for e in erp if e.id not in usados_e]))
    return total

# app/modulos/bancos/etl.py
"""
Motor ETL de extractos bancarios.

Funciones puras (sin BD ni red) para leer el archivo de cada banco y
normalizarlo a una lista de movimientos. Así se pueden probar con los
archivos reales sin levantar la aplicación.

Convención de signo (igual que Business Central):
    CREDITO  -> monto positivo  (entra dinero)
    DEBITO   -> monto negativo  (sale dinero)
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

CENTAVO = Decimal("0.01")
FILAS_BUSQUEDA_CABECERA = 60
EXTENSIONES_PERMITIDAS = (".xlsx", ".xls", ".csv")


class EtlError(Exception):
    """Error de validación del archivo que se puede mostrar al usuario."""


@dataclass
class MovimientoNormalizado:
    fecha: date
    referencia: str
    concepto: str
    monto: Decimal              # con signo
    info_adicional: str
    saldo: Optional[Decimal] = None   # saldo informado por el banco tras el movimiento

    @property
    def tipo(self) -> str:
        return "CREDITO" if self.monto >= 0 else "DEBITO"


@dataclass
class ResultadoEtl:
    banco: str
    movimientos: List[MovimientoNormalizado]
    filas_leidas: int
    filas_omitidas: int = 0
    saldo_inicial: Optional[Decimal] = None
    saldo_final: Optional[Decimal] = None
    advertencias: List[str] = field(default_factory=list)

    @property
    def fecha_desde(self) -> Optional[date]:
        return min((m.fecha for m in self.movimientos), default=None)

    @property
    def fecha_hasta(self) -> Optional[date]:
        return max((m.fecha for m in self.movimientos), default=None)

    @property
    def total_creditos(self) -> Decimal:
        return sum((m.monto for m in self.movimientos if m.monto > 0), Decimal("0"))

    @property
    def total_debitos(self) -> Decimal:
        return sum((m.monto for m in self.movimientos if m.monto < 0), Decimal("0"))


# ---------------------------------------------------------------------------
# Limpieza de valores
# ---------------------------------------------------------------------------

def normalizar_texto(valor: Any) -> str:
    """Mayúsculas, sin tildes, '_' -> espacio y espacios colapsados."""
    s = "".join(
        c for c in unicodedata.normalize("NFD", str(valor))
        if unicodedata.category(c) != "Mn"
    )
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip().upper()


def es_vacio(valor: Any) -> bool:
    if valor is None:
        return True
    if isinstance(valor, float) and pd.isna(valor):
        return True
    if valor is pd.NaT:
        return True
    return isinstance(valor, str) and valor.strip() in ("", "nan", "NaN", "None")


def texto(valor: Any) -> str:
    """Convierte a texto limpio; '' para vacíos (nunca 'nan'). 123.0 -> '123'."""
    if es_vacio(valor):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    s = str(valor).strip()
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s


def parse_monto(valor: Any) -> Optional[Decimal]:
    """
    Convierte un monto a Decimal (2 decimales).
    Soporta: 1234.5 | '622.64' | '$1,211.44' | '$ 713.00' | '1.234,56' | '(100.00)' | '-5'.
    Devuelve None si está vacío; lanza ValueError si no se puede interpretar.
    """
    if es_vacio(valor):
        return None
    if isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool):
        return Decimal(str(valor)).quantize(CENTAVO)

    s = str(valor).strip()
    negativo = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.endswith("-")
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        raise ValueError(f"Monto inválido: {valor!r}")

    ult_coma, ult_punto = s.rfind(","), s.rfind(".")
    if ult_coma >= 0 and ult_punto >= 0:
        dec = "," if ult_coma > ult_punto else "."
    else:
        sep = "," if ult_coma >= 0 else ("." if ult_punto >= 0 else None)
        dec = sep
        # Un único tipo de separador seguido de exactamente 3 dígitos = miles (1,234 / 1.234.567)
        if sep and (s.count(sep) > 1 or re.fullmatch(rf"\d{{1,3}}\{sep}\d{{3}}", s)):
            dec = None

    miles = {",": ".", ".": ",", None: ",."}[dec]
    for ch in miles:
        s = s.replace(ch, "")
    if dec == ",":
        s = s.replace(",", ".")
    try:
        monto = Decimal(s).quantize(CENTAVO)
    except InvalidOperation as e:
        raise ValueError(f"Monto inválido: {valor!r}") from e
    return -monto if negativo else monto


_FORMATOS_FECHA = (
    "%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y", "%Y/%m/%d",
)


def parse_fecha(valor: Any) -> Optional[date]:
    """Fecha sin ambigüedad: dd/mm/yyyy o ISO. Devuelve None si está vacía."""
    if es_vacio(valor):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    s = str(valor).strip()
    for fmt in _FORMATOS_FECHA:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Fecha inválida: {valor!r}")


# ---------------------------------------------------------------------------
# Lectura del archivo y detección de cabecera
# ---------------------------------------------------------------------------

def _leer_tabla_cruda(file_bytes: bytes, nombre_archivo: str) -> pd.DataFrame:
    """Lee el archivo completo SIN cabecera (todo como objeto), una sola vez."""
    nombre = (nombre_archivo or "").lower()
    if not nombre.endswith(EXTENSIONES_PERMITIDAS):
        raise EtlError("Formato no soportado. Sube un archivo .xlsx, .xls o .csv.")

    if nombre.endswith((".xlsx", ".xls")):
        engine = "openpyxl" if nombre.endswith(".xlsx") else "xlrd"
        try:
            return pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=object, engine=engine)
        except ImportError as e:
            raise EtlError("El servidor no tiene soporte para archivos .xls; guárdalo como .xlsx.") from e
        except Exception as e:
            raise EtlError("No se pudo abrir el archivo Excel. Verifica que no esté dañado.") from e

    # CSV: decodificar el archivo completo con el primer encoding válido
    for enc in ("utf-8-sig", "windows-1252", "latin1"):
        try:
            contenido = file_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise EtlError("No se pudo decodificar el archivo CSV.")

    # Algunos bancos exportan cada línea completa entre comillas: "a,b,c"
    lineas = contenido.splitlines()
    if lineas and all(l.startswith('"') and l.endswith('"') for l in lineas if l.strip()):
        lineas = [l[1:-1].replace('""', '"') if l.strip() else l for l in lineas]
    try:
        return pd.read_csv(
            io.StringIO("\n".join(lineas)), header=None, dtype=str, sep=None,
            engine="python", skip_blank_lines=False, on_bad_lines="skip",
        )
    except Exception as e:
        raise EtlError("No se pudo leer el archivo CSV.") from e


def _firma_guayaquil(c: set) -> bool:
    return {"FECHA DE TRANSACCION", "TIPO DE MOVIMIENTO", "MONTO", "SIGNO"} <= c


def _firma_solidario(c: set) -> bool:
    return {"FECHA MOVIMIENTO", "NO DOCUMENTO", "SIGNO", "MONTO"} <= c


def _firma_produbanco(c: set) -> bool:
    return ({"FECHA", "VALOR"} <= c and bool({"SIGNO", "+/-"} & c)
            and bool({"TRANSACCION", "DESCRIPCION"} & c))


def _firma_pichincha(c: set) -> bool:
    return {"FECHA", "DOCUMENTO", "TIPO", "OFICINA"} <= c and bool({"MONTO", "VALOR"} & c)


# El orden importa: de la firma más específica a la más genérica
FIRMAS: List[Tuple[str, Callable[[set], bool]]] = [
    ("GUAYAQUIL", _firma_guayaquil),
    ("SOLIDARIO", _firma_solidario),
    ("PRODUBANCO", _firma_produbanco),
    ("PICHINCHA", _firma_pichincha),
]


def detectar_banco(crudo: pd.DataFrame) -> Tuple[str, int]:
    """Devuelve (banco, índice de la fila de cabecera)."""
    for i in range(min(FILAS_BUSQUEDA_CABECERA, len(crudo))):
        celdas = {normalizar_texto(v) for v in crudo.iloc[i].tolist() if not es_vacio(v)}
        for banco, firma in FIRMAS:
            if firma(celdas):
                return banco, i
    raise EtlError("Archivo inválido: no se reconoció la estructura de columnas de ningún banco "
                   "(Guayaquil, Pichincha, Produbanco, Solidario).")


def _tabla_con_cabecera(crudo: pd.DataFrame, fila_cabecera: int) -> List[Dict[str, Any]]:
    """Filas debajo de la cabecera como dicts {COLUMNA_NORMALIZADA: valor}."""
    cabecera = [normalizar_texto(v) if not es_vacio(v) else None for v in crudo.iloc[fila_cabecera]]
    filas = []
    for valores in crudo.iloc[fila_cabecera + 1:].itertuples(index=False, name=None):
        fila = {col: val for col, val in zip(cabecera, valores) if col}
        if any(not es_vacio(v) for v in fila.values()):
            filas.append(fila)
    return filas


# ---------------------------------------------------------------------------
# Transformación por banco: cada función devuelve un movimiento o None (omitir)
# ---------------------------------------------------------------------------

def _signo(valor: Any) -> int:
    s = texto(valor)
    if "+" in s:
        return 1
    if "-" in s:
        return -1
    raise ValueError(f"Signo inválido: {valor!r}")


def _info(**partes: Any) -> str:
    return " / ".join(f"{k}: {v}" for k, v in partes.items() if texto(v) not in ("", "-"))[:512]


def _fila_pichincha(r: Dict[str, Any]) -> Optional[MovimientoNormalizado]:
    fecha = parse_fecha(r.get("FECHA"))
    if not fecha:
        return None
    tipo = texto(r.get("TIPO")).upper()
    if tipo not in ("C", "D"):
        raise ValueError(f"Tipo inválido: {tipo!r} (se esperaba C o D)")
    monto = parse_monto(r.get("MONTO", r.get("VALOR")))
    return MovimientoNormalizado(
        fecha=fecha,
        referencia=texto(r.get("DOCUMENTO")),
        concepto=texto(r.get("CONCEPTO")),
        monto=abs(monto) if tipo == "C" else -abs(monto),
        info_adicional=_info(COD=texto(r.get("CODIGO")), OF=texto(r.get("OFICINA"))),
        saldo=parse_monto(r.get("SALDO")),
    )


def _fila_solidario(r: Dict[str, Any]) -> Optional[MovimientoNormalizado]:
    fecha = parse_fecha(r.get("FECHA MOVIMIENTO"))
    if not fecha:
        return None
    monto = abs(parse_monto(r.get("MONTO", r.get("VALOR"))))
    cheque = texto(r.get("NO CHEQUE"))
    return MovimientoNormalizado(
        fecha=fecha,
        referencia=re.sub(r"^NO\.?\s*", "", texto(r.get("NO DOCUMENTO")), flags=re.I),
        concepto=texto(r.get("DESCRIPCION")),
        monto=monto * _signo(r.get("SIGNO")),
        info_adicional=_info(OF=texto(r.get("OFICINA")), PAP=texto(r.get("PAPELETA")),
                             CHEQUE="" if cheque == "0" else cheque),
        saldo=parse_monto(r.get("SALDO CONTABLE")),
    )


def _fila_produbanco(r: Dict[str, Any]) -> Optional[MovimientoNormalizado]:
    fecha = parse_fecha(r.get("FECHA"))
    if not fecha:
        return None
    # Formato nuevo: REFERENCIA = tipo ("NOTA DE DEBITO") y REFERENCIA 1 = nº de documento
    ref_raw = texto(r.get("REFERENCIA 1")) or texto(r.get("REFERENCIA"))
    detalle = ""
    if "|#|" in ref_raw:  # formato largo legacy
        partes = [p.strip() for p in ref_raw.split("|#|") if p.strip()]
        ref_raw, detalle = (partes[0], " / ".join(partes[1:])) if partes else (ref_raw, "")
    monto = abs(parse_monto(r.get("VALOR")))
    return MovimientoNormalizado(
        fecha=fecha,
        referencia=ref_raw,
        concepto=texto(r.get("TRANSACCION")) or texto(r.get("DESCRIPCION")),
        monto=monto * _signo(r.get("SIGNO", r.get("+/-"))),
        info_adicional=_info(
            REF2=texto(r.get("REFERENCIA 2")) or texto(r.get("REFERENCIA2")),
            OF=texto(r.get("OFICINA")),
            TIPO=texto(r.get("COD TRANSACCION")) or (texto(r.get("REFERENCIA")) if "REFERENCIA 1" in r else ""),
            DETALLE=detalle,
        ),
        saldo=parse_monto(r.get("SALDO CONTABLE")),
    )


# Movimientos internos del Banco Guayaquil que no alteran el saldo contable
# (pasan dinero de "saldo por confirmar" a "saldo efectivo"): duplicarían el depósito.
_CONCEPTOS_INTERNOS_GUAYAQUIL = ("EFECTIVIZACION DE CHEQUES",)


def _fila_guayaquil(r: Dict[str, Any]) -> Optional[MovimientoNormalizado]:
    fecha = parse_fecha(r.get("FECHA DE TRANSACCION"))
    if not fecha:
        return None
    concepto = texto(r.get("CONCEPTO"))
    if normalizar_texto(concepto).startswith(_CONCEPTOS_INTERNOS_GUAYAQUIL):
        return None
    fecha_contable = parse_fecha(r.get("FECHA CONTABLE"))
    monto = abs(parse_monto(r.get("MONTO", r.get("VALOR"))))
    ref1 = texto(r.get("REFERENCIA"))
    return MovimientoNormalizado(
        fecha=fecha,
        referencia=texto(r.get("DOCUMENTO")),
        concepto=concepto,
        monto=monto * _signo(r.get("SIGNO")),
        info_adicional=_info(
            TIPO=texto(r.get("TIPO DE MOVIMIENTO")),
            F_CONTABLE=fecha_contable.isoformat() if fecha_contable and fecha_contable != fecha else "",
            AG=texto(r.get("AGENCIA")),
            REF="" if ref1 == "0" else ref1,
            REF2=texto(r.get("REFERENCIA 2")),
            REF3=texto(r.get("REFERENCIA 3")),
        ),
        saldo=parse_monto(r.get("SALDO TOTAL")),
    )


TRANSFORMADORES: Dict[str, Callable[[Dict[str, Any]], Optional[MovimientoNormalizado]]] = {
    "PICHINCHA": _fila_pichincha,
    "SOLIDARIO": _fila_solidario,
    "PRODUBANCO": _fila_produbanco,
    "GUAYAQUIL": _fila_guayaquil,
}


# ---------------------------------------------------------------------------
# Validación del saldo corrido
# ---------------------------------------------------------------------------

def _validar_saldos(movs: List[MovimientoNormalizado]) -> Tuple[Optional[Decimal], Optional[Decimal], List[str]]:
    """
    Comprueba que saldo_anterior + monto = saldo en cada fila (el archivo puede venir
    en orden ascendente o descendente). Devuelve (saldo_inicial, saldo_final, advertencias).
    """
    if len(movs) < 2 or any(m.saldo is None for m in movs):
        return None, None, []

    def quiebres(seq: List[MovimientoNormalizado]) -> int:
        return sum(1 for a, b in zip(seq, seq[1:]) if a.saldo + b.monto != b.saldo)

    asc = quiebres(movs)
    desc = quiebres(movs[::-1])
    orden = movs if asc <= desc else movs[::-1]
    n = min(asc, desc)

    saldo_inicial = orden[0].saldo - orden[0].monto
    saldo_final = orden[-1].saldo
    advertencias = []
    if n:
        advertencias.append(f"El saldo corrido del banco no cuadra en {n} fila(s); revisa el archivo.")
    neto = sum((m.monto for m in movs), Decimal("0"))
    if saldo_inicial + neto != saldo_final:
        advertencias.append(
            f"Saldo inicial {saldo_inicial} + movimientos {neto} ≠ saldo final {saldo_final}."
        )
    return saldo_inicial, saldo_final, advertencias


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def procesar_extracto(file_bytes: bytes, nombre_archivo: str, banco_esperado: Optional[str] = None) -> ResultadoEtl:
    """
    Lee y normaliza un extracto.
    banco_esperado: texto de la cuenta seleccionada (nombre, grupo BC, institución);
    si se indica, el banco detectado debe aparecer en él.
    """
    crudo = _leer_tabla_cruda(file_bytes, nombre_archivo)
    banco, fila_cab = detectar_banco(crudo)

    if banco_esperado and banco not in normalizar_texto(banco_esperado):
        raise EtlError(f"Inconsistencia: el archivo es de {banco} y no corresponde "
                       f"al banco de la cuenta seleccionada.")

    filas = _tabla_con_cabecera(crudo, fila_cab)
    transformar = TRANSFORMADORES[banco]
    movimientos: List[MovimientoNormalizado] = []
    errores: List[str] = []
    omitidas = 0

    for n, fila in enumerate(filas, start=fila_cab + 2):  # nº de fila como en Excel
        try:
            mov = transformar(fila)
        except (ValueError, TypeError) as e:
            errores.append(f"fila {n}: {e}")
            continue
        if mov is None:
            omitidas += 1
            continue
        if not mov.referencia:
            mov.referencia = "S/N"
        movimientos.append(mov)

    if errores:
        detalle = "; ".join(errores[:5]) + (f" (y {len(errores) - 5} más)" if len(errores) > 5 else "")
        raise EtlError(f"El archivo tiene {len(errores)} fila(s) con datos inválidos: {detalle}")
    if not movimientos:
        raise EtlError("El archivo no contiene movimientos.")

    saldo_ini, saldo_fin, advertencias = _validar_saldos(movimientos)

    claves = [(m.fecha, m.referencia, m.monto) for m in movimientos]
    repetidas = len(claves) - len(set(claves))
    if repetidas:
        advertencias.append(
            f"{repetidas} movimiento(s) repiten fecha, referencia y monto dentro del archivo; "
            "solo se registrará uno de cada grupo."
        )

    return ResultadoEtl(
        banco=banco,
        movimientos=movimientos,
        filas_leidas=len(filas),
        filas_omitidas=omitidas,
        saldo_inicial=saldo_ini,
        saldo_final=saldo_fin,
        advertencias=advertencias,
    )

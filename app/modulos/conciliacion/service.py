# app/modulos/conciliacion/service.py
import io
import re
import json
import uuid
import itertools
import pandas as pd
from datetime import datetime, date
from typing import List, Dict, Any, Tuple, Optional, Union, Set
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
import time

from app.modulos.conciliacion.bc_service import BusinessCentralReaderService
from app.modulos.conciliacion.repository import ConciliacionRepository
from app.modulos.bancos.repository import BancosRepository
from app.modulos.conciliacion.services.reporte_excel import generar_excel_informe_conciliacion
from app.modulos.conciliacion.models import Acta
from app.modulos.bancos.models import CuentaBancaria

def normalizar_referencia(ref: Optional[Any]) -> str:
    """Elimina espacios, convierte a mayúsculas y remueve ceros a la izquierda."""
    if ref is None:
        return ""
    s = str(ref).strip().upper()
    cleaned = s.lstrip("0")
    return cleaned if cleaned else ("0" if s else "")

def _val(obj: Any, key: str, default: Any = None) -> Any:
    """Helper para leer atributos ya sea que el registro venga como dict o modelo SQLAlchemy."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def extraer_referencias_candidatas(mov_banco: Any) -> Set[str]:
    """
    Extrae un conjunto único de referencias válidas desde:
    1. numero_referencia / referencia principal
    2. info_adicional (JSON/Dict: referencia_2, ref2, Referencia2)
    3. Patrones por Regex en texto (ej. 'REF: 680063559')
    """
    candidatas: Set[str] = set()

    # 1. Referencia principal en tabla (soporta 'numero_referencia' y 'referencia')
    ref_raw = _val(mov_banco, "numero_referencia") or _val(mov_banco, "referencia")
    ref_principal = normalizar_referencia(ref_raw)
    if ref_principal and ref_principal not in {"0", "00", "000", "0000", "NULL", "NONE"}:
        candidatas.add(ref_principal)

    # 2. Claves de info_adicional (JSON o Dict)
    info_raw = _val(mov_banco, "info_adicional", {}) or {}
    info_dict = {}
    texto_acumulado = ""

    if isinstance(info_raw, str):
        texto_acumulado = info_raw
        try:
            info_dict = json.loads(info_raw)
        except (json.JSONDecodeError, TypeError):
            info_dict = {}
    elif isinstance(info_raw, dict):
        info_dict = info_raw
        texto_acumulado = json.dumps(info_raw)

    for key in ["referencia_2", "ref2", "Referencia2", "referencia2", "ref_2"]:
        val = info_dict.get(key)
        if val:
            n_val = normalizar_referencia(val)
            if n_val and n_val not in {"0", "00", "000", "0000"}:
                candidatas.add(n_val)

    # 3. Extracción por Regex en texto embebido
    concepto = _val(mov_banco, "concepto", "") or ""
    descripcion = _val(mov_banco, "descripcion", "") or ""
    concepto_desc = f"{texto_acumulado} {concepto} {descripcion}"
    
    matches = re.findall(r'REF[:\.\s]*([A-Z0-9]+)', concepto_desc, re.IGNORECASE)
    for m in matches:
        n_m = normalizar_referencia(m)
        if n_m and len(n_m) >= 4 and n_m not in {"0", "00", "000", "0000"}:
            candidatas.add(n_m)

    return candidatas

def es_diferencia_valida(d1: date, d2: date, max_dias: int = 3) -> bool:
    """Comparación directa de fechas en memoria sin parsear strings"""
    return abs((d1 - d2).days) <= max_dias


def buscar_combinacion_subconjunto(candidatos_bc: List[Dict[str, Any]], monto_banco: float, max_combo: int = 3) -> Optional[Tuple]:
    """
    Algoritmo de Cruce por Subconjuntos optimizado.
    Busca si una combinación de 2 o 3 registros de BC suma exactamente el monto del banco.
    """
    monto_target = round(abs(monto_banco), 2)
    n = len(candidatos_bc)
    
    if n > 15:
        candidatos_bc = candidatos_bc[:15]
        n = 15

    for k in range(2, min(n + 1, max_combo + 1)):
        for combo in itertools.combinations(candidatos_bc, k):
            suma_combo = round(sum(abs(float(c['monto'])) for c in combo), 2)
            if abs(suma_combo - monto_target) < 0.001:
                return combo
    return None


class ConciliacionService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = ConciliacionRepository(db)
        self.bancos_repo = BancosRepository(db)

    def obtener_y_preparar_mayor(self, fecha_inicio: str, fecha_fin: str, cuenta_contable: str):
        t0 = time.time()
        grupo_bc, codigo_bc = BusinessCentralReaderService.resolver_grupo_banco_por_cuenta(self.db, cuenta_contable)
        target_filtro = grupo_bc or codigo_bc or cuenta_contable

        raw_entries, error = BusinessCentralReaderService.consultar_mayor_bancos(
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            cuenta_contable=cuenta_contable,
            grupo_registro_bc=target_filtro,
            db=self.db
        )

        novedades = {"nuevos": 0, "modificados": 0, "requiere_rematch": False}
        if raw_entries:
            novedades = self.repo.upsert_movimientos_erp_espejo(raw_entries, target_filtro)

        f_ini = datetime.strptime(str(fecha_inicio).split("T")[0], "%Y-%m-%d").date()
        f_fin = datetime.strptime(str(fecha_fin).split("T")[0], "%Y-%m-%d").date()
        movs_pg = self.repo.obtener_movimientos_erp_pendientes(target_filtro, f_ini, f_fin)

        df = pd.DataFrame(movs_pg) if movs_pg else pd.DataFrame()
        return df, novedades, None

    def ejecutar_conciliacion_periodo(
        self, 
        id_cuenta: Union[uuid.UUID, str], 
        periodo: str, 
        fecha_inicio: Union[date, str], 
        fecha_fin: Union[date, str],
        creado_por: str,
        cuenta_contable: Optional[str] = None
    ) -> Dict[str, Any]:
        
        # 1. Obtener objeto de cuenta bancaria
        cuenta_obj = self.repo.obtener_cuenta_bancaria(id_cuenta)
        if not cuenta_obj:
            raise HTTPException(status_code=404, detail="Cuenta bancaria no encontrada.")

        # 2. Parsear fechas de forma segura a objetos 'date'
        f_ini_dt = (
            datetime.strptime(str(fecha_inicio).split("T")[0], "%Y-%m-%d").date()
            if isinstance(fecha_inicio, str)
            else fecha_inicio
        )
        f_fin_dt = (
            datetime.strptime(str(fecha_fin).split("T")[0], "%Y-%m-%d").date()
            if isinstance(fecha_fin, str)
            else fecha_fin
        )

        f_ini_str = f_ini_dt.strftime("%Y-%m-%d")
        f_fin_str = f_fin_dt.strftime("%Y-%m-%d")

        # 3. Resolver cuenta contable y filtros para Business Central
        cuenta_gl = cuenta_contable or cuenta_obj.cuenta_contable_bc
        grupo_bc, codigo_bc = BusinessCentralReaderService.resolver_grupo_banco_por_cuenta(self.db, cuenta_gl)
        target_filtro = grupo_bc or codigo_bc or cuenta_obj.grupo_registro_bc or cuenta_gl

        # 4. Sincronizar espejo desde BC
        raw_bc, err_bc = BusinessCentralReaderService.consultar_mayor_bancos(
            fecha_inicio=f_ini_str,
            fecha_fin=f_fin_str,
            cuenta_contable=cuenta_gl,
            grupo_registro_bc=target_filtro,
            db=self.db
        )
        if err_bc:
            raise HTTPException(status_code=502, detail=err_bc)

        if raw_bc:
            self.repo.upsert_movimientos_erp_espejo(raw_bc, target_filtro)

        # 5. Cargar pendientes desde PostgreSQL
        movs_banco = self.repo.obtener_movimientos_banco_pendientes(cuenta_obj.id_cuenta, f_ini_dt, f_fin_dt) or []
        movs_erp = self.repo.obtener_movimientos_erp_pendientes(target_filtro, f_ini_dt, f_fin_dt) or []

        # 6. Parsear fechas a objetos date una sola vez para optimización de CPU
        for b in movs_banco:
            ft = b.get("fecha_transaccion") or b.get("fecha_movimiento")
            if isinstance(ft, date):
                b["fecha_dt"] = ft
            else:
                b["fecha_dt"] = datetime.strptime(str(ft).split("T")[0], "%Y-%m-%d").date()

        for e in movs_erp:
            pd_val = e.get("posting_date") or e.get("fecha")
            if isinstance(pd_val, date):
                e["fecha_dt"] = pd_val
            else:
                e["fecha_dt"] = datetime.strptime(str(pd_val).split("T")[0], "%Y-%m-%d").date()

        id_acta = uuid.uuid4()
        total_conciliado = 0.0

        banco_updates = []
        erp_updates = []

        movs_banco_procesados = set()
        movs_erp_procesados = set()

        banco_nombre = getattr(cuenta_obj, "banco_nombre", "") or ""

        # =========================================================================
        # REGLA 1: COINCIDENCIA POR REFERENCIAS UNIFICADAS (1:1 y 1:N / Ref2 GYE)
        # =========================================================================
        for b in movs_banco:
            if b['id_movimiento'] in movs_banco_procesados:
                continue

            # Filtrar candidatos ERP que aún no hayan sido cruzados
            movs_erp_disponibles = [
                e for e in movs_erp if e['id_movimiento_erp'] not in movs_erp_procesados
            ]

            match = self.evaluar_coincidencia_referencia_unificada(
                mov_banco=b,
                movs_erp_pendientes=movs_erp_disponibles,
                banco_nombre=banco_nombre
            )

            if match:
                # 1. Marcar banco como procesado
                movs_banco_procesados.add(b['id_movimiento'])
                banco_updates.append({
                    "id_movimiento": b['id_movimiento'],
                    "estado": "CONCILIADO",
                    "id_acta": id_acta,
                    "regla_cruce": match["regla_cruce"]
                })

                # 2. Marcar las N partidas ERP resultantes del match
                for erp_item in match["movimientos_erp"]:
                    movs_erp_procesados.add(erp_item['id_movimiento_erp'])
                    erp_updates.append({
                        "id_movimiento_erp": erp_item['id_movimiento_erp'],
                        "id_movimiento_banco": b['id_movimiento'],
                        "estado": "CONCILIADO",
                        "id_acta": id_acta,
                        "regla_cruce": match["regla_cruce"]
                    })

                total_conciliado += float(b['monto'])

        # =========================================================================
        # REGLA 2: COINCIDENCIA PARCIAL / RESIDUAL (MONTO + FECHA <= 3d)
        # =========================================================================
        for b in movs_banco:
            if b['id_movimiento'] in movs_banco_procesados:
                continue

            monto_b = round(abs(float(b['monto'])), 2)

            for e in movs_erp:
                if e['id_movimiento_erp'] in movs_erp_procesados:
                    continue

                monto_e = round(abs(float(e['monto'])), 2)

                if monto_b == monto_e:
                    if es_diferencia_valida(b['fecha_dt'], e['fecha_dt']):
                        movs_banco_procesados.add(b['id_movimiento'])
                        movs_erp_procesados.add(e['id_movimiento_erp'])

                        banco_updates.append({
                            "id_movimiento": b['id_movimiento'],
                            "estado": "CONCILIADO_PARCIAL",
                            "id_acta": id_acta,
                            "regla_cruce": "REGLA_2_PARCIAL"
                        })
                        erp_updates.append({
                            "id_movimiento_erp": e['id_movimiento_erp'],
                            "id_movimiento_banco": b['id_movimiento'],
                            "estado": "CONCILIADO_PARCIAL",
                            "id_acta": id_acta,
                            "regla_cruce": "REGLA_2_PARCIAL"
                        })
                        total_conciliado += float(b['monto'])
                        break

        # =========================================================================
        # REGLA 3: SUBSET SUM (Combinatoria N -> 1)
        # =========================================================================
        candidatos_erp_libres = [e for e in movs_erp if e['id_movimiento_erp'] not in movs_erp_procesados]

        for b in movs_banco:
            if b['id_movimiento'] in movs_banco_procesados:
                continue

            candidatos_ventana = [
                e for e in candidatos_erp_libres 
                if e['id_movimiento_erp'] not in movs_erp_procesados and 
                es_diferencia_valida(b['fecha_dt'], e['fecha_dt'])
            ]

            if len(candidatos_ventana) >= 2:
                combo_found = buscar_combinacion_subconjunto(candidatos_ventana, float(b['monto']))
                if combo_found:
                    movs_banco_procesados.add(b['id_movimiento'])
                    banco_updates.append({
                        "id_movimiento": b['id_movimiento'],
                        "estado": "CONCILIADO_PARCIAL",
                        "id_acta": id_acta,
                        "regla_cruce": "REGLA_4_SUBCONJUNTO_COMBINATORIO"
                    })

                    for e_item in combo_found:
                        movs_erp_procesados.add(e_item['id_movimiento_erp'])
                        erp_updates.append({
                            "id_movimiento_erp": e_item['id_movimiento_erp'],
                            "id_movimiento_banco": b['id_movimiento'],
                            "estado": "CONCILIADO_PARCIAL",
                            "id_acta": id_acta,
                            "regla_cruce": "REGLA_4_SUBCONJUNTO_COMBINATORIO"
                        })
                    total_conciliado += float(b['monto'])

        # =========================================================================
        # PERSISTENCIA TRANSACCIONAL EN BASE DE DATOS
        # =========================================================================
        saldo_banco = sum(float(b['monto']) for b in movs_banco) if movs_banco else 0.0
        saldo_libros = sum(float(e['monto']) for e in movs_erp) if movs_erp else 0.0

        # 1. Registrar primero el Acta Maestro en conciliacion.actas (Padre)
        id_acta_final = self.repo.registrar_acta_maestro(
            id_acta=id_acta,
            id_cuenta=cuenta_obj.id_cuenta,
            cuenta_contable=cuenta_gl,
            periodo=periodo,
            saldo_banco=saldo_banco,
            saldo_libros=saldo_libros,
            total_conciliado=total_conciliado,
            creado_por=creado_por
        )

        if id_acta_final != id_acta:
            for b in banco_updates:
                b["id_acta"] = id_acta_final
            for e in erp_updates:
                e["id_acta"] = id_acta_final

        # 2. Aplicar actualizaciones masivas de estado
        self.repo.aplicar_actualizaciones_conciliacion(banco_updates, erp_updates)

        num_transitos_banco = len(movs_banco) - len(movs_banco_procesados)
        num_transitos_erp = len(movs_erp) - len(movs_erp_procesados)

        return {
            "id_acta": str(id_acta_final),
            "periodo": periodo,
            "cuenta_contable": cuenta_gl,
            "saldo_banco": float(saldo_banco),
            "saldo_libros": float(saldo_libros),
            "total_conciliado": float(total_conciliado),
            "transitos_banco": num_transitos_banco,
            "transitos_erp": num_transitos_erp,
            "total_partidas_en_transito": num_transitos_banco + num_transitos_erp,
            "estado": "BORRADOR"
        }

    def evaluar_coincidencia_referencia_unificada(
        self,
        mov_banco: Any,
        movs_erp_pendientes: List[Any],
        banco_nombre: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Evaluador unificado compatible tanto con dicts como con modelos SQLAlchemy."""
        monto_banco = abs(float(_val(mov_banco, "monto", 0)))
        refs_banco = extraer_referencias_candidatas(mov_banco)

        if not refs_banco:
            return None

        fecha_b = _val(mov_banco, "fecha_dt") or _val(mov_banco, "fecha_transaccion") or _val(mov_banco, "fecha_movimiento")
        ref_principal_raw = _val(mov_banco, "numero_referencia") or _val(mov_banco, "referencia")
        ref_principal_banco = normalizar_referencia(ref_principal_raw)

        # -------------------------------------------------------------
        # FASE 1: MATCH 1 A 1 (Monto exacto, referencia en BC y fecha <= 3d)
        # -------------------------------------------------------------
        for mov_erp in movs_erp_pendientes:
            monto_erp = abs(float(_val(mov_erp, "monto", 0)))
            if round(monto_banco, 2) != round(monto_erp, 2):
                continue

            fecha_e = _val(mov_erp, "fecha_dt") or _val(mov_erp, "posting_date") or _val(mov_erp, "fecha")
            if fecha_b and fecha_e and not es_diferencia_valida(fecha_b, fecha_e):
                continue

            # Extraer tanto external_document_no como document_no
            ref_ext_raw = _val(mov_erp, "external_document_no") or _val(mov_erp, "no_documento_externo") or ""
            ref_doc_raw = _val(mov_erp, "document_no") or _val(mov_erp, "no_documento") or ""

            ref_erp_ext = normalizar_referencia(ref_ext_raw)
            ref_erp_doc = normalizar_referencia(ref_doc_raw)

            # Si el documento externo O el número interno coinciden con las referencias del extracto
            if ref_erp_ext in refs_banco or ref_erp_doc in refs_banco:
                ref_usada = ref_erp_ext if ref_erp_ext in refs_banco else ref_erp_doc
                es_gye = "GUAYAQUIL" in (banco_nombre or "").upper()
                es_ref2 = ref_principal_banco in {"", "0", "00", "000", "0000"}

                regla_tag = "REGLA_1_EXACTA_REF2_GYE" if (es_gye and es_ref2) else "REGLA_1_EXACTA"

                return {
                    "tipo": "1_A_1",
                    "movimiento_banco": mov_banco,
                    "movimientos_erp": [mov_erp],
                    "regla_cruce": regla_tag,
                    "referencia_usada": ref_usada
                }

        # -------------------------------------------------------------
        # FASE 2: MATCH 1 A N (Suma de N partidas ERP == Monto Banco)
        # -------------------------------------------------------------
        for ref_cand in refs_banco:
            candidatos_erp = []
            for mov_erp in movs_erp_pendientes:
                ref_ext_raw = _val(mov_erp, "external_document_no") or _val(mov_erp, "no_documento_externo") or ""
                ref_doc_raw = _val(mov_erp, "document_no") or _val(mov_erp, "no_documento") or ""
                
                ref_erp_ext = normalizar_referencia(ref_ext_raw)
                ref_erp_doc = normalizar_referencia(ref_doc_raw)

                if ref_cand in {ref_erp_ext, ref_erp_doc}:
                    fecha_e = _val(mov_erp, "fecha_dt") or _val(mov_erp, "posting_date") or _val(mov_erp, "fecha")
                    if not fecha_b or not fecha_e or es_diferencia_valida(fecha_b, fecha_e):
                        candidatos_erp.append(mov_erp)

            if len(candidatos_erp) >= 1:
                suma_erp = sum(abs(float(_val(m, "monto", 0))) for m in candidatos_erp)
                if round(monto_banco, 2) == round(suma_erp, 2):
                    es_consolidado = len(candidatos_erp) > 1
                    regla_tag = "REGLA_2_1_A_N_REF" if es_consolidado else "REGLA_1_EXACTA"
                    
                    return {
                        "tipo": "1_A_N" if es_consolidado else "1_A_1",
                        "movimiento_banco": mov_banco,
                        "movimientos_erp": candidatos_erp,
                        "regla_cruce": regla_tag,
                        "referencia_usada": ref_cand,
                        "cantidad_partidas_erp": len(candidatos_erp)
                    }

        return None

    def generar_reporte_excel(
        self,
        id_cuenta: str,
        fecha_inicio: str,
        fecha_fin: str,
        usuario_email: str
    ) -> io.BytesIO:
        # 1. Obtener los metadatos de la cuenta bancaria desde la BD
        cuenta = self.bancos_repo.obtener_cuenta_por_id(id_cuenta)
        if not cuenta:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="La cuenta bancaria especificada no existe."
            )

        # 2. Consultar partidas pendientes e histórico desde PostgreSQL
        pendientes_banco = self.repo.obtener_movimientos_banco_pendientes(id_cuenta, fecha_inicio, fecha_fin) or []
        pendientes_erp = self.repo.obtener_movimientos_erp_pendientes(cuenta.cuenta_contable_bc, fecha_inicio, fecha_fin) or []

        # 3. Calcular métricas de saldo real
        total_creditos = sum(m.monto for m in pendientes_banco if getattr(m, "tipo_movimiento", "") == "CREDITO")
        total_debitos = sum(m.monto for m in pendientes_banco if getattr(m, "tipo_movimiento", "") == "DEBITO")
        
        # Saldo inicial base (consultado o por diferencia al cierre anterior)
        saldo_extracto_inicial = self.repo.obtener_saldo_inicial_extracto(id_cuenta, fecha_inicio) or 0.0
        saldo_extracto_calculado = saldo_extracto_inicial + total_creditos - total_debitos
        
        saldo_libros = self.repo.obtener_saldo_contable_libros(cuenta.cuenta_contable_bc, fecha_fin) or 0.0
        diferencia = saldo_extracto_calculado - saldo_libros

        # 4. Clasificar partidas conciliatorias reales
        # Depósitos en tránsito (Créditos del extracto pendientes de cruce contable)
        partidas_transito = [
            {
                "fecha": m.fecha_movimiento.strftime("%Y-%m-%d") if hasattr(m.fecha_movimiento, "strftime") else str(m.fecha_movimiento),
                "numero": getattr(m, "referencia", "N/A"),
                "concepto": getattr(m, "concepto", "Sin concepto"),
                "monto": float(m.monto)
            }
            for m in pendientes_banco if getattr(m, "tipo_movimiento", "") == "CREDITO"
        ]

        # Cargos bancarios no registrados en libros (Notas de Débito)
        notas_debito = [
            {
                "fecha": m.fecha_movimiento.strftime("%Y-%m-%d") if hasattr(m.fecha_movimiento, "strftime") else str(m.fecha_movimiento),
                "numero": getattr(m, "referencia", "N/A"),
                "concepto": getattr(m, "concepto", "Cargo Bancario Pendiente"),
                "monto": float(m.monto)
            }
            for m in pendientes_banco if getattr(m, "tipo_movimiento", "") == "DEBITO"
        ]

        # Abonos bancarios no registrados en libros (Notas de Crédito ERP)
        notas_credito = [
            {
                "fecha": m.fecha.strftime("%Y-%m-%d") if hasattr(m.fecha, "strftime") else str(m.fecha),
                "numero": getattr(m, "no_documento", "N/A"),
                "concepto": getattr(m, "descripcion", "Abono Contable Pendiente"),
                "monto": float(m.monto)
            }
            for m in pendientes_erp if getattr(m, "monto", 0) > 0
        ]

        # Formatear el periodo
        fecha_dt = datetime.strptime(fecha_inicio, "%Y-%m-%d")
        periodo_nombre = fecha_dt.strftime("%B %Y").capitalize()

        # 5. Ensamblar el diccionario dinamico 'acta_data' exigido por reporte_excel.py
        acta_data: Dict[str, Any] = {
            "empresa": "BATERÍAS ECUADOR - FABRIBAT S.A.",
            "periodo_nombre": periodo_nombre,
            "banco": getattr(cuenta, "banco_nombre", "N/A"),
            "numero_cuenta": getattr(cuenta, "numero_cuenta", "N/A"),
            "fecha_inicio": fecha_inicio,
            "fecha_fin": fecha_fin,
            "saldo_extracto_inicial": float(saldo_extracto_inicial),
            "total_creditos": float(total_creditos),
            "total_debitos": float(total_debitos),
            "saldo_extracto_calculado": float(saldo_extracto_calculado),
            "saldo_libros": float(saldo_libros),
            "diferencia": float(diferencia),
            "partidas_transito_banco": partidas_transito,
            "notas_debito": notas_debito,
            "notas_credito": notas_credito,
            "generado_por": usuario_email
        }

        # 6. Generar el archivo Excel en memoria mediante tu función existente
        return generar_excel_informe_conciliacion(acta_data)
    

    def obtener_datos_reporte_acta(self, id_acta: str) -> dict:
        """
        Recupera los datos del acta de conciliación y sus partidas
        para la generación del Excel.
        """
        from fastapi import HTTPException
        # Asegúrate de importar tus modelos, por ejemplo:
        # from app.modulos.conciliacion.models import ActaConciliacion
        # from app.modulos.bancos.models import CuentaBancaria
        
        # 1. Buscar el acta
        acta = self.db.query(Acta).filter(Acta.id_acta == id_acta).first()
        if not acta:
            raise HTTPException(status_code=404, detail=f"No se encontró el acta {id_acta}")

        # 2. Buscar datos de la cuenta/banco para el nombre del reporte
        banco_nombre = "Banco"
        cuenta = None

        # Buscar la cuenta bancaria relacionada al acta
        if hasattr(acta, "id_cuenta") and acta.id_cuenta:
            cuenta = self.db.query(CuentaBancaria).filter(
                CuentaBancaria.id_cuenta == acta.id_cuenta
            ).first()
        elif getattr(acta, "cuenta_contable", None):
            cuenta = self.db.query(CuentaBancaria).filter(
                CuentaBancaria.cuenta_contable_bc == acta.cuenta_contable
            ).first()

        # Usar el atributo real de tu modelo: nombre_cuenta
        if cuenta and cuenta.nombre_cuenta:
            banco_nombre = cuenta.nombre_cuenta
        # 3. Obtener el detalle (partidas en tránsito, etc.)
        # Si tienes una función en el mismo service que traiga las partidas en tránsito, úsala.
        # Si no, y tienes la relación en SQLAlchemy, puedes usar acta.detalles (si aplica).
        detalles_transito = getattr(acta, "detalles", [])

        # 4. Retornar el diccionario que espera generar_excel_informe_conciliacion
        return {
            "acta": acta,
            "periodo_nombre": acta.periodo, # Lo usa tu router
            "banco": banco_nombre,          # Lo usa tu router
            "cuenta_contable": acta.cuenta_contable,
            "saldo_banco": float(acta.saldo_banco or 0),
            "saldo_libros": float(acta.saldo_libros or 0),
            "total_conciliado": float(acta.total_conciliado or 0),
            "estado": acta.estado,
            "detalles": detalles_transito
        }
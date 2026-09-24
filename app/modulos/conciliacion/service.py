# app/modulos/conciliacion/service.py
import uuid
import itertools
import pandas as pd
from datetime import datetime, date
from typing import List, Dict, Any, Tuple, Optional, Union
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.modulos.conciliacion.bc_service import BusinessCentralReaderService
from app.modulos.conciliacion.repository import ConciliacionRepository


def normalizar_referencia(ref: Any) -> str:
    s = str(ref or '').strip().lstrip('0')
    return s if s else '0'


def dias_diferencia(fecha1_str: str, fecha2_str: str) -> int:
    d1 = datetime.strptime(fecha1_str, '%Y-%m-%d').date()
    d2 = datetime.strptime(fecha2_str, '%Y-%m-%d').date()
    return abs((d1 - d2).days)


def buscar_combinacion_subconjunto(candidatos_bc: List[Dict[str, Any]], monto_banco: float, max_combo: int = 3) -> Optional[Tuple]:
    """
    Algoritmo de Cruce por Subconjuntos optimizado.
    Busca si una combinación de 2 o 3 registros de BC suma exactamente el monto del banco.
    """
    monto_target = round(abs(monto_banco), 2)
    n = len(candidatos_bc)
    
    # Acotar espacio de búsqueda a máximo 15 candidatos más cercanos en la ventana
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

    def obtener_y_preparar_mayor(
        self, 
        fecha_inicio: str, 
        fecha_fin: str, 
        cuenta_contable: str
    ) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        raw_entries, error = BusinessCentralReaderService.consultar_mayor_bancos(
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            cuenta_contable=cuenta_contable,
            db=self.db
        )
        if error:
            return None, error
        
        if not raw_entries:
            return pd.DataFrame(), None

        df = pd.DataFrame(raw_entries)
        return df, None

    def ejecutar_conciliacion_periodo(
        self, 
        id_cuenta: Union[uuid.UUID, str], 
        periodo: str, 
        fecha_inicio: date, 
        fecha_fin: date,
        creado_por: str
    ) -> Dict[str, Any]:
        cuenta_obj = self.repo.obtener_cuenta_bancaria(id_cuenta)
        if not cuenta_obj:
            raise HTTPException(status_code=404, detail="Cuenta bancaria no encontrada.")

        grupo_bc = cuenta_obj.grupo_registro_bc
        cuenta_gl = cuenta_obj.cuenta_contable_bc

        # 1. Sincronizar espejo desde BC
        raw_bc, err_bc = BusinessCentralReaderService.consultar_mayor_bancos(
            fecha_inicio.strftime('%Y-%m-%d'),
            fecha_fin.strftime('%Y-%m-%d'),
            grupo_registro_bc=grupo_bc
        )
        if err_bc:
            raise HTTPException(status_code=502, detail=err_bc)

        self.repo.upsert_movimientos_erp_espejo(raw_bc, grupo_bc)

        # 2. Cargar pendientes
        movs_banco = self.repo.obtener_movimientos_banco_pendientes(cuenta_obj.id_cuenta, fecha_inicio, fecha_fin)
        movs_erp = self.repo.obtener_movimientos_erp_pendientes(grupo_bc, fecha_inicio, fecha_fin)

        id_acta = uuid.uuid4()
        total_conciliado = 0.0

        banco_updates = []
        erp_updates = []

        movs_banco_procesados = set()
        movs_erp_procesados = set()

        df_erp = pd.DataFrame(movs_erp) if movs_erp else pd.DataFrame()
        
        # =========================================================================
        # REGLA 1.1: COINCIDENCIA EXACTA (Consolidado por Documento Externo)
        # =========================================================================
        if not df_erp.empty and 'external_document_no' in df_erp.columns:
            grouped = df_erp[df_erp['external_document_no'] != ''].groupby('external_document_no')
            for ext_doc, group in grouped:
                ref_group_clean = normalizar_referencia(ext_doc)
                monto_group_sum = round(abs(group['monto'].astype(float).sum()), 2)
                fecha_group_min = group['posting_date'].min()

                for b in movs_banco:
                    if b['id_movimiento'] in movs_banco_procesados:
                        continue
                    
                    ref_banco_clean = normalizar_referencia(b['numero_referencia'])
                    monto_banco_abs = round(abs(float(b['monto'])), 2)

                    if ref_group_clean == ref_banco_clean and monto_group_sum == monto_banco_abs:
                        if dias_diferencia(b['fecha_transaccion'], fecha_group_min) <= 3:
                            movs_banco_procesados.add(b['id_movimiento'])
                            banco_updates.append({
                                "id_movimiento": b['id_movimiento'],
                                "estado": "CONCILIADO",
                                "id_acta": id_acta,
                                "regla_cruce": "REGLA_1_EXACTA_CONSOLIDADO"
                            })

                            for _, erp_row in group.iterrows():
                                movs_erp_procesados.add(erp_row['id_movimiento_erp'])
                                erp_updates.append({
                                    "id_movimiento_erp": erp_row['id_movimiento_erp'],
                                    "id_movimiento_banco": b['id_movimiento'],
                                    "estado": "CONCILIADO",
                                    "id_acta": id_acta,
                                    "regla_cruce": "REGLA_1_EXACTA_CONSOLIDADO"
                                })
                            total_conciliado += float(b['monto'])
                            break

        # =========================================================================
        # REGLA 1.2: MATCH 1 a 1 INDIVIDUAL EXACTO (Referencia + Monto + Fecha <= 3d)
        # =========================================================================
        for b in movs_banco:
            if b['id_movimiento'] in movs_banco_procesados:
                continue
            
            ref_b = normalizar_referencia(b['numero_referencia'])
            monto_b = round(abs(float(b['monto'])), 2)

            for e in movs_erp:
                if e['id_movimiento_erp'] in movs_erp_procesados:
                    continue
                
                ref_e = normalizar_referencia(e['external_document_no'] or e['document_no'])
                monto_e = round(abs(float(e['monto'])), 2)

                if ref_b == ref_e and monto_b == monto_e:
                    if dias_diferencia(b['fecha_transaccion'], e['posting_date']) <= 3:
                        movs_banco_procesados.add(b['id_movimiento'])
                        movs_erp_procesados.add(e['id_movimiento_erp'])

                        banco_updates.append({
                            "id_movimiento": b['id_movimiento'],
                            "estado": "CONCILIADO",
                            "id_acta": id_acta,
                            "regla_cruce": "REGLA_1_EXACTA"
                        })
                        erp_updates.append({
                            "id_movimiento_erp": e['id_movimiento_erp'],
                            "id_movimiento_banco": b['id_movimiento'],
                            "estado": "CONCILIADO",
                            "id_acta": id_acta,
                            "regla_cruce": "REGLA_1_EXACTA"
                        })
                        total_conciliado += float(b['monto'])
                        break

        # =========================================================================
        # REGLA 2: COINCIDENCIA 1 a 1 PARCIAL (Monto + Fecha <= 3d)
        # (Se ejecuta ANTES de la combinatoria para depurar masivamente en O(N))
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
                    if dias_diferencia(b['fecha_transaccion'], e['posting_date']) <= 3:
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
        # REGLA 3: COMBINATORIA DE SUBCONJUNTOS (Subset Sum N -> 1)
        # (Ejecutada únicamente sobre las pocas partidas residuales)
        # =========================================================================
        candidatos_erp_libres = [e for e in movs_erp if e['id_movimiento_erp'] not in movs_erp_procesados]

        for b in movs_banco:
            if b['id_movimiento'] in movs_banco_procesados:
                continue

            candidatos_ventana = [
                e for e in candidatos_erp_libres 
                if e['id_movimiento_erp'] not in movs_erp_procesados and 
                dias_diferencia(b['fecha_transaccion'], e['posting_date']) <= 3
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

        # Persistir cambios
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

        # 2. Actualizar las referencias de id_acta en bancos.movimientos y bancos.movimientos_erp (Hijos)
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
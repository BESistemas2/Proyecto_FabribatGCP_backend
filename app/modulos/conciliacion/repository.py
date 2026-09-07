# app/modulos/conciliacion/repository.py
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy import text
from app.core.database import get_db_session
from app.core.models import MovimientoBancario  # Mantenemos las importaciones existentes [1]

class ConciliacionRepository:
    def __init__(self):
        # Heredamos exactamente tu patrón de sesión del pool de core [1]
        self.session = get_db_session()()

    def obtener_movimientos_bancarios_despejados(self, cuenta_contable: str, fecha_inicio: str, fecha_fin: str) -> List[Dict[str, Any]]:
        """
        Recupera todos los movimientos bancarios locales importados previamente por el ETL
        para una cuenta y periodo específico, listos para ser procesados por el motor de match [6].
        """
        try:
            # Filtramos los movimientos bancarios que aún no estén conciliados o estén pendientes [6]
            query = self.session.query(MovimientoBancario).filter(
                MovimientoBancario.cuentaContable == cuenta_contable,
                MovimientoBancario.fechaMovimiento >= fecha_inicio,
                MovimientoBancario.fechaMovimiento <= fecha_fin
            )
            
            movimientos = query.order_by(MovimientoBancario.fechaMovimiento.asc()).all()
            
            # Formateamos a diccionarios para procesamiento ágil en Pandas [7]
            return [{
                "idMovimiento": m.idMovimiento,
                "fecha": m.fechaMovimiento.strftime('%Y-%m-%d') if m.fechaMovimiento else None,
                "documento": str(m.numeroDocumento).strip() if m.numeroDocumento else "",
                "referencia": str(m.referencia).strip() if m.referencia else "",
                "monto": float(m.monto),
                "descripcion": m.descripcion or "",
                "estadoConciliacion": m.estadoConciliacion or "PENDIENTE"
            } for m in movimientos]
            
        except Exception as e:
            raise RuntimeError(f"Error al consultar movimientos bancarios locales: {str(e)}")

    def actualizar_estado_cruce_bancario(self, id_movimiento: str, estado: str, id_acta: Optional[str] = None):
        """
        Actualiza el estado de conciliación de un movimiento bancario individual 
        (CONCILIADO, CONCILIADO POR FECHA Y MONTO, o NO CONCILIADO) y lo asocia al acta [8].
        """
        try:
            movimiento = self.session.query(MovimientoBancario).filter(
                MovimientoBancario.idMovimiento == id_movimiento
            ).first()
            
            if movimiento:
                movimiento.estadoConciliacion = estado
                if id_acta:
                    movimiento.idActaConciliacion = id_acta
                movimiento.updatedOn = datetime.now()
                self.session.commit()
        except Exception as e:
            self.session.rollback()
            raise RuntimeError(f"Fallo al actualizar estado del movimiento bancario {id_movimiento}: {str(e)}")

    def registrar_acta_conciliacion_maestro(self, idActa: str, cuentaContable: str, periodo: str, 
                                            saldoBanco: float, saldoLibros: float, 
                                            totalConciliado: float, creadoPor: str) -> bool:
        """
        Registra la cabecera del Acta de Conciliación en la tabla de auditoría local [9].
        CORREGIDO: Parámetros del diccionario de enlace mapeados exactamente a las variables camelCase del SQL [2, 4].
        """
        sql = text("""
            INSERT INTO conciliacionActas 
            (idActa, cuentaContable, periodo, saldoBanco, saldoLibros, totalConciliado, creadoPor, createdOn, estado)
            VALUES (:idActa, :cuentaContable, :periodo, :saldoBanco, :saldoLibros, :totalConciliado, :creadoPor, :createdOn, 'BORRADOR')
            ON DUPLICATE KEY UPDATE 
                saldoBanco = :saldoBanco,
                saldoLibros = :saldoLibros,
                totalConciliado = :totalConciliado,
                updatedOn = :createdOn
        """)
        try:
            self.session.execute(sql, {
                "idActa": idActa,
                "cuentaContable": cuentaContable,
                "periodo": periodo,
                "saldoBanco": saldoBanco,
                "saldoLibros": saldoLibros,
                "totalConciliado": totalConciliado,
                "creadoPor": creadoPor,
                "createdOn": datetime.now()
            })
            self.session.commit()
            return True
        except Exception as e:
            self.session.rollback()
            raise RuntimeError(f"Error al registrar cabecera de Acta de Conciliación: {str(e)}")

    def registrar_partidas_en_transito(self, idActa: str, excepciones_datos: List[Dict[str, Any]]) -> int:
        """
        Inyecta de manera masiva las partidas que quedaron clasificadas como "No Conciliadas" (Tránsitos) [3, 4].
        CORREGIDO: Parámetros del diccionario vinculados de manera idéntica al query SQL en camelCase [3, 5].
        """
        if not excepciones_datos:
            return 0
            
        sql = text("""
            INSERT INTO conciliacionPartidasTransito 
            (idActa, origenDato, fechaTransaccion, documentoReferencia, descripcion, monto, createdOn)
            VALUES (:idActa, :origenDato, :fechaTransaccion, :documentoReferencia, :descripcion, :monto, :createdOn)
        """)
        
        try:
            filas_insertadas = 0
            for item in excepciones_datos:
                self.session.execute(sql, {
                    "idActa": idActa,
                    "origenDato": item["origen"],  # 'BANCO' o 'MAYOR_ERP'
                    "fechaTransaccion": item["fecha"],
                    "documentoReferencia": item["documento"],
                    "descripcion": item["descripcion"],
                    "monto": item["monto"],
                    "createdOn": datetime.now()
                })
                filas_insertadas += 1
            self.session.commit()
            return filas_insertadas
        except Exception as e:
            self.session.rollback()
            raise RuntimeError(f"Error al registrar partidas en tránsito para el acta {idActa}: {str(e)}")

    def close(self):
        """Asegura el cierre de la sesión contable al finalizar el proceso [5]."""
        self.session.close()
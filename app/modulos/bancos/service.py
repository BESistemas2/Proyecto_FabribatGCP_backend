# app/modulos/bancos/service.py
import hashlib
import logging
import re
import uuid
from datetime import datetime
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.modulos.bancos.etl import EtlError, ResultadoEtl, procesar_extracto
from app.modulos.bancos.models import CuentaBancaria
from app.modulos.bancos.repository import BancosRepository
from app.utils.storage import upload_to_minio

logger = logging.getLogger(__name__)
TZ_EC = ZoneInfo("America/Guayaquil")

CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
}


def nombre_archivo_seguro(nombre: str) -> str:
    """Quita rutas y caracteres problemáticos para usarlo como clave en MinIO."""
    base = re.split(r"[\\/]", nombre or "archivo")[-1]
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return (base or "archivo")[:120]


class BancosService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = BancosRepository(db)

    @staticmethod
    def _texto_banco_cuenta(cuenta: CuentaBancaria) -> str:
        """Texto con el que se valida que el archivo corresponda al banco de la cuenta."""
        partes = [cuenta.nombre_cuenta, cuenta.grupo_registro_bc]
        if cuenta.institucion is not None:
            partes.append(cuenta.institucion.nombre)
        return " ".join(p for p in partes if p)

    def procesar_archivo_bancos_service(
        self,
        file_bytes: bytes,
        id_carga: UUID,
        cuenta: CuentaBancaria,
        filename_original: str,
    ) -> tuple[bool, str, Optional[ResultadoEtl]]:
        """
        Pipeline completo: hash -> ETL -> respaldo en MinIO -> inserción -> cierre.
        El archivo se valida ANTES de subirlo, para no guardar respaldos de archivos inválidos.
        """
        try:
            # 1. Auditoría por hash
            hash_archivo = hashlib.sha256(file_bytes).hexdigest()
            duplicado = self.repo.verificar_hash_duplicado(hash_archivo, id_carga)
            if duplicado:
                msg = (f"Omitido: este mismo archivo ya fue procesado como "
                       f"'{duplicado.nombre_archivo}' por '{duplicado.created_by}'.")
                self.repo.registrar_error_carga(id_carga, msg)
                return False, msg, None

            # 2. Lectura, validación y normalización
            resultado = procesar_extracto(file_bytes, filename_original, self._texto_banco_cuenta(cuenta))

            # 3. Respaldo físico
            ahora = datetime.now(TZ_EC)
            nombre_seguro = nombre_archivo_seguro(filename_original)
            extension = "." + nombre_seguro.rsplit(".", 1)[-1].lower() if "." in nombre_seguro else ""
            object_name = (f"bancos/{ahora:%Y/%m}/{resultado.banco}_{ahora:%Y%m%d_%H%M%S}_"
                           f"{hash_archivo[:8]}_{nombre_seguro}")
            url_archivo = upload_to_minio(file_bytes, object_name,
                                          CONTENT_TYPES.get(extension, "application/octet-stream"))
            if not url_archivo:
                msg = "No se pudo guardar el respaldo del archivo en el almacenamiento (MinIO)."
                self.repo.registrar_error_carga(id_carga, msg)
                return False, msg, None
            self.repo.actualizar_estado_procesando(id_carga, url_archivo, hash_archivo)

            # 4. Persistencia (una sola transacción: movimientos + cierre de la carga)
            filas = [
                {
                    "id_movimiento": uuid.uuid4(),
                    "id_carga": id_carga,
                    "id_cuenta": cuenta.id_cuenta,
                    "fecha_transaccion": m.fecha,
                    "numero_referencia": m.referencia[:100],
                    "concepto": m.concepto[:255],
                    "monto": m.monto,
                    "tipo_movimiento": m.tipo,
                    "estado": "Pendiente",
                    "info_adicional": m.info_adicional,
                }
                for m in resultado.movimientos
            ]
            insertados = self.repo.insertar_movimientos_ignorar_duplicados(filas)
            duplicados = len(filas) - insertados

            partes = [
                f"Banco: {resultado.banco}.",
                f"Filas leídas: {resultado.filas_leidas}.",
                f"Insertados: {insertados}.",
            ]
            if duplicados:
                partes.append(f"Ya existentes (omitidos): {duplicados}.")
            if resultado.filas_omitidas:
                partes.append(f"Filas internas del banco omitidas: {resultado.filas_omitidas}.")
            if resultado.saldo_inicial is not None:
                partes.append(f"Saldo inicial: {resultado.saldo_inicial}. Saldo final: {resultado.saldo_final}.")
            partes.extend(f"⚠️ {a}" for a in resultado.advertencias)
            msg_final = " ".join(partes)

            self.repo.finalizar_carga_maestro(id_carga, resultado, insertados, msg_final)
            return True, msg_final, resultado

        except EtlError as e:
            self.repo.registrar_error_carga(id_carga, str(e))
            return False, str(e), None
        except Exception:
            logger.exception("Fallo inesperado en el ETL de la carga %s", id_carga)
            msg = f"Error interno al procesar el archivo (carga {id_carga}). Contacta a soporte."
            self.repo.registrar_error_carga(id_carga, msg)
            return False, msg, None

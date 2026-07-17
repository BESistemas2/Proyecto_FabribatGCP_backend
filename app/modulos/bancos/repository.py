# app/modulos/bancos/repository.py
from app.core.database import get_bancos_session
from app.core.models import CargaBancaria, MovimientoBancario
from sqlalchemy import insert

class BancosRepository:
    def __init__(self):
        self.session = get_bancos_session()

    def verificar_hash_duplicado(self, hash_archivo, id_carga):
        """Verifica si el contenido del archivo ya fue procesado antes."""
        return self.session.query(CargaBancaria).filter(
            CargaBancaria.hashArchivo == hash_archivo,
            CargaBancaria.estado == 'Completado',
            CargaBancaria.idCarga != id_carga
        ).first()

    def registrar_error_carga(self, id_carga, mensaje_error):
        """Registra el fallo de auditoría o de procesamiento."""
        carga = self.session.query(CargaBancaria).filter(CargaBancaria.idCarga == id_carga).first()
        if carga:
            carga.estado = 'Error'
            carga.mensajeLog = mensaje_error
            self.session.commit()

    def actualizar_estado_procesando(self, id_carga, url_archivo, hash_archivo):
        """Actualiza el maestro al iniciar la lectura del ETL."""
        carga = self.session.query(CargaBancaria).filter(CargaBancaria.idCarga == id_carga).first()
        if carga:
            carga.urlArchivo = url_archivo
            carga.hashArchivo = hash_archivo
            carga.estado = 'Procesando'
            carga.mensajeLog = 'Iniciando lectura de Pandas...'
            self.session.commit()

    def insertar_movimientos_ignorar_duplicados(self, movimientos_datos):
        """
        Inyecta masivamente los movimientos bancarios usando el prefijo IGNORE de MySQL 
        para respetar el índice único compuesto de auditoría.
        """
        if not movimientos_datos:
            return 0
        
        # prefix_with('IGNORE') emula perfectamente el 'INSERT IGNORE' nativo de MySQL
        stmt = insert(MovimientoBancario).values(movimientos_datos).prefix_with('IGNORE')
        result = self.session.execute(stmt)
        self.session.commit()
        return result.rowcount

    def finalizar_carga_maestro(self, id_carga, total_leidos, importados, mensaje_final):
        """Cierra el ciclo del maestro marcándolo como Completado[cite: 1]."""
        carga = self.session.query(CargaBancaria).filter(CargaBancaria.idCarga == id_carga).first()
        if carga:
            carga.estado = 'Completado'
            carga.totalRegistrosLeidos = total_leidos
            carga.registrosImportados = importados
            carga.mensajeLog = mensaje_final
            self.session.commit()

    def close(self):
        """Asegura el cierre de la sesión del pool al terminar."""
        self.session.close()
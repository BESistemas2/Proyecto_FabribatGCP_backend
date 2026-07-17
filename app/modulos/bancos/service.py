# app/modulos/bancos/service.py
import io
import re
import uuid
import hashlib
import pandas as pd
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import MINIO_CONF
from app.utils.storage import upload_to_minio  # Asumimos que esta utilidad existirá
from .repository import BancosRepository

def limpiar_monto_robusto(valor_raw):
    """Limpia strings de moneda detectando comas/puntos decimales inteligentemente[cite: 1]."""
    if pd.isna(valor_raw) or valor_raw == '':
        return 0.0
    if isinstance(valor_raw, (int, float)):
        return float(valor_raw)
        
    s = str(valor_raw).strip()
    s = re.sub(r'[^\d\.,\-]', '', s)
    if not s: return 0.0
    
    last_comma = s.rfind(',')
    last_dot = s.rfind('.')
    
    if last_comma > last_dot:
        s = s.replace('.', '').replace(',', '.')    
    elif last_dot > last_comma:
        s = s.replace(',', '')     
    try:
        return float(s)
    except ValueError:
        return 0.0

def analizar_columnas_firma(file_data, nombre_archivo):
    """Escanea las cabeceras originales del archivo para detectar la firma del banco."""
    es_excel = nombre_archivo.lower().endswith(('.xls', '.xlsx'))
    lineas = []

    if es_excel:
        try:
            df_preview = pd.read_excel(io.BytesIO(file_data), header=None, nrows=25, engine='openpyxl')
            for _, row in df_preview.iterrows():
                lineas.append(' '.join(row.dropna().astype(str)))
        except:
            return None, -1, "Error al pre-leer el archivo Excel."
    else:
        try:
            texto = file_data[:7000].decode('utf-8', errors='ignore')
            lineas = texto.splitlines()
        except:
            return None, -1, "Fallo crítico al decodificar CSV."

    firma_archivo = None
    fila_header = -1
    
    for i, linea in enumerate(lineas):
        linea_up = ''.join(c for c in unicodedata.normalize('NFD', linea) if unicodedata.category(c) != 'Mn').upper()
        
        if 'REFERENCIA' in linea_up and 'REFERENCIA2' in linea_up and '+/-' in linea_up:
            firma_archivo = 'PRODUBANCO'
            fila_header = i
            break
        elif 'FECHA DE TRANSACCION' in linea_up and 'TIPO DE MOVIMIENTO' in linea_up:
            firma_archivo = 'GUAYAQUIL'
            fila_header = i
            break
        elif 'DOCUMENTO' in linea_up and 'OFICINA' in linea_up and 'CODIGO' in linea_up:
            firma_archivo = 'PICHINCHA' 
            fila_header = i
            break

    if fila_header == -1:
        return None, -1, "Archivo inválido: No se reconoció la estructura de columnas de ningún banco[cite: 1]."

    return firma_archivo, fila_header, "OK"

def procesar_archivo_bancos_service(file_data, id_carga, id_institucion, filename_original, usuario_carga):
    """Ejecuta el pipeline completo del motor ETL Bancario."""
    tz_ec = ZoneInfo("America/Guayaquil")
    ahora_ec = datetime.now(tz_ec)
    
    repo = BancosRepository()
    
    try:
        # 1. Auditoría Criptográfica[cite: 1]
        hash_archivo = hashlib.sha256(file_data).hexdigest()
        duplicado = repo.verificar_hash_duplicado(hash_archivo, id_carga)
        
        if duplicado:
            msg = f"⚠️ Omitido por Auditoría: Este archivo ya fue procesado anteriormente bajo el nombre '{duplicado.nombreArchivo}' por el usuario '{duplicado.createdBy}'[cite: 1]."
            repo.registrar_error_carga(id_carga, msg)
            return False, msg

        # 2. Validación de Firma de Columnas[cite: 1]
        nombre_banco, fila_header, msg_val = analizar_columnas_firma(file_data, filename_original)
        if not nombre_banco:
            repo.registrar_error_carga(id_carga, msg_val)
            return False, msg_val

        # 3. Guardar Respaldo en MinIO[cite: 1]
        timestamp = ahora_ec.strftime("%Y%m%d_%H%M%S")
        short_hash = hash_archivo[:8]
        safe_filename = f"{nombre_banco}_{timestamp}_{short_hash}_{filename_original}"
        object_name = f"Bancos/{ahora_ec.year}/{ahora_ec.month:02d}/{safe_filename}"
        
        # Invocamos la subida a MinIO (Abstraído en utils)
        url_archivo = upload_to_minio(file_data, object_name, 'application/octet-stream')
        if not url_archivo:
            msg_err = "Error al almacenar el respaldo físico en MinIO."
            repo.registrar_error_carga(id_carga, msg_err)
            return False, msg_err

        # 4. Actualizar Maestro a "Procesando"[cite: 1]
        repo.actualizar_estado_procesando(id_carga, url_archivo, hash_archivo)

        # 5. Carga de Motor Pandas[cite: 1]
        es_excel = filename_original.lower().endswith(('.xls', '.xlsx'))
        if es_excel:
            df = pd.read_excel(io.BytesIO(file_data), skiprows=fila_header, engine='openpyxl')
        else:
            df = pd.read_csv(io.BytesIO(file_data), header=fila_header, sep=None, engine='python', encoding='utf-8', on_bad_lines='skip')
            
        df.columns = [''.join(c for c in unicodedata.normalize('NFD', str(col)) if unicodedata.category(c) != 'Mn').strip().upper() for col in df.columns]

        movimientos_a_insertar = []
        total_leidos = 0

        # 6. Transformación por Banco[cite: 1]
        if nombre_banco == 'PRODUBANCO':
            df_ingresos = df[df['+/-'].astype(str).str.contains(r'\+', na=False)]
            total_leidos = len(df_ingresos)
            for _, row in df_ingresos.iterrows():
                if pd.isna(row['FECHA']): continue
                fecha_str = pd.to_datetime(row['FECHA']).strftime('%Y-%m-%d')
                
                info_adi = f"REF2: {row.get('REFERENCIA2', '')} / OF: {row.get('OFICINA', '')} / COD TR: {row.get('COD TRANSACCION', '')}"
                movimientos_a_insertar.append({
                    'idMovimiento': str(uuid.uuid4()), 'idCarga': id_carga, 'idInstitucion': id_institucion,
                    'fechaTransaccion': fecha_str, 'numeroReferencia': str(row['REFERENCIA']).strip(),
                    'concepto': str(row['DESCRIPCION']).strip(), 'monto': limpiar_monto_robusto(row['VALOR']),
                    'tipoMovimiento': 'CREDITO', 'estado': 'Pendiente', 'infoAdicional': info_adi
                })

        elif nombre_banco == 'PICHINCHA':
            df_ingresos = df[df['TIPO'].astype(str).str.strip().str.upper() == 'C']
            total_leidos = len(df_ingresos)
            for _, row in df_ingresos.iterrows():
                if pd.isna(row['FECHA']): continue
                fecha_str = pd.to_datetime(row['FECHA'], dayfirst=True).strftime('%Y-%m-%d')
                
                info_adi = f"COD: {row.get('CODIGO', '')} / OF: {row.get('OFICINA', '')}"
                movimientos_a_insertar.append({
                    'idMovimiento': str(uuid.uuid4()), 'idCarga': id_carga, 'idInstitucion': id_institucion,
                    'fechaTransaccion': fecha_str, 'numeroReferencia': str(row['DOCUMENTO']).strip(),
                    'concepto': str(row['CONCEPTO']).strip(), 'monto': limpiar_monto_robusto(row['MONTO']),
                    'tipoMovimiento': 'CREDITO', 'estado': 'Pendiente', 'infoAdicional': info_adi
                })

        elif nombre_banco == 'GUAYAQUIL':
            df_ingresos = df[df['TIPO DE MOVIMIENTO'].astype(str).str.contains('DEP.SITO|NOTA DE CR.DITO|DEPOSITO|NOTA DE CREDITO', case=False, na=False, regex=True)]
            total_leidos = len(df_ingresos)
            for _, row in df_ingresos.iterrows():
                if pd.isna(row['FECHA DE TRANSACCION']): continue
                fecha_str = pd.to_datetime(row['FECHA DE TRANSACCION']).strftime('%Y-%m-%d')

                info_adi = f"AG: {row.get('AGENCIA', '')} / REF: {row.get('REFERENCIA', '')} / REF2: {row.get('REFERENCIA 2', '')} / REF3: {row.get('REFERENCIA 3', '')}"
                movimientos_a_insertar.append({
                    'idMovimiento': str(uuid.uuid4()), 'idCarga': id_carga, 'idInstitucion': id_institucion,
                    'fechaTransaccion': fecha_str, 'numeroReferencia': str(row['DOCUMENTO']).strip(),
                    'concepto': str(row['CONCEPTO']).strip(), 'monto': limpiar_monto_robusto(row['MONTO']),
                    'tipoMovimiento': str(row['TIPO DE MOVIMIENTO']).strip(), 'estado': 'Pendiente', 'infoAdicional': info_adi
                })

        # 7. Persistencia Masiva mediante el Repositorio
        registros_importados = repo.insertar_movimientos_ignorar_duplicados(movimientos_a_insertar)
        
        # 8. Cierre Exitoso de la Carga
        msg_final = f"Banco detectado: {nombre_banco}. Leídos: {total_leidos}. Nuevos insertados: {registros_importados}. Duplicados omitidos: {total_leidos - registros_importados}."
        repo.finalizar_carga_maestro(id_carga, total_leidos, registros_importados, msg_final)
        
        return True, msg_final

    except Exception as e:
        msg_err = f"Fallo en pipeline ETL: {str(e)}"
        repo.registrar_error_carga(id_carga, msg_err)
        return False, msg_err
    finally:
        repo.close()
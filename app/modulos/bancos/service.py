# app/modulos/bancos/service.py
import io
import re
import uuid
import hashlib
import unicodedata
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Tuple, List, Dict, Any, Optional
from uuid import UUID
from sqlalchemy.orm import Session

from app.utils.storage import upload_to_minio
from app.modulos.bancos.repository import BancosRepository


def limpiar_monto_robusto(valor_raw: Any) -> float:
    """Convierte montos en string a float detectando comas/puntos decimales inteligentemente."""
    if pd.isna(valor_raw) or valor_raw == '':
        return 0.0
    if isinstance(valor_raw, (int, float)):
        return float(valor_raw)
        
    s = str(valor_raw).strip()
    s = re.sub(r'[^\d\.,\-]', '', s)
    if not s: 
        return 0.0
    
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


def descargar_archivo_appsheet(url_appsheet: str) -> Tuple[Optional[bytes], str]:
    """Descarga el archivo a la memoria RAM usando la URL de AppSheet."""
    try:
        if not url_appsheet.startswith('http'):
            return None, "No se recibió una URL válida de AppSheet."
        response = requests.get(url_appsheet, timeout=30)
        if response.status_code == 200:
            return response.content, "OK"
        return None, f"Error descargando desde AppSheet (HTTP {response.status_code})"
    except Exception as e:
        return None, f"Error de red al descargar el archivo: {str(e)}"


class BancosService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = BancosRepository(db)

    def analizar_y_validar_archivo_con_usuario(
        self, 
        file_data: bytes, 
        nombre_banco_bd: str, 
        nombre_archivo: str
    ) -> Tuple[Optional[str], int, str, str]:
        """Escáner híbrido (Excel/CSV) con detección precisa de firma de banco."""
        es_excel = nombre_archivo.lower().endswith(('.xls', '.xlsx'))
        lineas = []
        enc_usado = 'utf-8'

        if es_excel:
            try:
                df_preview = pd.read_excel(io.BytesIO(file_data), header=None, nrows=100, engine='openpyxl')
                for _, row in df_preview.iterrows():
                    lineas.append(' '.join(row.dropna().astype(str)))
            except Exception as e:
                return None, -1, enc_usado, f"Error al abrir Excel (.xlsx): {str(e)}"
        else:
            encodings_a_probar = ['utf-8', 'latin1', 'windows-1252']
            texto_crudo = None
            for enc in encodings_a_probar:
                try:
                    texto_crudo = file_data[:7000].decode(enc)
                    enc_usado = enc
                    break 
                except UnicodeDecodeError:
                    continue
            if not texto_crudo:
                return None, -1, enc_usado, "Fallo crítico: No se pudo decodificar el archivo CSV."
            lineas = texto_crudo.splitlines()

        firma_archivo = None
        fila_header_detectada = -1
        
        for i, linea in enumerate(lineas):
            linea_up = ''.join(c for c in unicodedata.normalize('NFD', linea) if unicodedata.category(c) != 'Mn').upper()
            
            # 1. BANCO GUAYAQUIL (Específico por 'FECHA DE TRANSACCION' y 'TIPO DE MOVIMIENTO')
            if 'FECHA DE TRANSACCION' in linea_up and 'TIPO DE MOVIMIENTO' in linea_up:
                firma_archivo = 'GUAYAQUIL'
                fila_header_detectada = i
                break

            # 2. PRODUBANCO (Legacy y Nuevo / ReporteDetalleCuentaSC)
            elif ('REFERENCIA' in linea_up and 'REFERENCIA2' in linea_up and '+/-' in linea_up) or \
                 ('REFERENCIA 1' in linea_up and 'REFERENCIA 2' in linea_up and 'TRANSACCION' in linea_up) or \
                 ('REFERENCIA' in linea_up and 'TRANSACCION' in linea_up and 'SIGNO' in linea_up and not 'TIPO DE MOVIMIENTO' in linea_up):
                firma_archivo = 'PRODUBANCO'
                fila_header_detectada = i
                break

            # 3. BANCO PICHINCHA
            elif ('DOCUMENTO' in linea_up and 'OFICINA' in linea_up and ('CODIGO' in linea_up or 'COD' in linea_up)):
                firma_archivo = 'PICHINCHA' 
                fila_header_detectada = i
                break

            # 4. BANCO SOLIDARIO
            elif ('FECHA_MOVIMIENTO' in linea_up or 'FECHA MOVIMIENTO' in linea_up) and ('NO_DOCUMENTO' in linea_up or 'NO DOCUMENTO' in linea_up):
                firma_archivo = 'SOLIDARIO'
                fila_header_detectada = i
                break

        if fila_header_detectada == -1:
            return None, -1, enc_usado, "Archivo inválido: No se reconoció la estructura de columnas de ningún banco."

        if firma_archivo.upper() not in nombre_banco_bd.upper():
            return None, -1, enc_usado, f"⚠️ Inconsistencia: Seleccionaste '{nombre_banco_bd}', pero el archivo subido es de '{firma_archivo}'."

        return firma_archivo, fila_header_detectada, enc_usado, "OK"

    async def procesar_archivo_bancos_service(
        self, 
        file_bytes: bytes, 
        id_carga: uuid.UUID, 
        id_cuenta: UUID, 
        filename_original: str, 
        usuario_carga: str,
        nombre_banco_bd: str = "PRODUBANCO/GUAYAQUIL/PICHINCHA/SOLIDARIO"
    ) -> Tuple[bool, str]:
        """Ejecuta el pipeline completo del motor ETL Bancario."""
        tz_ec = ZoneInfo("America/Guayaquil")
        ahora_ec = datetime.now(tz_ec)

        try:
            # 1. Auditoría Criptográfica SHA-256
            hash_archivo = hashlib.sha256(file_bytes).hexdigest()
            duplicado = self.repo.verificar_hash_duplicado(hash_archivo, id_carga)
            if duplicado:
                msg = f"⚠️ Omitido por Auditoría: El archivo ya fue procesado anteriormente bajo el nombre '{duplicado.nombre_archivo}' por '{duplicado.created_by}'."
                self.repo.registrar_error_carga(id_carga, msg)
                return False, msg

            # 2. Escaneo y Validación Cruzada
            nombre_banco, fila_header, enc_usado, msg_val = self.analizar_y_validar_archivo_con_usuario(
                file_bytes, nombre_banco_bd, filename_original
            )
            if not nombre_banco:
                self.repo.registrar_error_carga(id_carga, msg_val)
                return False, msg_val

            # 3. Guardar Respaldo en MinIO
            timestamp = ahora_ec.strftime("%Y%m%d_%H%M%S")
            short_hash = hash_archivo[:8]
            safe_filename = f"{nombre_banco}_{timestamp}_{short_hash}_{filename_original}"
            object_name = f"bancos/{ahora_ec.year}/{ahora_ec.month:02d}/{safe_filename}"
            
            url_archivo = upload_to_minio(file_bytes, object_name, 'application/octet-stream')
            if not url_archivo:
                msg_err = "Error al almacenar el respaldo físico en MinIO."
                self.repo.registrar_error_carga(id_carga, msg_err)
                return False, msg_err

            # 4. Actualizar Estado
            self.repo.actualizar_estado_procesando(id_carga, url_archivo, hash_archivo)

            # 5. Carga y Normalización con Pandas
            es_excel = filename_original.lower().endswith(('.xls', '.xlsx'))
            if es_excel:
                df = pd.read_excel(io.BytesIO(file_bytes), skiprows=fila_header, engine='openpyxl')
            else:
                df = pd.read_csv(io.BytesIO(file_bytes), header=fila_header, sep=None, engine='python', encoding=enc_usado, on_bad_lines='skip')
                
                if len(df.columns) <= 1:
                    try:
                        texto_crudo = file_bytes.decode(enc_usado)
                        lineas_limpias = [
                            l[1:-1].replace('""', '"') if l.startswith('"') and l.endswith('"') else l.replace('""', '"')
                            for l in texto_crudo.splitlines()
                        ]
                        file_data_clean = '\n'.join(lineas_limpias).encode(enc_usado)
                        df = pd.read_csv(io.BytesIO(file_data_clean), header=fila_header, sep=None, engine='python', encoding=enc_usado, on_bad_lines='skip')
                    except Exception:
                        pass

            df.columns = [''.join(c for c in unicodedata.normalize('NFD', str(col)) if unicodedata.category(c) != 'Mn').strip().upper() for col in df.columns]

            movimientos_a_insertar = []
            total_leidos = len(df)

            # 6. Transformación por Banco
            if nombre_banco == 'PRODUBANCO':
                for _, row in df.iterrows():
                    if pd.isna(row.get('FECHA')): 
                        continue
                    
                    fecha_str = pd.to_datetime(row['FECHA']).strftime('%Y-%m-%d')
                    
                    signo = str(row.get('+/-', row.get('SIGNO', ''))).strip()
                    tipo_mov = 'CREDITO' if '+' in signo else ('DEBITO' if '-' in signo else 'VARIOS')
                    
                    ref_1 = row.get('REFERENCIA 1')
                    ref_raw = str(ref_1).strip() if pd.notna(ref_1) and str(ref_1).strip() != '' else str(row.get('REFERENCIA', '')).strip()
                    
                    # Sanitizar delimitadores |#| del formato largo de Produbanco
                    info_extra_ref = ""
                    if '|#|' in ref_raw:
                        partes_ref = [p.strip() for p in ref_raw.split('|#|') if p.strip()]
                        ref_principal = partes_ref[0] if partes_ref else ref_raw
                        if len(partes_ref) > 1:
                            info_extra_ref = " / ".join(partes_ref[1:])
                    else:
                        ref_principal = ref_raw

                    ref_2 = row.get('REFERENCIA 2')
                    ref_secundaria = str(ref_2).strip() if pd.notna(ref_2) else str(row.get('REFERENCIA2', '')).strip()
                    
                    concepto_raw = row.get('TRANSACCION')
                    concepto = str(concepto_raw).strip() if pd.notna(concepto_raw) and str(concepto_raw).strip() != '' else str(row.get('DESCRIPCION', '')).strip()
                    
                    cod_tr = row.get('COD TRANSACCION', row.get('REFERENCIA', ''))
                    info_adi_partes = [f"REF2: {ref_secundaria}", f"OF: {row.get('OFICINA', '')}", f"COD TR: {cod_tr}"]
                    if info_extra_ref:
                        info_adi_partes.append(f"DETALLE: {info_extra_ref}")
                    info_adi = " / ".join(info_adi_partes)
                    
                    movimientos_a_insertar.append({
                        'id_movimiento': uuid.uuid4(),
                        'id_carga': id_carga,
                        'id_cuenta': id_cuenta,
                        'fecha_transaccion': fecha_str,
                        'numero_referencia': ref_principal,
                        'concepto': concepto,
                        'monto': limpiar_monto_robusto(row.get('VALOR', 0)),
                        'tipo_movimiento': tipo_mov,
                        'estado': 'Pendiente',
                        'info_adicional': info_adi
                    })

            elif nombre_banco == 'PICHINCHA':
                for _, row in df.iterrows():
                    if pd.isna(row.get('FECHA')): continue
                    fecha_str = pd.to_datetime(row['FECHA'], dayfirst=True).strftime('%Y-%m-%d')
                    tipo_raw = str(row.get('TIPO', 'C')).strip().upper()
                    tipo_mov = 'CREDITO' if tipo_raw == 'C' else 'DEBITO'

                    doc_raw = str(row.get('DOCUMENTO', '')).strip()
                    if doc_raw.endswith('.0'):
                        doc_raw = doc_raw[:-2]
                    
                    info_adi = f"COD: {row.get('CODIGO', '')} / OF: {row.get('OFICINA', '')}"
                    movimientos_a_insertar.append({
                        'id_movimiento': uuid.uuid4(),
                        'id_carga': id_carga,
                        'id_cuenta': id_cuenta,
                        'fecha_transaccion': fecha_str,
                        'numero_referencia': doc_raw,
                        'concepto': str(row.get('CONCEPTO', '')).strip(),
                        'monto': limpiar_monto_robusto(row.get('MONTO', row.get('VALOR', 0))),
                        'tipo_movimiento': tipo_mov,
                        'estado': 'Pendiente',
                        'info_adicional': info_adi
                    })

            elif nombre_banco == 'GUAYAQUIL':
                for _, row in df.iterrows():
                    if pd.isna(row.get('FECHA DE TRANSACCION')): 
                        continue
                    
                    fecha_str = pd.to_datetime(row['FECHA DE TRANSACCION']).strftime('%Y-%m-%d')
                    
                    signo = str(row.get('SIGNO', '')).strip()
                    tipo_raw = str(row.get('TIPO DE MOVIMIENTO', '')).strip().upper()
                    
                    if '+' in signo or 'CREDITO' in tipo_raw or 'DEPOSITO' in tipo_raw:
                        tipo_mov = 'CREDITO'
                    elif '-' in signo or 'DEBITO' in tipo_raw or 'CHEQUE' in tipo_raw:
                        tipo_mov = 'DEBITO'
                    else:
                        tipo_mov = 'VARIOS'
                        
                    doc_raw = str(row.get('DOCUMENTO', '')).strip()
                    if doc_raw.endswith('.0'):
                        doc_raw = doc_raw[:-2]

                    ref_1 = str(row.get('REFERENCIA', '')).strip()
                    ref_2 = str(row.get('REFERENCIA 2', '')).strip()
                    ref_3 = str(row.get('REFERENCIA 3', '')).strip()
                    
                    info_adi = f"AG: {row.get('AGENCIA', '')} / REF: {ref_1} / REF2: {ref_2} / REF3: {ref_3}"
                    
                    movimientos_a_insertar.append({
                        'id_movimiento': uuid.uuid4(),
                        'id_carga': id_carga,
                        'id_cuenta': id_cuenta,
                        'fecha_transaccion': fecha_str,
                        'numero_referencia': doc_raw,
                        'concepto': str(row.get('CONCEPTO', '')).strip(),
                        'monto': limpiar_monto_robusto(row.get('MONTO', row.get('VALOR', 0))),
                        'tipo_movimiento': tipo_mov,
                        'estado': 'Pendiente',
                        'info_adicional': info_adi
                    })

            elif nombre_banco == 'SOLIDARIO':
                for _, row in df.iterrows():
                    if pd.isna(row.get('FECHA_MOVIMIENTO')): continue
                    fecha_str = pd.to_datetime(row['FECHA_MOVIMIENTO'], dayfirst=True).strftime('%Y-%m-%d')
                    doc_clean = str(row.get('NO_DOCUMENTO', '')).replace('No.', '').strip()
                    signo = str(row.get('SIGNO', '')).strip()
                    tipo_mov = 'CREDITO' if '+' in signo else ('DEBITO' if '-' in signo else 'VARIOS')
                    
                    info_adi = f"OF: {row.get('OFICINA', '')} / PAP: {row.get('PAPELETA', '')} / CHEQUE: {row.get('NO_CHEQUE', '')} / SALDO: {row.get('SALDO_DISPONIBLE', '')}"
                    movimientos_a_insertar.append({
                        'id_movimiento': uuid.uuid4(),
                        'id_carga': id_carga,
                        'id_cuenta': id_cuenta,
                        'fecha_transaccion': fecha_str,
                        'numero_referencia': doc_clean,
                        'concepto': str(row.get('DESCRIPCION', '')).strip(),
                        'monto': limpiar_monto_robusto(row.get('MONTO', row.get('VALOR', 0))),
                        'tipo_movimiento': tipo_mov,
                        'estado': 'Pendiente',
                        'info_adicional': info_adi
                    })

            # 7. Persistencia Masiva
            registros_importados = self.repo.insertar_movimientos_ignorar_duplicados(movimientos_a_insertar)
            
            # 8. Cierre de Carga
            msg_final = f"Banco detectado: {nombre_banco}. Leídos: {total_leidos}. Nuevos insertados: {registros_importados}. Duplicados omitidos: {total_leidos - registros_importados}."
            self.repo.finalizar_carga_maestro(id_carga, total_leidos, registros_importados, msg_final)
            
            return True, msg_final

        except Exception as e:
            msg_err = f"Fallo en pipeline ETL: {str(e)}"
            self.repo.registrar_error_carga(id_carga, msg_err)
            return False, msg_err
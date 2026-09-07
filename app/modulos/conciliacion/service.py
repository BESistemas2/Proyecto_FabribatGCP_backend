# app/modulos/conciliacion/service.py
import pandas as pd
from typing import List, Dict, Any, Tuple
from app.modulos.conciliacion.bc_service import BusinessCentralReaderService

class ConciliacionService:
    @staticmethod
    def obtener_y_preparar_mayor(fecha_inicio: str, fecha_fin: str) -> Tuple[pd.DataFrame, str]:
        """
        Extrae los datos crudos del ERP y aplica las reglas de normalización contable.
        """
        raw_entries, error = BusinessCentralReaderService.consultar_mayor_bancos(fecha_inicio, fecha_fin)
        if error:
            return pd.DataFrame(), error

        if not raw_entries:
            return pd.DataFrame(), None

        df = pd.DataFrame(raw_entries)

        # 1. Asegurar tipos numéricos [6]
        df['debitAmount'] = pd.to_numeric(df.get('debitAmount', 0)).fillna(0)
        df['creditAmount'] = pd.to_numeric(df.get('creditAmount', 0)).fillna(0)

        # 2. Lógica del Mayor Editado: (+Debe -Haber) [6]
        df['monto'] = df['debitAmount'] - df['creditAmount']
        df['postingDate'] = pd.to_datetime(df['postingDate']).dt.strftime('%Y-%m-%d')
        df['externalDocumentNo'] = df['externalDocumentNo'].astype(str).str.strip().fillna('')

        # 3. Regla Contable: Agrupar créditos (pagos) por Documento Externo para evitar duplicaciones [6]
        mayor_editado = df.groupby(['gLAccountNo', 'externalDocumentNo', 'postingDate']).agg({
            'monto': 'sum',
            'description': 'first',
            'documentNo': 'first'
        }).reset_index()

        mayor_editado = mayor_editado.sort_values(by=['gLAccountNo', 'postingDate']).reset_index(drop=True)
        return mayor_editado, None
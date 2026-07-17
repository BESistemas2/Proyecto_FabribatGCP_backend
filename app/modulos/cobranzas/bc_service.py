# app/modulos/cobranzas/bc_service.py
import requests
from datetime import datetime
from app.core.config import BC_CONFIG
from app.core.bc_auth import get_oauth_token, resolve_company_info
from app.core.database import get_db_session
from app.core.models import Comprobante, PagoFactura, Cliente

class BusinessCentralSyncService:

    @staticmethod
    def sincronizar_comprobante_journal(id_comprobante: str):
        """
        Extrae un comprobante de la base de datos local mediante el ORM,
        construye las líneas del Diario de Recaudación (Cash Receipt Journal)
        y las inyecta en Dynamics 365 Business Central de forma masiva.
        """
        session = get_db_session()
        
        try:
            # 1. Recuperar el comprobante con carga ansiosa de sus relaciones usando el ORM
            comprobante = session.query(Comprobante).filter(Comprobante.idComprobante == id_comprobante).first()
            if not comprobante:
                return False, f"El comprobante {id_comprobante} no existe en la base de datos local."
                
            if comprobante.syncBC_Status == 'COMPLETADO':
                return True, f"El comprobante {comprobante.numComprobante} ya fue sincronizado previamente."

            # 2. Conseguir Token de Azure de forma transparente
            token, err_token = get_oauth_token()
            if err_token:
                return False, f"Fallo de autenticación en Azure: {err_token}"

            # 3. Resolver la Empresa Activa en BC
            comp_info, err_company = resolve_company_info(token)
            if err_company:
                return False, f"Fallo al resolver la empresa en BC: {err_company}"
            
            company_id, _ = comp_info

            # 4. Construir la cabecera base de las URLs de Dynamics
            # Usamos el diario general (genJournalBatches) de la API v2.0 estándar de Microsoft
            base_url = f"https://api.businesscentral.dynamics.com/v2.0/{BC_CONFIG['tenant_id']}/Production/api/v2.0"
            journal_line_url = f"{base_url}/companies({company_id})/genJournalBatches(journalTemplateName='{BC_CONFIG['journal_template']}',name='{BC_CONFIG['journal_batch']}')/journalLines"

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }

            # 5. Iterar sobre las facturas cruzadas para generar las líneas contables
            lineas_enviadas = 0
            
            for pago in comprobante.pagos_facturas:
                # Buscamos el código real del cliente en Dynamics (bcCustomerNo)
                cliente = session.query(Cliente).filter(Cliente.idCliente == pago.idCliente).first()
                customer_no = cliente.bcCustomerNo if cliente else "C-CON-GENERAL"

                # Payload estándar que exige el diario contable de Business Central
                # Nota: En BC, los cobros a clientes se envían con signo NEGATIVO para reducir su saldo pendiente
                payload_linea = {
                    "postingDate": comprobante.fecha.strftime("%Y-%m-%d"),
                    "documentType": "Payment",
                    "documentNo": comprobante.numComprobante,
                    "accountType": "Customer",
                    "accountNo": customer_no,
                    "amount": float(pago.montoAsignado) * -1, 
                    "description": f"Cobro Recibo {comprobante.numComprobante} - Fact {pago.factura}",
                    "balAccountType": "Bank Account",
                    "balAccountNo": "BANCO_TRANSITORIO", # Aquí irá la cuenta contable de tu banco
                    "appliesToDocType": "Invoice",
                    "appliesToDocNo": pago.factura
                }

                # Petición HTTP POST individual por línea de factura liquidada
                response = requests.post(journal_line_url, json=payload_linea, headers=headers, timeout=20)
                
                if not response.ok:
                    raise RuntimeError(f"Error en BC al procesar línea de factura {pago.factura}: {response.status_code} - {response.text}")
                
                lineas_enviadas += 1

            # 6. Si todas las líneas entraron al diario del ERP con éxito, actualizamos el estado local
            comprobante.syncBC_Status = 'COMPLETADO'
            comprobante.syncBC_Date = datetime.now()
            session.commit()

            return True, f"Sincronización exitosa. {lineas_enviadas} líneas inyectadas en el lote {BC_CONFIG['journal_batch']}."

        except Exception as e:
            session.rollback()
            # Si algo falla, el comprobante se marca como fallido para auditoría en AppSheet
            if 'comprobante' in locals() and comprobante:
                comprobante.syncBC_Status = 'ERROR'
                session.commit()
            return False, f"Excepción crítica durante la sincronización a ERP: {str(e)}"
        finally:
            session.close()
# app/modulos/cobranzas/service.py
from app.core.database import get_cobranzas_session
from app.core.models import Cliente, Factura, Comprobante, ValorRecibido, PagoFactura, DetallePago, CierreCaja
from sqlalchemy.orm import joinedload
import uuid
from datetime import datetime
from sqlalchemy.sql import func

class CobranzasService:

    @staticmethod
    def obtener_facturas_pendientes(id_cliente=None, ruc=None):
        """
        Consulta las facturas que tienen un saldo pendiente mayor a 0.00.
        Permite filtrar dinámicamente por la ID interna del cliente o por su RUC.
        """
        session = get_cobranzas_session()
        try:
            # Iniciamos la consulta base filtrando solo las de saldo pendiente activo[cite: 1]
            query = session.query(Factura).options(joinedload(Factura.cliente)).filter(Factura.saldoPendiente > 0) #[cite: 1]
            
            # Si nos pasan la ID única de AppSheet, filtramos directamente[cite: 1]
            if id_cliente:
                query = query.filter(Factura.idCliente == id_cliente) #[cite: 1]
                
            # Si nos pasan el RUC, hacemos un JOIN con la tabla Clientes para buscarlo[cite: 1]
            if ruc:
                query = query.join(Cliente).filter(Cliente.ruc == ruc.strip()) #[cite: 1]
            
            facturas = query.order_by(Factura.fechaVencimiento.asc()).all() #[cite: 1]
            
            # Formateamos la respuesta en un diccionario limpio para el API JSON
            resultado = []
            for f in facturas:
                resultado.append({
                    "idFactura": f.idFactura, #[cite: 1]
                    "bcDocumentNo": f.bcDocumentNo, #[cite: 1]
                    "tipoDocumento": f.tipoDocumento, #[cite: 1]
                    "fechaEmision": f.fechaEmision.strftime('%Y-%m-%d') if f.fechaEmision else None, #[cite: 1]
                    "fechaVencimiento": f.fechaVencimiento.strftime('%Y-%m-%d') if f.fechaVencimiento else None, #[cite: 1]
                    "montoTotal": float(f.montoTotal), #[cite: 1]
                    "saldoPendiente": float(f.saldoPendiente), #[cite: 1]
                    "cliente": {
                        "idCliente": f.cliente.idCliente, #[cite: 1]
                        "ruc": f.cliente.ruc, #[cite: 1]
                        "nombre": f.cliente.nombre #[cite: 1]
                    } if f.cliente else None
                })
            return resultado
        finally:
            session.close()

    @staticmethod
    def procesar_comprobante_completo(datos):
        """
        Procesa de forma transaccional la creación de un Comprobante,
        registra los valores recibidos (efectivo/cheques) y realiza el
        desglose matemático para rebajar los saldos de las facturas.
        """
        session = get_cobranzas_session()
        try:
            # 1. Extraer bloques del payload de AppSheet
            cabecera = datos.get("cabecera", {})
            valores = datos.get("valores", [])      # Efectivo, cheques, transferencias
            pagos = datos.get("pagos", [])          # Facturas a las que se aplica el dinero
            
            id_comprobante = cabecera.get("idComprobante")
            if not id_comprobante:
                return None, "Falta el idComprobante en la cabecera."

            # 2. Registrar la Cabecera del Comprobante
            nuevo_comprobante = Comprobante(
                idComprobante=id_comprobante,
                puntoVenta=cabecera.get("puntoVenta"),
                puntoEmision=cabecera.get("puntoEmision"),
                numComprobante=cabecera.get("numComprobante"),
                fecha=datetime.strptime(cabecera.get("fecha"), "%Y-%m-%d %H:%M:%S") if cabecera.get("fecha") else func.now(),
                rucCliente=cabecera.get("rucCliente"),
                idCliente=cabecera.get("idCliente"),
                cliente=cabecera.get("cliente"),
                recibi=cabecera.get("recibi", 0.0),
                recibiLetras=cabecera.get("recibiLetras", ""),
                totalChequeTransfer=cabecera.get("totalChequeTransfer", 0.0),
                efectivo=cabecera.get("efectivo", 0.0),
                idEstado=cabecera.get("idEstado", 1), # 1 = Registrado / Pendiente Tesorería
                createdBy=cabecera.get("createdBy"),
                createdOn=func.now(),
                urlPdfComprobante2="" # Se generará en la capa de infraestructura posteriormente
            )
            session.add(nuevo_comprobante)

            # 3. Registrar los Detalles de Valores Recibidos
            for idx, val in enumerate(valores):
                nuevo_valor = ValorRecibido(
                    idValoresRecibidos=val.get("idValoresRecibidos"),
                    numChequeTransfer=val.get("numChequeTransfer", "EFECTIVO"),
                    banco=val.get("banco", "EFECTIVO"),
                    idInstitucion=val.get("idInstitucion"),
                    valor=val.get("valor", 0.0),
                    fechaTransaccion=datetime.strptime(val.get("fechaTransaccion"), "%Y-%m-%d").date() if val.get("fechaTransaccion") else None,
                    idComprobante=id_comprobante,
                    createdBy=cabecera.get("createdBy"),
                    createdOn=func.now(),
                    tipo=val.get("tipo", "EFECTIVO"),
                    orden=idx + 1
                )
                session.add(nuevo_valor)

            # 4. Algoritmo de Desglose Matemático y Rebaja de Saldos
            for idx, pago in enumerate(pagos):
                id_factura = pago.get("idFactura")
                monto_a_abonar = float(pago.get("montoAsignado", 0.0))
                
                if monto_a_abonar <= 0:
                    continue

                # Bloqueamos la fila de la factura (FOR UPDATE) para evitar condiciones de carrera
                factura = session.query(Factura).filter(Factura.idFactura == id_factura).with_for_update().first()
                
                if not factura:
                    raise ValueError(f"La factura con ID {id_factura} no existe en el catálogo.")

                # Verificación de sobrepago
                if monto_a_abonar > float(factura.saldoPendiente):
                    raise ValueError(f"El abono de ${monto_a_abonar} supera el saldo pendiente (${factura.saldoPendiente}) de la factura {factura.bcDocumentNo}.")

                # Rebajamos el saldo usando el ORM de forma directa
                factura.saldoPendiente = float(factura.saldoPendiente) - monto_a_abonar
                
                # Registramos el mapeo en la tabla PagosFacturas
                id_pago_factura = pago.get("idPagoFactura")
                nuevo_pago = PagoFactura(
                    idPagoFactura=id_pago_factura,
                    idFactura=id_factura,
                    factura=pago.get("factura"),
                    facturaOriginal=pago.get("facturaOriginal"),
                    montoAsignado=monto_a_abonar,
                    idComprobante=id_comprobante,
                    createdBy=cabecera.get("createdBy"),
                    createdOn=func.now(),
                    abono=pago.get("abono", True),
                    idCliente=cabecera.get("idCliente"),
                    orden=idx + 1
                )
                session.add(nuevo_pago)

                # Guardamos la traza de auditoría fina en DetallePagos
                traza_detalle = DetallePago(
                    idDetallePago=f"DET-{id_pago_factura}",
                    idPagoFactura=id_pago_factura,
                    idValoresRecibidos=valores[0].get("idValoresRecibidos") if valores else "EFECTIVO",
                    idComprobante=id_comprobante,
                    idFactura=id_factura,
                    montoApplied=monto_a_abonar,
                    origen='MIDDLEWARE'
                )
                session.add(traza_detalle)

            # 5. Si todo el desglose matemático es correcto, impactamos la BD
            session.commit()
            return {"idComprobante": id_comprobante, "status": "Completado", "mensaje": "Cobros aplicados correctamente."}, None

        except Exception as e:
            # Si una sola factura falla o el monto no cuadra, deshacemos todo el lote
            session.rollback()
            return None, f"Transacción abortada debido a un error: {str(e)}"
        finally:
            session.close()


    @staticmethod
    def generar_cierre_caja(id_usuario: str, caja_codigo: str, observaciones: str = None):
        """
        Consolida de manera transaccional todos los comprobantes recaudados por un usuario
        que aún no pertenezcan a ningún cierre de caja. Totaliza efectivo, cheques,
        crea el registro de CierreCaja y vincula los comprobantes a este.
        """
        session = get_cobranzas_session()
        try:
            # 1. Buscar todos los comprobantes pendientes de cierre para este usuario
            # Filtramos por creador (createdBy) y que no tengan un idCierreCaja asignado
            comprobantes_pendientes = session.query(Comprobante).filter(
                Comprobante.createdBy == id_usuario,
                Comprobante.idCierreCaja == None
            ).with_for_update().all() # Bloqueamos las filas para evitar modificaciones simultáneas

            if not comprobantes_pendientes:
                return None, f"No se encontraron comprobantes pendientes de cierre para el usuario {id_usuario}."

            # 2. Inicializar acumuladores matemáticos
            total_efectivo = 0.0
            total_cheques = 0.0
            id_cierre_caja = f"CC-{uuid.uuid4().hex[:8].upper()}-{datetime.now().strftime('%y%m%d')}"

            # 3. Sumarizar montos de cada comprobante localmente
            for comp in comprobantes_pendientes:
                total_efectivo += float(comp.efectivo or 0.0)
                total_cheques += float(comp.totalChequeTransfer or 0.0)
                
                # Vinculamos de inmediato el comprobante al ID del nuevo cierre
                comp.idCierreCaja = id_cierre_caja

            total_general = total_efectivo + total_cheques

            # 4. Crear el registro maestro del Cierre de Caja
            nuevo_cierre = CierreCaja(
                idCierreCaja=id_cierre_caja,
                fechaCierre=datetime.now().date(),
                idUsuarioCierre=id_usuario,
                createdOn=func.now(),
                totalEfectivo=total_efectivo,
                totalCheques=total_cheques,
                totalGeneral=total_general,
                estadoCierre='CERRADO',
                observaciones=observaciones,
                estadoIntegracion='PENDIENTE',
                caja=caja_codigo,
                codigoCierre=id_cierre_caja
            )

            session.add(nuevo_cierre)
            
            # Guardamos todos los cambios en la Base de Datos
            session.commit()

            return {
                "idCierreCaja": id_cierre_caja,
                "totalEfectivo": total_efectivo,
                "totalCheques": total_cheques,
                "totalGeneral": total_general,
                "comprobantesAsociados": len(comprobantes_pendientes),
                "estado": "CERRADO"
            }, None

        except Exception as e:
            session.rollback()
            return None, f"Error crítico durante la transacción de cierre de caja: {str(e)}"
        finally:
            session.close()
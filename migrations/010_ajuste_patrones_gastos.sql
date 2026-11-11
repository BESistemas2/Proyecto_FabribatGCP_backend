-- =====================================================================
-- Migración 010 · Ajuste del catálogo de gastos bancarios (octubre 2026)
-- - Nuevo: "Comis.sci sector publico" (Banco Guayaquil) viene en la factura del banco.
-- - Se desactiva "Comision financ. voucher": Tesorería la registra en BC con cada
--   cierre de caja y cruza uno a uno por referencia; no es parte de la factura mensual.
-- Idempotente.
-- =====================================================================
BEGIN;

INSERT INTO conciliacion.patrones_gasto_bancario (banco, patron, tipo_coincidencia, categoria, descripcion, creado_por)
VALUES ('GUAYAQUIL', 'COMIS.SCI', 'EMPIEZA', 'Comisión', 'Comis.sci sector publico', 'migracion_010')
ON CONFLICT (banco, patron) DO NOTHING;

UPDATE conciliacion.patrones_gasto_bancario
   SET activo = FALSE, updated_at = now(),
       descripcion = 'Comision financ. voucher (desactivado: se registra con cada cierre de caja)'
 WHERE banco = 'GUAYAQUIL' AND patron = 'COMISION FINANC. VOUCHER';

COMMIT;

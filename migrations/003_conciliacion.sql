-- =====================================================================
-- Migración 003 · Conciliación
-- Ejecutar después de 001 y 002.
-- =====================================================================

-- Diagnóstico (solo lectura). Si alguna devuelve filas, avísame antes de ejecutar:
--   Actas duplicadas por cuenta y periodo:
--     SELECT id_cuenta, periodo, COUNT(*) FROM conciliacion.actas GROUP BY 1, 2 HAVING COUNT(*) > 1;
--   EntryNo de BC repetidos en el espejo:
--     SELECT entry_no_bc, COUNT(*) FROM bancos.movimientos_erp
--      WHERE entry_no_bc IS NOT NULL GROUP BY 1 HAVING COUNT(*) > 1;

BEGIN;

-- 1) Rango de fechas del acta (antes solo se guardaba el periodo AAAA-MM)
ALTER TABLE conciliacion.actas
  ADD COLUMN IF NOT EXISTS fecha_inicio DATE,
  ADD COLUMN IF NOT EXISTS fecha_fin    DATE;

UPDATE conciliacion.actas
   SET fecha_inicio = to_date(periodo || '-01', 'YYYY-MM-DD'),
       fecha_fin    = (to_date(periodo || '-01', 'YYYY-MM-DD') + INTERVAL '1 month - 1 day')::date
 WHERE fecha_inicio IS NULL AND periodo ~ '^\d{4}-\d{2}$';

-- 2) Una sola acta por cuenta y periodo
ALTER TABLE conciliacion.actas DROP CONSTRAINT IF EXISTS uq_actas_cuenta_periodo;
ALTER TABLE conciliacion.actas ADD CONSTRAINT uq_actas_cuenta_periodo UNIQUE (id_cuenta, periodo);

-- 3) Espejo de BC: EntryNo único (lo exige el ON CONFLICT) e índices de consulta
CREATE UNIQUE INDEX IF NOT EXISTS uq_movimientos_erp_entry_no ON bancos.movimientos_erp (entry_no_bc);
CREATE INDEX IF NOT EXISTS ix_movimientos_erp_banco_estado_fecha
  ON bancos.movimientos_erp (codigo_banco_bc, estado, posting_date);
CREATE INDEX IF NOT EXISTS ix_movimientos_erp_mov_banco ON bancos.movimientos_erp (id_movimiento_banco);

COMMIT;

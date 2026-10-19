-- =====================================================================
-- Migración 001 · Módulo Bancos (ETL de extractos)
-- Ejecutar UNA vez en PostgreSQL (fabribat_db) ANTES de desplegar la
-- versión del backend de la rama revision/bancos.
--
--   psql -h <host> -U <usuario> -d fabribat_db -f migrations/001_bancos_etl.sql
--
-- Todo va en una transacción: si algún paso falla, no se aplica nada.
-- =====================================================================

-- 0) DIAGNÓSTICO (opcional, solo lectura) --------------------------------
-- Constraint/índices actuales de bancos.movimientos:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'bancos.movimientos'::regclass;
-- Duplicados que impedirían crear la nueva restricción única:
--   SELECT id_cuenta, fecha_transaccion, numero_referencia,
--          CASE WHEN tipo_movimiento = 'DEBITO' THEN -ABS(monto) ELSE ABS(monto) END AS monto_firmado,
--          COUNT(*)
--     FROM bancos.movimientos
--    GROUP BY 1, 2, 3, 4 HAVING COUNT(*) > 1;

BEGIN;

-- 1) Montos con signo: CREDITO > 0, DEBITO < 0 (misma convención que BC)
UPDATE bancos.movimientos
   SET monto = -ABS(monto)
 WHERE tipo_movimiento = 'DEBITO' AND monto > 0;

UPDATE bancos.movimientos
   SET monto = ABS(monto)
 WHERE tipo_movimiento = 'CREDITO' AND monto < 0;

-- 2) Restricción de duplicados por cuenta (el ETL hace ON CONFLICT DO NOTHING sobre ella)
ALTER TABLE bancos.movimientos DROP CONSTRAINT IF EXISTS unq_movimiento_banco;
DROP INDEX IF EXISTS bancos.unq_movimiento_banco;
ALTER TABLE bancos.movimientos
  ADD CONSTRAINT unq_movimiento_banco
  UNIQUE (id_cuenta, fecha_transaccion, numero_referencia, monto);

-- 3) Índice para las consultas de pendientes / conciliación
CREATE INDEX IF NOT EXISTS ix_movimientos_cuenta_estado_fecha
  ON bancos.movimientos (id_cuenta, estado, fecha_transaccion);

-- 4) Resumen del extracto en la carga (lo llena el ETL)
ALTER TABLE bancos.cargas_extractos
  ADD COLUMN IF NOT EXISTS banco_detectado VARCHAR(30),
  ADD COLUMN IF NOT EXISTS fecha_desde     DATE,
  ADD COLUMN IF NOT EXISTS fecha_hasta     DATE,
  ADD COLUMN IF NOT EXISTS saldo_inicial   NUMERIC(18, 2),
  ADD COLUMN IF NOT EXISTS saldo_final     NUMERIC(18, 2);

-- 5) Normalizar estados heredados del endpoint de AppSheet (eliminado)
UPDATE bancos.cargas_extractos SET estado = 'Completado' WHERE estado = 'PROCESADO';

COMMIT;

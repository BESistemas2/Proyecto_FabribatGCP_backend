-- =====================================================================
-- Migración 005 · Auto-Match programado
-- Guarda la última ejecución automática de cada cuenta y su resultado.
-- Idempotente.
-- =====================================================================
BEGIN;

ALTER TABLE bancos.cuentas_bancarias
  ADD COLUMN IF NOT EXISTS auto_match_ultima_ejecucion TIMESTAMP,
  ADD COLUMN IF NOT EXISTS auto_match_ultimo_resultado VARCHAR(500);

-- Valores válidos de la configuración (los que ofrece la pantalla de Cuentas)
UPDATE bancos.cuentas_bancarias SET auto_match_frecuencia = 'DIARIA'
 WHERE auto_match_frecuencia IS NULL OR auto_match_frecuencia NOT IN ('DIARIA', 'SEMANAL', 'MENSUAL');
UPDATE bancos.cuentas_bancarias SET auto_match_intervalo_minutos = LEAST(GREATEST(COALESCE(auto_match_intervalo_minutos, 1440), 15), 1440);

COMMIT;

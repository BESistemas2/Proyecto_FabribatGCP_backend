-- =====================================================================
-- Migración 006 · Envío por correo del Auto-Match programado
-- Por cuenta: enviar el informe a los usuarios RECEPTOR_MAIL y CONTABILIDAD.
-- Idempotente.
-- =====================================================================
ALTER TABLE bancos.cuentas_bancarias
  ADD COLUMN IF NOT EXISTS auto_match_enviar_correo BOOLEAN NOT NULL DEFAULT true;

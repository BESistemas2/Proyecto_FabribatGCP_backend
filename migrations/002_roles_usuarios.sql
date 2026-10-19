-- =====================================================================
-- Migración 002 · Roles de usuario
-- Roles válidos: ADMIN, CONTABILIDAD, BASICO, RECEPTOR_MAIL
-- (los mismos de app/modulos/identidad/schemas.py y src/types/identity.ts)
-- =====================================================================

-- Diagnóstico (solo lectura):
--   SELECT rol, COUNT(*) FROM identidad.usuarios GROUP BY rol;

BEGIN;

-- Roles antiguos del formulario anterior -> roles definitivos
UPDATE identidad.usuarios SET rol = 'CONTABILIDAD' WHERE rol = 'TESORERO';
UPDATE identidad.usuarios SET rol = 'BASICO'       WHERE rol = 'ANALISTA' OR rol IS NULL;

-- La base solo acepta los 4 roles válidos
ALTER TABLE identidad.usuarios DROP CONSTRAINT IF EXISTS ck_usuarios_rol;
ALTER TABLE identidad.usuarios
  ADD CONSTRAINT ck_usuarios_rol CHECK (rol IN ('ADMIN', 'CONTABILIDAD', 'BASICO', 'RECEPTOR_MAIL'));

COMMIT;

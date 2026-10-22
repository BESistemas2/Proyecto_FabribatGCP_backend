-- =====================================================================
-- Migración 007 · Destinatarios del correo del Auto-Match por cuenta
-- Cada cuenta elige qué usuarios (de cualquier rol) reciben el informe.
-- Para no cambiar el comportamiento actual, las cuentas con el envío activo
-- arrancan con los usuarios activos de rol CONTABILIDAD y RECEPTOR_MAIL.
-- Idempotente.
-- =====================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS bancos.cuentas_destinatarios (
    id_cuenta  UUID NOT NULL REFERENCES bancos.cuentas_bancarias(id_cuenta) ON DELETE CASCADE,
    id_usuario UUID NOT NULL REFERENCES identidad.usuarios(id_usuario) ON DELETE CASCADE,
    PRIMARY KEY (id_cuenta, id_usuario)
);

INSERT INTO bancos.cuentas_destinatarios (id_cuenta, id_usuario)
SELECT c.id_cuenta, u.id_usuario
  FROM bancos.cuentas_bancarias c
  CROSS JOIN identidad.usuarios u
 WHERE c.auto_match_enviar_correo
   AND u.activo AND u.rol IN ('CONTABILIDAD', 'RECEPTOR_MAIL')
   AND NOT EXISTS (SELECT 1 FROM bancos.cuentas_destinatarios d WHERE d.id_cuenta = c.id_cuenta)
ON CONFLICT DO NOTHING;

COMMIT;

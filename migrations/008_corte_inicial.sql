-- =====================================================================
-- Migración 008 · Corte inicial por cuenta
-- Punto de partida de la conciliación en el sistema: saldos a la fecha de
-- corte (última acta cerrada fuera del sistema) y las partidas que seguían
-- en tránsito (cheques no cobrados, notas no registradas, etc.).
-- Una sola fila por cuenta. Idempotente.
-- =====================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS conciliacion.cortes_iniciales (
    id_corte        UUID PRIMARY KEY,
    id_cuenta       UUID NOT NULL UNIQUE REFERENCES bancos.cuentas_bancarias(id_cuenta) ON DELETE CASCADE,
    fecha_corte     DATE NOT NULL,
    saldo_banco     NUMERIC(18, 2) NOT NULL,
    saldo_libros    NUMERIC(18, 2) NOT NULL,
    observacion     VARCHAR(500),
    creado_por      VARCHAR(100) NOT NULL,
    actualizado_por VARCHAR(100),
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conciliacion.partidas_iniciales (
    id_partida   SERIAL PRIMARY KEY,
    id_corte     UUID NOT NULL REFERENCES conciliacion.cortes_iniciales(id_corte) ON DELETE CASCADE,
    -- CHEQUE_NO_COBRADO | NOTA_DEBITO_NO_REGISTRADA | NOTA_CREDITO_NO_REGISTRADA
    -- | PENDIENTE_ACREDITAR | PENDIENTE_DEBITAR
    categoria    VARCHAR(40) NOT NULL,
    fecha        DATE NOT NULL,
    detalle      VARCHAR(255) NOT NULL,
    referencia   VARCHAR(100),
    valor        NUMERIC(18, 2) NOT NULL CHECK (valor > 0),
    observacion  VARCHAR(255),
    estado       VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',   -- PENDIENTE | CRUZADA
    created_at   TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_partidas_iniciales_corte ON conciliacion.partidas_iniciales (id_corte);

COMMIT;

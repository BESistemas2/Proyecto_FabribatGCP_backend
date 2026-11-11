-- =====================================================================
-- Migración 009 · Gastos bancarios
-- Catálogo de patrones para reconocer comisiones, tarifas e IVA cobrados por
-- el banco. Los débitos que coinciden se agrupan por mes y se concilian
-- contra UN asiento de BC por el total del mes (regla REGLA_GASTOS_BANCARIOS).
-- `banco` se compara con el nombre del banco de la cuenta (contiene).
-- `tipo_coincidencia`: EMPIEZA | CONTIENE | REGEX, sobre el concepto del
-- movimiento en mayúsculas, sin tildes y con espacios colapsados.
-- Idempotente.
-- =====================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS conciliacion.patrones_gasto_bancario (
    id_patron          SERIAL PRIMARY KEY,
    banco              VARCHAR(50)  NOT NULL,
    patron             VARCHAR(150) NOT NULL,
    tipo_coincidencia  VARCHAR(10)  NOT NULL DEFAULT 'EMPIEZA'
                       CHECK (tipo_coincidencia IN ('EMPIEZA', 'CONTIENE', 'REGEX')),
    categoria          VARCHAR(30)  NOT NULL DEFAULT 'Comisión',
    descripcion        VARCHAR(150),
    activo             BOOLEAN      NOT NULL DEFAULT TRUE,
    creado_por         VARCHAR(100) NOT NULL DEFAULT 'migracion_009',
    created_at         TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at         TIMESTAMP    NOT NULL DEFAULT now(),
    UNIQUE (banco, patron)
);

INSERT INTO conciliacion.patrones_gasto_bancario (banco, patron, tipo_coincidencia, categoria, descripcion) VALUES
    ('GUAYAQUIL', 'COM.PAGO SERVICIOS',            'EMPIEZA',  'Comisión', 'Com.pago servicios'),
    ('GUAYAQUIL', 'COMIS.PGO.PROVEEDORES',         'EMPIEZA',  'Comisión', 'Comis.pgo.proveedores'),
    ('GUAYAQUIL', 'COMIS.SPI PROVEEDORES',         'EMPIEZA',  'Comisión', 'Comis.spi proveedores'),
    ('GUAYAQUIL', 'COMIS.SWIFT',                   'EMPIEZA',  'Comisión', 'Comis.swift Europa / América proveedores'),
    ('GUAYAQUIL', 'COMISION CHEQUE DEV',           'EMPIEZA',  'Comisión', 'Comisión cheque devuelto'),
    ('GUAYAQUIL', 'COMISION CH.DEVUELTO',          'EMPIEZA',  'Comisión', 'Comisión ch.devuelto transferido (por validar)'),
    ('GUAYAQUIL', 'COMISION FINANC. VOUCHER',      'EMPIEZA',  'Comisión', 'Comisión financ. voucher (por validar)'),
    ('GUAYAQUIL', 'COMISION TRANF.BCE',            'EMPIEZA',  'Comisión', 'Comisión transferencia BCE'),
    ('GUAYAQUIL', '^IMP [0-9]+% IVA',              'REGEX',    'IVA',      'Imp 15% IVA iva servicios (cualquier tarifa)'),
    ('GUAYAQUIL', 'TARIFA RECAUD',                 'EMPIEZA',  'Tarifa',   'Tarifa recaud x deb.autom.'),
    ('GUAYAQUIL', '^TARIFA PAGO ?/ ?NOMINA',       'REGEX',    'Tarifa',   'Tarifa pago / nómina'),
    ('PICHINCHA', 'COSTO IVA CASH-RECAUDACIONES',  'CONTIENE', 'IVA',      'Costo IVA cash recaudaciones'),
    ('PICHINCHA', 'COSTO OPER CASH-RECAUDACIONES', 'CONTIENE', 'Comisión', 'Costo operativo cash recaudaciones'),
    ('PICHINCHA', 'IVA COBRADO',                   'EMPIEZA',  'IVA',      'IVA cobrado'),
    ('PICHINCHA', '-COMISION-PAG-',                'CONTIENE', 'Comisión', 'Comisión de pagos (XXXX-COMISION-PAG-RUC)')
ON CONFLICT (banco, patron) DO NOTHING;

COMMIT;

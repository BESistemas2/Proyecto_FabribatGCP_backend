-- =====================================================================
-- Migración 004 · bancos.cargas_extractos: clave primaria e índices
-- La tabla tenía un índice único en id_carga, pero sin restricción PRIMARY KEY.
-- Es idempotente: se puede ejecutar más de una vez.
-- =====================================================================
BEGIN;

ALTER TABLE bancos.cargas_extractos ALTER COLUMN id_carga SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conrelid = 'bancos.cargas_extractos'::regclass AND contype = 'p') THEN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'bancos' AND indexname = 'pk_cargas_bancarias') THEN
      ALTER TABLE bancos.cargas_extractos
        ADD CONSTRAINT pk_cargas_extractos PRIMARY KEY USING INDEX pk_cargas_bancarias;
    ELSE
      ALTER TABLE bancos.cargas_extractos ADD CONSTRAINT pk_cargas_extractos PRIMARY KEY (id_carga);
    END IF;
  END IF;
END $$;

-- Búsqueda de archivos ya cargados (deduplicación por hash)
CREATE INDEX IF NOT EXISTS ix_cargas_extractos_hash ON bancos.cargas_extractos (hash_archivo);

COMMIT;

-- Opcional (requiere que no haya id_institucion huérfanos):
-- ALTER TABLE bancos.cargas_extractos
--   ADD CONSTRAINT fk_cargas_institucion FOREIGN KEY (id_institucion)
--   REFERENCES bancos.instituciones(id_institucion) ON UPDATE CASCADE ON DELETE RESTRICT;

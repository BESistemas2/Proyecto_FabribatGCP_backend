-- =====================================================================
-- Migración 011 · Módulo de auditoría (fase 1: base)
--
-- 1. public.schema_migrations: qué migraciones se ejecutaron (el backend avisa al
--    arrancar si falta alguna). Desde aquí, cada migración termina insertando su fila.
-- 2. auditoria.acciones: catálogo de acciones (el mismo que usará el módulo de
--    administración para los permisos).
-- 3. auditoria.eventos: tabla de solo inserción, particionada por mes y encadenada con
--    SHA-256 (cada evento guarda el hash del anterior).
-- 4. Trigger genérico que registra los cambios hechos directo en la base (Portainer,
--    psql) en las tablas críticas. Los cambios hechos por la aplicación no pasan por
--    aquí: la aplicación registra sus propios eventos de negocio.
--
-- Requiere PostgreSQL 13 o superior. Idempotente.
-- Ejecutar desde el archivo:
--   docker exec -i postgres_db psql -U <usuario> -d fabribat_db -v ON_ERROR_STOP=1 < migrations/011_auditoria.sql
-- =====================================================================
BEGIN;

CREATE SCHEMA IF NOT EXISTS auditoria;

-- ---------------------------------------------------------------------
-- 1. Control de migraciones
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    numero        INTEGER PRIMARY KEY,
    archivo       TEXT NOT NULL,
    aplicada_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    aplicada_por  TEXT NOT NULL DEFAULT current_user,
    nota          TEXT
);

-- Las migraciones anteriores se registran solo si se nota que ya corrieron
DO $$
DECLARE
    nota CONSTANT TEXT := 'registrada por 011 (ejecutada antes del control de migraciones)';
BEGIN
    IF to_regclass('bancos.cuentas_bancarias') IS NOT NULL THEN
        INSERT INTO public.schema_migrations (numero, archivo, nota) VALUES
            (1, '001_bancos_etl.sql', nota),
            (2, '002_roles_usuarios.sql', nota),
            (3, '003_conciliacion.sql', nota),
            (4, '004_cargas_extractos.sql', nota),
            (5, '005_auto_match.sql', nota),
            (6, '006_auto_match_correo.sql', nota)
        ON CONFLICT (numero) DO NOTHING;
    END IF;
    IF to_regclass('bancos.cuentas_destinatarios') IS NOT NULL THEN
        INSERT INTO public.schema_migrations (numero, archivo, nota)
        VALUES (7, '007_destinatarios_por_cuenta.sql', nota) ON CONFLICT (numero) DO NOTHING;
    END IF;
    IF to_regclass('conciliacion.cortes_iniciales') IS NOT NULL THEN
        INSERT INTO public.schema_migrations (numero, archivo, nota)
        VALUES (8, '008_corte_inicial.sql', nota) ON CONFLICT (numero) DO NOTHING;
    END IF;
    IF to_regclass('conciliacion.patrones_gasto_bancario') IS NOT NULL THEN
        INSERT INTO public.schema_migrations (numero, archivo, nota)
        VALUES (9, '009_gastos_bancarios.sql', nota) ON CONFLICT (numero) DO NOTHING;
        IF EXISTS (SELECT 1 FROM conciliacion.patrones_gasto_bancario WHERE patron = 'COMIS.SCI') THEN
            INSERT INTO public.schema_migrations (numero, archivo, nota)
            VALUES (10, '010_ajuste_patrones_gastos.sql', nota) ON CONFLICT (numero) DO NOTHING;
        END IF;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 2. Catálogo de acciones
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auditoria.acciones (
    codigo       VARCHAR(100) PRIMARY KEY,
    modulo       VARCHAR(40)  NOT NULL,
    descripcion  TEXT         NOT NULL,
    nivel        VARCHAR(12)  NOT NULL,
    activo       BOOLEAN      NOT NULL DEFAULT TRUE,
    creado_en    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_acciones_codigo CHECK (codigo ~ '^[a-z][a-z_]*\.[a-z][a-z_]*\.[a-z][a-z_]*$'),
    CONSTRAINT ck_acciones_modulo CHECK (modulo = split_part(codigo, '.', 1)),
    CONSTRAINT ck_acciones_nivel  CHECK (nivel IN ('INFORMATIVO', 'IMPORTANTE', 'CRITICO'))
);

INSERT INTO auditoria.acciones (codigo, modulo, descripcion, nivel) VALUES
    -- Identidad
    ('identidad.sesion.iniciar',          'identidad', 'Inicio de sesión correcto', 'IMPORTANTE'),
    ('identidad.sesion.rechazar',         'identidad', 'Inicio de sesión rechazado', 'IMPORTANTE'),
    ('identidad.usuario.crear',           'identidad', 'Crear usuario', 'CRITICO'),
    ('identidad.usuario.editar',          'identidad', 'Editar usuario (correo, nombre, rol o estado)', 'CRITICO'),
    -- Bancos
    ('bancos.extracto.cargar',            'bancos', 'Cargar extracto bancario', 'IMPORTANTE'),
    ('bancos.cuenta.configurar',          'bancos', 'Configurar cuenta bancaria (Auto-Match, destinatarios, institución)', 'IMPORTANTE'),
    ('bancos.cuenta.sincronizar',         'bancos', 'Sincronizar el catálogo de cuentas desde Business Central', 'INFORMATIVO'),
    -- Conciliación
    ('conciliacion.auto_match.ejecutar',  'conciliacion', 'Ejecutar Auto-Match de un periodo (manual, programado o tras carga)', 'INFORMATIVO'),
    ('conciliacion.acta.cerrar',          'conciliacion', 'Cerrar acta de conciliación', 'CRITICO'),
    ('conciliacion.acta.reabrir',         'conciliacion', 'Reabrir acta de conciliación', 'CRITICO'),
    ('conciliacion.acta.enviar_correo',   'conciliacion', 'Enviar el informe del acta por correo', 'INFORMATIVO'),
    ('conciliacion.acta.descargar',       'conciliacion', 'Descargar el informe Excel del acta', 'INFORMATIVO'),
    ('conciliacion.reporte_cruces.descargar', 'conciliacion', 'Descargar el detalle de cruces', 'INFORMATIVO'),
    ('conciliacion.gastos_bancarios.descargar', 'conciliacion', 'Descargar el Excel de gastos bancarios', 'INFORMATIVO'),
    ('conciliacion.corte_inicial.guardar','conciliacion', 'Crear o modificar el corte inicial de una cuenta', 'CRITICO'),
    ('conciliacion.patron_gasto.crear',   'conciliacion', 'Crear patrón de gasto bancario', 'IMPORTANTE'),
    ('conciliacion.patron_gasto.editar',  'conciliacion', 'Modificar patrón de gasto bancario', 'IMPORTANTE'),
    ('conciliacion.patron_gasto.eliminar','conciliacion', 'Eliminar patrón de gasto bancario', 'IMPORTANTE'),
    -- Sistema y auditoría
    ('sistema.permiso.denegar',           'sistema', 'Acción negada por falta de permisos (403)', 'IMPORTANTE'),
    ('sistema.sql_directo.cambiar',       'sistema', 'Cambio hecho directo en la base de datos, fuera de la aplicación', 'CRITICO'),
    ('sistema.migracion.faltar',          'sistema', 'Al arrancar faltan migraciones por ejecutar', 'CRITICO'),
    ('auditoria.cadena.alertar',          'auditoria', 'La verificación diaria encontró la cadena de hash alterada', 'CRITICO'),
    ('auditoria.particion.purgar',        'auditoria', 'Archivo y borrado de meses vencidos (retención de 5 años)', 'CRITICO')
ON CONFLICT (codigo) DO UPDATE
    SET descripcion = EXCLUDED.descripcion, nivel = EXCLUDED.nivel;

-- ---------------------------------------------------------------------
-- 3. Eventos (solo inserción, particionada por mes, cadena de hash)
-- ---------------------------------------------------------------------
CREATE SEQUENCE IF NOT EXISTS auditoria.eventos_id_seq;

CREATE TABLE IF NOT EXISTS auditoria.eventos (
    id_evento         BIGINT       NOT NULL,
    ocurrido_en       TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    modulo            VARCHAR(40)  NOT NULL,
    accion            VARCHAR(100) NOT NULL REFERENCES auditoria.acciones (codigo),
    nivel             VARCHAR(12)  NOT NULL,
    resultado         VARCHAR(10)  NOT NULL DEFAULT 'OK',
    entidad           VARCHAR(80),
    id_entidad        VARCHAR(120),
    id_entidad_padre  VARCHAR(120),
    id_usuario        UUID,
    email             VARCHAR(255),
    rol               VARCHAR(40),
    origen            VARCHAR(12)  NOT NULL,
    ip                INET,
    user_agent        VARCHAR(300),
    id_solicitud      VARCHAR(64),
    resumen           TEXT         NOT NULL,
    cambios           JSONB,
    datos             JSONB,
    hash_anterior     CHAR(64)     NOT NULL,
    hash              CHAR(64)     NOT NULL,
    PRIMARY KEY (id_evento, ocurrido_en),
    CONSTRAINT ck_eventos_resultado CHECK (resultado IN ('OK', 'ERROR', 'DENEGADO')),
    CONSTRAINT ck_eventos_origen    CHECK (origen IN ('WEB', 'API', 'SCHEDULER', 'SQL_DIRECTO', 'MIGRADO'))
) PARTITION BY RANGE (ocurrido_en);

-- Red de seguridad: si faltara el mes, el evento no se pierde
CREATE TABLE IF NOT EXISTS auditoria.eventos_default PARTITION OF auditoria.eventos DEFAULT;

CREATE INDEX IF NOT EXISTS ix_eventos_entidad ON auditoria.eventos (entidad, id_entidad, ocurrido_en);
CREATE INDEX IF NOT EXISTS ix_eventos_padre   ON auditoria.eventos (id_entidad_padre, ocurrido_en)
    WHERE id_entidad_padre IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_eventos_usuario ON auditoria.eventos (email, ocurrido_en);
CREATE INDEX IF NOT EXISTS ix_eventos_accion  ON auditoria.eventos (accion, ocurrido_en);
CREATE INDEX IF NOT EXISTS ix_eventos_fecha   ON auditoria.eventos (ocurrido_en);

-- Estado de la cadena: una sola fila. Su bloqueo serializa las inserciones.
CREATE TABLE IF NOT EXISTS auditoria.cadena (
    id           SMALLINT PRIMARY KEY CHECK (id = 1),
    ultimo_id    BIGINT,
    ultimo_hash  CHAR(64) NOT NULL
);
INSERT INTO auditoria.cadena (id, ultimo_id, ultimo_hash)
VALUES (1, NULL, repeat('0', 64)) ON CONFLICT (id) DO NOTHING;

-- Puntos de partida que deja la purga de meses vencidos
CREATE TABLE IF NOT EXISTS auditoria.cortes_cadena (
    id_corte      SERIAL PRIMARY KEY,
    particion     TEXT        NOT NULL,
    hasta_fecha   TIMESTAMPTZ NOT NULL,
    ultimo_id     BIGINT,
    ultimo_hash   CHAR(64)    NOT NULL,
    eventos       BIGINT      NOT NULL,
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    creado_por    TEXT        NOT NULL DEFAULT current_user
);

-- Contenido canónico del evento (arreglo JSON: sin ambigüedad entre campos)
CREATE OR REPLACE FUNCTION auditoria.contenido_evento(e auditoria.eventos)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT jsonb_build_array(
        e.id_evento,
        to_char(e.ocurrido_en AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        e.accion, e.nivel, e.resultado, e.entidad, e.id_entidad, e.id_entidad_padre,
        e.id_usuario::text, e.email, e.rol, e.origen, host(e.ip), e.user_agent, e.id_solicitud,
        e.resumen, e.cambios, e.datos
    )::text
$$;

CREATE OR REPLACE FUNCTION auditoria.calcular_hash(e auditoria.eventos)
RETURNS CHAR(64) LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(e.hash_anterior || auditoria.contenido_evento(e), 'UTF8')), 'hex')
$$;

CREATE OR REPLACE FUNCTION auditoria.antes_de_insertar_evento()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_prev  CHAR(64);
    v_nivel VARCHAR(12);
BEGIN
    IF NEW.origen IS DISTINCT FROM 'MIGRADO'
       AND abs(extract(epoch FROM NEW.ocurrido_en - clock_timestamp())) > 300 THEN
        RAISE EXCEPTION 'auditoria: ocurrido_en solo se puede indicar en eventos MIGRADO';
    END IF;

    SELECT nivel INTO v_nivel FROM auditoria.acciones WHERE codigo = NEW.accion;
    IF v_nivel IS NULL THEN
        RAISE EXCEPTION 'auditoria: la acción % no está en auditoria.acciones', NEW.accion;
    END IF;

    -- Serializa: una inserción a la vez hasta el fin de la transacción
    SELECT ultimo_hash INTO v_prev FROM auditoria.cadena WHERE id = 1 FOR UPDATE;

    NEW.id_evento     := nextval('auditoria.eventos_id_seq');
    NEW.modulo        := split_part(NEW.accion, '.', 1);
    NEW.nivel         := v_nivel;
    NEW.resumen       := left(coalesce(NEW.resumen, ''), 2000);
    NEW.user_agent    := left(NEW.user_agent, 300);
    NEW.hash_anterior := v_prev;
    NEW.hash          := auditoria.calcular_hash(NEW);

    UPDATE auditoria.cadena SET ultimo_id = NEW.id_evento, ultimo_hash = NEW.hash WHERE id = 1;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION auditoria.rechazar_modificacion()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'auditoria.eventos solo admite inserciones (% rechazado)', TG_OP;
END $$;

DROP TRIGGER IF EXISTS tg_eventos_insertar ON auditoria.eventos;
CREATE TRIGGER tg_eventos_insertar BEFORE INSERT ON auditoria.eventos
    FOR EACH ROW EXECUTE FUNCTION auditoria.antes_de_insertar_evento();

DROP TRIGGER IF EXISTS tg_eventos_inmutables ON auditoria.eventos;
CREATE TRIGGER tg_eventos_inmutables BEFORE UPDATE OR DELETE ON auditoria.eventos
    FOR EACH ROW EXECUTE FUNCTION auditoria.rechazar_modificacion();

DROP TRIGGER IF EXISTS tg_eventos_sin_truncate ON auditoria.eventos;
CREATE TRIGGER tg_eventos_sin_truncate BEFORE TRUNCATE ON auditoria.eventos
    FOR EACH STATEMENT EXECUTE FUNCTION auditoria.rechazar_modificacion();

-- Particiones mensuales (eventos_AAAA_MM). Las crea también el scheduler cada día.
CREATE OR REPLACE FUNCTION auditoria.crear_particiones(p_desde DATE, p_hasta DATE)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    v_mes    DATE := date_trunc('month', p_desde)::date;
    v_nombre TEXT;
    v_creadas INTEGER := 0;
BEGIN
    WHILE v_mes <= p_hasta LOOP
        v_nombre := format('eventos_%s', to_char(v_mes, 'YYYY_MM'));
        IF to_regclass('auditoria.' || v_nombre) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE auditoria.%I PARTITION OF auditoria.eventos FOR VALUES FROM (%L) TO (%L)',
                v_nombre, v_mes::timestamptz, (v_mes + interval '1 month')::timestamptz);
            v_creadas := v_creadas + 1;
        END IF;
        v_mes := (v_mes + interval '1 month')::date;
    END LOOP;
    RETURN v_creadas;
END $$;

SELECT auditoria.crear_particiones((date_trunc('month', now()) - interval '1 month')::date,
                                   (date_trunc('month', now()) + interval '3 months')::date);

-- Verificación de la cadena: recalcula cada hash en orden de id_evento
CREATE OR REPLACE FUNCTION auditoria.verificar_cadena()
RETURNS TABLE (eventos_revisados BIGINT, correcta BOOLEAN, id_evento_con_error BIGINT, detalle TEXT)
LANGUAGE plpgsql AS $$
DECLARE
    r       auditoria.eventos;
    v_prev  CHAR(64);
    v_desde BIGINT;
    v_n     BIGINT := 0;
    v_final auditoria.cadena;
BEGIN
    SELECT c.ultimo_hash, c.ultimo_id INTO v_prev, v_desde
      FROM auditoria.cortes_cadena c ORDER BY c.id_corte DESC LIMIT 1;
    v_prev := coalesce(v_prev, repeat('0', 64));

    FOR r IN SELECT * FROM auditoria.eventos e
              WHERE v_desde IS NULL OR e.id_evento > v_desde ORDER BY e.id_evento LOOP
        v_n := v_n + 1;
        IF r.hash_anterior <> v_prev THEN
            RETURN QUERY SELECT v_n, FALSE, r.id_evento,
                'El evento no continúa la cadena: falta o se alteró el evento anterior'::text;
            RETURN;
        END IF;
        IF auditoria.calcular_hash(r) <> r.hash THEN
            RETURN QUERY SELECT v_n, FALSE, r.id_evento, 'El contenido del evento fue modificado'::text;
            RETURN;
        END IF;
        v_prev := r.hash;
    END LOOP;

    SELECT * INTO v_final FROM auditoria.cadena WHERE id = 1;
    IF v_final.ultimo_hash <> v_prev THEN
        RETURN QUERY SELECT v_n, FALSE, v_final.ultimo_id,
            'Faltan los últimos eventos de la cadena (fueron borrados)'::text;
        RETURN;
    END IF;
    RETURN QUERY SELECT v_n, TRUE, NULL::BIGINT, 'Cadena íntegra'::text;
END $$;

-- Retención: archiva (exporta antes con pg_dump) y borra los meses de más de p_anios años
CREATE OR REPLACE FUNCTION auditoria.purgar_particiones(p_anios INTEGER DEFAULT 5)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    v_limite TIMESTAMPTZ := date_trunc('month', now()) - make_interval(years => p_anios);
    v_part   RECORD;
    v_ult    RECORD;
    v_n      BIGINT;
    v_borradas INTEGER := 0;
BEGIN
    FOR v_part IN
        SELECT c.relname,
               to_timestamp(substring(c.relname FROM 'eventos_(\d{4}_\d{2})'), 'YYYY_MM') AS mes
          FROM pg_inherits i
          JOIN pg_class c ON c.oid = i.inhrelid
          JOIN pg_class p ON p.oid = i.inhparent
          JOIN pg_namespace n ON n.oid = p.relnamespace
         WHERE n.nspname = 'auditoria' AND p.relname = 'eventos' AND c.relname ~ '^eventos_\d{4}_\d{2}$'
         ORDER BY 2
    LOOP
        EXIT WHEN v_part.mes + interval '1 month' > v_limite;
        EXECUTE format('SELECT count(*) FROM auditoria.%I', v_part.relname) INTO v_n;
        EXECUTE format('SELECT id_evento, hash FROM auditoria.%I ORDER BY id_evento DESC LIMIT 1',
                       v_part.relname) INTO v_ult;
        IF v_n > 0 THEN
            INSERT INTO auditoria.cortes_cadena (particion, hasta_fecha, ultimo_id, ultimo_hash, eventos)
            VALUES (v_part.relname, v_part.mes + interval '1 month', v_ult.id_evento, v_ult.hash, v_n);
        END IF;
        EXECUTE format('ALTER TABLE auditoria.eventos DETACH PARTITION auditoria.%I', v_part.relname);
        EXECUTE format('DROP TABLE auditoria.%I', v_part.relname);
        INSERT INTO auditoria.eventos (accion, origen, resumen, datos, email)
        VALUES ('auditoria.particion.purgar', 'SCHEDULER',
                format('Se borró %s (%s eventos) por retención de %s años', v_part.relname, v_n, p_anios),
                jsonb_build_object('particion', v_part.relname, 'eventos', v_n,
                                   'ultimo_id', v_ult.id_evento, 'ultimo_hash', v_ult.hash),
                'bd:' || session_user);
        v_borradas := v_borradas + 1;
    END LOOP;
    RETURN v_borradas;
END $$;

-- ---------------------------------------------------------------------
-- 4. Cambios hechos directo en la base
--    La aplicación marca cada conexión con app.origen; si falta, el cambio vino de fuera.
--    Argumentos del trigger: columna(s) de la clave separadas por coma y, opcionalmente,
--    columnas que no se guardan (secretos).
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION auditoria.registrar_cambio_directo()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, auditoria AS $$
DECLARE
    v_antes   JSONB;
    v_despues JSONB;
    v_omitir  TEXT[] := CASE WHEN TG_NARGS > 1 THEN TG_ARGV[1:TG_NARGS - 1] ELSE ARRAY[]::TEXT[] END;
    v_cambios JSONB;
    v_id      TEXT;
    v_col     TEXT;
BEGIN
    IF coalesce(current_setting('app.origen', true), '') <> '' THEN
        RETURN NULL;
    END IF;

    IF TG_OP <> 'INSERT' THEN v_antes := to_jsonb(OLD) - v_omitir; END IF;
    IF TG_OP <> 'DELETE' THEN v_despues := to_jsonb(NEW) - v_omitir; END IF;

    SELECT jsonb_object_agg(k, jsonb_build_array(v_antes -> k, v_despues -> k))
      INTO v_cambios
      FROM (SELECT jsonb_object_keys(coalesce(v_despues, v_antes)) AS k) llaves
     WHERE (v_antes -> k) IS DISTINCT FROM (v_despues -> k);
    IF v_cambios IS NULL THEN
        RETURN NULL;  -- UPDATE sin cambios reales
    END IF;

    FOREACH v_col IN ARRAY string_to_array(TG_ARGV[0], ',') LOOP
        v_id := concat_ws('/', v_id, coalesce(v_despues, v_antes) ->> trim(v_col));
    END LOOP;

    INSERT INTO auditoria.eventos (accion, entidad, id_entidad, email, origen, resumen, cambios, datos, ip)
    VALUES ('sistema.sql_directo.cambiar', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, v_id,
            'bd:' || session_user, 'SQL_DIRECTO',
            format('%s directo en %s.%s (%s)', TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME, v_id),
            v_cambios,
            jsonb_build_object('operacion', TG_OP, 'usuario_bd', session_user,
                               'aplicacion', current_setting('application_name', true)),
            inet_client_addr());
    RETURN NULL;
END $$;

DO $$
DECLARE
    t RECORD;
BEGIN
    FOR t IN SELECT * FROM (VALUES
        ('identidad', 'usuarios',                'id_usuario'),
        ('bancos',    'instituciones',           'id_institucion'),
        ('bancos',    'cuentas_bancarias',       'id_cuenta'),
        ('bancos',    'cuentas_destinatarios',   'id_cuenta,id_usuario'),
        ('conciliacion', 'actas',                'id_acta'),
        ('conciliacion', 'cortes_iniciales',     'id_corte'),
        ('conciliacion', 'partidas_iniciales',   'id_partida'),
        ('conciliacion', 'patrones_gasto_bancario', 'id_patron'),
        ('auditoria', 'acciones',                'codigo')
    ) AS v(esquema, tabla, clave)
    LOOP
        IF to_regclass(t.esquema || '.' || t.tabla) IS NOT NULL THEN
            EXECUTE format('DROP TRIGGER IF EXISTS tg_auditoria_sql_directo ON %I.%I', t.esquema, t.tabla);
            EXECUTE format('CREATE TRIGGER tg_auditoria_sql_directo AFTER INSERT OR UPDATE OR DELETE ON %I.%I '
                           'FOR EACH ROW EXECUTE FUNCTION auditoria.registrar_cambio_directo(%L)',
                           t.esquema, t.tabla, t.clave);
        END IF;
    END LOOP;
END $$;

INSERT INTO public.schema_migrations (numero, archivo) VALUES (11, '011_auditoria.sql')
ON CONFLICT (numero) DO NOTHING;

COMMIT;

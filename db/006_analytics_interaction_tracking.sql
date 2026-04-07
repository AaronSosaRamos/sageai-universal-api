-- ============================================================================
-- ANALÍTICA, TRAZAS Y MÉTRICAS — TODOS LOS USUARIOS
-- ============================================================================
-- Tablas append-only y agregados para almacenar:
--   - Cada interacción relevante (API, chat, archivos, asistentes, auth, errores)
--   - Metadatos y métricas arbitrarias en JSONB (tokens, latencias, herramientas, etc.)
--   - Invocaciones LLM detalladas (opcionalmente enlazadas a un evento padre)
--   - Rollups diarios por usuario (rellenados por la aplicación o jobs)
--
-- Inserción: normalmente con la service role / backend (no exponer escritura pública).
-- Lectura: paneles internos con service role; RLS opcional por usuario solo-sus-datos.
-- ============================================================================

-- gen_random_uuid() está disponible en PostgreSQL 13+ sin extensión adicional.

-- ---------------------------------------------------------------------------
-- 1) EVENTOS DE INTERACCIÓN (log principal, una fila por hecho trazable)
-- ---------------------------------------------------------------------------
create table if not exists interaction_events (
    id                      uuid primary key default gen_random_uuid(),
    occurred_at             timestamp with time zone not null default timezone('utc'::text, now()),

    -- Usuario (mismo formato que chat_threads.user_id, típicamente uuid en texto); null = sistema / no identificado
    user_id                 text,

    -- Clasificación
    event_category          text not null,
    event_name              text not null,

    -- Contexto de producto (nullable según evento)
    thread_id               text,
    assistant_id            uuid,
    session_key             text,
    correlation_id          text,
    parent_event_id         uuid references interaction_events (id) on delete set null,

    -- Petición HTTP / API (si aplica)
    http_method             text,
    http_path               text,
    status_code             integer,
    duration_ms             integer,

    success                 boolean,
    error_type              text,
    error_message           text,

    -- Bloques flexibles (métricas numéricas, contadores, flags, nombres de tools, etc.)
    metadata                jsonb not null default '{}'::jsonb,
    metrics                 jsonb not null default '{}'::jsonb,

    -- Cliente / entorno (sin PII cruda; preferir hashes)
    client                  jsonb not null default '{}'::jsonb,

    constraint chk_interaction_events_category check (
        event_category in (
            'auth',
            'chat',
            'assistant',
            'supervisor',
            'storage',
            'export',
            'memory',
            'custom_space',
            'thread',
            'user',
            'api',
            'system',
            'security',
            'other'
        )
    )
);

comment on table interaction_events is 'Log append-only de interacciones y telemetría; metadata/metrics en JSONB para evolucionar sin migraciones.';
comment on column interaction_events.event_category is 'Área funcional: auth, chat, supervisor, storage, etc.';
comment on column interaction_events.event_name is 'Nombre estable p.ej. api.supervisor.invoke, file.uploaded, export.response';
comment on column interaction_events.metadata is 'Contexto no numérico: model, tools_used[], flags, rutas sanitizadas, etc.';
comment on column interaction_events.metrics is 'Medidas: tokens_in/out, bytes, counts, latency_breakdown_ms, etc.';
comment on column interaction_events.client is 'ip_hash, user_agent, referer_hash, app_version';

create index if not exists idx_interaction_events_user_occurred
    on interaction_events (user_id, occurred_at desc);

create index if not exists idx_interaction_events_occurred
    on interaction_events (occurred_at desc);

create index if not exists idx_interaction_events_category_name
    on interaction_events (event_category, event_name, occurred_at desc);

create index if not exists idx_interaction_events_thread
    on interaction_events (thread_id)
    where thread_id is not null;

create index if not exists idx_interaction_events_assistant
    on interaction_events (assistant_id)
    where assistant_id is not null;

create index if not exists idx_interaction_events_correlation
    on interaction_events (correlation_id)
    where correlation_id is not null;

create index if not exists idx_interaction_events_parent
    on interaction_events (parent_event_id)
    where parent_event_id is not null;

create index if not exists idx_interaction_events_metadata_gin
    on interaction_events using gin (metadata jsonb_path_ops);

create index if not exists idx_interaction_events_metrics_gin
    on interaction_events using gin (metrics jsonb_path_ops);

-- ---------------------------------------------------------------------------
-- 2) INVOCACIONES LLM (detalle de coste/rendimiento; enlazable a interaction_events)
-- ---------------------------------------------------------------------------
create table if not exists llm_invocation_metrics (
    id                      uuid primary key default gen_random_uuid(),
    occurred_at             timestamp with time zone not null default timezone('utc'::text, now()),

    user_id                 text,
    thread_id               text,
    assistant_id            uuid,

    interaction_event_id    uuid references interaction_events (id) on delete set null,

    provider                text not null default 'google',
    model_name              text not null,

    input_tokens            integer,
    output_tokens           integer,
    total_tokens            integer,

    latency_ms              integer,
    time_to_first_token_ms  integer,

    finish_reason           text,
    tool_calls_count        integer default 0,
    tools_used              text[],

    estimated_cost_usd      numeric(18, 8),

    metadata                jsonb not null default '{}'::jsonb,
    metrics                 jsonb not null default '{}'::jsonb
);

comment on table llm_invocation_metrics is 'Métricas por llamada al modelo; opcionalmente ligadas a interaction_events.';

create index if not exists idx_llm_invocation_user_time
    on llm_invocation_metrics (user_id, occurred_at desc);

create index if not exists idx_llm_invocation_model_time
    on llm_invocation_metrics (model_name, occurred_at desc);

create index if not exists idx_llm_invocation_thread
    on llm_invocation_metrics (thread_id)
    where thread_id is not null;

create index if not exists idx_llm_invocation_event
    on llm_invocation_metrics (interaction_event_id)
    where interaction_event_id is not null;

-- ---------------------------------------------------------------------------
-- 3) AGREGADOS DIARIOS POR USUARIO (relleno por job/app; estructura fija + JSONB)
-- ---------------------------------------------------------------------------
create table if not exists user_metrics_daily (
    user_id                 text not null,
    bucket_date             date not null,

    -- Contadores comunes (denormalizados para dashboards rápidos)
    messages_human          bigint not null default 0,
    messages_ai             bigint not null default 0,
    supervisor_invocations  bigint not null default 0,
    assistant_messages      bigint not null default 0,
    files_uploaded_count    bigint not null default 0,
    files_uploaded_bytes    bigint not null default 0,
    exports_docx_count      bigint not null default 0,
    exports_pdf_count       bigint not null default 0,

    total_llm_tokens        bigint not null default 0,
    total_estimated_cost_usd numeric(18, 8) not null default 0,

    -- Cualquier otra métrica sin migración nueva
    extra                   jsonb not null default '{}'::jsonb,

    updated_at              timestamp with time zone not null default timezone('utc'::text, now()),

    primary key (user_id, bucket_date)
);

comment on table user_metrics_daily is 'Rollups diarios por usuario; actualizar desde workers/cron sumando interaction_events / llm_invocation_metrics.';

create index if not exists idx_user_metrics_daily_date
    on user_metrics_daily (bucket_date desc);

-- ---------------------------------------------------------------------------
-- 4) SESIONES / CORRELACIÓN (opcional: agrupar flujos multi-paso)
-- ---------------------------------------------------------------------------
create table if not exists interaction_sessions (
    id                      uuid primary key default gen_random_uuid(),
    started_at              timestamp with time zone not null default timezone('utc'::text, now()),
    ended_at                timestamp with time zone,
    user_id                 text,

    client                  jsonb not null default '{}'::jsonb,
    metadata                jsonb not null default '{}'::jsonb,

    primary_correlation_id  text unique
);

create index if not exists idx_interaction_sessions_user_started
    on interaction_sessions (user_id, started_at desc);

comment on table interaction_sessions is 'Sesiones lógicas (p.ej. visita al chat) para agrupar correlation_id.';

-- ---------------------------------------------------------------------------
-- 5) DEFINICIÓN DE EVENTOS (documentación en BD para equipos y validación opcional)
-- ---------------------------------------------------------------------------
create table if not exists analytics_event_catalog (
    event_name              text primary key,
    event_category          text not null,
    description             text,
    metadata_schema_hint    jsonb not null default '{}'::jsonb,
    metrics_schema_hint     jsonb not null default '{}'::jsonb,
    updated_at              timestamp with time zone not null default timezone('utc'::text, now())
);

comment on table analytics_event_catalog is 'Catálogo de nombres de eventos recomendados; la app puede insertar filas al desplegar nuevas features.';

-- Semillas mínimas (idempotentes)
insert into analytics_event_catalog (event_name, event_category, description)
values
    ('api.supervisor.invoke', 'supervisor', 'Llamada al endpoint supervisor / agente React'),
    ('api.assistant_chat.invoke', 'assistant', 'Mensaje a asistente personalizado'),
    ('chat.message.persisted', 'chat', 'Mensaje guardado en chat_threads'),
    ('file.uploaded', 'storage', 'Archivo subido a storage de sesión'),
    ('file.deleted', 'storage', 'Archivo eliminado'),
    ('export.response', 'export', 'Exportación Word/PDF de mensaje'),
    ('auth.token.issued', 'auth', 'Token JWT emitido'),
    ('user.registered', 'user', 'Registro de usuario'),
    ('thread.created', 'thread', 'Thread de chat creado'),
    ('custom_space.updated', 'custom_space', 'Espacio personalizado actualizado')
on conflict (event_name) do nothing;

-- ---------------------------------------------------------------------------
-- RLS (opcional en Supabase): la clave service_role suele omitir RLS.
-- Si activas RLS en estas tablas, define políticas (p. ej. solo lectura propia por user_id).
-- ---------------------------------------------------------------------------

-- Catálogo de eventos de evaluaciones (interaction_events.event_category = 'api').
-- Ejecutar en Supabase junto con el despliegue de rutas /evaluations.

insert into analytics_event_catalog (event_name, event_category, description, metadata_schema_hint, metrics_schema_hint)
values
    (
        'evaluation.generate.completed',
        'api',
        'POST /evaluations/generate: borrador generado por LLM desde archivos.',
        '{"file_ref_count": "int", "question_count": "int", "has_requirements": "bool", "has_additional_context": "bool"}'::jsonb,
        '{"duration_ms": "int"}'::jsonb
    ),
    (
        'evaluation.created',
        'api',
        'POST /evaluations: evaluación persistida (borrador o publicada).',
        '{"evaluation_id": "uuid", "published": "bool", "question_count": "int", "duration_minutes": "int|null"}'::jsonb,
        '{}'::jsonb
    ),
    (
        'evaluation.list',
        'api',
        'GET /evaluations: listado scope mine|published.',
        '{"scope": "mine|published", "result_count": "int"}'::jsonb,
        '{}'::jsonb
    ),
    (
        'evaluation.detail.viewed',
        'api',
        'GET /evaluations/{id}: detalle (autor o publicada).',
        '{"evaluation_id": "uuid", "preview_student": "bool", "student_view": "bool", "is_owner": "bool"}'::jsonb,
        '{}'::jsonb
    ),
    (
        'evaluation.updated',
        'api',
        'PUT /evaluations/{id}: actualización por administrador/autor.',
        '{"evaluation_id": "uuid", "published": "bool", "question_count": "int"}'::jsonb,
        '{}'::jsonb
    ),
    (
        'evaluation.deleted',
        'api',
        'DELETE /evaluations/{id}.',
        '{"evaluation_id": "uuid"}'::jsonb,
        '{}'::jsonb
    ),
    (
        'evaluation.share.meta.viewed',
        'api',
        'GET /evaluations/share/{token}/meta (público, sin JWT).',
        '{"evaluation_id": "uuid", "timed": "bool", "duration_minutes": "int"}'::jsonb,
        '{}'::jsonb
    ),
    (
        'evaluation.session.started',
        'api',
        'POST /evaluations/session/start: sesión de respuesta (con o sin temporizador).',
        '{"evaluation_id": "uuid", "timed": "bool", "resumed": "bool", "session_id": "uuid|null", "duration_minutes": "int|null", "via_share_token": "bool"}'::jsonb,
        '{}'::jsonb
    ),
    (
        'evaluation.share.rotated',
        'api',
        'POST /evaluations/{id}/share/rotate: nuevo token de enlace.',
        '{"evaluation_id": "uuid"}'::jsonb,
        '{}'::jsonb
    ),
    (
        'evaluation.submitted',
        'api',
        'POST /evaluations/{id}/submit: intento calificado y guardado.',
        '{"evaluation_id": "uuid", "attempt_id": "uuid", "timed": "bool", "session_id": "uuid|null"}'::jsonb,
        '{"score_percent": "float|null", "question_count": "int", "duration_seconds": "int|null"}'::jsonb
    ),
    (
        'evaluation.attempts.listed',
        'api',
        'GET /evaluations/{id}/attempts: listado de intentos (alcance según rol).',
        '{"evaluation_id": "uuid", "is_owner": "bool", "admin_view_all": "bool", "result_count": "int"}'::jsonb,
        '{}'::jsonb
    )
on conflict (event_name) do update set
    event_category = excluded.event_category,
    description = excluded.description,
    metadata_schema_hint = excluded.metadata_schema_hint,
    metrics_schema_hint = excluded.metrics_schema_hint,
    updated_at = timezone('utc'::text, now());

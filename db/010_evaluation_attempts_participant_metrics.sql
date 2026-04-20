-- Quién respondió, cuándo, duración y métricas por intento

alter table evaluation_attempts
  add column if not exists participant_email text;

alter table evaluation_attempts
  add column if not exists participant_name text;

alter table evaluation_attempts
  add column if not exists started_at timestamp with time zone;

alter table evaluation_attempts
  add column if not exists duration_seconds integer;

alter table evaluation_attempts
  add column if not exists metrics_json jsonb not null default '{}'::jsonb;

comment on column evaluation_attempts.participant_email is 'Email del JWT al enviar';
comment on column evaluation_attempts.participant_name is 'Nombre mostrable (nombre + apellido) al enviar';
comment on column evaluation_attempts.started_at is 'Inicio del intento (sesión temporizada o null si sin tiempo)';
comment on column evaluation_attempts.duration_seconds is 'Segundos entre inicio y envío';
comment on column evaluation_attempts.metrics_json is 'Puntajes por pregunta y conteos; score_percent sigue siendo la nota global';

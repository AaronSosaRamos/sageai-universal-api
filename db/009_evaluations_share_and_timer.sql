-- Enlace compartido y tiempo máximo por evaluación

alter table evaluations
  add column if not exists share_token text unique;

alter table evaluations
  add column if not exists duration_minutes integer;

comment on column evaluations.share_token is 'Token opaco para URL pública /evaluations/s/{token}';
comment on column evaluations.duration_minutes is 'Tiempo máximo en minutos; null o 0 = sin límite';

create table if not exists evaluation_take_sessions (
    id uuid primary key default gen_random_uuid(),
    evaluation_id uuid not null references evaluations (id) on delete cascade,
    user_id text not null,
    started_at timestamp with time zone not null default timezone('utc'::text, now()),
    deadline_at timestamp with time zone not null,
    submitted_at timestamp with time zone
);

create index if not exists idx_take_sessions_eval_user
  on evaluation_take_sessions (evaluation_id, user_id);

alter table evaluation_attempts
  add column if not exists take_session_id uuid references evaluation_take_sessions (id) on delete set null;

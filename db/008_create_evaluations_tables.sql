-- Evaluaciones generadas por docentes / autores y completadas por otros usuarios

create table if not exists evaluations (
    id uuid primary key default gen_random_uuid(),
    author_user_id text not null,
    title text not null,
    description text default '',
    requirements_hint text default '',
    questions_json jsonb not null default '[]'::jsonb,
    published boolean not null default false,
    published_at timestamp with time zone,
    created_at timestamp with time zone default timezone('utc'::text, now()),
    updated_at timestamp with time zone default timezone('utc'::text, now())
);

create index if not exists idx_evaluations_author on evaluations (author_user_id);
create index if not exists idx_evaluations_published on evaluations (published) where published = true;

create table if not exists evaluation_attempts (
    id uuid primary key default gen_random_uuid(),
    evaluation_id uuid not null references evaluations (id) on delete cascade,
    user_id text not null,
    answers_json jsonb not null default '{}'::jsonb,
    score_percent numeric(5, 2),
    feedback text default '',
    created_at timestamp with time zone default timezone('utc'::text, now())
);

create index if not exists idx_evaluation_attempts_eval on evaluation_attempts (evaluation_id);
create index if not exists idx_evaluation_attempts_user on evaluation_attempts (user_id);

-- ============================================================================
-- TABLA PARA ASISTENTES PERSONALIZADOS
-- ============================================================================
-- Almacena system prompts personalizados por usuario (estilo GPTs)
-- ============================================================================

create table if not exists user_assistants (
    id uuid primary key default gen_random_uuid(),
    user_id text not null,
    name text not null,
    description text default '',
    system_prompt text not null,
    created_at timestamp with time zone default timezone('utc'::text, now()),
    updated_at timestamp with time zone default timezone('utc'::text, now())
);

create index if not exists idx_user_assistants_user_id on user_assistants (user_id);

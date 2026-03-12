-- ============================================================================
-- TABLA PARA ESPACIOS PERSONALIZADOS DE USUARIOS
-- ============================================================================
-- Permite a cada usuario definir sus memorias personalizadas y cómo debe
-- actuar el agente según sus preferencias específicas
-- ============================================================================

create table if not exists user_custom_spaces (
    id uuid primary key default gen_random_uuid(),
    user_id text not null,                              -- ID del usuario propietario
    title text not null default 'Mi Espacio Personalizado', -- Título del espacio
    custom_memories text not null default '',           -- Memorias personalizadas del usuario (texto libre)
    agent_instructions text not null default '',        -- Instrucciones de cómo debe actuar el agente
    is_active boolean not null default true,           -- Si el espacio está activo
    created_at timestamp with time zone default timezone('utc'::text, now()),
    updated_at timestamp with time zone default timezone('utc'::text, now())
);

-- Índice para búsquedas rápidas por usuario
create index if not exists idx_user_custom_spaces_user_id
on user_custom_spaces (user_id);

-- Índice para búsquedas de espacios activos por usuario
create index if not exists idx_user_custom_spaces_user_active
on user_custom_spaces (user_id, is_active);

-- Un solo espacio activo por usuario (opcional, puedes tener múltiples si prefieres)
-- Si quieres permitir múltiples espacios activos, elimina esta restricción única
-- create unique index if not exists idx_user_custom_spaces_one_active
-- on user_custom_spaces (user_id) where is_active = true;

-- ============================================================================
-- SCHEMA PARA MEMORIAS SEMÁNTICAS Y PROCEDIMENTALES
-- Un solo registro por usuario (perfil consolidado)
-- Los embeddings se generan y usan en memoria, no se almacenan en DB
-- ============================================================================

-- ============================================================================
-- MEMORIAS SEMÁNTICAS (Perfil Semántico del Usuario)
-- ============================================================================
-- Almacena el perfil semántico consolidado del usuario (preferencias, conocimiento, hechos)
-- Un solo registro por usuario que se actualiza constantemente
create table if not exists semantic_memories (
    user_id text primary key,                        -- Usuario propietario (PK único)
    user_name text not null,                        -- Nombre completo del usuario
    profile_summary text not null,                  -- Resumen del perfil semántico del usuario
    key_concepts text[],                            -- Conceptos clave identificados
    preferences text[],                             -- Preferencias del usuario
    interests text[],                               -- Intereses identificados
    knowledge_domains text[],                       -- Dominios de conocimiento
    tags text[],                                    -- Tags para organización
    last_updated_at timestamp with time zone default timezone('utc'::text, now()),
    created_at timestamp with time zone default timezone('utc'::text, now())
);

-- ============================================================================
-- MEMORIAS PROCEDIMENTALES (Perfil Procedimental del Usuario)
-- ============================================================================
-- Almacena el perfil procedimental consolidado del usuario (métodos, procesos preferidos)
-- Un solo registro por usuario que se actualiza constantemente
create table if not exists procedural_memories (
    user_id text primary key,                        -- Usuario propietario (PK único)
    user_name text not null,                        -- Nombre completo del usuario
    profile_summary text not null,                  -- Resumen del perfil procedimental
    preferred_methods text[],                        -- Métodos preferidos del usuario
    common_procedures text[],                       -- Procedimientos comunes identificados
    workflow_patterns text[],                       -- Patrones de flujo de trabajo
    efficiency_tips text[],                         -- Tips de eficiencia aprendidos
    tags text[],                                    -- Tags para organización
    last_updated_at timestamp with time zone default timezone('utc'::text, now()),
    created_at timestamp with time zone default timezone('utc'::text, now())
);

-- ============================================================================
-- TRIGGERS ELIMINADOS - No se actualiza last_updated_at automáticamente
-- ============================================================================

-- Eliminar cualquier trigger que pueda causar problemas
drop trigger if exists update_semantic_memories_updated_at on semantic_memories;
drop trigger if exists update_procedural_memories_updated_at on procedural_memories;
drop trigger if exists update_semantic_memories_last_updated_at on semantic_memories;
drop trigger if exists update_procedural_memories_last_updated_at on procedural_memories;

-- Eliminar funciones relacionadas completamente
drop function if exists update_updated_at_column() cascade;
drop function if exists update_last_updated_at_column() cascade;

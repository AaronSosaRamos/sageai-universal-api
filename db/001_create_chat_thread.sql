-- Crear tabla para almacenar threads de chat
create table if not exists chat_threads (
    id uuid primary key default gen_random_uuid(),   -- ID único
    user_id text not null,                           -- Usuario que envía/recibe
    thread_id text not null,                         -- Identificador del thread
    message text not null,                           -- Contenido del mensaje
    role text check (role in ('AI', 'Human')) not null, -- Rol: AI o Humano
    created_at timestamp with time zone default timezone('utc'::text, now()) -- Marca de tiempo automática
);

-- Índice para optimizar búsquedas por thread
create index if not exists idx_chat_threads_thread_id
on chat_threads (thread_id);

-- Índice para filtrar rápido por user_id
create index if not exists idx_chat_threads_user_id
on chat_threads (user_id);

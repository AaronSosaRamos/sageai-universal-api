-- Asegurar que la extensión uuid esté disponible
create extension if not exists "uuid-ossp";

-- Crear tabla users en Supabase
create table if not exists users (
  id uuid primary key default uuid_generate_v4(),
  nombre text not null,
  apellido text not null,
  email text unique not null,
  password text not null, -- se guarda el hash bcrypt
  created_at timestamp with time zone default now()
);

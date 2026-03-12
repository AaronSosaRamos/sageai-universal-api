#!/usr/bin/env python3
"""
Script para ejecutar migraciones SQL en Supabase.

USO:
    python run_migrations.py [migration_file.sql]

Si no se especifica archivo, ejecuta todas las migraciones en orden.
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
from supabase import create_client, Client

# Cargar variables de entorno
load_dotenv(find_dotenv(), override=True)

def run_migration(sql_file: Path):
    """Ejecuta un archivo SQL en Supabase."""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    
    if not supabase_url or not supabase_key:
        print("❌ Error: SUPABASE_URL y/o SUPABASE_KEY no configuradas en .env")
        sys.exit(1)
    
    if not sql_file.exists():
        print(f"❌ Error: El archivo {sql_file} no existe")
        sys.exit(1)
    
    print(f"📄 Leyendo migración: {sql_file.name}")
    with open(sql_file, "r", encoding="utf-8") as f:
        sql_content = f.read()
    
    if not sql_content.strip():
        print(f"⚠️  El archivo {sql_file.name} está vacío")
        return
    
    print(f"🚀 Ejecutando migración: {sql_file.name}")
    
    try:
        # Usar la función RPC de Supabase para ejecutar SQL
        # Nota: Esto requiere que tengas permisos de administrador
        # Alternativamente, puedes ejecutar esto directamente en el SQL Editor de Supabase
        client: Client = create_client(supabase_url, supabase_key)
        
        # Supabase no tiene una API directa para ejecutar SQL arbitrario desde el cliente Python
        # Necesitas ejecutarlo manualmente en el SQL Editor de Supabase
        print("\n" + "="*80)
        print("⚠️  IMPORTANTE: Supabase no permite ejecutar SQL arbitrario desde el cliente Python.")
        print("   Debes ejecutar este SQL manualmente en el SQL Editor de Supabase.")
        print("="*80)
        print("\nSQL a ejecutar:\n")
        print("-"*80)
        print(sql_content)
        print("-"*80)
        print("\n📝 Pasos:")
        print("1. Ve a tu proyecto en Supabase Dashboard")
        print("2. Navega a SQL Editor")
        print("3. Crea una nueva query")
        print("4. Copia y pega el SQL de arriba")
        print("5. Ejecuta la query")
        print("="*80 + "\n")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

def main():
    db_dir = Path(__file__).parent / "db"
    
    if len(sys.argv) > 1:
        # Ejecutar migración específica
        migration_file = Path(sys.argv[1])
        if not migration_file.is_absolute():
            migration_file = db_dir / migration_file
        run_migration(migration_file)
    else:
        # Ejecutar todas las migraciones en orden
        migration_files = sorted(db_dir.glob("*.sql"))
        if not migration_files:
            print("❌ No se encontraron archivos de migración en db/")
            sys.exit(1)
        
        print(f"📋 Encontradas {len(migration_files)} migraciones:")
        for f in migration_files:
            print(f"   - {f.name}")
        
        print("\n⚠️  Ejecutando todas las migraciones...")
        for migration_file in migration_files:
            print(f"\n{'='*80}")
            run_migration(migration_file)
            print(f"{'='*80}\n")

if __name__ == "__main__":
    main()

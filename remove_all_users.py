#!/usr/bin/env python3
"""
Script para eliminar todos los usuarios de la base de datos.

USO:
    python remove_all_users.py

ADVERTENCIA: Este script eliminará TODOS los usuarios de la base de datos.
"""

import sys
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# Cargar variables de entorno
load_dotenv(find_dotenv(), override=True)

# Agregar el directorio app al path
sys.path.insert(0, str(Path(__file__).parent))

from app.db.user_management import UserRepository


def remove_all_users():
    """Elimina todos los usuarios de la base de datos."""
    try:
        repo = UserRepository()
        
        # Obtener todos los usuarios
        print("📋 Obteniendo lista de usuarios...")
        users = repo.list_users(limit=10000)  # Límite alto para obtener todos
        
        if not users:
            print("✅ No hay usuarios para eliminar.")
            return
        
        print(f"⚠️  ADVERTENCIA: Se eliminarán {len(users)} usuario(s).")
        print("\nUsuarios a eliminar:")
        print("-" * 80)
        for user in users:
            print(f"  • {user.nombre} {user.apellido} ({user.email}) - ID: {user.id}")
        print("-" * 80)
        
        # Confirmar eliminación
        confirm = input("\n¿Estás seguro de que deseas eliminar TODOS estos usuarios? (escribe 'SI' para confirmar): ")
        
        if confirm != "SI":
            print("❌ Operación cancelada.")
            return
        
        # Eliminar usuarios
        print("\n🗑️  Eliminando usuarios...")
        deleted_count = 0
        errors = []
        
        for user in users:
            try:
                repo.delete_user(user.id)
                deleted_count += 1
                print(f"  ✓ Eliminado: {user.email}")
            except Exception as e:
                error_msg = f"Error eliminando {user.email}: {e}"
                errors.append(error_msg)
                print(f"  ✗ {error_msg}")
        
        # Resumen
        print("\n" + "=" * 80)
        print(f"✅ Proceso completado:")
        print(f"   • Usuarios eliminados: {deleted_count}/{len(users)}")
        if errors:
            print(f"   • Errores: {len(errors)}")
            for error in errors:
                print(f"     - {error}")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ Error fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    remove_all_users()

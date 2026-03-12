#!/usr/bin/env python3
"""
Script para crear usuarios desde un archivo JSON.

USO:
    python create_users_from_json.py <ruta_al_archivo.json>

FORMATO DEL JSON:
    [
        {
            "nombre": "Juan",
            "apellido": "Pérez",
            "email": "juan.perez@example.com",
            "password": "contraseña123"
        },
        {
            "nombre": "María",
            "apellido": "García",
            "email": "maria.garcia@example.com",
            "password": "contraseña456"
        }
    ]

EJEMPLO:
    python create_users_from_json.py users.json
"""

import sys
import json
from pathlib import Path
from typing import Tuple
from dotenv import load_dotenv, find_dotenv

# Cargar variables de entorno
load_dotenv(find_dotenv(), override=True)

# Agregar el directorio app al path
sys.path.insert(0, str(Path(__file__).parent))

from app.db.user_management import UserRepository, UserCreate


def validate_user_data(user_data: dict, index: int) -> Tuple[bool, str]:
    """Valida que los datos del usuario sean correctos."""
    required_fields = ["nombre", "apellido", "email", "password"]
    
    for field in required_fields:
        if field not in user_data:
            return False, f"Falta el campo requerido: '{field}'"
        
        if not isinstance(user_data[field], str) or not user_data[field].strip():
            return False, f"El campo '{field}' debe ser una cadena no vacía"
    
    # Validar formato de email básico
    email = user_data["email"].strip()
    if "@" not in email or "." not in email.split("@")[1]:
        return False, f"El email '{email}' no tiene un formato válido"
    
    return True, ""


def create_users_from_json(json_file_path: str):
    """Crea usuarios desde un archivo JSON."""
    try:
        # Verificar que el archivo existe
        json_path = Path(json_file_path)
        if not json_path.exists():
            print(f"❌ Error: El archivo '{json_file_path}' no existe.")
            sys.exit(1)
        
        # Leer el archivo JSON
        print(f"📖 Leyendo archivo: {json_file_path}")
        with open(json_path, "r", encoding="utf-8") as f:
            users_data = json.load(f)
        
        if not isinstance(users_data, list):
            print("❌ Error: El JSON debe ser un array de objetos.")
            sys.exit(1)
        
        if not users_data:
            print("⚠️  El archivo JSON está vacío. No hay usuarios para crear.")
            return
        
        print(f"📋 Se encontraron {len(users_data)} usuario(s) en el archivo.\n")
        
        # Validar todos los usuarios antes de crear
        print("🔍 Validando datos de usuarios...")
        validation_errors = []
        for i, user_data in enumerate(users_data, 1):
            is_valid, error_msg = validate_user_data(user_data, i)
            if not is_valid:
                validation_errors.append(f"Usuario #{i}: {error_msg}")
        
        if validation_errors:
            print("❌ Errores de validación encontrados:")
            for error in validation_errors:
                print(f"   • {error}")
            sys.exit(1)
        
        print("✅ Todos los usuarios son válidos.\n")
        
        # Mostrar resumen de usuarios a crear
        print("Usuarios a crear:")
        print("-" * 80)
        for user_data in users_data:
            print(f"  • {user_data['nombre']} {user_data['apellido']} ({user_data['email']})")
        print("-" * 80)
        
        # Confirmar creación
        confirm = input(f"\n¿Deseas crear estos {len(users_data)} usuario(s)? (escribe 'SI' para confirmar): ")
        
        if confirm != "SI":
            print("❌ Operación cancelada.")
            return
        
        # Crear usuarios
        repo = UserRepository()
        created_count = 0
        skipped_count = 0
        errors = []
        
        print("\n👤 Creando usuarios...")
        for user_data in users_data:
            try:
                # Verificar si el usuario ya existe
                existing_user = repo.get_user_by_email(user_data["email"])
                if existing_user:
                    print(f"  ⚠️  Saltado (ya existe): {user_data['email']}")
                    skipped_count += 1
                    continue
                
                # Crear usuario
                user_create = UserCreate(
                    nombre=user_data["nombre"].strip(),
                    apellido=user_data["apellido"].strip(),
                    email=user_data["email"].strip().lower(),
                    password=user_data["password"]
                )
                
                new_user = repo.create_user(user_create)
                created_count += 1
                print(f"  ✓ Creado: {new_user.email} (ID: {new_user.id})")
                
            except Exception as e:
                error_msg = f"Error creando {user_data.get('email', 'desconocido')}: {e}"
                errors.append(error_msg)
                print(f"  ✗ {error_msg}")
        
        # Resumen
        print("\n" + "=" * 80)
        print(f"✅ Proceso completado:")
        print(f"   • Usuarios creados: {created_count}")
        print(f"   • Usuarios saltados (ya existían): {skipped_count}")
        print(f"   • Errores: {len(errors)}")
        if errors:
            for error in errors:
                print(f"     - {error}")
        print("=" * 80)
        
    except json.JSONDecodeError as e:
        print(f"❌ Error al parsear el archivo JSON: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python create_users_from_json.py <ruta_al_archivo.json>")
        print("\nEjemplo:")
        print("  python create_users_from_json.py users.json")
        sys.exit(1)
    
    create_users_from_json(sys.argv[1])

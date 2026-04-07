"""
Thread Manager - Gestión profesional de chat threads.

Este módulo proporciona una capa de alto nivel para gestionar threads de chat,
incluyendo operaciones CRUD, validaciones de negocio y estadísticas.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

from supabase import create_client, Client
from postgrest.exceptions import APIError

from .chat_management import ChatThreadRepository, ChatMessageCreate


# ---------- Modelos de Dominio ----------
@dataclass(frozen=True)
class ThreadSummary:
    """Resumen de un thread con información agregada."""
    thread_id: str
    user_id: str
    message_count: int
    last_message: Optional[str]
    last_message_at: Optional[datetime]
    last_role: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class ThreadStats:
    """Estadísticas de un thread."""
    thread_id: str
    total_messages: int
    ai_messages: int
    human_messages: int
    first_message_at: Optional[datetime]
    last_message_at: Optional[datetime]


@dataclass(frozen=True)
class ThreadCreate:
    """Datos para crear un nuevo thread."""
    user_id: str
    thread_id: Optional[str] = None  # Si no se proporciona, se genera uno


# ---------- Manager Principal ----------
class ThreadManager:
    """
    Manager profesional para gestionar threads de chat.
    
    Proporciona una interfaz de alto nivel para operaciones de threads,
    incluyendo validaciones de negocio, manejo de errores y operaciones
    transaccionales cuando sea necesario.
    
    Requiere variables de entorno:
      - SUPABASE_URL
      - SUPABASE_KEY
    """

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
        storage_base_dir: Optional[Path] = None
    ):
        """
        Inicializa el ThreadManager.
        
        Args:
            supabase_url: URL de Supabase (opcional, usa env var si no se proporciona)
            supabase_key: Clave de Supabase (opcional, usa env var si no se proporciona)
            storage_base_dir: Directorio base para almacenamiento de archivos
        """
        self.supabase_url = supabase_url or os.getenv("SUPABASE_URL")
        self.supabase_key = supabase_key or os.getenv("SUPABASE_KEY")
        
        if not self.supabase_url or not self.supabase_key:
            raise RuntimeError("SUPABASE_URL y/o SUPABASE_KEY no configuradas.")
        
        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        self.chat_repo = ChatThreadRepository(supabase_url, supabase_key)
        self.storage_base_dir = storage_base_dir or Path("storage")

    # ------- Validaciones -------
    def _validate_user_id(self, user_id: str) -> None:
        """Valida que el user_id tenga formato válido."""
        if not user_id or not isinstance(user_id, str):
            raise ValueError("user_id debe ser una cadena no vacía")
        if len(user_id) > 255:
            raise ValueError("user_id excede la longitud máxima permitida")

    def _validate_thread_id(self, thread_id: str) -> None:
        """Valida que el thread_id tenga formato válido."""
        if not thread_id or not isinstance(thread_id, str):
            raise ValueError("thread_id debe ser una cadena no vacía")
        if len(thread_id) > 255:
            raise ValueError("thread_id excede la longitud máxima permitida")

    def _verify_thread_ownership(self, thread_id: str, user_id: str) -> bool:
        """
        Verifica que un thread pertenezca a un usuario específico.
        
        Args:
            thread_id: ID del thread a verificar
            user_id: ID del usuario propietario
            
        Returns:
            True si el thread pertenece al usuario, False en caso contrario
        """
        try:
            # Chat con asistente personalizado: thread_id = "assistant_{uuid}"
            if thread_id.startswith("assistant_"):
                from app.db.assistant_management import AssistantRepository

                aid = thread_id.replace("assistant_", "", 1)
                repo = AssistantRepository(self.supabase_url, self.supabase_key)
                a = repo.get(aid)
                if not a:
                    return False
                if a.user_id == user_id:
                    return True
                # Cualquier usuario puede usar hilos de asistentes ajenos (catálogo global)
                return True

            messages = self.chat_repo.get_thread_messages(thread_id, limit=1)
            if not messages:
                return False
            return messages[0].user_id == user_id
        except Exception:
            return False

    # ------- Create -------
    def create_thread(
        self,
        data: ThreadCreate,
        create_storage_dir: bool = True
    ) -> Dict[str, Any]:
        """
        Crea un nuevo thread de chat.
        
        Args:
            data: Datos para crear el thread
            create_storage_dir: Si True, crea el directorio de almacenamiento
            
        Returns:
            Diccionario con información del thread creado
            
        Raises:
            ValueError: Si los datos son inválidos
            RuntimeError: Si hay un error al crear el thread
        """
        self._validate_user_id(data.user_id)
        
        # Generar thread_id si no se proporciona
        thread_id = data.thread_id or str(uuid.uuid4())
        self._validate_thread_id(thread_id)
        
        # Crear directorio de almacenamiento si es necesario
        storage_path = None
        if create_storage_dir:
            storage_path = self.storage_base_dir / data.user_id / thread_id
            storage_path.mkdir(parents=True, exist_ok=True)
        
        # Crear mensaje inicial de bienvenida
        try:
            welcome_message = "¡Hola! Soy tu asistente. ¿En qué puedo ayudarte hoy?"
            initial_message = self.chat_repo.create_message(ChatMessageCreate(
                user_id=data.user_id,
                thread_id=thread_id,
                message=welcome_message,
                role="AI"
            ))
            
            return {
                "thread_id": thread_id,
                "user_id": data.user_id,
                "created_at": initial_message.created_at.isoformat() + "Z",
                "storage_path": str(storage_path) if storage_path else None,
                "initial_message_id": str(initial_message.id)
            }
        except Exception as e:
            # Si falla la creación del mensaje, limpiar el directorio si se creó
            if storage_path and storage_path.exists():
                try:
                    storage_path.rmdir()
                except Exception:
                    pass
            raise RuntimeError(f"Error al crear thread: {e}") from e

    # ------- Read -------
    def get_thread_summary(
        self,
        thread_id: str,
        user_id: Optional[str] = None
    ) -> Optional[ThreadSummary]:
        """
        Obtiene un resumen de un thread específico.
        
        Args:
            thread_id: ID del thread
            user_id: ID del usuario (opcional, para validación de propiedad)
            
        Returns:
            ThreadSummary si el thread existe, None en caso contrario
            
        Raises:
            ValueError: Si el thread no pertenece al usuario especificado
        """
        self._validate_thread_id(thread_id)
        
        try:
            messages = self.chat_repo.get_thread_messages(thread_id, limit=1000)
            
            if not messages:
                return None
            
            # Validar propiedad si se proporciona user_id
            if user_id:
                if messages[0].user_id != user_id:
                    raise ValueError("El thread no pertenece al usuario especificado")
            
            # Calcular estadísticas
            message_count = len(messages)
            last_message_obj = messages[-1] if messages else None
            
            # Obtener el primer mensaje para created_at
            first_message = messages[0] if messages else None
            
            return ThreadSummary(
                thread_id=thread_id,
                user_id=messages[0].user_id,
                message_count=message_count,
                last_message=last_message_obj.message if last_message_obj else None,
                last_message_at=last_message_obj.created_at if last_message_obj else None,
                last_role=last_message_obj.role if last_message_obj else None,
                created_at=first_message.created_at if first_message else datetime.utcnow()
            )
        except ValueError:
            raise
        except Exception as e:
            raise RuntimeError(f"Error al obtener resumen del thread {thread_id}: {e}") from e

    def list_user_threads(
        self,
        user_id: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Lista todos los threads de un usuario con información resumida.
        
        Args:
            user_id: ID del usuario
            limit: Número máximo de threads a retornar
            offset: Número de threads a saltar (para paginación)
            
        Returns:
            Lista de diccionarios con información resumida de cada thread
        """
        self._validate_user_id(user_id)
        
        if limit < 1 or limit > 1000:
            raise ValueError("limit debe estar entre 1 y 1000")
        if offset < 0:
            raise ValueError("offset debe ser mayor o igual a 0")
        
        try:
            threads_data = self.chat_repo.list_user_threads(
                user_id,
                limit_threads=limit + offset
            )
            
            # Aplicar offset
            threads_data = threads_data[offset:offset + limit]
            
            # Formatear resultados
            result = []
            for thread_data in threads_data:
                result.append({
                    "thread_id": thread_data["thread_id"],
                    "last_message": thread_data.get("last_message"),
                    "last_message_at": thread_data.get("last_message_at"),
                    "last_role": thread_data.get("last_role")
                })
            
            return result
        except Exception as e:
            raise RuntimeError(f"Error al listar threads del usuario {user_id}: {e}") from e

    def get_thread_stats(self, thread_id: str, user_id: Optional[str] = None) -> Optional[ThreadStats]:
        """
        Obtiene estadísticas detalladas de un thread.
        
        Args:
            thread_id: ID del thread
            user_id: ID del usuario (opcional, para validación)
            
        Returns:
            ThreadStats si el thread existe, None en caso contrario
        """
        self._validate_thread_id(thread_id)
        
        try:
            messages = self.chat_repo.get_thread_messages(thread_id, limit=10000)
            
            if not messages:
                return None
            
            # Validar propiedad si se proporciona user_id
            if user_id:
                if messages[0].user_id != user_id:
                    raise ValueError("El thread no pertenece al usuario especificado")
            
            # Calcular estadísticas
            total_messages = len(messages)
            ai_messages = sum(1 for m in messages if m.role == "AI")
            human_messages = total_messages - ai_messages
            
            first_message = messages[0] if messages else None
            last_message = messages[-1] if messages else None
            
            return ThreadStats(
                thread_id=thread_id,
                total_messages=total_messages,
                ai_messages=ai_messages,
                human_messages=human_messages,
                first_message_at=first_message.created_at if first_message else None,
                last_message_at=last_message.created_at if last_message else None
            )
        except ValueError:
            raise
        except Exception as e:
            raise RuntimeError(f"Error al obtener estadísticas del thread {thread_id}: {e}") from e

    # ------- Delete -------
    def delete_thread(
        self,
        thread_id: str,
        user_id: str,
        delete_storage: bool = True
    ) -> Dict[str, Any]:
        """
        Elimina un thread y todos sus mensajes.
        
        Args:
            thread_id: ID del thread a eliminar
            user_id: ID del usuario propietario
            delete_storage: Si True, elimina también el directorio de almacenamiento
            
        Returns:
            Diccionario con información de la eliminación
            
        Raises:
            ValueError: Si el thread no pertenece al usuario
            RuntimeError: Si hay un error al eliminar
        """
        self._validate_thread_id(thread_id)
        self._validate_user_id(user_id)
        
        # Verificar propiedad
        if not self._verify_thread_ownership(thread_id, user_id):
            raise ValueError("El thread no pertenece al usuario especificado")
        
        try:
            # Eliminar mensajes de la base de datos (assistant_* puede compartir thread_id entre usuarios)
            if thread_id.startswith("assistant_"):
                deleted_count = self.chat_repo.delete_thread_messages_for_user(thread_id, user_id)
            else:
                deleted_count = self.chat_repo.delete_thread(thread_id)
            
            # Eliminar directorio de almacenamiento si es necesario
            storage_path = self.storage_base_dir / user_id / thread_id
            storage_deleted = False
            if delete_storage and storage_path.exists():
                import shutil
                shutil.rmtree(storage_path, ignore_errors=True)
                storage_deleted = True
            
            return {
                "thread_id": thread_id,
                "deleted_messages": deleted_count,
                "storage_deleted": storage_deleted,
                "deleted_at": datetime.utcnow().isoformat() + "Z"
            }
        except ValueError:
            raise
        except Exception as e:
            raise RuntimeError(f"Error al eliminar thread {thread_id}: {e}") from e

    # ------- Utilidades -------
    def thread_exists(self, thread_id: str) -> bool:
        """
        Verifica si un thread existe.
        
        Args:
            thread_id: ID del thread
            
        Returns:
            True si el thread existe, False en caso contrario
        """
        try:
            messages = self.chat_repo.get_thread_messages(thread_id, limit=1)
            return len(messages) > 0
        except Exception:
            return False

    def get_user_thread_count(self, user_id: str) -> int:
        """
        Obtiene el número total de threads de un usuario.
        
        Args:
            user_id: ID del usuario
            
        Returns:
            Número total de threads
        """
        try:
            threads = self.chat_repo.list_user_threads(user_id, limit_threads=10000)
            return len(threads)
        except Exception:
            return 0


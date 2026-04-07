# chat_repository.py
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional, List, Dict, Any

from supabase import create_client, Client
from postgrest.exceptions import APIError


# ---------- Modelos ----------
Role = Literal["AI", "Human"]

@dataclass(frozen=True)
class ChatMessage:
    id: uuid.UUID
    user_id: str
    thread_id: str
    message: str
    role: Role
    created_at: datetime


@dataclass(frozen=True)
class ChatMessageCreate:
    user_id: str
    thread_id: str
    message: str
    role: Role


# ---------- Repositorio ----------
class ChatThreadRepository:
    """
    Repositorio para gestionar mensajes de chat en la tabla 'chat_threads' (Supabase / Postgres).

    Requiere variables de entorno:
      - SUPABASE_URL
      - SUPABASE_KEY  (usa la service-role key en servidores; para frontend usar anon key con RLS bien definidas)
    """

    def __init__(self, supabase_url: Optional[str] = None, supabase_key: Optional[str] = None, table: str = "chat_threads"):
        self.supabase_url = supabase_url or os.getenv("SUPABASE_URL")
        self.supabase_key = supabase_key or os.getenv("SUPABASE_KEY")
        if not self.supabase_url or not self.supabase_key:
            raise RuntimeError("SUPABASE_URL y/o SUPABASE_KEY no configuradas.")
        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        self.table = table

    # ------- Helpers -------
    @staticmethod
    def _to_chat_message(row: Dict[str, Any]) -> ChatMessage:
        return ChatMessage(
            id=uuid.UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
            user_id=row["user_id"],
            thread_id=row["thread_id"],
            message=row["message"],
            role=row["role"],
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if isinstance(row["created_at"], str)
            else row["created_at"],
        )

    # ------- Create -------
    def create_message(self, data: ChatMessageCreate) -> ChatMessage:
        if data.role not in ("AI", "Human"):
            raise ValueError("role debe ser 'AI' o 'Human'.")

        payload = {
            "user_id": data.user_id,
            "thread_id": data.thread_id,
            "message": data.message,
            "role": data.role,
        }
        try:
            # Primero insertamos el mensaje
            res = self.client.table(self.table).insert(payload).execute()
            if not res.data:
                raise RuntimeError("No se recibieron datos después de la inserción")
            
            # Luego obtenemos el mensaje insertado
            inserted_id = res.data[0]["id"]
            get_res = self.client.table(self.table).select("*").eq("id", inserted_id).execute()
            if not get_res.data:
                raise RuntimeError("No se pudo recuperar el mensaje insertado")
            
            return self._to_chat_message(get_res.data[0])
        except APIError as e:
            # Loguea e.json() si necesitas detalle
            raise RuntimeError(f"Error al crear mensaje: {e}") from e

    # ------- Read -------
    def get_message(self, message_id: uuid.UUID | str) -> Optional[ChatMessage]:
        try:
            res = self.client.table(self.table).select("*").eq("id", str(message_id)).execute()
            if not res.data:
                return None
            return self._to_chat_message(res.data[0])
        except APIError as e:
            # 406/410/404 => no encontrado
            return None

    def get_thread_messages(
        self,
        thread_id: str,
        *,
        limit: int = 200,
        ascending: bool = True,
        user_id: Optional[str] = None,
    ) -> List[ChatMessage]:
        try:
            q = self.client.table(self.table).select("*").eq("thread_id", thread_id)
            if user_id is not None:
                q = q.eq("user_id", user_id)
            res = q.order("created_at", desc=not ascending).limit(limit).execute()
            return [self._to_chat_message(r) for r in (res.data or [])]
        except APIError as e:
            raise RuntimeError(f"Error al listar mensajes del thread {thread_id}: {e}") from e

    def list_user_threads(
        self,
        user_id: str,
        *,
        limit_threads: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Devuelve threads del usuario con su último mensaje.
        Nota: PostgREST no soporta 'group by' directo desde el SDK; usamos una vista simple emulada:
        1) Obtenemos últimos mensajes por thread ordenando desc y aplicando 'select distinct on' vía RPC opcional
           o, si no tienes RPC, filtramos en cliente.
        Para simplicidad y portabilidad, lo hacemos en cliente.
        """
        try:
            res = (
                self.client.table(self.table)
                .select("*")
                .eq("user_id", user_id)
                .order("thread_id", desc=False)
                .order("created_at", desc=True)
                .limit(5000)
                .execute()
            )
            rows = res.data or []
            last_by_thread: Dict[str, Dict[str, Any]] = {}
            for r in rows:
                tid = r["thread_id"]
                if tid not in last_by_thread:
                    last_by_thread[tid] = r
            # Ordena por fecha más reciente y limita
            items = sorted(last_by_thread.values(), key=lambda x: x["created_at"], reverse=True)[:limit_threads]
            return [
                {
                    "thread_id": it["thread_id"],
                    "last_message_at": it["created_at"],
                    "last_role": it["role"],
                    "last_message": it["message"],
                }
                for it in items
            ]
        except APIError as e:
            raise RuntimeError(f"Error al listar threads de usuario {user_id}: {e}") from e

    # ------- Update -------
    def update_message(
        self,
        message_id: uuid.UUID | str,
        *,
        message: Optional[str] = None,
        role: Optional[Role] = None,
    ) -> ChatMessage:
        if role is not None and role not in ("AI", "Human"):
            raise ValueError("role debe ser 'AI' o 'Human'.")
        updates: Dict[str, Any] = {}
        if message is not None:
            updates["message"] = message
        if role is not None:
            updates["role"] = role
        if not updates:
            raise ValueError("No hay campos para actualizar.")

        try:
            # Primero actualizamos
            update_res = self.client.table(self.table).update(updates).eq("id", str(message_id)).execute()
            if not update_res.data:
                raise RuntimeError("No se pudo actualizar el mensaje")
            
            # Luego obtenemos el mensaje actualizado
            get_res = self.client.table(self.table).select("*").eq("id", str(message_id)).execute()
            if not get_res.data:
                raise RuntimeError("No se pudo recuperar el mensaje actualizado")
            
            return self._to_chat_message(get_res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al actualizar mensaje {message_id}: {e}") from e

    # ------- Delete -------
    def delete_message(self, message_id: uuid.UUID | str) -> bool:
        try:
            _ = self.client.table(self.table).delete().eq("id", str(message_id)).execute()
            return True
        except APIError as e:
            raise RuntimeError(f"Error al borrar mensaje {message_id}: {e}") from e

    def delete_thread(self, thread_id: str) -> int:
        """
        Borra todos los mensajes del thread. Retorna cantidad eliminada.
        """
        try:
            res = self.client.table(self.table).delete().eq("thread_id", thread_id).execute()
            # postgrest no siempre retorna count si no se configura prefer=return=representation.
            # Hacemos una verificación adicional:
            # Si necesitas count exacto, configura RLS/Prefer o primero cuenta y luego borra.
            return len(res.data) if isinstance(res.data, list) else 0
        except APIError as e:
            raise RuntimeError(f"Error al borrar thread {thread_id}: {e}") from e

    def delete_thread_messages_for_user(self, thread_id: str, user_id: str) -> int:
        """Borra mensajes de un thread solo para un usuario (p. ej. assistant_* compartido)."""
        try:
            res = (
                self.client.table(self.table)
                .delete()
                .eq("thread_id", thread_id)
                .eq("user_id", user_id)
                .execute()
            )
            return len(res.data) if isinstance(res.data, list) else 0
        except APIError as e:
            raise RuntimeError(f"Error al borrar mensajes del thread {thread_id}: {e}") from e

# chat_history.py
from typing import Optional

class ChatHistory:
    """
    Clase para obtener historial de un thread como texto plano.
    """

    def __init__(self, repo: Optional[ChatThreadRepository] = None):
        self.repo = repo or ChatThreadRepository()

    def get_history_string(
        self, thread_id: str, limit: int = 10, ascending: bool = True
    ) -> str:
        """
        Retorna un mega-string con los últimos N mensajes del thread en orden cronológico.
        Formato:
            [USER] mensaje...
            [AI] mensaje...
        """
        messages = self.repo.get_thread_messages(
            thread_id, limit=limit, ascending=ascending
        )

        lines = []
        for m in messages:
            prefix = "[AI]" if m.role == "AI" else "[USER]"
            lines.append(f"{prefix} {m.message.strip()}")

        return "\n".join(lines)

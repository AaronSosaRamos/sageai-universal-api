"""
Custom Space Management - Gestión de espacios personalizados de usuarios.

Este módulo gestiona los espacios personalizados donde los usuarios pueden
definir sus memorias personalizadas y cómo debe actuar el agente.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
from supabase import create_client, Client
from postgrest.exceptions import APIError


# ---------- Modelos ----------
@dataclass(frozen=True)
class CustomSpace:
    """Espacio personalizado de un usuario."""
    id: uuid.UUID
    user_id: str
    title: str
    custom_memories: str
    agent_instructions: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class CustomSpaceCreate:
    """Datos para crear un espacio personalizado."""
    user_id: str
    title: str = "Mi Espacio Personalizado"
    custom_memories: str = ""
    agent_instructions: str = ""
    is_active: bool = True


@dataclass
class CustomSpaceUpdate:
    """Datos para actualizar un espacio personalizado."""
    title: Optional[str] = None
    custom_memories: Optional[str] = None
    agent_instructions: Optional[str] = None
    is_active: Optional[bool] = None


# ---------- Repositorio ----------
class CustomSpaceRepository:
    """
    Repositorio para gestionar espacios personalizados de usuarios.
    
    Requiere variables de entorno:
      - SUPABASE_URL
      - SUPABASE_KEY
    """

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None
    ):
        self.supabase_url = supabase_url or os.getenv("SUPABASE_URL")
        self.supabase_key = supabase_key or os.getenv("SUPABASE_KEY")
        if not self.supabase_url or not self.supabase_key:
            raise RuntimeError("SUPABASE_URL y/o SUPABASE_KEY no configuradas.")
        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        self.table = "user_custom_spaces"

    # ------- Helpers -------
    @staticmethod
    def _to_custom_space(row: Dict[str, Any]) -> CustomSpace:
        """Convierte una fila de la base de datos a un objeto CustomSpace."""
        return CustomSpace(
            id=uuid.UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
            user_id=row["user_id"],
            title=row["title"],
            custom_memories=row["custom_memories"] or "",
            agent_instructions=row["agent_instructions"] or "",
            is_active=row.get("is_active", True),
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if isinstance(row["created_at"], str)
            else row["created_at"],
            updated_at=datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
            if isinstance(row["updated_at"], str)
            else row["updated_at"],
        )

    # ------- Create -------
    def create_space(self, data: CustomSpaceCreate) -> CustomSpace:
        """Crea un nuevo espacio personalizado."""
        payload = {
            "user_id": data.user_id,
            "title": data.title,
            "custom_memories": data.custom_memories,
            "agent_instructions": data.agent_instructions,
            "is_active": data.is_active,
        }

        try:
            res = self.client.table(self.table).insert(payload).execute()
            if not res.data:
                raise RuntimeError("No se recibieron datos después de la inserción")
            return self._to_custom_space(res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al crear espacio personalizado: {e}") from e

    # ------- Read -------
    def get_space(self, space_id: uuid.UUID | str) -> Optional[CustomSpace]:
        """Obtiene un espacio personalizado por su ID."""
        try:
            res = self.client.table(self.table).select("*").eq("id", str(space_id)).execute()
            if not res.data:
                return None
            return self._to_custom_space(res.data[0])
        except APIError:
            return None

    def get_user_spaces(self, user_id: str, active_only: bool = False) -> List[CustomSpace]:
        """Obtiene todos los espacios personalizados de un usuario."""
        try:
            query = self.client.table(self.table).select("*").eq("user_id", user_id)
            if active_only:
                query = query.eq("is_active", True)
            res = query.order("created_at", desc=True).execute()
            return [self._to_custom_space(r) for r in (res.data or [])]
        except APIError as e:
            raise RuntimeError(f"Error al obtener espacios del usuario: {e}") from e

    def get_active_space(self, user_id: str) -> Optional[CustomSpace]:
        """Obtiene el espacio activo de un usuario (si existe)."""
        spaces = self.get_user_spaces(user_id, active_only=True)
        return spaces[0] if spaces else None

    # ------- Update -------
    def update_space(self, space_id: uuid.UUID | str, data: CustomSpaceUpdate) -> CustomSpace:
        """Actualiza un espacio personalizado."""
        payload: Dict[str, Any] = {}
        
        if data.title is not None:
            payload["title"] = data.title
        if data.custom_memories is not None:
            payload["custom_memories"] = data.custom_memories
        if data.agent_instructions is not None:
            payload["agent_instructions"] = data.agent_instructions
        if data.is_active is not None:
            payload["is_active"] = data.is_active
        
        # Actualizar timestamp
        payload["updated_at"] = datetime.utcnow().isoformat()

        try:
            res = self.client.table(self.table).update(payload).eq("id", str(space_id)).execute()
            if not res.data:
                raise RuntimeError("No se recibieron datos después de la actualización")
            return self._to_custom_space(res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al actualizar espacio personalizado: {e}") from e

    # ------- Delete -------
    def delete_space(self, space_id: uuid.UUID | str) -> bool:
        """Elimina un espacio personalizado."""
        try:
            _ = self.client.table(self.table).delete().eq("id", str(space_id)).execute()
            return True
        except APIError as e:
            raise RuntimeError(f"Error al eliminar espacio personalizado: {e}") from e

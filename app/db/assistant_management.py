"""
Assistant Management - Gestión de asistentes personalizados (system prompts).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
from supabase import create_client, Client
from postgrest.exceptions import APIError


@dataclass(frozen=True)
class Assistant:
    id: uuid.UUID
    user_id: str
    name: str
    description: str
    system_prompt: str
    created_at: datetime
    updated_at: datetime


@dataclass
class AssistantCreate:
    user_id: str
    name: str
    description: str = ""
    system_prompt: str = ""


@dataclass
class AssistantUpdate:
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None


class AssistantRepository:
    def __init__(self, supabase_url: Optional[str] = None, supabase_key: Optional[str] = None):
        self.supabase_url = supabase_url or os.getenv("SUPABASE_URL")
        self.supabase_key = supabase_key or os.getenv("SUPABASE_KEY")
        if not self.supabase_url or not self.supabase_key:
            raise RuntimeError("SUPABASE_URL y/o SUPABASE_KEY no configuradas.")
        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        self.table = "user_assistants"

    @staticmethod
    def _to_assistant(row: Dict[str, Any]) -> Assistant:
        return Assistant(
            id=uuid.UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
            user_id=row["user_id"],
            name=row["name"],
            description=row.get("description") or "",
            system_prompt=row["system_prompt"] or "",
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if isinstance(row["created_at"], str) else row["created_at"],
            updated_at=datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
            if isinstance(row["updated_at"], str) else row["updated_at"],
        )

    def create(self, data: AssistantCreate) -> Assistant:
        payload = {
            "user_id": data.user_id,
            "name": data.name,
            "description": data.description,
            "system_prompt": data.system_prompt,
        }
        try:
            res = self.client.table(self.table).insert(payload).execute()
            if not res.data:
                raise RuntimeError("No se recibieron datos después de la inserción")
            return self._to_assistant(res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al crear asistente: {e}") from e

    def get(self, assistant_id: uuid.UUID | str) -> Optional[Assistant]:
        try:
            res = self.client.table(self.table).select("*").eq("id", str(assistant_id)).execute()
            if not res.data:
                return None
            return self._to_assistant(res.data[0])
        except APIError:
            return None

    def get_user_assistants(self, user_id: str, limit: int = 10, offset: int = 0) -> List[Assistant]:
        try:
            res = self.client.table(self.table).select("*").eq("user_id", user_id).order("updated_at", desc=True).range(offset, offset + limit - 1).execute()
            return [self._to_assistant(r) for r in (res.data or [])]
        except APIError as e:
            raise RuntimeError(f"Error al obtener asistentes: {e}") from e

    def count_user_assistants(self, user_id: str) -> int:
        """Cuenta el total de asistentes del usuario."""
        try:
            res = self.client.table(self.table).select("id", count="exact").eq("user_id", user_id).execute()
            return res.count or 0
        except APIError as e:
            raise RuntimeError(f"Error al contar asistentes: {e}") from e

    def update(self, assistant_id: uuid.UUID | str, data: AssistantUpdate) -> Assistant:
        payload: Dict[str, Any] = {}
        if data.name is not None:
            payload["name"] = data.name
        if data.description is not None:
            payload["description"] = data.description
        if data.system_prompt is not None:
            payload["system_prompt"] = data.system_prompt
        payload["updated_at"] = datetime.utcnow().isoformat()

        try:
            res = self.client.table(self.table).update(payload).eq("id", str(assistant_id)).execute()
            if not res.data:
                raise RuntimeError("No se recibieron datos después de la actualización")
            return self._to_assistant(res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al actualizar asistente: {e}") from e

    def delete(self, assistant_id: uuid.UUID | str) -> bool:
        try:
            _ = self.client.table(self.table).delete().eq("id", str(assistant_id)).execute()
            return True
        except APIError as e:
            raise RuntimeError(f"Error al eliminar asistente: {e}") from e

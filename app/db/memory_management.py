"""
Memory Management - Gestión de memorias semánticas y procedimentales.

Este módulo gestiona los perfiles de usuario (semántico y procedimental)
que se actualizan asíncronamente basándose en las conversaciones del usuario.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from supabase import create_client, Client
from postgrest.exceptions import APIError


# ---------- Modelos ----------
@dataclass(frozen=True)
class SemanticMemory:
    """Perfil semántico del usuario."""
    user_id: str
    user_name: str
    profile_summary: str
    key_concepts: List[str]
    preferences: List[str]
    interests: List[str]
    knowledge_domains: List[str]
    tags: List[str]
    last_updated_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class ProceduralMemory:
    """Perfil procedimental del usuario."""
    user_id: str
    user_name: str
    profile_summary: str
    preferred_methods: List[str]
    common_procedures: List[str]
    workflow_patterns: List[str]
    efficiency_tips: List[str]
    tags: List[str]
    last_updated_at: datetime
    created_at: datetime


# ---------- Repositorio ----------
class MemoryRepository:
    """
    Repositorio para gestionar memorias semánticas y procedimentales.
    
    Requiere variables de entorno:
      - SUPABASE_URL
      - SUPABASE_KEY
      - GOOGLE_API_KEY (para generar embeddings)
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
        self.embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")

    # ------- Helpers -------
    @staticmethod
    def _to_semantic_memory(row: Dict[str, Any]) -> SemanticMemory:
        return SemanticMemory(
            user_id=row["user_id"],
            user_name=row.get("user_name", "Usuario"),
            profile_summary=row["profile_summary"],
            key_concepts=row.get("key_concepts", []) or [],
            preferences=row.get("preferences", []) or [],
            interests=row.get("interests", []) or [],
            knowledge_domains=row.get("knowledge_domains", []) or [],
            tags=row.get("tags", []) or [],
            last_updated_at=datetime.fromisoformat(row["last_updated_at"].replace("Z", "+00:00"))
            if isinstance(row["last_updated_at"], str)
            else row["last_updated_at"],
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if isinstance(row["created_at"], str)
            else row["created_at"],
        )

    @staticmethod
    def _to_procedural_memory(row: Dict[str, Any]) -> ProceduralMemory:
        return ProceduralMemory(
            user_id=row["user_id"],
            user_name=row.get("user_name", "Usuario"),
            profile_summary=row["profile_summary"],
            preferred_methods=row.get("preferred_methods", []) or [],
            common_procedures=row.get("common_procedures", []) or [],
            workflow_patterns=row.get("workflow_patterns", []) or [],
            efficiency_tips=row.get("efficiency_tips", []) or [],
            tags=row.get("tags", []) or [],
            last_updated_at=datetime.fromisoformat(row["last_updated_at"].replace("Z", "+00:00"))
            if isinstance(row["last_updated_at"], str)
            else row["last_updated_at"],
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if isinstance(row["created_at"], str)
            else row["created_at"],
        )

    def _generate_embedding(self, text: str) -> List[float]:
        """Genera embedding usando Google Generative AI."""
        try:
            return self.embeddings.embed_query(text)
        except Exception as e:
            raise RuntimeError(f"Error generando embedding: {e}") from e

    # ------- Semantic Memory -------
    def get_semantic_memory(self, user_id: str) -> Optional[SemanticMemory]:
        """Obtiene el perfil semántico del usuario."""
        try:
            res = self.client.table("semantic_memories").select("*").eq("user_id", user_id).execute()
            if not res.data:
                return None
            return self._to_semantic_memory(res.data[0])
        except APIError:
            return None

    def upsert_semantic_memory(
        self,
        user_id: str,
        user_name: str,
        profile_summary: str,
        key_concepts: Optional[List[str]] = None,
        preferences: Optional[List[str]] = None,
        interests: Optional[List[str]] = None,
        knowledge_domains: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ) -> SemanticMemory:
        """Crea o actualiza el perfil semántico del usuario."""
        payload = {
            "user_id": user_id,
            "user_name": user_name,
            "profile_summary": profile_summary,
            "key_concepts": key_concepts or [],
            "preferences": preferences or [],
            "interests": interests or [],
            "knowledge_domains": knowledge_domains or [],
            "tags": tags or [],
        }

        try:
            # Upsert (insert or update)
            res = self.client.table("semantic_memories").upsert(payload).execute()
            if not res.data:
                raise RuntimeError("No se recibieron datos después del upsert")
            return self._to_semantic_memory(res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al guardar memoria semántica: {e}") from e

    # ------- Procedural Memory -------
    def get_procedural_memory(self, user_id: str) -> Optional[ProceduralMemory]:
        """Obtiene el perfil procedimental del usuario."""
        try:
            res = self.client.table("procedural_memories").select("*").eq("user_id", user_id).execute()
            if not res.data:
                return None
            return self._to_procedural_memory(res.data[0])
        except APIError:
            return None

    def upsert_procedural_memory(
        self,
        user_id: str,
        user_name: str,
        profile_summary: str,
        preferred_methods: Optional[List[str]] = None,
        common_procedures: Optional[List[str]] = None,
        workflow_patterns: Optional[List[str]] = None,
        efficiency_tips: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ) -> ProceduralMemory:
        """Crea o actualiza el perfil procedimental del usuario."""
        payload = {
            "user_id": user_id,
            "user_name": user_name,
            "profile_summary": profile_summary,
            "preferred_methods": preferred_methods or [],
            "common_procedures": common_procedures or [],
            "workflow_patterns": workflow_patterns or [],
            "efficiency_tips": efficiency_tips or [],
            "tags": tags or [],
        }

        try:
            # Upsert (insert or update)
            res = self.client.table("procedural_memories").upsert(payload).execute()
            if not res.data:
                raise RuntimeError("No se recibieron datos después del upsert")
            return self._to_procedural_memory(res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al guardar memoria procedimental: {e}") from e


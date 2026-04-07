# user_repository.py
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List

import bcrypt
from supabase import create_client, Client
from postgrest.exceptions import APIError


# ---------- Modelo ----------
@dataclass(frozen=True)
class User:
    id: uuid.UUID
    nombre: str
    apellido: str
    email: str
    password: str  # hash bcrypt
    user_type: str  # 'user' | 'admin'
    created_at: datetime


@dataclass(frozen=True)
class UserCreate:
    nombre: str
    apellido: str
    email: str
    password: str  # en texto plano, se encripta antes de guardar
    user_type: str = "user"


# ---------- Repositorio ----------
class UserRepository:
    """
    Repositorio para gestionar usuarios en la tabla 'users' (Supabase / Postgres).
    """

    def __init__(self, supabase_url: Optional[str] = None, supabase_key: Optional[str] = None, table: str = "users"):
        self.supabase_url = supabase_url or os.getenv("SUPABASE_URL")
        self.supabase_key = supabase_key or os.getenv("SUPABASE_KEY")
        if not self.supabase_url or not self.supabase_key:
            raise RuntimeError("SUPABASE_URL y/o SUPABASE_KEY no configuradas.")
        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        self.table = table

    # ------- Helpers -------
    @staticmethod
    def _to_user(row: Dict[str, Any]) -> User:
        return User(
            id=uuid.UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
            nombre=row["nombre"],
            apellido=row["apellido"],
            email=row["email"],
            password=row["password"],  # hash bcrypt
            user_type=(row.get("user_type") or "user"),
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if isinstance(row["created_at"], str)
            else row["created_at"],
        )

    @staticmethod
    def _hash_password(plain_password: str) -> str:
        """Hashea la contraseña en bcrypt."""
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
        return hashed.decode("utf-8")

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verifica si una contraseña en texto plano coincide con el hash bcrypt almacenado."""
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

    # ------- Create -------
    def create_user(self, data: UserCreate) -> User:
        ut = (data.user_type or "user").strip().lower()
        if ut not in ("user", "admin"):
            ut = "user"
        payload = {
            "nombre": data.nombre,
            "apellido": data.apellido,
            "email": data.email,
            "password": self._hash_password(data.password),
            "user_type": ut,
        }
        try:
            res = self.client.table(self.table).insert(payload).execute()
            if not res.data:
                raise RuntimeError("No se recibieron datos después de la inserción")

            return self._to_user(res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al crear usuario: {e}") from e

    # ------- Read -------
    def get_user_by_email(self, email: str) -> Optional[User]:
        try:
            res = self.client.table(self.table).select("*").eq("email", email).execute()
            if not res.data:
                return None
            return self._to_user(res.data[0])
        except APIError:
            return None

    def get_user(self, user_id: uuid.UUID | str) -> Optional[User]:
        try:
            res = self.client.table(self.table).select("*").eq("id", str(user_id)).execute()
            if not res.data:
                return None
            return self._to_user(res.data[0])
        except APIError:
            return None

    # ------- List -------
    def list_users(self, limit: int = 100) -> List[User]:
        try:
            res = self.client.table(self.table).select("*").limit(limit).execute()
            return [self._to_user(r) for r in (res.data or [])]
        except APIError as e:
            raise RuntimeError(f"Error al listar usuarios: {e}") from e

    def count_users(self) -> int:
        try:
            res = self.client.table(self.table).select("id", count="exact").execute()
            return res.count or 0
        except APIError:
            return 0

    def count_users_created_since(self, since_iso: str) -> int:
        """Cuentas nuevas en el periodo (para informes de adopción)."""
        try:
            res = (
                self.client.table(self.table)
                .select("id", count="exact")
                .gte("created_at", since_iso)
                .execute()
            )
            return res.count or 0
        except APIError:
            return 0

    # ------- Delete -------
    def delete_user(self, user_id: uuid.UUID | str) -> bool:
        try:
            _ = self.client.table(self.table).delete().eq("id", str(user_id)).execute()
            return True
        except APIError as e:
            raise RuntimeError(f"Error al borrar usuario {user_id}: {e}") from e

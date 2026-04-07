"""
Límites de uso diario (interacciones) por usuario tipo 'user'.
Requiere la función SQL consume_daily_interaction (ver db/007_user_type_and_daily_limits.sql).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Optional, Tuple

from supabase import create_client, Client
from postgrest.exceptions import APIError


class UsageLimitRepository:
    def __init__(self, supabase_url: Optional[str] = None, supabase_key: Optional[str] = None):
        self.supabase_url = supabase_url or os.getenv("SUPABASE_URL")
        self.supabase_key = supabase_key or os.getenv("SUPABASE_KEY")
        if not self.supabase_url or not self.supabase_key:
            raise RuntimeError("SUPABASE_URL y/o SUPABASE_KEY no configuradas.")
        self.client: Client = create_client(self.supabase_url, self.supabase_key)

    def consume_daily_interaction(
        self,
        user_id: str,
        day: date,
        limit: int,
    ) -> Tuple[bool, int]:
        """
        Intenta consumir una interacción del cupo diario (atómico).
        Retorna (allowed, current_count después de la operación o el conteo actual si no hubo cupo).
        """
        try:
            res = (
                self.client.rpc(
                    "consume_daily_interaction",
                    {
                        "p_user_id": user_id,
                        "p_day": day.isoformat(),
                        "p_limit": limit,
                    },
                )
                .execute()
            )
            if not res.data:
                raise RuntimeError("consume_daily_interaction no devolvió datos")
            row = res.data[0] if isinstance(res.data, list) else res.data
            allowed = bool(row.get("allowed"))
            cnt = int(row.get("current_count", 0))
            return allowed, cnt
        except APIError as e:
            print(f"[UsageLimit] RPC consume_daily_interaction: {e}")
            raise RuntimeError(f"Error en límite de uso: {e}") from e

"""
Evaluaciones: almacenamiento en Supabase (Postgres).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from supabase import create_client, Client
from postgrest.exceptions import APIError


@dataclass(frozen=True)
class Evaluation:
    id: uuid.UUID
    author_user_id: str
    title: str
    description: str
    requirements_hint: str
    questions_json: List[Dict[str, Any]]
    published: bool
    published_at: Optional[datetime]
    share_token: Optional[str]
    duration_minutes: Optional[int]
    created_at: datetime
    updated_at: datetime


@dataclass
class EvaluationCreate:
    author_user_id: str
    title: str
    description: str = ""
    requirements_hint: str = ""
    questions_json: List[Dict[str, Any]] = field(default_factory=list)
    duration_minutes: Optional[int] = None
    share_token: Optional[str] = None


@dataclass
class EvaluationUpdate:
    title: Optional[str] = None
    description: Optional[str] = None
    requirements_hint: Optional[str] = None
    questions_json: Optional[List[Dict[str, Any]]] = None
    published: Optional[bool] = None
    duration_minutes: Optional[int] = None
    share_token: Optional[str] = None


@dataclass(frozen=True)
class EvaluationAttempt:
    id: uuid.UUID
    evaluation_id: uuid.UUID
    user_id: str
    answers_json: Dict[str, Any]
    score_percent: Optional[float]
    feedback: str
    created_at: datetime
    take_session_id: Optional[uuid.UUID] = None
    participant_email: Optional[str] = None
    participant_name: Optional[str] = None
    started_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    metrics_json: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class TakeSession:
    id: uuid.UUID
    evaluation_id: uuid.UUID
    user_id: str
    started_at: datetime
    deadline_at: datetime
    submitted_at: Optional[datetime]


# Columnas mínimas para analytics (evita leer answers_json en bloque).
_ATTEMPTS_ANALYTICS_SELECT = (
    "id,evaluation_id,user_id,score_percent,feedback,created_at,started_at,"
    "duration_seconds,participant_email,participant_name,metrics_json,take_session_id"
)


class EvaluationRepository:
    def __init__(self, supabase_url: Optional[str] = None, supabase_key: Optional[str] = None):
        self.supabase_url = supabase_url or os.getenv("SUPABASE_URL")
        self.supabase_key = supabase_key or os.getenv("SUPABASE_KEY")
        if not self.supabase_url or not self.supabase_key:
            raise RuntimeError("SUPABASE_URL y/o SUPABASE_KEY no configuradas.")
        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        self.table = "evaluations"
        self.attempts_table = "evaluation_attempts"
        self.sessions_table = "evaluation_take_sessions"

    @staticmethod
    def _to_eval(row: Dict[str, Any]) -> Evaluation:
        qj = row.get("questions_json")
        if isinstance(qj, str):
            import json

            qj = json.loads(qj)
        dm = row.get("duration_minutes")
        if dm is not None:
            try:
                dm = int(dm)
            except (TypeError, ValueError):
                dm = None
        if dm is not None and dm <= 0:
            dm = None
        return Evaluation(
            id=uuid.UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
            author_user_id=row["author_user_id"],
            title=row["title"],
            description=row.get("description") or "",
            requirements_hint=row.get("requirements_hint") or "",
            questions_json=list(qj or []),
            published=bool(row.get("published")),
            published_at=(
                datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
                if row.get("published_at")
                else None
            ),
            share_token=row.get("share_token"),
            duration_minutes=dm,
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if isinstance(row["created_at"], str)
            else row["created_at"],
            updated_at=datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
            if isinstance(row["updated_at"], str)
            else row["updated_at"],
        )

    @staticmethod
    def _to_attempt(row: Dict[str, Any]) -> EvaluationAttempt:
        aj = row.get("answers_json")
        if isinstance(aj, str):
            import json

            aj = json.loads(aj)
        ts = row.get("take_session_id")
        take_sid = uuid.UUID(ts) if ts else None
        mj = row.get("metrics_json")
        if isinstance(mj, str):
            import json

            mj = json.loads(mj)
        st = row.get("started_at")
        started = (
            datetime.fromisoformat(str(st).replace("Z", "+00:00"))
            if st
            else None
        )
        ds = row.get("duration_seconds")
        if ds is not None:
            try:
                ds = int(ds)
            except (TypeError, ValueError):
                ds = None
        return EvaluationAttempt(
            id=uuid.UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
            evaluation_id=uuid.UUID(row["evaluation_id"])
            if isinstance(row["evaluation_id"], str)
            else row["evaluation_id"],
            user_id=row["user_id"],
            answers_json=dict(aj or {}),
            score_percent=float(row["score_percent"]) if row.get("score_percent") is not None else None,
            feedback=row.get("feedback") or "",
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if isinstance(row["created_at"], str)
            else row["created_at"],
            take_session_id=take_sid,
            participant_email=row.get("participant_email"),
            participant_name=row.get("participant_name"),
            started_at=started,
            duration_seconds=ds,
            metrics_json=(dict(mj) if isinstance(mj, dict) else {}) or {},
        )

    def create(self, data: EvaluationCreate) -> Evaluation:
        qj = data.questions_json
        payload: Dict[str, Any] = {
            "author_user_id": data.author_user_id,
            "title": data.title,
            "description": data.description,
            "requirements_hint": data.requirements_hint,
            "questions_json": qj,
        }
        if data.duration_minutes is not None and data.duration_minutes > 0:
            payload["duration_minutes"] = int(data.duration_minutes)
        if data.share_token:
            payload["share_token"] = data.share_token
        try:
            res = self.client.table(self.table).insert(payload).execute()
            if not res.data:
                raise RuntimeError("No se recibieron datos después de la inserción")
            return self._to_eval(res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al crear evaluación: {e}") from e

    def get(self, evaluation_id: uuid.UUID | str) -> Optional[Evaluation]:
        try:
            res = self.client.table(self.table).select("*").eq("id", str(evaluation_id)).execute()
            if not res.data:
                return None
            return self._to_eval(res.data[0])
        except APIError:
            return None

    def update(self, evaluation_id: uuid.UUID | str, data: EvaluationUpdate) -> Optional[Evaluation]:
        payload: Dict[str, Any] = {}
        if data.title is not None:
            payload["title"] = data.title
        if data.description is not None:
            payload["description"] = data.description
        if data.requirements_hint is not None:
            payload["requirements_hint"] = data.requirements_hint
        if data.questions_json is not None:
            payload["questions_json"] = data.questions_json
        if data.published is not None:
            payload["published"] = data.published
            if data.published:
                payload["published_at"] = datetime.utcnow().isoformat() + "Z"
            else:
                payload["published_at"] = None
        if data.duration_minutes is not None:
            dm = data.duration_minutes
            if dm <= 0:
                payload["duration_minutes"] = None
            else:
                payload["duration_minutes"] = int(dm)
        if data.share_token is not None:
            payload["share_token"] = data.share_token
        if not payload:
            return self.get(evaluation_id)
        payload["updated_at"] = datetime.utcnow().isoformat() + "Z"
        try:
            res = self.client.table(self.table).update(payload).eq("id", str(evaluation_id)).execute()
            if not res.data:
                return None
            return self._to_eval(res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al actualizar evaluación: {e}") from e

    def delete(self, evaluation_id: uuid.UUID | str) -> bool:
        try:
            res = self.client.table(self.table).delete().eq("id", str(evaluation_id)).execute()
            return bool(res.data)
        except APIError:
            return False

    def list_by_author(self, author_user_id: str, limit: int = 50, offset: int = 0) -> List[Evaluation]:
        try:
            res = (
                self.client.table(self.table)
                .select("*")
                .eq("author_user_id", author_user_id)
                .order("updated_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            return [self._to_eval(r) for r in (res.data or [])]
        except APIError as e:
            raise RuntimeError(f"Error al listar evaluaciones: {e}") from e

    def list_published(self, limit: int = 50, offset: int = 0) -> List[Evaluation]:
        try:
            res = (
                self.client.table(self.table)
                .select("*")
                .eq("published", True)
                .order("published_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            return [self._to_eval(r) for r in (res.data or [])]
        except APIError as e:
            raise RuntimeError(f"Error al listar evaluaciones publicadas: {e}") from e

    def get_by_share_token(self, share_token: str) -> Optional[Evaluation]:
        try:
            res = self.client.table(self.table).select("*").eq("share_token", share_token).limit(1).execute()
            if not res.data:
                return None
            return self._to_eval(res.data[0])
        except APIError:
            return None

    @staticmethod
    def _to_take_session(row: Dict[str, Any]) -> TakeSession:
        return TakeSession(
            id=uuid.UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
            evaluation_id=uuid.UUID(row["evaluation_id"])
            if isinstance(row["evaluation_id"], str)
            else row["evaluation_id"],
            user_id=row["user_id"],
            started_at=datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
            if isinstance(row["started_at"], str)
            else row["started_at"],
            deadline_at=datetime.fromisoformat(row["deadline_at"].replace("Z", "+00:00"))
            if isinstance(row["deadline_at"], str)
            else row["deadline_at"],
            submitted_at=(
                datetime.fromisoformat(row["submitted_at"].replace("Z", "+00:00"))
                if row.get("submitted_at")
                else None
            ),
        )

    def close_expired_open_sessions(self, evaluation_id: uuid.UUID | str, user_id: str) -> None:
        """Marca submitted_at en sesiones abiertas cuyo plazo ya venció."""
        try:
            now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            res = (
                self.client.table(self.sessions_table)
                .select("id,deadline_at")
                .eq("evaluation_id", str(evaluation_id))
                .eq("user_id", user_id)
                .is_("submitted_at", "null")
                .execute()
            )
            for row in res.data or []:
                dl_raw = row.get("deadline_at")
                if not dl_raw:
                    continue
                dl = datetime.fromisoformat(str(dl_raw).replace("Z", "+00:00"))
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=timezone.utc)
                if dl < datetime.now(timezone.utc):
                    sid = row["id"]
                    self.client.table(self.sessions_table).update({"submitted_at": now_iso}).eq("id", sid).execute()
        except APIError:
            pass

    def get_resumable_session(
        self, evaluation_id: uuid.UUID | str, user_id: str
    ) -> Optional[TakeSession]:
        """Sesión activa no enviada y aún dentro del plazo."""
        try:
            res = (
                self.client.table(self.sessions_table)
                .select("*")
                .eq("evaluation_id", str(evaluation_id))
                .eq("user_id", user_id)
                .is_("submitted_at", "null")
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            )
            if not res.data:
                return None
            ts = self._to_take_session(res.data[0])
            dl = ts.deadline_at
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc)
            if dl >= datetime.now(timezone.utc):
                return ts
            return None
        except APIError:
            return None

    def create_take_session(
        self,
        evaluation_id: uuid.UUID | str,
        user_id: str,
        duration_minutes: int,
    ) -> TakeSession:
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(minutes=int(duration_minutes))
        started_iso = now.isoformat().replace("+00:00", "Z")
        deadline_iso = deadline.isoformat().replace("+00:00", "Z")
        payload = {
            "evaluation_id": str(evaluation_id),
            "user_id": user_id,
            "started_at": started_iso,
            "deadline_at": deadline_iso,
        }
        try:
            res = self.client.table(self.sessions_table).insert(payload).execute()
            if not res.data:
                raise RuntimeError("No se creó la sesión")
            return self._to_take_session(res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al crear sesión: {e}") from e

    def mark_session_submitted(self, session_id: uuid.UUID | str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            self.client.table(self.sessions_table).update({"submitted_at": now_iso}).eq("id", str(session_id)).execute()
        except APIError:
            pass

    def get_take_session(self, session_id: uuid.UUID | str) -> Optional[TakeSession]:
        try:
            res = self.client.table(self.sessions_table).select("*").eq("id", str(session_id)).limit(1).execute()
            if not res.data:
                return None
            return self._to_take_session(res.data[0])
        except APIError:
            return None

    def insert_attempt(
        self,
        evaluation_id: uuid.UUID | str,
        user_id: str,
        answers_json: Dict[str, Any],
        score_percent: float,
        feedback: str,
        take_session_id: Optional[uuid.UUID | str] = None,
        *,
        participant_email: Optional[str] = None,
        participant_name: Optional[str] = None,
        started_at: Optional[datetime] = None,
        duration_seconds: Optional[int] = None,
        metrics_json: Optional[Dict[str, Any]] = None,
    ) -> EvaluationAttempt:
        payload: Dict[str, Any] = {
            "evaluation_id": str(evaluation_id),
            "user_id": user_id,
            "answers_json": answers_json,
            "score_percent": round(score_percent, 2),
            "feedback": feedback,
        }
        if take_session_id:
            payload["take_session_id"] = str(take_session_id)
        if participant_email:
            payload["participant_email"] = participant_email[:512]
        if participant_name:
            payload["participant_name"] = participant_name[:512]
        if started_at is not None:
            sa = started_at
            if sa.tzinfo is None:
                sa = sa.replace(tzinfo=timezone.utc)
            payload["started_at"] = sa.isoformat().replace("+00:00", "Z")
        if duration_seconds is not None:
            payload["duration_seconds"] = max(0, int(duration_seconds))
        if metrics_json is not None:
            payload["metrics_json"] = metrics_json
        try:
            res = self.client.table(self.attempts_table).insert(payload).execute()
            if not res.data:
                raise RuntimeError("No se recibieron datos del intento")
            return self._to_attempt(res.data[0])
        except APIError as e:
            raise RuntimeError(f"Error al guardar intento: {e}") from e

    def list_attempts_for_evaluation(
        self, evaluation_id: uuid.UUID | str, user_id_filter: Optional[str] = None
    ) -> List[EvaluationAttempt]:
        try:
            q = self.client.table(self.attempts_table).select("*").eq("evaluation_id", str(evaluation_id))
            if user_id_filter:
                q = q.eq("user_id", user_id_filter)
            res = q.order("created_at", desc=True).execute()
            return [self._to_attempt(r) for r in (res.data or [])]
        except APIError as e:
            raise RuntimeError(f"Error al listar intentos: {e}") from e

    def list_attempts_for_evaluation_analytics(
        self, evaluation_id: uuid.UUID | str, user_id_filter: Optional[str] = None
    ) -> List[EvaluationAttempt]:
        """Una query; sin answers_json (métricas y metadatos para panel de analytics)."""
        try:
            q = (
                self.client.table(self.attempts_table)
                .select(_ATTEMPTS_ANALYTICS_SELECT)
                .eq("evaluation_id", str(evaluation_id))
            )
            if user_id_filter:
                q = q.eq("user_id", user_id_filter)
            res = q.order("created_at", desc=True).execute()
            return [self._to_attempt(r) for r in (res.data or [])]
        except APIError as e:
            raise RuntimeError(f"Error al listar intentos (analytics): {e}") from e

    def list_attempts_for_evaluations_analytics(
        self, evaluation_ids: List[str],
    ) -> List[EvaluationAttempt]:
        """Una sola query IN para todos los evaluation_id (sin answers_json)."""
        if not evaluation_ids:
            return []
        try:
            res = (
                self.client.table(self.attempts_table)
                .select(_ATTEMPTS_ANALYTICS_SELECT)
                .in_("evaluation_id", evaluation_ids)
                .order("created_at", desc=True)
                .execute()
            )
            return [self._to_attempt(r) for r in (res.data or [])]
        except APIError as e:
            raise RuntimeError(f"Error al listar intentos (analytics batch): {e}") from e

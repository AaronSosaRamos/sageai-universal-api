from fastapi import FastAPI, HTTPException, status, Depends, Body, Query
from pydantic import BaseModel, EmailStr, constr
from datetime import timedelta, datetime, timezone
from typing import Dict, Literal, Optional
from .config import get_settings
from .security import create_access_token
from .auth import verify_token_dependency, require_admin_dependency
from .supervisor import get_supervisor_response, get_assistant_chat_response
from .db.user_management import UserRepository, UserCreate
from .user_import import build_user_import_template_xlsx, parse_user_import_xlsx
from .db.thread_manager import ThreadManager, ThreadCreate
from .db.chat_management import ChatThreadRepository
from .db.custom_space_management import CustomSpaceRepository, CustomSpaceCreate, CustomSpaceUpdate
from .db.assistant_management import AssistantRepository, AssistantCreate, AssistantUpdate, Assistant
from .db.evaluation_management import (
    EvaluationRepository,
    EvaluationCreate,
    EvaluationUpdate,
    Evaluation,
    EvaluationAttempt,
    TakeSession,
)
from .evaluation_generator import (
    generate_evaluation_from_files,
    strip_questions_for_student,
    grade_submission,
    validate_answers_complete,
    normalize_answers_for_grading,
    build_submission_review,
)
from .db.usage_limits import UsageLimitRepository
from .assistant_prompt_generator import generate_system_prompt_from_files
from .response_export import export_markdown
from .security import verify_token
from .db.analytics_management import (
    AnalyticsRepository,
    InteractionEventInsert,
    safe_record_interaction_event,
)
from .analytics_helpers import client_snapshot_from_request, track_event

# pip install fastapi uvicorn python-multipart
import os
import io
import uuid
import hashlib
import shutil
import asyncio
import time
import re
import statistics as statistics_mod
from pathlib import Path
from typing import List, Any
from unicodedata import normalize

from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict, deque

app = FastAPI()

BASE_DIR = Path("storage")
BASE_DIR.mkdir(exist_ok=True)

# Límites: usuarios tipo 'user' — interacciones/día en chat principal y assistant; archivos — todos los usuarios
DAILY_INTERACTION_LIMIT_USER = int(os.getenv("DAILY_INTERACTION_LIMIT_USER", "50"))
MAX_UPLOAD_FILE_BYTES = int(os.getenv("MAX_UPLOAD_FILE_BYTES", str(2 * 1024 * 1024)))

# Diccionario de sesiones: { session_uuid: {"last": timestamp, "inner": inner_uuid} }
sessions: dict[str, dict] = {}

# Diccionario de security codes: { code: {"created_at": datetime, "expires_at": datetime} }
security_codes: dict[str, dict] = {}
SESSION_TIMEOUT = 900  # 15 minutos

# ---------- Protección DoS/DDoS ----------
# Rate limiting por IP: { ip: deque([timestamp1, timestamp2, ...]) }
ip_request_times: dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

# Rate limiting por usuario: { user_id: deque([timestamp1, timestamp2, ...]) }
user_request_times: dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

# IPs bloqueadas temporalmente: { ip: block_until_timestamp }
blocked_ips: dict[str, float] = {}

# Configuración de rate limiting
RATE_LIMIT_WINDOW = 60  # Ventana de tiempo en segundos
MAX_REQUESTS_PER_WINDOW_IP = 100  # Máximo de requests por IP por ventana
MAX_REQUESTS_PER_WINDOW_USER = 200  # Máximo de requests por usuario por ventana
BLOCK_DURATION = 300  # Duración del bloqueo en segundos (5 minutos)
SUSPICIOUS_THRESHOLD = 50  # Requests sospechosos en ventana corta
SUSPICIOUS_WINDOW = 10  # Ventana corta para detectar patrones sospechosos


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc"
)


# ---------- Middleware de Protección DoS/DDoS ----------
class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware para prevenir ataques DoS/DDoS con rate limiting"""
    
    async def dispatch(self, request: Request, call_next):
        # Obtener IP del cliente
        client_ip = request.client.host if request.client else "unknown"
        
        # Verificar si la IP está bloqueada
        if client_ip in blocked_ips:
            if time.time() < blocked_ips[client_ip]:
                return JSONResponse(
                    {"error": "IP bloqueada temporalmente por exceso de requests"},
                    status_code=429
                )
            else:
                # Desbloquear IP si ya pasó el tiempo
                blocked_ips.pop(client_ip, None)
        
        # Obtener user_id del token si existe
        user_id = None
        token = request.headers.get("Token")
        if token:
            try:
                payload = verify_token(token)
                if payload and payload.get("secret_value") == settings.secret_value:
                    user_id = payload.get("user_id")
            except Exception:
                pass
        
        current_time = time.time()
        
        # Limpiar timestamps antiguos para la IP
        ip_times = ip_request_times[client_ip]
        while ip_times and current_time - ip_times[0] > RATE_LIMIT_WINDOW:
            ip_times.popleft()
        
        # Verificar rate limit por IP
        if len(ip_times) >= MAX_REQUESTS_PER_WINDOW_IP:
            # Bloquear IP temporalmente
            blocked_ips[client_ip] = current_time + BLOCK_DURATION
            print(f"[DoS Protection] IP {client_ip} bloqueada por exceso de requests ({len(ip_times)} requests)")
            return JSONResponse(
                {"error": "Demasiados requests. Intenta más tarde."},
                status_code=429
            )
        
        # Detectar patrones sospechosos (muchos requests en ventana corta)
        recent_requests = sum(1 for t in ip_times if current_time - t < SUSPICIOUS_WINDOW)
        if recent_requests >= SUSPICIOUS_THRESHOLD:
            blocked_ips[client_ip] = current_time + BLOCK_DURATION
            print(f"[DoS Protection] IP {client_ip} bloqueada por patrón sospechoso ({recent_requests} requests en {SUSPICIOUS_WINDOW}s)")
            return JSONResponse(
                {"error": "Patrón de requests sospechoso detectado. IP bloqueada temporalmente."},
                status_code=429
            )
        
        # Verificar rate limit por usuario si existe
        if user_id:
            user_times = user_request_times[user_id]
            while user_times and current_time - user_times[0] > RATE_LIMIT_WINDOW:
                user_times.popleft()
            
            if len(user_times) >= MAX_REQUESTS_PER_WINDOW_USER:
                print(f"[DoS Protection] Usuario {user_id} excedió límite de requests ({len(user_times)} requests)")
                return JSONResponse(
                    {"error": "Demasiados requests. Intenta más tarde."},
                    status_code=429
                )
            
            # Registrar request del usuario
            user_times.append(current_time)
        
        # Registrar request de la IP
        ip_times.append(current_time)
        
        # Continuar con la request
        response = await call_next(request)
        return response


# Aplicar middleware
app.add_middleware(RateLimitMiddleware)


# Limpieza periódica de registros de rate limiting
async def cleanup_rate_limit_records():
    """Limpia registros antiguos de rate limiting para evitar consumo excesivo de memoria"""
    while True:
        try:
            await asyncio.sleep(300)  # Cada 5 minutos
            
            current_time = time.time()
            
            # Limpiar IPs bloqueadas expiradas
            expired_ips = [ip for ip, block_until in blocked_ips.items() if current_time >= block_until]
            for ip in expired_ips:
                blocked_ips.pop(ip, None)
            
            # Limpiar registros de IPs antiguos (sin requests en última hora)
            expired_ips = [
                ip for ip, times in ip_request_times.items()
                if not times or current_time - times[-1] > 3600
            ]
            for ip in expired_ips:
                ip_request_times.pop(ip, None)
            
            # Limpiar registros de usuarios antiguos
            expired_users = [
                user_id for user_id, times in user_request_times.items()
                if not times or current_time - times[-1] > 3600
            ]
            for user_id in expired_users:
                user_request_times.pop(user_id, None)
            
            print(f"[DoS Protection] Limpieza completada: {len(expired_ips)} IPs, {len(expired_users)} usuarios limpiados")
            
        except Exception as e:
            print(f"[DoS Protection] Error en limpieza de registros: {e}")


class TokenRequest(BaseModel):
    email: EmailStr
    password: str
    secret_value: str

    class Config:
        json_schema_extra = {
            "example": {
                "email": "juan.perez@example.com",
                "password": "contraseña123",
                "secret_value": "your-secret-value"
            }
        }


class SupervisorRequest(BaseModel):
    query: str
    user_id: str
    thread_id: str


class SecurityCodeRequest(BaseModel):
    secret_value: str

    class Config:
        json_schema_extra = {
            "example": {
                "secret_value": "your-secret-value"
            }
        }

class SecurityCodeResponse(BaseModel):
    code: str
    expires_in: int  # segundos


# ---------- Thread Models ----------
class ThreadCreateRequest(BaseModel):
    thread_id: Optional[str] = None  # Opcional, se genera automáticamente si no se proporciona

    class Config:
        json_schema_extra = {
            "example": {
                "thread_id": None
            }
        }


class ThreadResponse(BaseModel):
    thread_id: str
    user_id: str
    created_at: str
    storage_path: Optional[str] = None
    initial_message_id: Optional[str] = None


class ThreadSummaryResponse(BaseModel):
    thread_id: str
    user_id: str
    message_count: int
    last_message: Optional[str]
    last_message_at: Optional[str]
    last_role: Optional[str]
    created_at: str


class ThreadStatsResponse(BaseModel):
    thread_id: str
    total_messages: int
    ai_messages: int
    human_messages: int
    first_message_at: Optional[str]
    last_message_at: Optional[str]


class ThreadListResponse(BaseModel):
    threads: List[Dict[str, Any]]
    total: int
    limit: int
    offset: int


class ThreadDeleteResponse(BaseModel):
    thread_id: str
    deleted_messages: int
    storage_deleted: bool
    deleted_at: str
    message: str


class BatchDeleteRequest(BaseModel):
    thread_ids: List[str]

    class Config:
        json_schema_extra = {
            "example": {
                "thread_ids": ["thread-id-1", "thread-id-2", "thread-id-3"]
            }
        }


class UserRegistrationRequest(BaseModel):
    nombre: constr(min_length=2, max_length=50)
    apellido: constr(min_length=2, max_length=50)
    email: EmailStr
    password: constr(min_length=8, max_length=50)

    class Config:
        json_schema_extra = {
            "example": {
                "nombre": "Juan",
                "apellido": "Pérez",
                "email": "juan.perez@example.com",
                "password": "contraseña123"
            }
        }


# ---------- Custom Space Models ----------
class CustomSpaceCreateRequest(BaseModel):
    title: str = "Mi Espacio Personalizado"
    custom_memories: str = ""
    agent_instructions: str = ""
    is_active: bool = True

    class Config:
        json_schema_extra = {
            "example": {
                "title": "Mi Espacio de Trabajo",
                "custom_memories": "Soy desarrollador Python. Me gusta trabajar con FastAPI y React.",
                "agent_instructions": "Sé conciso y técnico. Usa ejemplos de código cuando sea relevante.",
                "is_active": True
            }
        }


class CustomSpaceUpdateRequest(BaseModel):
    title: Optional[str] = None
    custom_memories: Optional[str] = None
    agent_instructions: Optional[str] = None
    is_active: Optional[bool] = None

    class Config:
        json_schema_extra = {
            "example": {
                "title": "Mi Espacio Actualizado",
                "custom_memories": "Memorias actualizadas...",
                "agent_instructions": "Instrucciones actualizadas...",
                "is_active": True
            }
        }


class CustomSpaceResponse(BaseModel):
    id: str
    user_id: str
    title: str
    custom_memories: str
    agent_instructions: str
    is_active: bool
    created_at: str
    updated_at: str


# ---------- Assistant Models ----------
class AssistantCreateRequest(BaseModel):
    name: str
    description: str = ""
    system_prompt: str

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Asistente de Ventas",
                "description": "Especializado en consultas comerciales",
                "system_prompt": "Eres un asistente de ventas experto..."
            }
        }


class AssistantUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None


class AssistantResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: str
    system_prompt: str
    created_at: str
    updated_at: str
    source: Literal["mine", "catalog"] = "mine"
    is_owner: bool = True


def _can_use_assistant(a: Assistant, _viewer_id: str) -> bool:
    """Cualquier usuario autenticado puede chatear con cualquier asistente; solo el dueño edita/borra."""
    return a is not None


def _assistant_to_response(
    a: Assistant,
    viewer_id: str,
    *,
    list_scope: Optional[Literal["mine", "catalog"]] = None,
) -> AssistantResponse:
    is_owner = a.user_id == viewer_id

    if list_scope == "catalog":
        source: Literal["mine", "catalog"] = "catalog"
        system_prompt = a.system_prompt if is_owner else ""
    elif list_scope == "mine":
        source = "mine"
        system_prompt = a.system_prompt
    else:
        source = "catalog" if not is_owner else "mine"
        system_prompt = "" if not is_owner else a.system_prompt

    return AssistantResponse(
        id=str(a.id),
        user_id=a.user_id,
        name=a.name,
        description=a.description,
        system_prompt=system_prompt,
        created_at=a.created_at.isoformat(),
        updated_at=a.updated_at.isoformat(),
        source=source,
        is_owner=is_owner,
    )


def _token_user_type(payload: dict) -> str:
    return (payload.get("user_type") or "user").strip().lower()


def _require_admin_payload(payload: dict) -> None:
    """Misma regla que require_admin_dependency, para comprobar dentro del handler."""
    if _token_user_type(payload) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere cuenta de administrador",
        )


def _is_restricted_user(payload: dict) -> bool:
    """Plan 'user': límite diario de interacciones y no puede crear asistentes."""
    return _token_user_type(payload) == "user"


def _consume_interaction_if_limited(payload: dict) -> None:
    if not _is_restricted_user(payload):
        return
    uid = payload.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Token inválido")
    try:
        repo = UsageLimitRepository()
        allowed, _ = repo.consume_daily_interaction(
            str(uid), datetime.utcnow().date(), DAILY_INTERACTION_LIMIT_USER
        )
    except Exception as e:
        print(f"[UsageLimit] Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo verificar el límite de uso. Intenta más tarde.",
        )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Has alcanzado el límite de {DAILY_INTERACTION_LIMIT_USER} interacciones por día. "
                "Vuelve mañana (UTC)."
            ),
        )


class GeneratePromptRequest(BaseModel):
    file_refs: List[str]  # ["session_uuid/inner_uuid/filename", ...]
    user_hint: str = ""


class GenerateEvaluationRequest(BaseModel):
    file_refs: List[str]
    requirements: str = ""
    additional_context: str = ""


class EvaluationCreateRequest(BaseModel):
    title: str
    description: str = ""
    requirements_hint: str = ""
    questions: List[Dict[str, Any]]
    published: bool = False
    duration_minutes: Optional[int] = None  # None o 0 = sin límite de tiempo


class EvaluationUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    requirements_hint: Optional[str] = None
    questions: Optional[List[Dict[str, Any]]] = None
    published: Optional[bool] = None
    duration_minutes: Optional[int] = None


class EvaluationSubmitRequest(BaseModel):
    answers: Dict[str, Any]
    session_id: Optional[str] = None  # Obligatorio si la evaluación tiene tiempo límite


class StartEvalSessionRequest(BaseModel):
    evaluation_id: Optional[str] = None
    share_token: Optional[str] = None


class AssistantChatRequest(BaseModel):
    query: str
    assistant_id: str


class ExportResponseRequest(BaseModel):
    """Exporta contenido Markdown (respuesta del chat) a Word o PDF."""

    content: str
    format: Literal["docx", "pdf"]
    title: Optional[str] = None


@app.get("/")
async def root():
    return {"message": f"Welcome to {settings.app_name} API"}


@app.get("/health")
async def health_check():
    """
    Health check endpoint para mantener la API activa y verificar su estado.
    Endpoint rápido sin autenticación requerida.
    """
    return {
        "status": "ok",
        "service": settings.app_name,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }


@app.post("/token")
async def generate_token(http_request: Request, body: TokenRequest) -> Dict[str, str]:
    """
    Generates a JWT token if all validations pass:
    - secret_value must match the environment variable
    - email must exist in the database
    - password must be correct for that email
    """
    t0 = time.perf_counter()
    email_key = hashlib.sha256(body.email.strip().lower().encode("utf-8")).hexdigest()[:16]

    def _auth_track(success: bool, status: int, user_id: Optional[str] = None, err: Optional[str] = None):
        track_event(
            event_category="auth",
            event_name="auth.login.success" if success else "auth.login.failure",
            user_id=user_id,
            request=http_request,
            http_method="POST",
            http_path="/token",
            status_code=status,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            success=success,
            error_message=err,
            metadata={"email_key": email_key},
        )

    try:
        if body.secret_value != settings.secret_value:
            _auth_track(False, 401, err="invalid_secret")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid secret value"
            )

        user_repo = UserRepository()
        user = user_repo.get_user_by_email(body.email)

        if not user or not user_repo.verify_password(body.password, user.password):
            _auth_track(False, 401, err="invalid_credentials")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )

        access_token = create_access_token(
            data={
                "secret_value": settings.secret_value,
                "user_id": str(user.id),
                "email": user.email,
                "nombre": user.nombre,
                "apellido": user.apellido,
                "user_type": getattr(user, "user_type", None) or "user",
            },
            expires_delta=timedelta(minutes=settings.access_token_expire_minutes)
        )

        _auth_track(True, 200, user_id=str(user.id))
        return {"access_token": access_token}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error generating token: {str(e)}")
        track_event(
            event_category="auth",
            event_name="auth.login.failure",
            request=http_request,
            http_method="POST",
            http_path="/token",
            status_code=500,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            success=False,
            error_message=str(e)[:500],
            metadata={"email_key": email_key},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error generating token"
        )


@app.get("/test", dependencies=[Depends(verify_token_dependency)])
async def test_auth():
    """
    Test endpoint that requires a valid JWT token in the Token header.
    """
    return {"message": "Token is valid!"}


@app.post("/security-code")
async def generate_security_code(http_request: Request, _request: SecurityCodeRequest):
    """Deshabilitado: el registro ya no usa códigos generados con clave secreta."""
    track_event(
        event_category="security",
        event_name="security.code_generation.blocked",
        request=http_request,
        http_method="POST",
        http_path="/security-code",
        status_code=403,
        success=False,
        metadata={"reason": "disabled"},
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="La generación de códigos con clave secreta está deshabilitada.",
    )


@app.post("/users", status_code=status.HTTP_403_FORBIDDEN)
async def create_user_public_disabled(http_request: Request, _request: UserRegistrationRequest):
    """El registro público está cerrado; los usuarios se crean por importación administrativa (.xlsx)."""
    track_event(
        event_category="security",
        event_name="auth.register.blocked",
        request=http_request,
        http_method="POST",
        http_path="/users",
        status_code=403,
        success=False,
        metadata={"reason": "public_registration_disabled"},
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="El registro público está deshabilitado. Los usuarios se crean mediante importación administrativa.",
    )


@app.get("/admin/users/import-template")
async def download_user_import_template(
    http_request: Request,
    admin_payload: dict = Depends(require_admin_dependency),
) -> Response:
    """Descarga plantilla Excel para alta masiva de usuarios (solo administradores)."""
    track_event(
        event_category="api",
        event_name="admin.users.template_downloaded",
        user_id=admin_payload.get("user_id"),
        request=http_request,
        http_method="GET",
        http_path="/admin/users/import-template",
        status_code=200,
        success=True,
    )
    data = build_user_import_template_xlsx()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="plantilla_usuarios.xlsx"',
        },
    )


@app.post("/admin/users/bulk-import")
async def bulk_import_users(
    http_request: Request,
    file: UploadFile = File(...),
    admin_payload: dict = Depends(require_admin_dependency),
) -> Dict[str, Any]:
    """
    Importa usuarios desde un .xlsx según la plantilla (solo administradores).
    """
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se admiten archivos .xlsx",
        )
    content = await file.read()
    rows, parse_errors = parse_user_import_xlsx(content)
    if not rows and parse_errors:
        return {
            "created": [],
            "failed": parse_errors,
            "total_created": 0,
            "total_failed": len(parse_errors),
            "message": "No se importó ningún usuario",
        }

    user_repo = UserRepository()
    created: List[Dict[str, str]] = []
    failed: List[Dict[str, Any]] = list(parse_errors)
    seen_email: set[str] = set()

    for pr in rows:
        if pr.email in seen_email:
            failed.append(
                {"row": pr.row_num, "email": pr.email, "error": "Email duplicado en el archivo"}
            )
            continue
        seen_email.add(pr.email)
        try:
            if user_repo.get_user_by_email(pr.email):
                failed.append(
                    {"row": pr.row_num, "email": pr.email, "error": "El email ya está registrado"}
                )
                continue
            user = user_repo.create_user(
                UserCreate(
                    nombre=pr.nombre,
                    apellido=pr.apellido,
                    email=pr.email,
                    password=pr.password,
                    user_type=pr.user_type,
                )
            )
            created.append({"user_id": str(user.id), "email": user.email})
            safe_record_interaction_event(
                InteractionEventInsert(
                    event_category="user",
                    event_name="user.registered",
                    user_id=str(user.id),
                    success=True,
                    metadata={"source": "admin.bulk_import", "row": pr.row_num},
                )
            )
        except Exception as e:
            failed.append({"row": pr.row_num, "email": pr.email, "error": str(e)})

    track_event(
        event_category="api",
        event_name="admin.users.bulk_import.completed",
        user_id=admin_payload.get("user_id"),
        request=http_request,
        http_method="POST",
        http_path="/admin/users/bulk-import",
        status_code=200,
        success=True,
        metadata={"filename": file.filename},
        metrics={
            "created_count": len(created),
            "failed_count": len(failed),
            "parse_errors": len(parse_errors),
        },
    )

    return {
        "created": created,
        "failed": failed,
        "total_created": len(created),
        "total_failed": len(failed),
    }


@app.get("/admin/analytics/dashboard")
async def admin_analytics_dashboard(
    days: int = Query(30, ge=1, le=365),
    admin_payload: dict = Depends(require_admin_dependency),
) -> Dict[str, Any]:
    """
    Resumen de métricas: eventos, categorías, serie diaria, LLM, auth y actividad reciente.
    """
    repo = AnalyticsRepository()
    dash = repo.get_admin_dashboard(days=days)
    try:
        ur = UserRepository()
        n_reg = ur.count_users()
        dash["totals"]["users"] = n_reg
        dash["adoption_reach"]["new_user_accounts_in_period"] = ur.count_users_created_since(
            dash["period_start"]
        )
        uq = dash["adoption_reach"].get("unique_active_users_in_sample") or 0
        if n_reg and isinstance(uq, int):
            dash["adoption_reach"]["ratio_sample_active_users_to_registered_total"] = round(
                min(uq, n_reg) / n_reg, 4
            )
    except Exception:
        dash["totals"]["users"] = None
        dash["adoption_reach"]["new_user_accounts_in_period"] = None
    try:
        dash["totals"]["assistants"] = AssistantRepository().count_all_assistants()
    except Exception:
        dash["totals"]["assistants"] = None
    dash["generated_for_user_id"] = admin_payload.get("user_id")
    return dash


@app.post("/supervisor", dependencies=[Depends(verify_token_dependency)])
async def supervisor_endpoint(
    http_request: Request,
    supervisor_request: SupervisorRequest,
    token_data: dict = Depends(verify_token_dependency),
) -> Dict[str, str]:
    """
    Endpoint that calls the supervisor with the provided query in the request body.
    Requires:
    - query: The user's question or request
    - user_id: The session UUID that identifies the user
    - thread_id: The inner UUID that identifies the chat thread
    """
    t0 = time.perf_counter()
    uid = supervisor_request.user_id
    tid = supervisor_request.thread_id
    client = client_snapshot_from_request(http_request)
    try:
        token_uid = token_data.get("user_id")
        if not token_uid or token_uid != supervisor_request.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="El token no coincide con el usuario de la solicitud",
            )

        if not supervisor_request.query.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La consulta no puede estar vacía"
            )

        if not supervisor_request.user_id or not supervisor_request.thread_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Se requieren user_id y thread_id"
            )

        session_path = Path("storage") / supervisor_request.user_id / supervisor_request.thread_id
        if not session_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sesión no encontrada o expirada"
            )

        _consume_interaction_if_limited(token_data)

        q = supervisor_request.query or ""
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            get_supervisor_response,
            supervisor_request.query,
            supervisor_request.user_id,
            supervisor_request.thread_id,
        )

        duration_ms = int((time.perf_counter() - t0) * 1000)
        safe_record_interaction_event(
            InteractionEventInsert(
                event_category="supervisor",
                event_name="api.supervisor.invoke",
                user_id=uid,
                thread_id=tid,
                http_method="POST",
                http_path="/supervisor",
                status_code=200,
                duration_ms=duration_ms,
                success=True,
                metrics={
                    "response_chars": len(response or ""),
                    "query_chars": len(q),
                    "query_has_file_refs": ("Files:" in q or "/files/" in q),
                },
                metadata={"user_type": (token_data.get("user_type") or "user")},
                client=client,
            )
        )

        return {
            "response": response,
            "user_id": supervisor_request.user_id,
            "thread_id": supervisor_request.thread_id,
        }

    except HTTPException as he:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        meta = {"reason_code": he.status_code}
        if he.status_code == 429:
            meta["usage_limit"] = "daily_interactions"
        safe_record_interaction_event(
            InteractionEventInsert(
                event_category="supervisor",
                event_name="api.supervisor.invoke",
                user_id=uid,
                thread_id=tid,
                http_method="POST",
                http_path="/supervisor",
                status_code=he.status_code,
                duration_ms=duration_ms,
                success=False,
                error_type="HTTPException",
                error_message=str(he.detail)[:2000] if he.detail else None,
                metadata=meta,
                client=client,
            )
        )
        raise
    except Exception as e:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        safe_record_interaction_event(
            InteractionEventInsert(
                event_category="supervisor",
                event_name="api.supervisor.invoke",
                user_id=uid,
                thread_id=tid,
                http_method="POST",
                http_path="/supervisor",
                status_code=500,
                duration_ms=duration_ms,
                success=False,
                error_type=type(e).__name__,
                error_message=str(e)[:2000],
                client=client,
            )
        )
        print(f"Error en supervisor_endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al procesar la solicitud: {str(e)}"
        )


@app.post("/assistant-chat", dependencies=[Depends(verify_token_dependency)])
async def assistant_chat_endpoint(
    http_request: Request,
    chat_request: AssistantChatRequest,
    payload: dict = Depends(verify_token_dependency),
) -> Dict[str, str]:
    """
    Chat con un asistente personalizado. Usa el system prompt del asistente,
    sin herramientas. Historial por asistente.
    """
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")

    client = client_snapshot_from_request(http_request)
    assistant_repo = AssistantRepository()
    assistant = assistant_repo.get(chat_request.assistant_id)
    if not assistant or not _can_use_assistant(assistant, user_id):
        raise HTTPException(status_code=404, detail="Asistente no encontrado")

    if not chat_request.query.strip():
        raise HTTPException(status_code=400, detail="La consulta no puede estar vacía")

    _consume_interaction_if_limited(payload)

    t0 = time.perf_counter()
    aid = str(chat_request.assistant_id)
    try:
        loop = asyncio.get_event_loop()
        cq = chat_request.query or ""
        response = await loop.run_in_executor(
            None,
            get_assistant_chat_response,
            chat_request.query,
            user_id,
            str(chat_request.assistant_id),
            assistant.system_prompt or "",
        )
        duration_ms = int((time.perf_counter() - t0) * 1000)
        safe_record_interaction_event(
            InteractionEventInsert(
                event_category="assistant",
                event_name="api.assistant_chat.invoke",
                user_id=user_id,
                assistant_id=aid,
                thread_id=f"assistant_{aid}",
                http_method="POST",
                http_path="/assistant-chat",
                status_code=200,
                duration_ms=duration_ms,
                success=True,
                metrics={
                    "response_chars": len(response or ""),
                    "query_chars": len(cq),
                    "query_has_file_refs": ("Files:" in cq or "/files/" in cq),
                },
                metadata={
                    "assistant_owner_id": assistant.user_id,
                    "user_type": (payload.get("user_type") or "user"),
                },
                client=client,
            )
        )
        return {"response": response}
    except HTTPException as he:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        meta = {}
        if he.status_code == 429:
            meta["usage_limit"] = "daily_interactions"
        safe_record_interaction_event(
            InteractionEventInsert(
                event_category="assistant",
                event_name="api.assistant_chat.invoke",
                user_id=user_id,
                assistant_id=aid,
                thread_id=f"assistant_{aid}",
                http_method="POST",
                http_path="/assistant-chat",
                status_code=he.status_code,
                duration_ms=duration_ms,
                success=False,
                error_type="HTTPException",
                error_message=str(he.detail)[:2000] if he.detail else None,
                metadata=meta,
                client=client,
            )
        )
        raise
    except Exception as e:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        safe_record_interaction_event(
            InteractionEventInsert(
                event_category="assistant",
                event_name="api.assistant_chat.invoke",
                user_id=user_id,
                assistant_id=aid,
                thread_id=f"assistant_{aid}",
                http_method="POST",
                http_path="/assistant-chat",
                status_code=500,
                duration_ms=duration_ms,
                success=False,
                error_type=type(e).__name__,
                error_message=str(e)[:2000],
                client=client,
            )
        )
        print(f"Error en assistant_chat_endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@app.post("/export/response", dependencies=[Depends(verify_token_dependency)])
async def export_response_endpoint(
    http_request: Request,
    export_body: ExportResponseRequest,
    payload: dict = Depends(verify_token_dependency),
):
    """
    Genera un archivo Word (.docx) o PDF a partir del texto del mensaje (Markdown).
    Requiere token. El contenido se trunca si supera el límite interno de seguridad.
    """
    if not payload.get("user_id"):
        raise HTTPException(status_code=401, detail="Token inválido")
    if not export_body.content or not export_body.content.strip():
        raise HTTPException(status_code=400, detail="El contenido no puede estar vacío")
    uid = payload.get("user_id")
    t0 = time.perf_counter()
    try:
        data, media_type, filename = export_markdown(
            export_body.content.strip(),
            export_body.format,
            export_body.title,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        print(f"Error export_response: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al generar el archivo",
        ) from e

    duration_ms = int((time.perf_counter() - t0) * 1000)
    safe_record_interaction_event(
        InteractionEventInsert(
            event_category="export",
            event_name="export.response",
            user_id=uid,
            http_method="POST",
            http_path="/export/response",
            status_code=200,
            duration_ms=duration_ms,
            success=True,
            metadata={"format": export_body.format},
            metrics={
                "output_bytes": len(data),
                "content_chars": len(export_body.content.strip()),
            },
            client=client_snapshot_from_request(http_request),
        )
    )

    return StreamingResponse(
        io.BytesIO(data),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# ---------- Custom Space Endpoints ----------
@app.post("/custom-spaces", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_token_dependency)])
async def create_custom_space(
    request: CustomSpaceCreateRequest,
    payload: dict = Depends(verify_token_dependency)
) -> CustomSpaceResponse:
    """
    Crea un nuevo espacio personalizado para el usuario autenticado.
    """
    try:
        # Obtener user_id del payload
        user_id = payload.get("user_id")
        
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o sin user_id"
            )
        
        # Crear espacio
        space_repo = CustomSpaceRepository()
        space = space_repo.create_space(CustomSpaceCreate(
            user_id=user_id,
            title=request.title,
            custom_memories=request.custom_memories,
            agent_instructions=request.agent_instructions,
            is_active=request.is_active
        ))
        
        return CustomSpaceResponse(
            id=str(space.id),
            user_id=space.user_id,
            title=space.title,
            custom_memories=space.custom_memories,
            agent_instructions=space.agent_instructions,
            is_active=space.is_active,
            created_at=space.created_at.isoformat(),
            updated_at=space.updated_at.isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error al crear espacio personalizado: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al crear espacio personalizado: {str(e)}"
        )


@app.get("/custom-spaces", dependencies=[Depends(verify_token_dependency)])
async def get_user_custom_spaces(
    active_only: bool = False,
    payload: dict = Depends(verify_token_dependency)
) -> List[CustomSpaceResponse]:
    """
    Obtiene todos los espacios personalizados del usuario autenticado.
    """
    try:
        # Obtener user_id del payload
        user_id = payload.get("user_id")
        
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o sin user_id"
            )
        
        # Obtener espacios
        space_repo = CustomSpaceRepository()
        spaces = space_repo.get_user_spaces(user_id, active_only=active_only)
        
        return [
            CustomSpaceResponse(
                id=str(space.id),
                user_id=space.user_id,
                title=space.title,
                custom_memories=space.custom_memories,
                agent_instructions=space.agent_instructions,
                is_active=space.is_active,
                created_at=space.created_at.isoformat(),
                updated_at=space.updated_at.isoformat()
            )
            for space in spaces
        ]
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error al obtener espacios personalizados: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al obtener espacios personalizados: {str(e)}"
        )


@app.get("/custom-spaces/{space_id}", dependencies=[Depends(verify_token_dependency)])
async def get_custom_space(
    space_id: str,
    payload: dict = Depends(verify_token_dependency)
) -> CustomSpaceResponse:
    """
    Obtiene un espacio personalizado específico por su ID.
    """
    try:
        # Obtener user_id del payload
        user_id = payload.get("user_id")
        
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o sin user_id"
            )
        
        # Obtener espacio
        space_repo = CustomSpaceRepository()
        space = space_repo.get_space(space_id)
        
        if not space:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Espacio personalizado no encontrado"
            )
        
        # Verificar que el espacio pertenece al usuario
        if space.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para acceder a este espacio"
            )
        
        return CustomSpaceResponse(
            id=str(space.id),
            user_id=space.user_id,
            title=space.title,
            custom_memories=space.custom_memories,
            agent_instructions=space.agent_instructions,
            is_active=space.is_active,
            created_at=space.created_at.isoformat(),
            updated_at=space.updated_at.isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error al obtener espacio personalizado: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al obtener espacio personalizado: {str(e)}"
        )


@app.put("/custom-spaces/{space_id}", dependencies=[Depends(verify_token_dependency)])
async def update_custom_space(
    space_id: str,
    request: CustomSpaceUpdateRequest,
    payload: dict = Depends(verify_token_dependency)
) -> CustomSpaceResponse:
    """
    Actualiza un espacio personalizado existente.
    """
    try:
        # Obtener user_id del payload
        user_id = payload.get("user_id")
        
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o sin user_id"
            )
        
        # Verificar que el espacio existe y pertenece al usuario
        space_repo = CustomSpaceRepository()
        space = space_repo.get_space(space_id)
        
        if not space:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Espacio personalizado no encontrado"
            )
        
        if space.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para modificar este espacio"
            )
        
        # Actualizar espacio
        updated_space = space_repo.update_space(space_id, CustomSpaceUpdate(
            title=request.title,
            custom_memories=request.custom_memories,
            agent_instructions=request.agent_instructions,
            is_active=request.is_active
        ))
        
        return CustomSpaceResponse(
            id=str(updated_space.id),
            user_id=updated_space.user_id,
            title=updated_space.title,
            custom_memories=updated_space.custom_memories,
            agent_instructions=updated_space.agent_instructions,
            is_active=updated_space.is_active,
            created_at=updated_space.created_at.isoformat(),
            updated_at=updated_space.updated_at.isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error al actualizar espacio personalizado: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al actualizar espacio personalizado: {str(e)}"
        )


@app.delete("/custom-spaces/{space_id}", dependencies=[Depends(verify_token_dependency)])
async def delete_custom_space(
    space_id: str,
    payload: dict = Depends(verify_token_dependency)
) -> Dict[str, str]:
    """
    Elimina un espacio personalizado.
    """
    try:
        # Obtener user_id del payload
        user_id = payload.get("user_id")
        
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o sin user_id"
            )
        
        # Verificar que el espacio existe y pertenece al usuario
        space_repo = CustomSpaceRepository()
        space = space_repo.get_space(space_id)
        
        if not space:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Espacio personalizado no encontrado"
            )
        
        if space.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para eliminar este espacio"
            )
        
        # Eliminar espacio
        space_repo.delete_space(space_id)
        
        return {
            "message": "Espacio personalizado eliminado exitosamente",
            "space_id": space_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error al eliminar espacio personalizado: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al eliminar espacio personalizado: {str(e)}"
        )


# ---------- Assistant Endpoints ----------
@app.post("/assistants/generate-prompt", dependencies=[Depends(verify_token_dependency)])
async def generate_assistant_prompt(
    request: GeneratePromptRequest,
    payload: dict = Depends(verify_token_dependency)
) -> Dict[str, str]:
    """Genera un system prompt a partir de archivos subidos."""
    try:
        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token inválido")
        if _is_restricted_user(payload):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tu tipo de cuenta no permite generar asistentes personalizados",
            )

        file_paths = []
        for ref in request.file_refs:
            parts = ref.split("/")
            if len(parts) != 3:
                continue
            session_uuid, inner_uuid, filename = parts
            if session_uuid != user_id:
                raise HTTPException(status_code=403, detail="Archivos no pertenecen al usuario")
            path = BASE_DIR / session_uuid / inner_uuid / filename
            if path.exists():
                file_paths.append((str(path), filename))

        if not file_paths:
            raise HTTPException(status_code=400, detail="No se encontraron archivos válidos")

        prompt = generate_system_prompt_from_files(file_paths, request.user_hint)
        return {"system_prompt": prompt}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error generando prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/assistants", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_token_dependency)])
async def create_assistant(request: AssistantCreateRequest, payload: dict = Depends(verify_token_dependency)):
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    if _is_restricted_user(payload):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu tipo de cuenta no permite crear asistentes personalizados",
        )
    repo = AssistantRepository()
    try:
        a = repo.create(AssistantCreate(user_id=user_id, name=request.name, description=request.description, system_prompt=request.system_prompt))
        return _assistant_to_response(a, user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/assistants", dependencies=[Depends(verify_token_dependency)])
async def list_assistants(
    page: int = 1,
    limit: int = 10,
    scope: str = Query("mine", description="'mine' = tus asistentes; 'catalog' = todos los asistentes"),
    payload: dict = Depends(verify_token_dependency),
):
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")

    scope_norm = (scope or "mine").strip().lower()
    if scope_norm not in ("mine", "catalog"):
        raise HTTPException(status_code=400, detail="scope debe ser mine o catalog")

    if page < 1:
        page = 1
    if limit < 1 or limit > 50:
        limit = 10

    offset = (page - 1) * limit
    repo = AssistantRepository()

    try:
        if scope_norm == "catalog":
            assistants = repo.list_all_assistants(limit=limit, offset=offset)
            total = repo.count_all_assistants()
        else:
            assistants = repo.get_user_assistants(user_id, limit=limit, offset=offset)
            total = repo.count_user_assistants(user_id)
        total_pages = (total + limit - 1) // limit if total > 0 else 1

        list_scope: Literal["mine", "catalog"] = "catalog" if scope_norm == "catalog" else "mine"
        return {
            "items": [_assistant_to_response(a, user_id, list_scope=list_scope) for a in assistants],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/assistants/{assistant_id}", dependencies=[Depends(verify_token_dependency)])
async def get_assistant(assistant_id: str, payload: dict = Depends(verify_token_dependency)):
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    repo = AssistantRepository()
    a = repo.get(assistant_id)
    if not a or not _can_use_assistant(a, user_id):
        raise HTTPException(status_code=404, detail="Asistente no encontrado")
    return _assistant_to_response(a, user_id)


@app.put("/assistants/{assistant_id}", dependencies=[Depends(verify_token_dependency)])
async def update_assistant(assistant_id: str, request: AssistantUpdateRequest, payload: dict = Depends(verify_token_dependency)):
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    repo = AssistantRepository()
    a = repo.get(assistant_id)
    if not a or a.user_id != user_id:
        raise HTTPException(status_code=404, detail="Asistente no encontrado")
    a = repo.update(assistant_id, AssistantUpdate(name=request.name, description=request.description, system_prompt=request.system_prompt))
    return _assistant_to_response(a, user_id)


@app.delete("/assistants/{assistant_id}", dependencies=[Depends(verify_token_dependency)])
async def delete_assistant(assistant_id: str, payload: dict = Depends(verify_token_dependency)):
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    repo = AssistantRepository()
    a = repo.get(assistant_id)
    if not a or a.user_id != user_id:
        raise HTTPException(status_code=404, detail="Asistente no encontrado")
    repo.delete(assistant_id)
    return {"message": "Asistente eliminado", "assistant_id": assistant_id}


# ---------- Evaluations ----------
def _track_evaluation_api_event(
    *,
    http_request: Optional[Request],
    event_name: str,
    user_id: Optional[str],
    http_method: str,
    http_path: str,
    status_code: int = 200,
    success: bool = True,
    metadata: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[int] = None,
) -> None:
    """Eventos api.* registrados también en analytics_event_catalog (migración SQL)."""
    track_event(
        event_category="api",
        event_name=event_name,
        user_id=user_id,
        request=http_request,
        http_method=http_method,
        http_path=http_path,
        status_code=status_code,
        duration_ms=duration_ms,
        success=success,
        metadata=metadata or {},
        metrics=metrics or {},
    )


def _payload_participant_snapshot(payload: dict) -> tuple[str, str]:
    """Email y nombre para mostrar desde el JWT."""
    email = (payload.get("email") or "").strip()
    nombre = (payload.get("nombre") or "").strip()
    apellido = (payload.get("apellido") or "").strip()
    display = f"{nombre} {apellido}".strip()
    if not display:
        display = email
    if not display:
        display = str(payload.get("user_id") or "")
    return email, display


def _evaluation_list_item(e: Evaluation, viewer_id: str) -> Dict[str, Any]:
    is_owner = e.author_user_id == viewer_id
    return {
        "id": str(e.id),
        "title": e.title,
        "description": e.description,
        "published": e.published,
        "author_user_id": e.author_user_id,
        "is_owner": is_owner,
        "question_count": len(e.questions_json or []),
        "created_at": e.created_at.isoformat(),
        "updated_at": e.updated_at.isoformat(),
        "published_at": e.published_at.isoformat() if e.published_at else None,
    }


def _evaluation_detail(
    e: Evaluation,
    viewer_id: str,
    *,
    student_view: bool = False,
) -> Dict[str, Any]:
    is_owner = e.author_user_id == viewer_id
    questions = e.questions_json
    if student_view:
        questions = strip_questions_for_student(questions)
    dm = e.duration_minutes or 0
    return {
        "id": str(e.id),
        "title": e.title,
        "description": e.description,
        "requirements_hint": e.requirements_hint if is_owner else "",
        "published": e.published,
        "author_user_id": e.author_user_id,
        "is_owner": is_owner,
        "questions": questions,
        "duration_minutes": dm if (e.published or is_owner) else 0,
        "timed": dm > 0,
        "share_token": e.share_token if is_owner else None,
        "created_at": e.created_at.isoformat(),
        "updated_at": e.updated_at.isoformat(),
        "published_at": e.published_at.isoformat() if e.published_at else None,
    }


EVAL_SUBMIT_GRACE_SECONDS = 15


def _session_deadline_passed(deadline_at: datetime, grace_seconds: int = EVAL_SUBMIT_GRACE_SECONDS) -> bool:
    now = datetime.now(timezone.utc)
    dl = deadline_at
    if dl.tzinfo is None:
        dl = dl.replace(tzinfo=timezone.utc)
    return now > dl + timedelta(seconds=grace_seconds)


def _resolve_uploaded_paths(user_id: str, file_refs: List[str]) -> List[tuple]:
    paths: List[tuple] = []
    for ref in file_refs:
        parts = ref.split("/")
        if len(parts) != 3:
            continue
        session_uuid, inner_uuid, filename = parts
        if session_uuid != user_id:
            raise HTTPException(status_code=403, detail="Archivos no pertenecen al usuario")
        path = BASE_DIR / session_uuid / inner_uuid / filename
        if path.exists():
            paths.append((str(path), filename))
    return paths


@app.post("/evaluations/generate")
async def generate_evaluation_endpoint(
    http_request: Request,
    gen_body: GenerateEvaluationRequest,
    payload: dict = Depends(require_admin_dependency),
) -> Dict[str, Any]:
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    t0 = time.perf_counter()
    try:
        paths = _resolve_uploaded_paths(user_id, gen_body.file_refs)
        if not paths:
            raise HTTPException(status_code=400, detail="No se encontraron archivos válidos")
        req = (gen_body.requirements or "").strip()
        extra = (gen_body.additional_context or "").strip()
        merged = ""
        if req:
            merged += "Requisitos y enfoque de la evaluación:\n" + req
        if extra:
            if merged:
                merged += "\n\n---\n\n"
            merged += "Contexto adicional (audiencia, programa, prioridades, etc.):\n" + extra
        draft = generate_evaluation_from_files(paths, merged)
        nq = len((draft or {}).get("questions") or [])
        _track_evaluation_api_event(
            http_request=http_request,
            event_name="evaluation.generate.completed",
            user_id=str(user_id),
            http_method="POST",
            http_path="/evaluations/generate",
            metadata={
                "file_ref_count": len(gen_body.file_refs),
                "question_count": nq,
                "has_requirements": bool(req),
                "has_additional_context": bool(extra),
            },
            metrics={"duration_ms": int((time.perf_counter() - t0) * 1000)},
        )
        return draft
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Error generando evaluación: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/evaluations", status_code=status.HTTP_201_CREATED)
async def create_evaluation_endpoint(
    http_request: Request,
    body: EvaluationCreateRequest,
    payload: dict = Depends(require_admin_dependency),
) -> Dict[str, Any]:
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Título requerido")
    if not body.questions:
        raise HTTPException(status_code=400, detail="La evaluación debe incluir preguntas")
    repo = EvaluationRepository()
    dm_req = body.duration_minutes
    dm: Optional[int] = None
    if dm_req is not None and dm_req > 0:
        dm = int(dm_req)
    share_tok = str(uuid.uuid4()) if body.published else None
    ev = repo.create(
        EvaluationCreate(
            author_user_id=user_id,
            title=body.title.strip(),
            description=body.description or "",
            requirements_hint=body.requirements_hint or "",
            questions_json=body.questions,
            duration_minutes=dm,
            share_token=share_tok,
        )
    )
    if body.published:
        updated = repo.update(ev.id, EvaluationUpdate(published=True))
        if updated:
            ev = updated
            if not ev.share_token:
                ev = repo.update(ev.id, EvaluationUpdate(share_token=str(uuid.uuid4()))) or ev
    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.created",
        user_id=str(user_id),
        http_method="POST",
        http_path="/evaluations",
        metadata={
            "evaluation_id": str(ev.id),
            "published": bool(ev.published),
            "question_count": len(ev.questions_json or []),
            "duration_minutes": ev.duration_minutes,
        },
    )
    return _evaluation_detail(ev, user_id)


@app.get("/evaluations", dependencies=[Depends(verify_token_dependency)])
async def list_evaluations_endpoint(
    http_request: Request,
    scope: str = Query("published", description="mine | published"),
    payload: dict = Depends(verify_token_dependency),
) -> Dict[str, Any]:
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    scope_n = (scope or "published").strip().lower()
    repo = EvaluationRepository()
    if scope_n == "mine":
        _require_admin_payload(payload)
        items = repo.list_by_author(user_id, limit=50, offset=0)
    elif scope_n == "published":
        items = repo.list_published(limit=50, offset=0)
    else:
        raise HTTPException(status_code=400, detail="scope debe ser mine o published")
    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.list",
        user_id=str(user_id),
        http_method="GET",
        http_path="/evaluations",
        metadata={"scope": scope_n, "result_count": len(items)},
    )
    return {"items": [_evaluation_list_item(e, user_id) for e in items]}


@app.get("/evaluations/{evaluation_id}", dependencies=[Depends(verify_token_dependency)])
async def get_evaluation_endpoint(
    http_request: Request,
    evaluation_id: str,
    preview_student: bool = Query(
        False,
        description="Solo el autor: devuelve preguntas sin solución (vista participante)",
    ),
    payload: dict = Depends(verify_token_dependency),
) -> Dict[str, Any]:
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    repo = EvaluationRepository()
    ev = repo.get(evaluation_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Evaluación no encontrada")
    is_owner = ev.author_user_id == user_id
    if not ev.published and not is_owner:
        raise HTTPException(status_code=404, detail="Evaluación no encontrada")
    student_view = (not is_owner) or (is_owner and preview_student)
    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.detail.viewed",
        user_id=str(user_id),
        http_method="GET",
        http_path="/evaluations/{evaluation_id}",
        metadata={
            "evaluation_id": evaluation_id,
            "preview_student": preview_student,
            "student_view": student_view,
            "is_owner": is_owner,
        },
    )
    return _evaluation_detail(ev, user_id, student_view=student_view)


@app.put("/evaluations/{evaluation_id}")
async def update_evaluation_endpoint(
    http_request: Request,
    evaluation_id: str,
    body: EvaluationUpdateRequest,
    payload: dict = Depends(require_admin_dependency),
) -> Dict[str, Any]:
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    repo = EvaluationRepository()
    ev = repo.get(evaluation_id)
    if not ev or ev.author_user_id != user_id:
        raise HTTPException(status_code=404, detail="Evaluación no encontrada")
    gen_share: Optional[str] = None
    if body.published is True and not ev.share_token:
        gen_share = str(uuid.uuid4())
    updated = repo.update(
        evaluation_id,
        EvaluationUpdate(
            title=body.title,
            description=body.description,
            requirements_hint=body.requirements_hint,
            questions_json=body.questions,
            published=body.published,
            duration_minutes=body.duration_minutes,
            share_token=gen_share,
        ),
    )
    if not updated:
        raise HTTPException(status_code=500, detail="No se pudo actualizar")
    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.updated",
        user_id=str(user_id),
        http_method="PUT",
        http_path="/evaluations/{evaluation_id}",
        metadata={
            "evaluation_id": evaluation_id,
            "published": updated.published,
            "question_count": len(updated.questions_json or []),
        },
    )
    return _evaluation_detail(updated, user_id)


@app.delete("/evaluations/{evaluation_id}")
async def delete_evaluation_endpoint(
    http_request: Request,
    evaluation_id: str,
    payload: dict = Depends(require_admin_dependency),
) -> Dict[str, str]:
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    repo = EvaluationRepository()
    ev = repo.get(evaluation_id)
    if not ev or ev.author_user_id != user_id:
        raise HTTPException(status_code=404, detail="Evaluación no encontrada")
    repo.delete(evaluation_id)
    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.deleted",
        user_id=str(user_id),
        http_method="DELETE",
        http_path="/evaluations/{evaluation_id}",
        metadata={"evaluation_id": evaluation_id},
    )
    return {"message": "Evaluación eliminada", "evaluation_id": evaluation_id}


@app.get("/evaluations/share/{share_token}/meta")
async def evaluation_share_meta(http_request: Request, share_token: str) -> Dict[str, Any]:
    """Metadatos públicos (sin autenticación) para la página de enlace compartido."""
    repo = EvaluationRepository()
    ev = repo.get_by_share_token(share_token)
    if not ev or not ev.published:
        raise HTTPException(status_code=404, detail="Evaluación no disponible")
    dm = ev.duration_minutes or 0
    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.share.meta.viewed",
        user_id=None,
        http_method="GET",
        http_path="/evaluations/share/{share_token}/meta",
        metadata={
            "evaluation_id": str(ev.id),
            "timed": dm > 0,
            "duration_minutes": dm,
        },
    )
    return {
        "evaluation_id": str(ev.id),
        "title": ev.title,
        "description": ev.description,
        "duration_minutes": dm,
        "timed": dm > 0,
    }


@app.post("/evaluations/session/start", dependencies=[Depends(verify_token_dependency)])
async def start_evaluation_session(
    http_request: Request,
    body: StartEvalSessionRequest,
    payload: dict = Depends(verify_token_dependency),
) -> Dict[str, Any]:
    """Inicia o reanuda una sesión temporizada; sin tiempo devuelve solo las preguntas."""
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    if bool(body.share_token) == bool(body.evaluation_id):
        raise HTTPException(
            status_code=400,
            detail="Indica exactamente uno: share_token o evaluation_id",
        )
    repo = EvaluationRepository()
    ev: Optional[Evaluation] = None
    if body.share_token:
        ev = repo.get_by_share_token(body.share_token.strip())
    else:
        ev = repo.get(body.evaluation_id or "")
    if not ev or not ev.published:
        raise HTTPException(status_code=404, detail="Evaluación no disponible")
    questions_student = strip_questions_for_student(ev.questions_json)
    server_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    dmin = ev.duration_minutes or 0
    ev_id_str = str(ev.id)
    if dmin <= 0:
        _track_evaluation_api_event(
            http_request=http_request,
            event_name="evaluation.session.started",
            user_id=str(user_id),
            http_method="POST",
            http_path="/evaluations/session/start",
            metadata={
                "evaluation_id": ev_id_str,
                "timed": False,
                "resumed": False,
                "via_share_token": bool(body.share_token),
            },
        )
        return {
            "evaluation_id": ev_id_str,
            "session_id": None,
            "deadline_at": None,
            "server_now": server_now,
            "questions": questions_student,
            "timed": False,
            "resumed": False,
        }
    repo.close_expired_open_sessions(ev.id, user_id)
    existing = repo.get_resumable_session(ev.id, user_id)
    if existing:
        _track_evaluation_api_event(
            http_request=http_request,
            event_name="evaluation.session.started",
            user_id=str(user_id),
            http_method="POST",
            http_path="/evaluations/session/start",
            metadata={
                "evaluation_id": ev_id_str,
                "timed": True,
                "resumed": True,
                "session_id": str(existing.id),
                "via_share_token": bool(body.share_token),
            },
        )
        return {
            "evaluation_id": ev_id_str,
            "session_id": str(existing.id),
            "deadline_at": existing.deadline_at.isoformat().replace("+00:00", "Z"),
            "server_now": server_now,
            "questions": questions_student,
            "timed": True,
            "resumed": True,
        }
    session = repo.create_take_session(ev.id, user_id, dmin)
    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.session.started",
        user_id=str(user_id),
        http_method="POST",
        http_path="/evaluations/session/start",
        metadata={
            "evaluation_id": ev_id_str,
            "timed": True,
            "resumed": False,
            "session_id": str(session.id),
            "duration_minutes": dmin,
            "via_share_token": bool(body.share_token),
        },
    )
    return {
        "evaluation_id": ev_id_str,
        "session_id": str(session.id),
        "deadline_at": session.deadline_at.isoformat().replace("+00:00", "Z"),
        "server_now": server_now,
        "questions": questions_student,
        "timed": True,
        "resumed": False,
    }


@app.post("/evaluations/{evaluation_id}/share/rotate")
async def rotate_evaluation_share_token(
    http_request: Request,
    evaluation_id: str,
    payload: dict = Depends(require_admin_dependency),
) -> Dict[str, Any]:
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    repo = EvaluationRepository()
    ev = repo.get(evaluation_id)
    if not ev or ev.author_user_id != user_id:
        raise HTTPException(status_code=404, detail="Evaluación no encontrada")
    new_tok = str(uuid.uuid4())
    updated = repo.update(evaluation_id, EvaluationUpdate(share_token=new_tok))
    if not updated:
        raise HTTPException(status_code=500, detail="No se pudo actualizar el enlace")
    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.share.rotated",
        user_id=str(user_id),
        http_method="POST",
        http_path="/evaluations/{evaluation_id}/share/rotate",
        metadata={"evaluation_id": evaluation_id},
    )
    return {"share_token": updated.share_token}


@app.post("/evaluations/{evaluation_id}/submit", dependencies=[Depends(verify_token_dependency)])
async def submit_evaluation_endpoint(
    http_request: Request,
    evaluation_id: str,
    body: EvaluationSubmitRequest,
    payload: dict = Depends(verify_token_dependency),
) -> Dict[str, Any]:
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    repo = EvaluationRepository()
    ev = repo.get(evaluation_id)
    if not ev or not ev.published:
        raise HTTPException(status_code=404, detail="Evaluación no disponible")
    questions = ev.questions_json
    dmin = ev.duration_minutes or 0
    take_session_id: Optional[uuid.UUID] = None

    if dmin > 0:
        if not body.session_id:
            raise HTTPException(
                status_code=400,
                detail="Esta evaluación tiene tiempo límite: inicia sesión con POST /evaluations/session/start",
            )
        sess = repo.get_take_session(body.session_id)
        if (
            not sess
            or str(sess.evaluation_id) != str(ev.id)
            or sess.user_id != user_id
        ):
            raise HTTPException(status_code=400, detail="Sesión inválida")
        if sess.submitted_at is not None:
            raise HTTPException(status_code=400, detail="Esta sesión ya fue enviada")
        if _session_deadline_passed(sess.deadline_at):
            raise HTTPException(
                status_code=400,
                detail="El tiempo de la evaluación ha finalizado. No se puede enviar.",
            )
        take_session_id = sess.id
        answers_graded = normalize_answers_for_grading(questions, body.answers)
    else:
        try:
            validate_answers_complete(questions, body.answers)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        answers_graded = body.answers

    now = datetime.now(timezone.utc)
    duration_seconds: Optional[int] = None
    seconds_remaining_at_submit: Optional[float] = None
    started_at: Optional[datetime] = None
    sess_obj: Optional[TakeSession] = None
    if take_session_id:
        sess_obj = repo.get_take_session(take_session_id)
        if sess_obj:
            st = sess_obj.started_at
            if isinstance(st, datetime):
                started_at = st if st.tzinfo else st.replace(tzinfo=timezone.utc)
                duration_seconds = max(0, int((now - started_at).total_seconds()))
            dl = sess_obj.deadline_at
            if isinstance(dl, datetime):
                dlx = dl if dl.tzinfo else dl.replace(tzinfo=timezone.utc)
                seconds_remaining_at_submit = max(0.0, (dlx - now).total_seconds())

    try:
        score, feedback, per_q, performance_profile = grade_submission(
            questions,
            answers_graded,
            duration_seconds=duration_seconds,
            time_limit_minutes=dmin if dmin > 0 else None,
            seconds_remaining_at_submit=seconds_remaining_at_submit,
        )
    except Exception as e:
        print(f"Error calificando evaluación: {e}")
        raise HTTPException(status_code=500, detail="No se pudo calificar la evaluación")

    review = build_submission_review(questions, answers_graded, per_q)

    p_email, p_name = _payload_participant_snapshot(payload)
    metrics: Dict[str, Any] = {
        "per_question_scores": {k: round(float(v), 4) for k, v in per_q.items()},
        "question_count": len(questions),
        "timed": dmin > 0,
        "multiple_choice_count": sum(1 for q in questions if q.get("type") == "multiple_choice"),
        "open_count": sum(1 for q in questions if q.get("type") == "open"),
        "performance_profile": performance_profile,
        "timing_context": {
            "duration_seconds": duration_seconds,
            "time_limit_minutes": dmin if dmin > 0 else None,
            "seconds_remaining_at_submit": seconds_remaining_at_submit,
        },
    }

    attempt = repo.insert_attempt(
        evaluation_id,
        user_id,
        answers_graded,
        score,
        feedback,
        take_session_id=take_session_id,
        participant_email=p_email or None,
        participant_name=p_name or None,
        started_at=started_at,
        duration_seconds=duration_seconds,
        metrics_json=metrics,
    )
    if take_session_id:
        repo.mark_session_submitted(take_session_id)
    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.submitted",
        user_id=str(user_id),
        http_method="POST",
        http_path="/evaluations/{evaluation_id}/submit",
        metadata={
            "evaluation_id": evaluation_id,
            "attempt_id": str(attempt.id),
            "timed": dmin > 0,
            "session_id": str(take_session_id) if take_session_id else None,
        },
        metrics={
            "score_percent": (
                float(attempt.score_percent)
                if attempt.score_percent is not None
                else None
            ),
            "question_count": len(questions),
            "duration_seconds": (
                int(attempt.duration_seconds)
                if attempt.duration_seconds is not None
                else None
            ),
        },
    )
    return {
        "attempt_id": str(attempt.id),
        "score_percent": attempt.score_percent,
        "feedback": attempt.feedback,
        "performance_profile": performance_profile,
        "review": review,
        "submitted_at": attempt.created_at.isoformat(),
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "duration_seconds": attempt.duration_seconds,
        "participant_name": attempt.participant_name,
        "participant_email": attempt.participant_email,
        "metrics": metrics,
    }


@app.get("/evaluations/{evaluation_id}/attempts", dependencies=[Depends(verify_token_dependency)])
async def list_evaluation_attempts_endpoint(
    http_request: Request,
    evaluation_id: str,
    payload: dict = Depends(verify_token_dependency),
) -> Dict[str, Any]:
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    repo = EvaluationRepository()
    ev = repo.get(evaluation_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Evaluación no encontrada")
    is_owner = ev.author_user_id == user_id
    if is_owner:
        if _token_user_type(payload) == "admin":
            attempts = repo.list_attempts_for_evaluation(evaluation_id, user_id_filter=None)
        else:
            attempts = repo.list_attempts_for_evaluation(evaluation_id, user_id_filter=user_id)
    else:
        if not ev.published:
            raise HTTPException(status_code=404, detail="Evaluación no encontrada")
        attempts = repo.list_attempts_for_evaluation(evaluation_id, user_id_filter=user_id)
    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.attempts.listed",
        user_id=str(user_id),
        http_method="GET",
        http_path="/evaluations/{evaluation_id}/attempts",
        metadata={
            "evaluation_id": evaluation_id,
            "is_owner": is_owner,
            "admin_view_all": is_owner and _token_user_type(payload) == "admin",
            "result_count": len(attempts),
        },
    )
    out = []
    for a in attempts:
        row: Dict[str, Any] = {
            "id": str(a.id),
            "score_percent": a.score_percent,
            "feedback": a.feedback,
            "created_at": a.created_at.isoformat(),
            "submitted_at": a.created_at.isoformat(),
            "started_at": a.started_at.isoformat() if a.started_at else None,
            "duration_seconds": a.duration_seconds,
            "participant_email": a.participant_email,
            "participant_name": a.participant_name,
            "metrics": a.metrics_json or {},
        }
        if is_owner:
            row["user_id"] = a.user_id
            row["answers"] = a.answers_json
        else:
            row["answers"] = a.answers_json
        out.append(row)
    return {"items": out}


def _compute_evaluation_analytics_payload(
    evaluation_id: str,
    evaluation_title: str,
    attempts: List[EvaluationAttempt],
    filtered_user_id: Optional[str],
) -> Dict[str, Any]:
    """Agrega métricas desde intentos ya cargados (sin I/O)."""

    def _safe_float(v: Any) -> Optional[float]:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    scores: List[float] = []
    durations: List[float] = []
    per_student: List[Dict[str, Any]] = []
    q_accum: Dict[str, List[float]] = {}
    radar_accum: Dict[str, List[float]] = {}
    band_counts: Dict[str, int] = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    mc_attempts: List[float] = []
    open_attempts: List[float] = []
    level_counts: Dict[str, int] = {}
    effort_counts: Dict[str, int] = {}
    depth_counts: Dict[str, int] = {}
    pacing_counts: Dict[str, int] = {}
    timed_count = 0

    for a in attempts:
        sc = _safe_float(a.score_percent)
        if sc is not None:
            scores.append(sc)
        if a.duration_seconds is not None:
            durations.append(float(a.duration_seconds))

        mj: Dict[str, Any] = a.metrics_json or {}
        pp: Dict[str, Any] = mj.get("performance_profile") or {}
        dc: Dict[str, Any] = pp.get("dashboard_charts") or {}
        summary_dc: Dict[str, Any] = dc.get("summary") or {}
        timing_dc: Dict[str, Any] = dc.get("timing") or {}

        if timing_dc.get("is_timed"):
            timed_count += 1

        mc_avg = summary_dc.get("multiple_choice_avg_percent")
        op_avg = summary_dc.get("open_avg_percent")
        if mc_avg is not None:
            v = _safe_float(mc_avg)
            if v is not None:
                mc_attempts.append(v)
        if op_avg is not None:
            v = _safe_float(op_avg)
            if v is not None:
                open_attempts.append(v)

        for item in dc.get("bar_by_item") or []:
            qid = item.get("key") or item.get("name") or ""
            s = _safe_float(item.get("score_percent"))
            if qid and s is not None:
                q_accum.setdefault(qid, []).append(s)
            band = item.get("performance_band") or ""
            if band == "high":
                b = "81-100"
            elif band == "mid":
                b = "41-60"
            else:
                b = "0-20"
            band_counts[b] = band_counts.get(b, 0) + 1

        for rd in dc.get("radar_dimensions") or []:
            ak = rd.get("axis_key") or ""
            v = _safe_float(rd.get("value"))
            if ak and v is not None:
                radar_accum.setdefault(ak, []).append(v)

        overall: Dict[str, Any] = pp.get("overall") or {}
        lvl = str(overall.get("relative_level") or "").lower()
        if lvl:
            level_counts[lvl] = level_counts.get(lvl, 0) + 1

        recs: Dict[str, Any] = pp.get("study_recommendations") or {}
        ef = str(recs.get("estimated_effort_to_improve") or "").lower()
        if ef:
            effort_counts[ef] = effort_counts.get(ef, 0) + 1

        pacing: Dict[str, Any] = pp.get("engagement_and_pacing") or {}
        dp = str(pacing.get("open_response_depth") or "").lower()
        if dp:
            depth_counts[dp] = depth_counts.get(dp, 0) + 1
        ps = str(pacing.get("time_pressure_signal") or "").lower()
        if ps:
            pacing_counts[ps] = pacing_counts.get(ps, 0) + 1

        student_row: Dict[str, Any] = {
            "attempt_id": str(a.id),
            "user_id": a.user_id,
            "participant_name": a.participant_name or "",
            "participant_email": a.participant_email or "",
            "score_percent": sc,
            "duration_seconds": a.duration_seconds,
            "submitted_at": a.created_at.isoformat(),
            "relative_level": lvl or "unknown",
            "mc_avg_percent": mc_avg,
            "open_avg_percent": op_avg,
            "bar_by_item": dc.get("bar_by_item") or [],
            "radar_dimensions": dc.get("radar_dimensions") or [],
            "per_question_scores": mj.get("per_question_scores") or {},
            "competency_dimensions": pp.get("competency_dimensions") or {},
            "patterns": pp.get("patterns") or {},
            "study_recommendations": pp.get("study_recommendations") or {},
            "engagement_and_pacing": pp.get("engagement_and_pacing") or {},
            "timing": dc.get("timing") or {},
        }
        per_student.append(student_row)

    n = len(attempts)
    avg_score = (sum(scores) / len(scores)) if scores else None
    avg_dur = (sum(durations) / len(durations)) if durations else None

    score_stddev = statistics_mod.pstdev(scores) if len(scores) > 1 else 0.0

    bar_by_item_agg = [
        {
            "key": qid,
            "name": qid,
            "avg_score_percent": round(sum(vals) / len(vals), 2),
            "sample_n": len(vals),
        }
        for qid, vals in sorted(q_accum.items())
    ]

    radar_agg = [
        {
            "subject": {
                "overall": "Promedio global",
                "mc_avg": "Opción múltiple (media)",
                "open_avg": "Respuesta abierta (media)",
                "consistency": "Consistencia entre ítems",
            }.get(ak, ak),
            "axis_key": ak,
            "value": round(sum(vals) / len(vals), 2),
            "fullMark": 100,
        }
        for ak, vals in radar_accum.items()
    ]

    histogram = [
        {"range_label": k, "attempt_count": v, "range_key": k.replace("-", "_")}
        for k, v in band_counts.items()
    ]

    score_series = [
        {
            "label": f"Int.{i+1}",
            "score_percent": round(s, 2),
            "participant": per_student[i].get("participant_name") or per_student[i].get("user_id", "")[:8],
        }
        for i, s in enumerate(scores)
    ]

    return {
        "evaluation_id": evaluation_id,
        "evaluation_title": evaluation_title,
        "filtered_user_id": filtered_user_id,
        "attempt_count": n,
        "timed_attempt_count": timed_count,
        "summary": {
            "avg_score_percent": round(avg_score, 2) if avg_score is not None else None,
            "score_stddev": round(score_stddev, 4),
            "avg_duration_seconds": round(avg_dur, 1) if avg_dur is not None else None,
            "mc_avg_percent_across_attempts": round(sum(mc_attempts) / len(mc_attempts), 2) if mc_attempts else None,
            "open_avg_percent_across_attempts": round(sum(open_attempts) / len(open_attempts), 2) if open_attempts else None,
            "max_score_percent": round(max(scores), 2) if scores else None,
            "min_score_percent": round(min(scores), 2) if scores else None,
        },
        "level_distribution": level_counts,
        "effort_to_improve_distribution": effort_counts,
        "open_response_depth_distribution": depth_counts,
        "time_pressure_distribution": pacing_counts,
        "bar_by_item_aggregated": bar_by_item_agg,
        "radar_aggregated": radar_agg,
        "histogram_score_distribution": histogram,
        "score_series": score_series,
        "per_student": per_student,
    }


@app.get("/evaluations/{evaluation_id}/analytics")
async def evaluation_analytics_endpoint(
    http_request: Request,
    evaluation_id: str,
    user_id_filter: Optional[str] = Query(None, alias="user_id"),
    payload: dict = Depends(require_admin_dependency),
) -> Dict[str, Any]:
    """
    Agrega métricas de TODOS los intentos de una evaluación para el dashboard.
    Solo admins. Acepta ?user_id=<id> para filtrar por un participante concreto.
    Una query lean (sin answers_json).
    """
    requester_id = payload.get("user_id") or ""
    repo = EvaluationRepository()
    ev = repo.get(evaluation_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Evaluación no encontrada")

    attempts = repo.list_attempts_for_evaluation_analytics(
        evaluation_id,
        user_id_filter=user_id_filter or None,
    )
    result = _compute_evaluation_analytics_payload(
        str(ev.id),
        ev.title,
        attempts,
        user_id_filter,
    )
    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.analytics.viewed",
        user_id=str(requester_id),
        http_method="GET",
        http_path="/evaluations/{evaluation_id}/analytics",
        metadata={
            "evaluation_id": evaluation_id,
            "user_id_filter": user_id_filter,
            "attempt_count": result["attempt_count"],
        },
    )
    return result


@app.get("/admin/evaluations/analytics")
async def admin_evaluations_analytics_bulk(
    http_request: Request,
    payload: dict = Depends(require_admin_dependency),
) -> Dict[str, Any]:
    """
    Todas las evaluaciones del autor + analytics en batch:
    1 consulta de evaluaciones, 1 consulta de intentos (IN), agregación en memoria.
    """
    requester_id = payload.get("user_id") or ""
    if not requester_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    repo = EvaluationRepository()
    evals = repo.list_by_author(str(requester_id), limit=50, offset=0)
    if not evals:
        return {"items": []}

    eids = [str(e.id) for e in evals]
    all_attempts = repo.list_attempts_for_evaluations_analytics(eids)
    by_eval: Dict[str, List[EvaluationAttempt]] = defaultdict(list)
    for a in all_attempts:
        by_eval[str(a.evaluation_id)].append(a)
    for eid in by_eval:
        by_eval[eid].sort(key=lambda x: x.created_at, reverse=True)

    items: List[Dict[str, Any]] = []
    total_attempts = 0
    for ev in evals:
        eid = str(ev.id)
        attempts = by_eval.get(eid, [])
        total_attempts += len(attempts)
        analytics = _compute_evaluation_analytics_payload(eid, ev.title, attempts, None)
        items.append(
            {
                "evaluation_id": eid,
                "title": ev.title,
                "published": ev.published,
                "created_at": ev.created_at.isoformat(),
                "analytics": analytics,
            }
        )

    _track_evaluation_api_event(
        http_request=http_request,
        event_name="evaluation.analytics.bulk_viewed",
        user_id=str(requester_id),
        http_method="GET",
        http_path="/admin/evaluations/analytics",
        metadata={"evaluation_count": len(evals), "attempt_rows": total_attempts},
    )
    return {"items": items}


# ---------- Helpers ----------
def sanitize_filename(filename: str) -> str:
    """
    Sanitiza el nombre del archivo para hacerlo seguro para el sistema de archivos.
    1. Normaliza caracteres Unicode (NFD)
    2. Elimina caracteres no ASCII y caracteres especiales
    3. Reemplaza espacios con guiones bajos
    4. Asegura que el nombre sea único agregando un UUID si es necesario
    """
    # Normalizar Unicode y convertir a ASCII
    filename = normalize('NFD', filename).encode('ascii', 'ignore').decode('ascii')
    
    # Obtener la extensión
    name, ext = os.path.splitext(filename)
    
    # Limpiar el nombre: solo letras, números, guiones y guiones bajos
    name = re.sub(r'[^\w\-_]', '_', name)
    name = re.sub(r'_+', '_', name)  # Reemplazar múltiples guiones bajos con uno solo
    name = name.strip('_')  # Eliminar guiones bajos al inicio y final
    
    # Si el nombre está vacío después de la limpieza, usar un UUID
    if not name:
        name = str(uuid.uuid4())
    
    # Asegurar que el nombre no sea demasiado largo (máximo 255 caracteres)
    max_length = 255 - len(ext)
    if len(name) > max_length:
        name = name[:max_length]
    
    return f"{name}{ext}"

def create_user_session_dir(user_id: str, inner_uuid: str) -> Path:
    """
    Crea el directorio de sesión para un usuario.
    Usa el user_id como directorio principal y un UUID para la sesión específica.
    """
    path = BASE_DIR / user_id / inner_uuid
    path.mkdir(parents=True, exist_ok=True)
    return path


async def cleanup_sessions():
    """Elimina sesiones inactivas y security codes expirados"""
    while True:
        # Limpiar sesiones
        now = time.time()
        expired = []
        for sess, meta in list(sessions.items()):
            if now - meta["last"] > SESSION_TIMEOUT:
                expired.append(sess)

        for sess in expired:
            try:
                shutil.rmtree(BASE_DIR / sess, ignore_errors=True)
            except Exception:
                pass
            sessions.pop(sess, None)

        # Limpiar security codes
        now_dt = datetime.utcnow()
        expired_codes = [
            code for code, meta in security_codes.items()
            if meta["expires_at"] < now_dt
        ]
        for code in expired_codes:
            security_codes.pop(code, None)

        await asyncio.sleep(60)  # correr cada minuto


async def cleanup_storage_periodically():
    """Elimina todo el contenido del storage cada hora"""
    while True:
        try:
            await asyncio.sleep(3600)  # 1 hora = 3600 segundos
            
            print(f"[Storage Cleanup] Iniciando limpieza periódica del storage a las {datetime.utcnow().isoformat()}")
            
            deleted_files_count = 0
            deleted_dirs_count = 0
            
            if BASE_DIR.exists():
                for user_dir in BASE_DIR.iterdir():
                    if user_dir.is_dir():
                        try:
                            # Contar archivos antes de eliminar
                            file_count = sum(1 for _ in user_dir.rglob('*') if _.is_file())
                            deleted_files_count += file_count
                            
                            # Eliminar directorio completo
                            shutil.rmtree(user_dir, ignore_errors=True)
                            deleted_dirs_count += 1
                        except Exception as e:
                            print(f"[Storage Cleanup] Error eliminando directorio {user_dir.name}: {e}")
            
            # Asegurar que el directorio base existe después de la limpieza
            BASE_DIR.mkdir(exist_ok=True)
            
            # Limpiar diccionario de sesiones
            sessions.clear()
            
            print(f"[Storage Cleanup] Limpieza completada: {deleted_files_count} archivos, {deleted_dirs_count} directorios eliminados")
            
        except Exception as e:
            print(f"[Storage Cleanup] Error en limpieza periódica: {e}")


async def cleanup_storage_daily():
    """Elimina todo el contenido del storage al final de cada día (medianoche UTC)"""
    while True:
        try:
            # Calcular tiempo hasta la próxima medianoche UTC
            now = datetime.utcnow()
            next_midnight = datetime(now.year, now.month, now.day, 23, 59, 59) + timedelta(days=1)
            seconds_until_midnight = (next_midnight - now).total_seconds()
            
            print(f"[Storage Cleanup] Próxima limpieza diaria programada para: {next_midnight.isoformat()} UTC")
            
            # Esperar hasta medianoche
            await asyncio.sleep(seconds_until_midnight)
            
            print(f"[Storage Cleanup] Iniciando limpieza diaria del storage a las {datetime.utcnow().isoformat()}")
            
            deleted_files_count = 0
            deleted_dirs_count = 0
            
            if BASE_DIR.exists():
                for user_dir in BASE_DIR.iterdir():
                    if user_dir.is_dir():
                        try:
                            # Contar archivos antes de eliminar
                            file_count = sum(1 for _ in user_dir.rglob('*') if _.is_file())
                            deleted_files_count += file_count
                            
                            # Eliminar directorio completo
                            shutil.rmtree(user_dir, ignore_errors=True)
                            deleted_dirs_count += 1
                        except Exception as e:
                            print(f"[Storage Cleanup] Error eliminando directorio {user_dir.name}: {e}")
            
            # Asegurar que el directorio base existe después de la limpieza
            BASE_DIR.mkdir(exist_ok=True)
            
            # Limpiar diccionario de sesiones
            sessions.clear()
            
            print(f"[Storage Cleanup] Limpieza diaria completada: {deleted_files_count} archivos, {deleted_dirs_count} directorios eliminados")
            
            # Esperar 1 segundo antes de recalcular para la próxima medianoche
            await asyncio.sleep(1)
            
        except Exception as e:
            print(f"[Storage Cleanup] Error en limpieza diaria: {e}")
            # Si hay error, esperar 1 hora antes de reintentar
            await asyncio.sleep(3600)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(cleanup_sessions())
    asyncio.create_task(cleanup_storage_periodically())
    asyncio.create_task(cleanup_storage_daily())
    asyncio.create_task(cleanup_rate_limit_records())
# ------------------------------


@app.post("/start-session", dependencies=[Depends(verify_token_dependency)])
async def start_session(
    http_request: Request,
    token_data: dict = Depends(verify_token_dependency),
):
    """
    Crea una nueva sesión de chat para el usuario autenticado.
    El user_id se obtiene del token JWT y se usa como session_uuid.
    """
    try:
        # Obtener user_id del token
        user_id = token_data.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido"
            )

        # Verificar que el usuario existe
        user_repo = UserRepository()
        user = user_repo.get_user(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuario no encontrado"
            )

        # Crear directorio de sesión usando user_id
        inner_uuid = str(uuid.uuid4())
        path = create_user_session_dir(user_id, inner_uuid)

        # Actualizar diccionario de sesiones
        if user_id not in sessions:
            sessions[user_id] = {}
        sessions[user_id] = {"last": time.time(), "inner": inner_uuid}

        track_event(
            event_category="api",
            event_name="session.started",
            user_id=user_id,
            request=http_request,
            thread_id=inner_uuid,
            http_method="POST",
            http_path="/start-session",
            status_code=200,
            success=True,
            metadata={"storage_path_set": bool(path)},
        )

        return {
            "session_uuid": user_id,  # Ahora es el user_id
            "inner_uuid": inner_uuid
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error creating session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al crear la sesión"
        )


@app.post("/files/{session_uuid}/{inner_uuid}", dependencies=[Depends(verify_token_dependency)])
async def upload_files(
    http_request: Request,
    session_uuid: str,
    inner_uuid: str,
    files: List[UploadFile] = File(...),
    token_data: dict = Depends(verify_token_dependency),
):
    """Sube archivos a la carpeta de la sesión"""
    # Verificar que el user_id del token coincide con session_uuid
    user_id = token_data.get("user_id")
    if user_id != session_uuid:
        return JSONResponse({"error": "Unauthorized: user_id mismatch"}, status_code=403)
    
    # Verificar si existe en el diccionario de sesiones (sesiones nuevas)
    meta = sessions.get(session_uuid)
    if not meta or meta.get("inner") != inner_uuid:
        # Si no está en sesiones, verificar si el thread existe en la base de datos
        try:
            thread_manager = ThreadManager(storage_base_dir=BASE_DIR)
            # Verificar que el thread existe y pertenece al usuario
            if not thread_manager._verify_thread_ownership(inner_uuid, session_uuid):
                return JSONResponse({"error": "Thread not found or access denied"}, status_code=404)
            
            # Thread existe y pertenece al usuario, actualizar diccionario de sesiones
            if session_uuid not in sessions:
                sessions[session_uuid] = {}
            sessions[session_uuid] = {"last": time.time(), "inner": inner_uuid}
            meta = sessions[session_uuid]
        except Exception as e:
            print(f"Error verifying thread: {e}")
            return JSONResponse({"error": "Thread verification failed"}, status_code=500)

    folder = BASE_DIR / session_uuid / inner_uuid
    if not folder.exists():
        # Crear el directorio si no existe
        folder.mkdir(parents=True, exist_ok=True)

    urls = []
    total_bytes = 0
    extensions: List[str] = []
    for file in files:
        # Sanitizar el nombre del archivo
        safe_filename = sanitize_filename(file.filename)
        _base, ext = os.path.splitext(safe_filename or "")
        if ext:
            extensions.append(ext.lower())
        file_path = folder / safe_filename
        
        # Si el archivo ya existe, agregar un sufijo único
        if file_path.exists():
            base, ext = os.path.splitext(safe_filename)
            safe_filename = f"{base}_{str(uuid.uuid4())[:8]}{ext}"
            file_path = folder / safe_filename

        raw = await file.read()
        if len(raw) > MAX_UPLOAD_FILE_BYTES:
            return JSONResponse(
                {
                    "error": "archivo demasiado grande",
                    "detail": f"Cada archivo debe ser de {MAX_UPLOAD_FILE_BYTES // (1024 * 1024)} MB como máximo",
                    "max_bytes": MAX_UPLOAD_FILE_BYTES,
                },
                status_code=413,
            )
        total_bytes += len(raw)
        with open(file_path, "wb") as f:
            f.write(raw)
        urls.append(f"/files/{session_uuid}/{inner_uuid}/{safe_filename}")

    meta["last"] = time.time()

    safe_record_interaction_event(
        InteractionEventInsert(
            event_category="storage",
            event_name="file.uploaded",
            user_id=user_id,
            thread_id=inner_uuid,
            http_method="POST",
            http_path=f"/files/{session_uuid}/{inner_uuid}",
            status_code=200,
            success=True,
            metadata={
                "file_count": len(urls),
                "extensions": list(dict.fromkeys(extensions))[:20],
            },
            metrics={"total_bytes": total_bytes, "files_count": len(urls)},
            client=client_snapshot_from_request(http_request),
        )
    )

    return {"uploaded": urls}


@app.get("/files/{session_uuid}/{inner_uuid}/{filename}", dependencies=[Depends(verify_token_dependency)])
async def download_file(
    http_request: Request,
    session_uuid: str,
    inner_uuid: str,
    filename: str,
    token_data: dict = Depends(verify_token_dependency),
):
    """Descargar archivo desde la sesión"""
    # Verificar que el user_id del token coincide con session_uuid
    user_id = token_data.get("user_id")
    if user_id != session_uuid:
        return JSONResponse({"error": "Unauthorized: user_id mismatch"}, status_code=403)
    
    # Verificar si existe en el diccionario de sesiones (sesiones nuevas)
    meta = sessions.get(session_uuid)
    if not meta or meta.get("inner") != inner_uuid:
        # Si no está en sesiones, verificar si el thread existe en la base de datos
        try:
            thread_manager = ThreadManager(storage_base_dir=BASE_DIR)
            if not thread_manager._verify_thread_ownership(inner_uuid, session_uuid):
                return JSONResponse({"error": "Thread not found or access denied"}, status_code=404)
            
            # Thread existe y pertenece al usuario, actualizar diccionario de sesiones
            if session_uuid not in sessions:
                sessions[session_uuid] = {}
            sessions[session_uuid] = {"last": time.time(), "inner": inner_uuid}
            meta = sessions[session_uuid]
        except Exception as e:
            print(f"Error verifying thread: {e}")
            return JSONResponse({"error": "Thread verification failed"}, status_code=500)

    # Sanitizar el nombre del archivo por seguridad
    safe_filename = sanitize_filename(filename)
    file_path = BASE_DIR / session_uuid / inner_uuid / safe_filename
    
    if not file_path.exists():
        # Si el archivo no existe con el nombre sanitizado, intentar buscar el archivo original
        original_path = BASE_DIR / session_uuid / inner_uuid / filename
        if not original_path.exists():
            return JSONResponse({"error": "File not found"}, status_code=404)
        file_path = original_path

    meta["last"] = time.time()
    try:
        sz = file_path.stat().st_size
    except Exception:
        sz = None
    safe_record_interaction_event(
        InteractionEventInsert(
            event_category="storage",
            event_name="file.downloaded",
            user_id=user_id,
            thread_id=inner_uuid,
            http_method="GET",
            http_path=f"/files/{session_uuid}/{inner_uuid}/[file]",
            status_code=200,
            success=True,
            metadata={"filename_suffix": os.path.splitext(filename)[1].lower()},
            metrics={"file_bytes": sz} if sz is not None else {},
            client=client_snapshot_from_request(http_request),
        )
    )
    return FileResponse(file_path, filename=filename)


@app.delete("/files/{session_uuid}/{inner_uuid}/{filename}", dependencies=[Depends(verify_token_dependency)])
async def delete_file(
    http_request: Request,
    session_uuid: str,
    inner_uuid: str,
    filename: str,
    token_data: dict = Depends(verify_token_dependency),
):
    """Eliminar archivo de la sesión"""
    # Verificar que el user_id del token coincide con session_uuid
    user_id = token_data.get("user_id")
    if user_id != session_uuid:
        return JSONResponse({"error": "Unauthorized: user_id mismatch"}, status_code=403)
    
    # Verificar si existe en el diccionario de sesiones (sesiones nuevas)
    meta = sessions.get(session_uuid)
    if not meta or meta.get("inner") != inner_uuid:
        # Si no está en sesiones, verificar si el thread existe en la base de datos
        try:
            thread_manager = ThreadManager(storage_base_dir=BASE_DIR)
            if not thread_manager._verify_thread_ownership(inner_uuid, session_uuid):
                return JSONResponse({"error": "Thread not found or access denied"}, status_code=404)
            
            # Thread existe y pertenece al usuario, actualizar diccionario de sesiones
            if session_uuid not in sessions:
                sessions[session_uuid] = {}
            sessions[session_uuid] = {"last": time.time(), "inner": inner_uuid}
            meta = sessions[session_uuid]
        except Exception as e:
            print(f"Error verifying thread: {e}")
            return JSONResponse({"error": "Thread verification failed"}, status_code=500)

    # Sanitizar el nombre del archivo por seguridad
    safe_filename = sanitize_filename(filename)
    file_path = BASE_DIR / session_uuid / inner_uuid / safe_filename
    
    if not file_path.exists():
        # Si el archivo no existe con el nombre sanitizado, intentar buscar el archivo original
        original_path = BASE_DIR / session_uuid / inner_uuid / filename
        if not original_path.exists():
            return JSONResponse({"error": "File not found"}, status_code=404)
        file_path = original_path

    try:
        os.remove(file_path)
        meta["last"] = time.time()
        track_event(
            event_category="storage",
            event_name="file.deleted",
            user_id=user_id,
            request=http_request,
            thread_id=inner_uuid,
            http_method="DELETE",
            http_path=f"/files/{session_uuid}/{inner_uuid}/…",
            status_code=200,
            success=True,
            metadata={"filename_suffix": os.path.splitext(filename)[1].lower()},
        )
        return {"message": f"File {filename} deleted successfully"}
    except Exception as e:
        return JSONResponse(
            {"error": f"Failed to delete file: {str(e)}"}, 
            status_code=500
        )


@app.get("/threads", dependencies=[Depends(verify_token_dependency)], response_model=ThreadListResponse)
async def list_threads(
    http_request: Request,
    limit: int = 100,
    offset: int = 0,
    token_data: dict = Depends(verify_token_dependency),
):
    """
    Lista todos los threads del usuario autenticado con paginación.
    
    Args:
        limit: Número máximo de threads a retornar (1-1000, default: 100)
        offset: Número de threads a saltar para paginación (default: 0)
        token_data: Datos del token JWT decodificado
        
    Returns:
        ThreadListResponse con lista de threads y metadatos de paginación
    """
    try:
        user_id = token_data.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido"
            )

        thread_manager = ThreadManager(storage_base_dir=BASE_DIR)
        threads = thread_manager.list_user_threads(user_id, limit=limit, offset=offset)
        total = thread_manager.get_user_thread_count(user_id)

        track_event(
            event_category="thread",
            event_name="api.threads.list",
            user_id=user_id,
            request=http_request,
            http_method="GET",
            http_path="/threads",
            status_code=200,
            success=True,
            metrics={"returned_count": len(threads), "total": total, "limit": limit, "offset": offset},
        )

        return ThreadListResponse(
            threads=threads,
            total=total,
            limit=limit,
            offset=offset
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error listing threads: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al listar threads"
        )


@app.post("/threads", dependencies=[Depends(verify_token_dependency)], status_code=status.HTTP_201_CREATED, response_model=ThreadResponse)
async def create_thread(
    http_request: Request,
    body: Optional[ThreadCreateRequest] = Body(default=None),
    token_data: dict = Depends(verify_token_dependency),
):
    """
    Crea un nuevo thread de chat para el usuario autenticado.
    
    Args:
        request: Datos opcionales para crear el thread (puede incluir thread_id personalizado)
        token_data: Datos del token JWT decodificado
        
    Returns:
        ThreadResponse con información del thread creado
    """
    try:
        user_id = token_data.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido"
            )

        thread_manager = ThreadManager(storage_base_dir=BASE_DIR)
        
        # Crear thread usando el manager
        thread_data = thread_manager.create_thread(
            ThreadCreate(
                user_id=user_id,
                thread_id=body.thread_id if body else None
            ),
            create_storage_dir=True
        )
        
        # Actualizar diccionario de sesiones
        if user_id not in sessions:
            sessions[user_id] = {}
        sessions[user_id] = {"last": time.time(), "inner": thread_data["thread_id"]}

        track_event(
            event_category="thread",
            event_name="api.threads.create",
            user_id=user_id,
            request=http_request,
            thread_id=thread_data["thread_id"],
            http_method="POST",
            http_path="/threads",
            status_code=201,
            success=True,
            metadata={"custom_thread_id": bool(body and body.thread_id)},
        )

        # Retornar formato compatible con frontend
        return {
            "session_uuid": thread_data["user_id"],
            "inner_uuid": thread_data["thread_id"],
            "thread_id": thread_data["thread_id"],
            "user_id": thread_data["user_id"],
            "created_at": thread_data["created_at"],
            "storage_path": thread_data.get("storage_path"),
            "initial_message_id": thread_data.get("initial_message_id")
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error creating thread: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al crear thread"
        )


@app.get("/threads/{thread_id}", dependencies=[Depends(verify_token_dependency)], response_model=ThreadSummaryResponse)
async def get_thread(
    thread_id: str,
    token_data: dict = Depends(verify_token_dependency)
):
    """
    Obtiene información detallada de un thread específico.
    
    Args:
        thread_id: ID del thread a obtener
        token_data: Datos del token JWT decodificado
        
    Returns:
        ThreadSummaryResponse con información del thread
    """
    try:
        user_id = token_data.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido"
            )

        thread_manager = ThreadManager(storage_base_dir=BASE_DIR)
        summary = thread_manager.get_thread_summary(thread_id, user_id=user_id)
        
        if not summary:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread no encontrado"
            )

        return ThreadSummaryResponse(
            thread_id=summary.thread_id,
            user_id=summary.user_id,
            message_count=summary.message_count,
            last_message=summary.last_message,
            last_message_at=summary.last_message_at.isoformat() + "Z" if summary.last_message_at else None,
            last_role=summary.last_role,
            created_at=summary.created_at.isoformat() + "Z"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting thread: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener thread"
        )


@app.get("/threads/{thread_id}/messages", dependencies=[Depends(verify_token_dependency)])
async def get_thread_messages(
    http_request: Request,
    thread_id: str,
    limit: int = 200,
    token_data: dict = Depends(verify_token_dependency),
):
    """
    Obtiene los mensajes de un thread específico.
    
    Args:
        thread_id: ID del thread
        limit: Número máximo de mensajes a retornar (default: 200)
        token_data: Datos del token JWT decodificado
        
    Returns:
        Lista de mensajes del thread
    """
    try:
        user_id = token_data.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido"
            )

        chat_repo = ChatThreadRepository()
        if thread_id.startswith("assistant_"):
            assistant_id = thread_id.replace("assistant_", "", 1)
            assistant_repo = AssistantRepository()
            assistant = assistant_repo.get(assistant_id)
            if not assistant or not _can_use_assistant(assistant, user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No tienes permiso para acceder a este asistente",
                )
            messages = chat_repo.get_thread_messages(
                thread_id, limit=limit, ascending=True, user_id=user_id
            )
        else:
            messages = chat_repo.get_thread_messages(thread_id, limit=limit, ascending=True)
            if messages and messages[0].user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No tienes permiso para acceder a este thread",
                )

        # Formatear mensajes para el frontend
        formatted_messages = [
            {
                "id": str(msg.id),
                "sender": "user" if msg.role == "Human" else "bot",
                "text": msg.message,
                "timestamp": int(msg.created_at.timestamp() * 1000),
                "role": msg.role
            }
            for msg in messages
        ]

        track_event(
            event_category="thread",
            event_name="api.threads.messages.read",
            user_id=user_id,
            request=http_request,
            thread_id=thread_id,
            http_method="GET",
            http_path=f"/threads/{thread_id}/messages",
            status_code=200,
            success=True,
            metrics={
                "message_count": len(formatted_messages),
                "limit": limit,
                "human_count": sum(1 for m in messages if m.role == "Human"),
                "ai_count": sum(1 for m in messages if m.role == "AI"),
            },
            metadata={"is_assistant_thread": thread_id.startswith("assistant_")},
        )

        return {"messages": formatted_messages}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting thread messages: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener mensajes del thread"
        )


@app.post("/threads/batch-delete", dependencies=[Depends(verify_token_dependency)])
async def delete_threads_batch(
    request: BatchDeleteRequest,
    token_data: dict = Depends(verify_token_dependency)
):
    """
    Elimina múltiples threads del usuario autenticado.
    
    Args:
        thread_ids: Lista de IDs de threads a eliminar
        token_data: Datos del token JWT decodificado
        
    Returns:
        Información sobre los threads eliminados
    """
    try:
        user_id = token_data.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido"
            )

        thread_ids = request.thread_ids
        
        if not thread_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Se requiere al menos un thread_id"
            )

        thread_manager = ThreadManager(storage_base_dir=BASE_DIR)
        results = []
        errors = []

        for thread_id in thread_ids:
            try:
                result = thread_manager.delete_thread(thread_id, user_id, delete_storage=True)
                results.append(result)
            except ValueError as e:
                errors.append({"thread_id": thread_id, "error": str(e)})
            except Exception as e:
                errors.append({"thread_id": thread_id, "error": str(e)})

        return {
            "deleted": len(results),
            "failed": len(errors),
            "results": results,
            "errors": errors
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting threads batch: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al eliminar threads"
        )


@app.get("/threads/{thread_id}/stats", dependencies=[Depends(verify_token_dependency)], response_model=ThreadStatsResponse)
async def get_thread_stats(
    thread_id: str,
    token_data: dict = Depends(verify_token_dependency)
):
    """
    Obtiene estadísticas detalladas de un thread.
    
    Args:
        thread_id: ID del thread
        token_data: Datos del token JWT decodificado
        
    Returns:
        ThreadStatsResponse con estadísticas del thread
    """
    try:
        user_id = token_data.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido"
            )

        thread_manager = ThreadManager(storage_base_dir=BASE_DIR)
        stats = thread_manager.get_thread_stats(thread_id, user_id=user_id)
        
        if not stats:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread no encontrado"
            )

        return ThreadStatsResponse(
            thread_id=stats.thread_id,
            total_messages=stats.total_messages,
            ai_messages=stats.ai_messages,
            human_messages=stats.human_messages,
            first_message_at=stats.first_message_at.isoformat() + "Z" if stats.first_message_at else None,
            last_message_at=stats.last_message_at.isoformat() + "Z" if stats.last_message_at else None
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting thread stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener estadísticas del thread"
        )


@app.delete("/threads/{thread_id}", dependencies=[Depends(verify_token_dependency)], response_model=ThreadDeleteResponse)
async def delete_thread(
    thread_id: str,
    token_data: dict = Depends(verify_token_dependency)
):
    """
    Elimina un thread y todos sus mensajes del usuario autenticado.
    
    Args:
        thread_id: ID del thread a eliminar
        token_data: Datos del token JWT decodificado
        
    Returns:
        ThreadDeleteResponse con información de la eliminación
    """
    try:
        user_id = token_data.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido"
            )

        thread_manager = ThreadManager(storage_base_dir=BASE_DIR)
        result = thread_manager.delete_thread(thread_id, user_id, delete_storage=True)

        return ThreadDeleteResponse(
            thread_id=result["thread_id"],
            deleted_messages=result["deleted_messages"],
            storage_deleted=result["storage_deleted"],
            deleted_at=result["deleted_at"],
            message="Thread eliminado exitosamente"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting thread: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al eliminar thread"
        )


@app.delete("/admin/storage", dependencies=[Depends(verify_token_dependency)])
async def delete_all_storage(
    delete_database: bool = False,
    token_data: dict = Depends(verify_token_dependency)
):
    """
    Elimina todo el contenido del storage y opcionalmente todos los threads y mensajes de la base de datos.
    
    ⚠️ ADVERTENCIA: Esta operación es IRREVERSIBLE y eliminará TODOS los archivos y datos.
    
    Args:
        delete_database: Si True, también elimina todos los threads y mensajes de la base de datos
        token_data: Datos del token JWT decodificado
        
    Returns:
        Información sobre lo que se eliminó
    """
    try:
        user_id = token_data.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido"
            )

        deleted_files_count = 0
        deleted_dirs_count = 0
        deleted_threads_count = 0
        deleted_messages_count = 0
        errors = []

        # 1. Eliminar todo el contenido del directorio storage
        try:
            if BASE_DIR.exists():
                for user_dir in BASE_DIR.iterdir():
                    if user_dir.is_dir():
                        try:
                            # Contar archivos antes de eliminar
                            file_count = sum(1 for _ in user_dir.rglob('*') if _.is_file())
                            deleted_files_count += file_count
                            
                            # Eliminar directorio completo
                            shutil.rmtree(user_dir, ignore_errors=True)
                            deleted_dirs_count += 1
                        except Exception as e:
                            errors.append(f"Error eliminando directorio {user_dir.name}: {str(e)}")
            
            # Asegurar que el directorio base existe después de la limpieza
            BASE_DIR.mkdir(exist_ok=True)
        except Exception as e:
            errors.append(f"Error eliminando storage: {str(e)}")

        # 2. Limpiar diccionario de sesiones
        sessions.clear()

        # 3. Opcionalmente eliminar todos los threads y mensajes de la base de datos
        if delete_database:
            try:
                chat_repo = ChatThreadRepository()
                thread_manager = ThreadManager(storage_base_dir=BASE_DIR)
                
                # Obtener todos los threads de todos los usuarios
                try:
                    # Obtener todos los threads únicos desde la tabla de mensajes
                    # Usar una consulta que agrupe por thread_id para obtener threads únicos
                    res = chat_repo.client.table("chat_threads").select("thread_id,user_id").execute()
                    
                    # Crear un diccionario para evitar procesar threads duplicados
                    threads_dict = {}
                    for row in res.data:
                        thread_id = row.get("thread_id")
                        user_id = row.get("user_id")
                        if thread_id and thread_id not in threads_dict:
                            threads_dict[thread_id] = user_id
                    
                    # Eliminar cada thread
                    for thread_id, thread_user_id in threads_dict.items():
                        try:
                            # Eliminar mensajes del thread
                            deleted_msgs = chat_repo.delete_thread(thread_id)
                            deleted_messages_count += deleted_msgs
                            deleted_threads_count += 1
                        except Exception as e:
                            errors.append(f"Error eliminando thread {thread_id}: {str(e)}")
                except Exception as e:
                    errors.append(f"Error obteniendo threads de la base de datos: {str(e)}")
            except Exception as e:
                errors.append(f"Error eliminando datos de la base de datos: {str(e)}")

        return {
            "message": "Storage eliminado exitosamente",
            "storage": {
                "deleted_files": deleted_files_count,
                "deleted_directories": deleted_dirs_count,
            },
            "database": {
                "deleted_threads": deleted_threads_count if delete_database else None,
                "deleted_messages": deleted_messages_count if delete_database else None,
            },
            "sessions_cleared": True,
            "errors": errors if errors else None,
            "deleted_at": datetime.utcnow().isoformat() + "Z"
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting all storage: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al eliminar storage: {str(e)}"
        )
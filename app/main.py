from fastapi import FastAPI, HTTPException, status, Depends, Body
from pydantic import BaseModel, EmailStr, constr
from datetime import timedelta, datetime
from typing import Dict, Optional
from .config import get_settings
from .security import create_access_token
from .auth import verify_token_dependency
from .supervisor import get_supervisor_response, get_assistant_chat_response
from .db.user_management import UserRepository, UserCreate
from .db.thread_manager import ThreadManager, ThreadCreate
from .db.chat_management import ChatThreadRepository
from .db.custom_space_management import CustomSpaceRepository, CustomSpaceCreate, CustomSpaceUpdate
from .db.assistant_management import AssistantRepository, AssistantCreate, AssistantUpdate
from .assistant_prompt_generator import generate_system_prompt_from_files
from .security import verify_token

# pip install fastapi uvicorn python-multipart
import os
import uuid
import shutil
import asyncio
import time
import re
from pathlib import Path
from typing import List, Any
from unicodedata import normalize

from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict, deque

app = FastAPI()

BASE_DIR = Path("storage")
BASE_DIR.mkdir(exist_ok=True)

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


class GeneratePromptRequest(BaseModel):
    file_refs: List[str]  # ["session_uuid/inner_uuid/filename", ...]
    user_hint: str = ""


class AssistantChatRequest(BaseModel):
    query: str
    assistant_id: str


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
async def generate_token(request: TokenRequest) -> Dict[str, str]:
    """
    Generates a JWT token if all validations pass:
    - secret_value must match the environment variable
    - email must exist in the database
    - password must be correct for that email
    """
    try:
        # 1. Validar secret_value
        if request.secret_value != settings.secret_value:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid secret value"
            )

        # 2. Validar credenciales del usuario
        user_repo = UserRepository()
        user = user_repo.get_user_by_email(request.email)
        
        if not user or not user_repo.verify_password(request.password, user.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )

        # 3. Generar token con datos del usuario y secret_value
        access_token = create_access_token(
            data={
                "secret_value": settings.secret_value,
                "user_id": str(user.id),
                "email": user.email,
                "nombre": user.nombre,
                "apellido": user.apellido
            },
            expires_delta=timedelta(minutes=settings.access_token_expire_minutes)
        )
        
        return {"access_token": access_token}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error generating token: {str(e)}")
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
async def generate_security_code(request: SecurityCodeRequest) -> SecurityCodeResponse:
    """
    Genera un código de seguridad temporal para el registro de usuarios.
    El código expira después de 15 minutos.
    Requiere el secret_value correcto para generar el código.
    """
    try:
        # Validar secret_value
        if request.secret_value != settings.secret_value:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Secret value inválido"
            )

        # Generar código aleatorio de 6 caracteres alfanuméricos
        code = ''.join(uuid.uuid4().hex[:6].upper())
        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=15)
        
        # Almacenar el código con su tiempo de expiración
        security_codes[code] = {
            "created_at": now,
            "expires_at": expires_at
        }
        
        return SecurityCodeResponse(
            code=code,
            expires_in=900  # 15 minutos en segundos
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error generando security code: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error generando código de seguridad"
        )

@app.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(request: UserRegistrationRequest) -> Dict[str, str]:
    """
    Crea un nuevo usuario.
    
    Args:
        request: Datos del usuario a crear
        
    Returns:
        Dict con mensaje de éxito e ID del usuario
        
    Raises:
        HTTPException: Si el email ya está registrado o hay otros errores
    """
    try:
        # Crear repositorio
        user_repo = UserRepository()
        
        # Verificar si el email ya existe
        existing_user = user_repo.get_user_by_email(request.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El email ya está registrado"
            )
        
        # Crear el usuario
        user = user_repo.create_user(UserCreate(
            nombre=request.nombre,
            apellido=request.apellido,
            email=request.email,
            password=request.password
        ))
        
        return {
            "message": "Usuario creado exitosamente",
            "user_id": str(user.id)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error al crear usuario: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al crear el usuario"
        )


@app.post("/supervisor", dependencies=[Depends(verify_token_dependency)])
async def supervisor_endpoint(request: SupervisorRequest) -> Dict[str, str]:
    """
    Endpoint that calls the supervisor with the provided query in the request body.
    Requires:
    - query: The user's question or request
    - user_id: The session UUID that identifies the user
    - thread_id: The inner UUID that identifies the chat thread
    """
    try:
        if not request.query.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La consulta no puede estar vacía"
            )

        if not request.user_id or not request.thread_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Se requieren user_id y thread_id"
            )

        # Verificar que el thread pertenece al usuario correcto
        session_path = Path("storage") / request.user_id / request.thread_id
        if not session_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sesión no encontrada o expirada"
            )

        # Ejecutar get_supervisor_response en un thread separado para no bloquear
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, 
            get_supervisor_response, 
            request.query,
            request.user_id,
            request.thread_id
        )

        return {
            "response": response,
            "user_id": request.user_id,
            "thread_id": request.thread_id
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error en supervisor_endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al procesar la solicitud: {str(e)}"
        )


@app.post("/assistant-chat", dependencies=[Depends(verify_token_dependency)])
async def assistant_chat_endpoint(
    request: AssistantChatRequest,
    payload: dict = Depends(verify_token_dependency)
) -> Dict[str, str]:
    """
    Chat con un asistente personalizado. Usa el system prompt del asistente,
    sin herramientas. Historial por asistente.
    """
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")

    assistant_repo = AssistantRepository()
    assistant = assistant_repo.get(request.assistant_id)
    if not assistant or assistant.user_id != user_id:
        raise HTTPException(status_code=404, detail="Asistente no encontrado")

    if not request.query.strip():
        raise HTTPException(status_code=400, detail="La consulta no puede estar vacía")

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            get_assistant_chat_response,
            request.query,
            user_id,
            str(request.assistant_id),
            assistant.system_prompt or ""
        )
        return {"response": response}
    except Exception as e:
        print(f"Error en assistant_chat_endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
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
    repo = AssistantRepository()
    try:
        a = repo.create(AssistantCreate(user_id=user_id, name=request.name, description=request.description, system_prompt=request.system_prompt))
        return AssistantResponse(id=str(a.id), user_id=a.user_id, name=a.name, description=a.description, system_prompt=a.system_prompt, created_at=a.created_at.isoformat(), updated_at=a.updated_at.isoformat())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/assistants", dependencies=[Depends(verify_token_dependency)])
async def list_assistants(
    page: int = 1,
    limit: int = 10,
    payload: dict = Depends(verify_token_dependency)
):
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    if page < 1:
        page = 1
    if limit < 1 or limit > 50:
        limit = 10
    
    offset = (page - 1) * limit
    repo = AssistantRepository()
    
    try:
        assistants = repo.get_user_assistants(user_id, limit=limit, offset=offset)
        total = repo.count_user_assistants(user_id)
        total_pages = (total + limit - 1) // limit if total > 0 else 1
        
        return {
            "items": [AssistantResponse(id=str(a.id), user_id=a.user_id, name=a.name, description=a.description, system_prompt=a.system_prompt, created_at=a.created_at.isoformat(), updated_at=a.updated_at.isoformat()) for a in assistants],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
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
    if not a or a.user_id != user_id:
        raise HTTPException(status_code=404, detail="Asistente no encontrado")
    return AssistantResponse(id=str(a.id), user_id=a.user_id, name=a.name, description=a.description, system_prompt=a.system_prompt, created_at=a.created_at.isoformat(), updated_at=a.updated_at.isoformat())


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
    return AssistantResponse(id=str(a.id), user_id=a.user_id, name=a.name, description=a.description, system_prompt=a.system_prompt, created_at=a.created_at.isoformat(), updated_at=a.updated_at.isoformat())


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
async def start_session(token_data: dict = Depends(verify_token_dependency)):
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
    session_uuid: str, 
    inner_uuid: str, 
    files: List[UploadFile] = File(...),
    token_data: dict = Depends(verify_token_dependency)
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
    for file in files:
        # Sanitizar el nombre del archivo
        safe_filename = sanitize_filename(file.filename)
        file_path = folder / safe_filename
        
        # Si el archivo ya existe, agregar un sufijo único
        if file_path.exists():
            base, ext = os.path.splitext(safe_filename)
            safe_filename = f"{base}_{str(uuid.uuid4())[:8]}{ext}"
            file_path = folder / safe_filename
            
        with open(file_path, "wb") as f:
            f.write(await file.read())
        urls.append(f"/files/{session_uuid}/{inner_uuid}/{safe_filename}")

    meta["last"] = time.time()
    return {"uploaded": urls}


@app.get("/files/{session_uuid}/{inner_uuid}/{filename}", dependencies=[Depends(verify_token_dependency)])
async def download_file(
    session_uuid: str, 
    inner_uuid: str, 
    filename: str,
    token_data: dict = Depends(verify_token_dependency)
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
    return FileResponse(file_path, filename=filename)


@app.delete("/files/{session_uuid}/{inner_uuid}/{filename}", dependencies=[Depends(verify_token_dependency)])
async def delete_file(
    session_uuid: str, 
    inner_uuid: str, 
    filename: str,
    token_data: dict = Depends(verify_token_dependency)
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
        return {"message": f"File {filename} deleted successfully"}
    except Exception as e:
        return JSONResponse(
            {"error": f"Failed to delete file: {str(e)}"}, 
            status_code=500
        )


@app.get("/threads", dependencies=[Depends(verify_token_dependency)], response_model=ThreadListResponse)
async def list_threads(
    limit: int = 100,
    offset: int = 0,
    token_data: dict = Depends(verify_token_dependency)
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
    request: Optional[ThreadCreateRequest] = Body(default=None),
    token_data: dict = Depends(verify_token_dependency)
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
                thread_id=request.thread_id if request else None
            ),
            create_storage_dir=True
        )
        
        # Actualizar diccionario de sesiones
        if user_id not in sessions:
            sessions[user_id] = {}
        sessions[user_id] = {"last": time.time(), "inner": thread_data["thread_id"]}

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
    thread_id: str,
    limit: int = 200,
    token_data: dict = Depends(verify_token_dependency)
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
        messages = chat_repo.get_thread_messages(thread_id, limit=limit, ascending=True)

        # Verificar que el thread pertenece al usuario
        if messages and messages[0].user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para acceder a este thread"
            )
        # Para threads de asistente (assistant_xxx) sin mensajes, verificar que el asistente pertenece al usuario
        if not messages and thread_id.startswith("assistant_"):
            assistant_id = thread_id.replace("assistant_", "", 1)
            assistant_repo = AssistantRepository()
            assistant = assistant_repo.get(assistant_id)
            if not assistant or assistant.user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No tienes permiso para acceder a este asistente"
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
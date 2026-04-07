from fastapi import HTTPException, status, Header, Depends
from typing import Optional
from .config import get_settings
from .security import verify_token

settings = get_settings()


async def verify_token_dependency(token: Optional[str] = Header(None, alias="Token")):
    """
    FastAPI dependency that verifies the JWT token from the Token header.
    Usage:
        @app.get("/your-endpoint")
        async def your_endpoint(payload = Depends(verify_token_dependency)):
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token header is required"
        )

    try:
        payload = verify_token(token)
        if not payload or payload.get("secret_value") != settings.secret_value:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
        return payload
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format"
        )


async def require_admin_dependency(payload: dict = Depends(verify_token_dependency)) -> dict:
    """Solo cuentas con user_type 'admin' en el JWT."""
    t = (payload.get("user_type") or "user").strip().lower()
    if t != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere cuenta de administrador",
        )
    return payload

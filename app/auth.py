from fastapi import HTTPException, status, Header
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
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format"
        )
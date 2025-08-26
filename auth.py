# auth.py
import os
from typing import Dict, Any
from dotenv import load_dotenv

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

from db import supabase

load_dotenv()
JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")
JWT_ALGORITHM = "HS256"

security = HTTPBearer()  # parses Authorization: Bearer <token>

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """
    Dependency to decode and validate Supabase JWT.
    Returns a dict with 'auth_id' (the Supabase sub) and full token claims.
    """
    if not JWT_SECRET:
        raise HTTPException(status_code=500, detail="JWT secret not configured")

    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        auth_id = payload.get("sub")
        if auth_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token (no sub)")
        return {"auth_id": auth_id, "claims": payload}
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


def get_user_from_token(current_user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Given current_user returned by get_current_user, map auth_id -> local users row.
    Returns the user row (dict) from the 'users' table (e.g. contains local integer id and auth_id).
    Raises 401 if mapping not found.
    """
    auth_id = current_user.get("auth_id")
    if not auth_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing auth_id in token claims")

    res = supabase.table("users").select("id, auth_id, email").eq("auth_id", auth_id).single().execute()

    # Supabase Python client returns a response object with .data and possibly .error
    if getattr(res, "data", None):
        return res.data
    # if there is an error or no data found
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not registered in users table")

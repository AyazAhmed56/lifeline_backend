# auth.py
import os
from typing import Dict, Any
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from db import supabase

load_dotenv()
JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")  # apne .env me define kare
JWT_ALGORITHM = "HS256"

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
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
    auth_id = current_user.get("auth_id")
    if not auth_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing auth_id in token claims")

    res = supabase.table("users").select("*").eq("auth_id", auth_id).single().execute()
    if getattr(res, "data", None):
        return res.data
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not registered in users table")

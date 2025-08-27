# users.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from db import supabase
from passlib.context import CryptContext
from auth import get_current_user, get_user_from_token
from datetime import datetime, timedelta
from jose import jwt
import uuid
import os

JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")
JWT_ALGORITHM = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
router = APIRouter(prefix="/users", tags=["Users"])

# ---------------- Schemas ----------------
class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    age: Optional[int] = None
    blood_group: Optional[str] = None
    phone_no: Optional[str] = None

class UserUpdate(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None
    blood_group: Optional[str] = None
    phone_no: Optional[str] = None

class LoginSchema(BaseModel):
    email: str
    password: str

# ---------------- JWT Helper ----------------
def create_jwt(auth_id: str):
    payload = {
        "sub": auth_id,
        "exp": datetime.utcnow() + timedelta(days=1)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

# ---------------- APIs ----------------

# Register
@router.post("/")
def create_user(payload: UserCreate):
    hashed_password = pwd_context.hash(payload.password)
    data = payload.model_dump()
    data["password"] = hashed_password
    data["auth_id"] = str(uuid.uuid4())  # generate auth_id
    try:
        res = supabase.table("users").insert(data).execute()
        return {"status": "success", "data": res.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Login
@router.post("/login")
def login_user(payload: LoginSchema):
    res = supabase.table("users").select("*").eq("email", payload.email).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="User not found")

    user = res.data[0]
    if not pwd_context.verify(payload.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = create_jwt(user["auth_id"])
    return {"access_token": token, "user": user}

# Get current user
@router.get("/me")
def get_me(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    return {"status": "success", "data": user}

# Update current user
@router.patch("/me")
def update_me(payload: UserUpdate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    res = supabase.table("users").update(update_data).eq("id", user["id"]).execute()
    return {"status": "success", "data": res.data[0] if res.data else None}

# Delete current user
@router.delete("/me")
def delete_me(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("users").delete().eq("id", user["id"]).execute()
    return {"status": "success", "deleted": True}

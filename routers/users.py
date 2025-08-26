from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from db import supabase
from auth import get_current_user, get_user_from_token

router = APIRouter(prefix="/users", tags=["Users"])

# ✅ Create schema
class UserCreate(BaseModel):
    name: str
    email: str
    age: Optional[int] = None
    blood_group: Optional[str] = None
    phone_no: Optional[str] = None   # NEW FIELD

# ✅ Update schema
class UserUpdate(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None
    blood_group: Optional[str] = None
    phone_no: Optional[str] = None   # NEW FIELD

# ---------------- APIs ----------------

@router.post("/")
def create_user(payload: UserCreate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    data = payload.model_dump()
    data["auth_id"] = user["auth_id"]  # ensure linked to Supabase auth
    res = supabase.table("users").insert(data).execute()
    return {"status": "success", "data": res.data[0]}

@router.get("/")
def list_users(current_user=Depends(get_current_user)):
    # ⚠️ Optional: in future, restrict to admin role
    res = supabase.table("users").select("*").execute()
    return {"status": "success", "data": res.data}

@router.get("/me")
def get_me(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    return {"status": "success", "data": user}

@router.patch("/me")
def update_me(payload: UserUpdate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    res = supabase.table("users").update(update_data).eq("id", user["id"]).execute()
    return {"status": "success", "data": res.data[0] if res.data else None}

@router.delete("/me")
def delete_me(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("users").delete().eq("id", user["id"]).execute()
    return {"status": "success", "deleted": True}

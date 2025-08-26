from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from db import supabase
from auth import get_current_user, get_user_from_token

router = APIRouter(prefix="/emergencies", tags=["Emergencies"])

class EmergencyCreate(BaseModel):
    location: Optional[str] = None
    critical_info: Optional[str] = None
    status: Optional[str] = "active"

class EmergencyUpdate(BaseModel):
    location: Optional[str] = None
    critical_info: Optional[str] = None
    status: Optional[str] = None

@router.post("/")
def create(payload: EmergencyCreate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    data = payload.model_dump()
    data["user_id"] = user["id"]
    res = supabase.table("emergencies").insert(data).execute()
    return {"status": "success", "data": res.data[0]}

@router.get("/")
def list_my(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("emergencies").select("*").eq("user_id", user["id"]).order("triggered_at", desc=True).execute()
    return {"status": "success", "data": res.data}

@router.get("/{emergency_id}")
def get_one(emergency_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("emergencies").select("*").eq("id", emergency_id).eq("user_id", user["id"]).single().execute()
    if not res.data:
        raise HTTPException(404, "Emergency not found")
    return {"status": "success", "data": res.data}

@router.patch("/{emergency_id}")
def update(emergency_id: int, payload: EmergencyUpdate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    res = supabase.table("emergencies").update(update_data).eq("id", emergency_id).eq("user_id", user["id"]).execute()
    if not res.data:
        raise HTTPException(404, "Emergency not found or not yours")
    return {"status": "success", "data": res.data[0]}

@router.delete("/{emergency_id}")
def delete(emergency_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("emergencies").delete().eq("id", emergency_id).eq("user_id", user["id"]).execute()
    return {"status": "success", "deleted": True}

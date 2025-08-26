from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from db import supabase
from auth import get_current_user, get_user_from_token

router = APIRouter(prefix="/health-records", tags=["Health Records"])

class RecordCreate(BaseModel):
    record_type: str
    description: Optional[str] = None
    date: Optional[str] = None

class RecordUpdate(BaseModel):
    record_type: Optional[str] = None
    description: Optional[str] = None
    date: Optional[str] = None

@router.post("/")
def create(payload: RecordCreate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    data = payload.model_dump()
    data["user_id"] = user["id"]
    res = supabase.table("health_records").insert(data).execute()
    return {"status": "success", "data": res.data[0]}

@router.get("/")
def list_my(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("health_records").select("*").eq("user_id", user["id"]).execute()
    return {"status": "success", "data": res.data}

@router.get("/{record_id}")
def get_one(record_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("health_records").select("*").eq("id", record_id).eq("user_id", user["id"]).single().execute()
    if not res.data:
        raise HTTPException(404, "Record not found")
    return {"status": "success", "data": res.data}

@router.patch("/{record_id}")
def update(record_id: int, payload: RecordUpdate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    res = supabase.table("health_records").update(update_data).eq("id", record_id).eq("user_id", user["id"]).execute()
    if not res.data:
        raise HTTPException(404, "Record not found or not yours")
    return {"status": "success", "data": res.data[0]}

@router.delete("/{record_id}")
def delete(record_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("health_records").delete().eq("id", record_id).eq("user_id", user["id"]).execute()
    return {"status": "success", "deleted": True}

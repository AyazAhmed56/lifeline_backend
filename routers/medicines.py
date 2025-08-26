from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from db import supabase
from auth import get_current_user, get_user_from_token

router = APIRouter(prefix="/medicines", tags=["Medicines"])

class MedicineCreate(BaseModel):
    name: str
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class MedicineUpdate(BaseModel):
    name: Optional[str] = None
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

@router.post("/")
def create(payload: MedicineCreate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    data = payload.model_dump()
    data["user_id"] = user["id"]
    res = supabase.table("medicines").insert(data).execute()
    return {"status": "success", "data": res.data[0]}

@router.get("/")
def list_my(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("medicines").select("*").eq("user_id", user["id"]).execute()
    return {"status": "success", "data": res.data}

@router.get("/{medicine_id}")
def get_one(medicine_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("medicines").select("*").eq("id", medicine_id).eq("user_id", user["id"]).single().execute()
    if not res.data:
        raise HTTPException(404, "Medicine not found")
    return {"status": "success", "data": res.data}

@router.patch("/{medicine_id}")
def update(medicine_id: int, payload: MedicineUpdate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    res = supabase.table("medicines").update(update_data).eq("id", medicine_id).eq("user_id", user["id"]).execute()
    if not res.data:
        raise HTTPException(404, "Medicine not found or not yours")
    return {"status": "success", "data": res.data[0]}

@router.delete("/{medicine_id}")
def delete(medicine_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("medicines").delete().eq("id", medicine_id).eq("user_id", user["id"]).execute()
    return {"status": "success", "deleted": True}

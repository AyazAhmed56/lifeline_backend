from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from db import supabase
from auth import get_current_user, get_user_from_token

router = APIRouter(prefix="/blood-requests", tags=["Blood Requests"])

class BloodRequestCreate(BaseModel):
    blood_group: str
    hospital_name: str
    contact: str
    status: Optional[str] = "open"

class BloodRequestUpdate(BaseModel):
    blood_group: Optional[str] = None
    hospital_name: Optional[str] = None
    contact: Optional[str] = None
    status: Optional[str] = None

@router.post("/")
def create(payload: BloodRequestCreate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    data = payload.model_dump()
    data["user_id"] = user["id"]
    res = supabase.table("blood_requests").insert(data).execute()
    return {"status": "success", "data": res.data[0]}

@router.get("/")
def list_my(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("blood_requests").select("*").eq("user_id", user["id"]).execute()
    return {"status": "success", "data": res.data}

@router.get("/{request_id}")
def get_one(request_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("blood_requests").select("*").eq("id", request_id).eq("user_id", user["id"]).single().execute()
    if not res.data:
        raise HTTPException(404, "Blood request not found")
    return {"status": "success", "data": res.data}

@router.patch("/{request_id}")
def update(request_id: int, payload: BloodRequestUpdate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    res = supabase.table("blood_requests").update(update_data).eq("id", request_id).eq("user_id", user["id"]).execute()
    if not res.data:
        raise HTTPException(404, "Blood request not found or not yours")
    return {"status": "success", "data": res.data[0]}

@router.delete("/{request_id}")
def delete(request_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("blood_requests").delete().eq("id", request_id).eq("user_id", user["id"]).execute()
    return {"status": "success", "deleted": True}

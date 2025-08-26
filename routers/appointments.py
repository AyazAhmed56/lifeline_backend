from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from db import supabase
from auth import get_current_user, get_user_from_token

router = APIRouter(prefix="/appointments", tags=["Appointments"])

class AppointmentCreate(BaseModel):
    doctor_name: str
    hospital_name: str
    appointment_date: str
    status: Optional[str] = "pending"

class AppointmentUpdate(BaseModel):
    doctor_name: Optional[str] = None
    hospital_name: Optional[str] = None
    appointment_date: Optional[str] = None
    status: Optional[str] = None

@router.post("/")
def create(payload: AppointmentCreate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    data = payload.model_dump()
    data["user_id"] = user["id"]
    res = supabase.table("appointments").insert(data).execute()
    return {"status": "success", "data": res.data[0]}

@router.get("/")
def list_my(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("appointments").select("*").eq("user_id", user["id"]).execute()
    return {"status": "success", "data": res.data}

@router.get("/{appointment_id}")
def get_one(appointment_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("appointments").select("*").eq("id", appointment_id).eq("user_id", user["id"]).single().execute()
    if not res.data:
        raise HTTPException(404, "Appointment not found")
    return {"status": "success", "data": res.data}

@router.patch("/{appointment_id}")
def update(appointment_id: int, payload: AppointmentUpdate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    res = supabase.table("appointments").update(update_data).eq("id", appointment_id).eq("user_id", user["id"]).execute()
    if not res.data:
        raise HTTPException(404, "Appointment not found or not yours")
    return {"status": "success", "data": res.data[0]}

@router.delete("/{appointment_id}")
def delete(appointment_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("appointments").delete().eq("id", appointment_id).eq("user_id", user["id"]).execute()
    return {"status": "success", "deleted": True}

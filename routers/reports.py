from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional
from db import supabase
from auth import get_current_user, get_user_from_token
import pytesseract
from PIL import Image
import io

router = APIRouter(prefix="/reports", tags=["Reports"])

class ReportCreate(BaseModel):
    title: str
    file_url: Optional[str] = None
    description: Optional[str] = None

class ReportUpdate(BaseModel):
    title: Optional[str] = None
    file_url: Optional[str] = None
    description: Optional[str] = None

@router.post("/")
def create(payload: ReportCreate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    data = payload.model_dump()
    data["user_id"] = user["id"]
    res = supabase.table("reports").insert(data).execute()
    return {"status": "success", "data": res.data[0]}

@router.get("/")
def list_my(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("reports").select("*").eq("user_id", user["id"]).execute()
    return {"status": "success", "data": res.data}

@router.get("/{report_id}")
def get_one(report_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("reports").select("*").eq("id", report_id).eq("user_id", user["id"]).single().execute()
    if not res.data:
        raise HTTPException(404, "Report not found")
    return {"status": "success", "data": res.data}

@router.patch("/{report_id}")
def update(report_id: int, payload: ReportUpdate, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    res = supabase.table("reports").update(update_data).eq("id", report_id).eq("user_id", user["id"]).execute()
    if not res.data:
        raise HTTPException(404, "Report not found or not yours")
    return {"status": "success", "data": res.data[0]}

@router.delete("/{report_id}")
def delete(report_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    res = supabase.table("reports").delete().eq("id", report_id).eq("user_id", user["id"]).execute()
    return {"status": "success", "deleted": True}

@router.post("/upload")
def upload_report(file: UploadFile = File(...), current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)

    # 1. File Supabase Storage me upload karo
    file_bytes = file.file.read()
    path = f"{user['id']}/{file.filename}"  # user-specific folder
    res = supabase.storage.from_("reports").upload(path, file_bytes)

    if "error" in str(res):
        raise HTTPException(500, "Upload failed")

    file_url = supabase.storage.from_("reports").get_public_url(path)

    # 2. OCR lagao (sirf images par, PDF ke liye alag logic add karenge)
    text_content = ""
    try:
        image = Image.open(io.BytesIO(file_bytes))
        text_content = pytesseract.image_to_string(image)
    except Exception:
        text_content = "[OCR not supported on this file type]"

    # 3. DB me save karo
    report_data = {
        "user_id": user["id"],
        "title": file.filename,
        "file_url": file_url,
        "text_content": text_content
    }
    saved = supabase.table("reports").insert(report_data).execute()

    return {"status": "success", "data": saved.data[0]}
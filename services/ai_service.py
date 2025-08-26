from fastapi import APIRouter, Depends, HTTPException
from auth import get_current_user, get_user_from_token
from db import supabase
import os
import google.generativeai as genai

router = APIRouter(prefix="/ai", tags=["AI Service"])

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

@router.post("/summarize-report/{report_id}")
def summarize_report(report_id: int, current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)

    # Report fetch karo
    res = supabase.table("reports").select("id, text_content").eq("id", report_id).eq("user_id", user["id"]).single().execute()
    if not res.data:
        raise HTTPException(404, "Report not found")

    text_content = res.data["text_content"]
    if not text_content:
        raise HTTPException(400, "No OCR text found in report")

    # Gemini se summary generate karna
    model = genai.GenerativeModel("gemini-pro")
    prompt = f"Summarize this medical report in simple terms for the patient:\n\n{text_content}"
    response = model.generate_content(prompt)

    summary = response.text

    # DB me save karo
    supabase.table("reports").update({"ai_summary": summary}).eq("id", report_id).execute()

    return {"status": "success", "summary": summary}

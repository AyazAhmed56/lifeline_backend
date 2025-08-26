from fastapi import APIRouter, Depends, HTTPException
from auth import get_current_user, get_user_from_token
from typing import Dict, Any
from db import supabase
import google.generativeai as genai
import os
from langchain.agents import initialize_agent, Tool
from langchain_google_genai import ChatGoogleGenerativeAI

# LangChain pieces
from langchain.agents import initialize_agent, Tool, AgentType
from langchain.memory import ConversationBufferMemory
from langchain_google_genai import ChatGoogleGenerativeAI

router = APIRouter(prefix="/agent", tags=["AI Agent"])

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# ---- Simple per-user in-memory memory store ----
# WARNING: This is ephemeral (process memory). Use Redis or DB for persistence in prod.
user_memories: Dict[int, ConversationBufferMemory] = {}

def get_user_memory(user_id: int) -> ConversationBufferMemory:
    mem = user_memories.get(user_id)
    if mem is None:
        # conversation_memory_key defaults to "chat_history"
        mem = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
        user_memories[user_id] = mem
    return mem

# ---- Tools (these are simple wrappers that return stringified DB results) ----
def fetch_reports_tool(user_id: int) -> str:
    res = supabase.table("reports").select("id, report_type, ai_summary, text_content, uploaded_at").eq("user_id", user_id).order("uploaded_at", desc=True).execute()
    return str(res.data or [])

def fetch_medicines_tool(user_id: int) -> str:
    res = supabase.table("medicines").select("id, name, dosage, frequency, start_date, end_date").eq("user_id", user_id).order("id", desc=True).execute()
    return str(res.data or [])

def fetch_appointments_tool(user_id: int) -> str:
    res = supabase.table("appointments").select("id, doctor_name, hospital_name, appointment_date, status").eq("user_id", user_id).order("appointment_date", desc=True).execute()
    return str(res.data or [])

# Wrap tools for LangChain: each tool expects a callable that accepts a single string argument.
def reports_tool_wrapper(_: str, user_id: int) -> str:
    return fetch_reports_tool(user_id)

def medicines_tool_wrapper(_: str, user_id: int) -> str:
    return fetch_medicines_tool(user_id)

def appointments_tool_wrapper(_: str, user_id: int) -> str:
    return fetch_appointments_tool(user_id)


# We will create Tool objects at runtime per-request because each tool needs the user_id bound.
def make_tools_for_user(user_id: int):
    return [
        Tool(
            name="fetch_reports",
            func=lambda q: fetch_reports_tool(user_id),
            description="Returns user's recent medical reports, OCR text and AI summaries."
        ),
        Tool(
            name="fetch_medicines",
            func=lambda q: fetch_medicines_tool(user_id),
            description="Returns user's active and past medicines with dosage/frequency."
        ),
        Tool(
            name="fetch_appointments",
            func=lambda q: fetch_appointments_tool(user_id),
            description="Returns user's appointments, doctor names and dates."
        ),
    ]

@router.post("/timeline")
def analyze_timeline(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)

    # Fetch reports, medicines, appointments
    reports = supabase.table("reports").select("report_type, text_content, ai_summary").eq("user_id", user["id"]).execute().data
    medicines = supabase.table("medicines").select("name, dosage, frequency").eq("user_id", user["id"]).execute().data
    appointments = supabase.table("appointments").select("doctor, date, notes").eq("user_id", user["id"]).execute().data

    # Combine data into one timeline text
    timeline = f"Reports: {reports}\n\nMedicines: {medicines}\n\nAppointments: {appointments}"

    # Ask Gemini for health analysis
    model = genai.GenerativeModel("gemini-pro")
    prompt = f"""Analyze this patient's complete medical timeline and give insights:
    {timeline}
    """
    response = model.generate_content(prompt)

    return {"status": "success", "analysis": response.text}

@router.post("/chat")
def agent_chat(query: str, current_user=Depends(get_current_user)):
    """
    Memory-enabled conversational agent.
    query: user's question (string)
    """
    user_auth = get_user_from_token(current_user)
    user_id = user_auth["id"]

    # Prepare LLM
    llm = ChatGoogleGenerativeAI(model="gemini-pro", temperature=0.0)

    # Prepare per-user memory
    memory = get_user_memory(user_id)

    # Create tools bound to this user
    tools = make_tools_for_user(user_id)

    # Initialize agent: use conversational agent with memory
    agent = initialize_agent(
        tools,
        llm,
        agent=AgentType.CHAT_CONVERSATIONAL,
        memory=memory,
        verbose=False,
    )

    # Run agent
    try:
        result = agent.run(query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")

    # Optionally return memory snapshot size for debugging
    return {"status": "success", "answer": result, "memory_items": len(memory.buffer.splitlines()) if getattr(memory, "buffer", None) else None}

# ---- Endpoint: clear conversation memory (optional) ----
@router.post("/chat/clear")
def clear_memory(current_user=Depends(get_current_user)):
    user = get_user_from_token(current_user)
    uid = user["id"]
    if uid in user_memories:
        del user_memories[uid]
    return {"status": "success", "cleared": True}

# ---- Doctor suggestion endpoint ----
@router.post("/suggest-doctor")
def suggest_doctor(symptoms: str, current_user=Depends(get_current_user)):
    """
    Takes free-text symptoms, fetches recent reports/medicines, and asks Gemini to suggest:
     - probable specialist(s)
     - urgency level (low/medium/high)
     - tests to run
     - short next steps (what patient should do)
    """
    user = get_user_from_token(current_user)
    user_id = user["id"]

    # fetch patient's context (limit to relevant fields)
    reports_summary = fetch_reports_tool(user_id)
    medicines = fetch_medicines_tool(user_id)

    # Build prompt for Gemini
    prompt = f"""
You are a helpful medical assistant (non-diagnostic; provide suggestions only).
User symptoms: {symptoms}

User recent reports (most relevant): {reports_summary}

User current/past medicines: {medicines}

Tasks:
1) Suggest 1-3 medical specialties a user should consult (e.g., "Cardiologist", "Endocrinologist").
2) Provide an urgency level: low / medium / high, and one sentence why.
3) Recommend 3 specific tests (if any) that are commonly ordered to investigate these symptoms.
4) Provide 3 short next steps the user can take (e.g., see a doctor, stop medication, bring tests).

Answer in JSON ONLY with keys: "specialties" (list), "urgency" (string), "tests" (list), "next_steps" (list), "explanation" (short string).
"""

    # call Gemini model
    try:
        model = genai.GenerativeModel("gemini-pro")
        response = model.generate_content(prompt)
        # response.text may be plain text; attempt to parse JSON if LLM returns JSON
        text = response.text.strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini request failed: {str(e)}")

    # Try to parse JSON from response; if parsing fails, return raw text under 'raw'
    import json
    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        # fallback: wrap the text
        parsed = {"raw": text}

    return {"status": "success", "suggestion": parsed}
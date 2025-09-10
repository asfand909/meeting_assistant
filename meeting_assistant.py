import os
import asyncio
from datetime import datetime, timedelta
import json
import logging
import httpx
from msal import ConfidentialClientApplication
from dateutil import parser as dp
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv
import uuid

# Agent library imports
from agents import Agent, function_tool, handoff, Runner, OpenAIChatCompletionsModel, AsyncOpenAI
from typing_extensions import TypedDict, NotRequired

# -------------------------
# Configuration
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Google/Gemini API Configuration
api = os.getenv("GOOGLE_API_KEY")
if not api:
    raise ValueError("GOOGLE_API_KEY environment variable is required")

# Disable OpenAI tracing to avoid the 401 errors
client = AsyncOpenAI(
    api_key=api,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

model = OpenAIChatCompletionsModel(
    model="gemini-2.0-flash-exp",
    openai_client=client
)

# Microsoft Graph Configuration
TENANT = os.environ.get("AZURE_TENANT_ID")
CLIENT_ID = os.environ.get("AZURE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET")
ORGANIZER = os.environ.get("ORGANIZER_UPN")
DEFAULT_TZ = os.environ.get("DEFAULT_TZ", "Arabian Standard Time")
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
WORKING_START_HOUR = 9
WORKING_END_HOUR = 18

# Check required variables
_missing = [name for name, val in {
    "AZURE_TENANT_ID": TENANT,
    "AZURE_CLIENT_ID": CLIENT_ID,
    "AZURE_CLIENT_SECRET": CLIENT_SECRET,
    "ORGANIZER_UPN": ORGANIZER
}.items() if not val]

if _missing:
    logger.warning(
        f"Missing Microsoft Graph environment variables: {', '.join(_missing)}")
    logger.warning(
        "Calendar integration will not work without these variables")

# MSAL client (only create if we have the required variables)
SCOPES = ["https://graph.microsoft.com/.default"]
_msal_app = None
if not _missing:
    try:
        _msal_app = ConfidentialClientApplication(
            CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{TENANT}",
            client_credential=CLIENT_SECRET,
        )
    except Exception as e:
        logger.error(f"Failed to create MSAL client: {e}")

# -------------------------
# Microsoft Graph Service Helpers
# -------------------------

def _get_access_token() -> str:
    """Acquire access token using client credentials flow."""
    if not _msal_app:
        raise RuntimeError(
            "MSAL client not initialized. Check Azure configuration.")
    try:
        token_response = _msal_app.acquire_token_for_client(scopes=SCOPES)
        if "access_token" not in token_response:
            error = token_response.get(
                "error_description", token_response.get("error", token_response))
            raise RuntimeError(f"MSAL token acquisition error: {error}")
        return token_response["access_token"]
    except Exception as e:
        logger.error(f"Error acquiring access token: {e}")
        raise

def _build_headers(token: str, tz: str = None) -> Dict[str, str]:
    """Build headers for Microsoft Graph API requests."""
    hdrs = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    if tz:
        hdrs["Prefer"] = f'outlook.timezone="{tz}"'
    return hdrs

def _iso_day_window(date_str: str, tz: str, start_hour: int = WORKING_START_HOUR,
                    end_hour: int = WORKING_END_HOUR) -> tuple[str, str]:
    """Convert date to ISO datetime range for working day."""
    try:
        if 'T' in date_str:
            date_obj = dp.isoparse(date_str).date()
        else:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_dt = datetime.combine(
            date_obj, datetime.min.time().replace(hour=start_hour))
        end_dt = datetime.combine(
            date_obj, datetime.min.time().replace(hour=end_hour))
        return start_dt.isoformat(), end_dt.isoformat()
    except Exception as e:
        logger.error(f"Error parsing date {date_str}: {e}")
        raise ValueError(f"Invalid date format: {date_str}")

def _free_slots_from_schedule(payload: Dict, start_iso: str, end_iso: str,
                              required_min: int) -> List[Dict]:
    """Extract free time slots from schedule response."""
    schedule_items = []
    if payload.get("value") and len(payload["value"]) > 0:
        schedule_items = payload["value"][0].get("scheduleItems", [])

    busy_periods = []
    for item in schedule_items:
        status = (item.get("status") or "").lower()
        if status in ("busy", "oof", "tentative"):
            try:
                busy_start = dp.isoparse(item["start"]["dateTime"])
                busy_end = dp.isoparse(item["end"]["dateTime"])
                busy_periods.append((busy_start, busy_end))
            except Exception as e:
                logger.warning(f"Error parsing busy period: {e}")
                continue

    busy_periods.sort(key=lambda x: x[0])

    current_time = dp.isoparse(start_iso)
    day_end = dp.isoparse(end_iso)
    free_slots = []

    for busy_start, busy_end in busy_periods:
        if busy_start > current_time:
            gap_duration = (busy_start - current_time).total_seconds() / 60
            if gap_duration >= required_min:
                slot_end = current_time + timedelta(minutes=required_min)
                free_slots.append({
                    "start": current_time.isoformat(),
                    "end": slot_end.isoformat(),
                    "duration_min": required_min,
                    "start_time": current_time.strftime("%H:%M"),
                    "end_time": slot_end.strftime("%H:%M")
                })
        current_time = max(current_time, busy_end)

    if current_time < day_end:
        remaining_minutes = (day_end - current_time).total_seconds() / 60
        if remaining_minutes >= required_min:
            slot_end = current_time + timedelta(minutes=required_min)
            free_slots.append({
                "start": current_time.isoformat(),
                "end": slot_end.isoformat(),
                "duration_min": required_min,
                "start_time": current_time.strftime("%H:%M"),
                "end_time": slot_end.strftime("%H:%M")
            })
    return free_slots

async def get_schedule(start_iso: str, end_iso: str, interval_min: int = 30, tz: str = DEFAULT_TZ) -> Dict:
    """Get calendar schedule from Microsoft Graph."""
    try:
        token = _get_access_token()
        body = {
            "schedules": [ORGANIZER],
            "startTime": {"dateTime": start_iso, "timeZone": tz},
            "endTime": {"dateTime": end_iso, "timeZone": tz},
            "availabilityViewInterval": max(5, min(60, interval_min)),
        }
        async with httpx.AsyncClient(timeout=30) as http_client:
            resp = await http_client.post(
                f"{GRAPH_API_BASE}/users/{ORGANIZER}/calendar/getSchedule",
                headers=_build_headers(token, tz),
                json=body
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Error getting schedule: {e}")
        return {"value": [{"scheduleItems": []}]}

async def create_calendar_event(subject: str, start_iso: str, end_iso: str,
                                attendees: List[str], tz: str = DEFAULT_TZ) -> Dict:
    """Create calendar event with Teams meeting."""
    try:
        token = _get_access_token()
        transaction_id = str(uuid.uuid4())
        body = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": f"<p>Meeting: {subject}</p>"},
            "start": {"dateTime": start_iso, "timeZone": tz},
            "end": {"dateTime": end_iso, "timeZone": tz},
            "attendees": [{"emailAddress": {"address": email}, "type": "required"} for email in attendees],
            "allowNewTimeProposals": True,
            "isOnlineMeeting": True,
            "onlineMeetingProvider": "teamsForBusiness",
            "transactionId": transaction_id,
        }
        async with httpx.AsyncClient(timeout=30) as http_client:
            resp = await http_client.post(
                f"{GRAPH_API_BASE}/users/{ORGANIZER}/events",
                headers=_build_headers(token, tz),
                json=body
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Error creating calendar event: {e}")
        raise

def get_next_business_days(num_days: int = 7) -> List[str]:
    """Get next business days."""
    business_days = []
    current_date = datetime.now().date()
    while len(business_days) < num_days:
        current_date += timedelta(days=1)
        if current_date.weekday() < 5:
            business_days.append(current_date.strftime("%Y-%m-%d"))
    return business_days

# -------------------------
# Agent Tool Definitions
# -------------------------

class FindFreeTimeArgs(TypedDict):
    date: str
    duration_min: int
    tz: NotRequired[str]

@function_tool
async def find_free_time_tool(details: FindFreeTimeArgs) -> str:
    """Finds available time slots on the organizer's calendar."""
    date_str = details['date']
    duration_min = details['duration_min']
    tz = details.get('tz', DEFAULT_TZ)
    try:
        duration_min = max(15, min(240, duration_min))
        start_iso, end_iso = _iso_day_window(date_str, tz)
        schedule_payload = await get_schedule(start_iso, end_iso, interval_min=duration_min, tz=tz)
        free_slots = _free_slots_from_schedule(
            schedule_payload, start_iso, end_iso, required_min=duration_min)
        if not free_slots:
            return f"No free slots of {duration_min} minutes found on {date_str}."
        slots_info = [
            f"from {slot['start_time']} to {slot['end_time']}" for slot in free_slots]
        response = f"Found {len(free_slots)} free slots of {duration_min} minutes on {date_str}: " + \
                   "; ".join(slots_info)
        return response
    except Exception as e:
        logger.error(f"Error finding free time: {e}")
        return f"An error occurred while trying to find free time: {str(e)}"

class ListSlotsArgs(TypedDict):
    date: str
    duration_min: NotRequired[int]
    tz: NotRequired[str]

@function_tool
async def list_available_slots_tool(details: ListSlotsArgs) -> str:
    """Lists available time slots for a specific date and duration."""
    date = details['date']
    duration_min = details.get('duration_min', 30)
    tz = details.get('tz', DEFAULT_TZ)
    try:
        duration_min = max(15, min(240, duration_min))
        start_iso, end_iso = _iso_day_window(date, tz)
        schedule_payload = await get_schedule(start_iso, end_iso, duration_min, tz)
        slots = _free_slots_from_schedule(
            schedule_payload, start_iso, end_iso, duration_min)
        if not slots:
            return f"No available slots found for {date} with a duration of {duration_min} minutes."
        slot_list = ", ".join(
            [f"from {s['start_time']} to {s['end_time']}" for s in slots])
        return f"Found {len(slots)} available slots on {date}: {slot_list}."
    except Exception as e:
        logger.error(f"Failed to retrieve schedule for {date}: {str(e)}")
        return f"Failed to retrieve available slots for {date}. An error occurred: {str(e)}"

class CalendarEventDetails(TypedDict):
    subject: str
    start_iso: str
    end_iso: str
    attendees: List[str]
    tz: str

@function_tool
async def create_calendar_event_tool(details: CalendarEventDetails) -> str:
    """Creates a new calendar event with a Teams meeting link."""
    try:
        event = await create_calendar_event(
            subject=details['subject'],
            start_iso=details['start_iso'],
            end_iso=details['end_iso'],
            attendees=details['attendees'],
            tz=details['tz']
        )
        return f"Successfully created event '{event['subject']}' with ID {event['id']}."
    except Exception as e:
        logger.error(f"Error creating event: {e}")
        return f"An error occurred while trying to create the event: {str(e)}"


class BookMeetingArgs(TypedDict):
    customer_name: str
    customer_email: str
    meeting_title: str
    date: str
    start_time: str
    duration_min: int

@function_tool
async def book_meeting_advanced_tool(details: BookMeetingArgs) -> str:
    """Books a new calendar meeting with a Teams link."""
    try:
        logger.info(f"🔄 Attempting to book meeting: {details}")
        
        start_dt = dp.isoparse(f"{details['date']}T{details['start_time']}")
        end_dt = start_dt + timedelta(minutes=details['duration_min'])
        meeting_subject = f"{details['meeting_title']} - {details['customer_name']}"
        attendees_list = [details['customer_email']]
        
        logger.info(f"📅 Creating event: '{meeting_subject}' from {start_dt.isoformat()} to {end_dt.isoformat()}")
        
        event = await create_calendar_event(
            subject=meeting_subject,
            start_iso=start_dt.isoformat(),
            end_iso=end_dt.isoformat(),
            attendees=attendees_list,
            tz=DEFAULT_TZ
        )
        
        logger.info(f"✅ Event response received: {json.dumps(event, indent=2)}")
        
        # Enhanced Teams link extraction
        join_url = None
        possible_paths = [
            ["onlineMeeting", "joinUrl"],
            ["onlineMeeting", "joinWebUrl"], 
            ["onlineMeeting", "join_url"],
            ["online_meeting", "joinUrl"],
            ["joinUrl"],
            ["webLink"]
        ]
        
        for path in possible_paths:
            try:
                temp_obj = event
                for key in path:
                    temp_obj = temp_obj.get(key, {})
                if isinstance(temp_obj, str) and temp_obj.startswith("https://"):
                    join_url = temp_obj
                    break
            except (AttributeError, TypeError):
                continue
        
        logger.info(f"🔗 Teams link extracted: {join_url}")
        
        if join_url:
            return f"""✅ **Meeting Booked Successfully!**

📋 **Meeting Details:**
• **Title:** {meeting_subject}
• **Date:** {details['date']}
• **Time:** {details['start_time']} 
• **Duration:** {details['duration_min']} minutes
• **Attendee:** {details['customer_email']}

🔗 **Teams Meeting Link:** 
{join_url}

📧 Calendar invite has been sent to the attendee."""
        else:
            return f"""⚠️ **Meeting Created (No Teams Link)**

📋 **Meeting Details:**
• **Title:** {meeting_subject}
• **Date:** {details['date']}
• **Time:** {details['start_time']}
• **Duration:** {details['duration_min']} minutes  
• **Attendee:** {details['customer_email']}

❗ Meeting was created but Teams link unavailable. Check your calendar for meeting details.

📧 Calendar invite has been sent to the attendee."""
        
    except Exception as e:
        logger.error(f"💥 Booking failed: {str(e)}")
        return f"❌ **Booking Failed**: {str(e)}"


class BusinessDaysArgs(TypedDict, total=False):
    num_days: int

@function_tool
def get_next_business_days_tool(details: BusinessDaysArgs) -> str:
    """Gets the next specified number of business days."""
    num_days = details.get('num_days', 7)
    business_days = get_next_business_days(num_days)
    days_str = ", ".join(business_days)
    return f"The next {num_days} business days are: {days_str}."

@function_tool
def get_business_days_formatted_tool(details: BusinessDaysArgs = None) -> str:
    """Retrieves the next 7 upcoming business days with formatted information."""
    business_days = get_next_business_days(7)
    formatted_days = []
    for i, date_str in enumerate(business_days, 1):
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        day_name = date_obj.strftime("%A")
        formatted_date = date_obj.strftime("%B %d, %Y")
        formatted_days.append({
            "number": i,
            "date": date_str,
            "day_name": day_name,
            "formatted_date": formatted_date
        })
    day_strings = [f"{d['day_name']}, {d['formatted_date']}" for d in formatted_days]
    return f"The next 7 business days are: {'; '.join(day_strings)}."

# -------------------------
# Agent Definitions
# -------------------------

meeting_booking_assistant = Agent(
    name="Meeting Booking Assistant",
    instructions="""You are a Meeting Booking Assistant. Your job is to collect meeting information and book meetings with Teams links.

🎯 **WORKFLOW - FOLLOW EXACTLY:**
1. Collect ALL 6 required pieces of information from the user
2. As soon as you have ALL 6 pieces, IMMEDIATELY call book_meeting_advanced_tool
3. Display the booking result to the user
4. DO NOT ask "How can I help you?" after booking

📋 **REQUIRED INFORMATION (collect all before booking):**
- customer_name: Full name of the person attending
- customer_email: Email address (must contain @)
- meeting_title: Subject/purpose of the meeting  
- date: Date in YYYY-MM-DD format (must be future date)
- start_time: Start time in HH:MM format (24-hour time)
- duration_min: Duration in minutes (15-240)

💡 **EXAMPLE:**
User: "Book a meeting with Sarah Johnson"
You: "I'll help book that meeting. I need:
• What's Sarah Johnson's email?
• What's the meeting about?  
• Which date (YYYY-MM-DD)?
• What time (HH:MM)?
• How long in minutes?"

When you get all 6 pieces → IMMEDIATELY call the tool. Don't ask anything else.

🚫 **CRITICAL RULES:**
- NEVER say "How can I help?" after collecting info
- ALWAYS call book_meeting_advanced_tool when you have complete details
- NEVER just describe what you'd do - actually call the tool""",
    tools=[book_meeting_advanced_tool],
    model=model
)



calendar_and_availability_agent = Agent(
    name="Calendar and Availability Agent",
    instructions="""You are a Calendar and Availability Agent. Your purpose is to provide information about available time slots and to create new calendar events.
    
    KEY RESPONSIBILITIES:
    1. Find free time slots for a specified date and duration using `find_free_time_tool`.
    2. List all available slots for a given date using `list_available_slots_tool`.
    3. Create a new calendar event with a subject, date, time, and list of attendees using `create_calendar_event_tool`.
    4. Handle timezone considerations (default is Arabian Standard Time).
    5. Validate duration parameters (minimum 15 minutes, maximum 240 minutes) for finding slots.
    
    Always provide clear, user-friendly responses and gracefully handle errors.""",
    tools=[find_free_time_tool, list_available_slots_tool, create_calendar_event_tool],
    model=model
)

business_days_finder = Agent(
    name="Business Days Finder",
    instructions="""You are a Business Days Finder agent. Your sole purpose is to identify and list the upcoming business days.
    
    KEY RESPONSIBILITIES:
    1. Find the next N business days (Monday to Friday) using `get_next_business_days_tool`.
    2. Format and present the list of business days in a human-readable format, including the day of the week and a formatted date, using `get_business_days_formatted_tool`.
    3. The default number of days to find is 7, unless the user specifies otherwise.
    
    REQUIRED INFORMATION:
    - num_days (optional): The number of business days to find.
    
    EXAMPLE USER QUERIES YOU CAN HANDLE:
    - "What are the next 5 business days?"
    - "Show me the next 7 weekdays."
    - "When are the next business days?"
    
    Your responses should be direct and informative.""",
    tools=[get_next_business_days_tool, get_business_days_formatted_tool],
    model=model
)

# -------------------------
# New Agent: Advanced meeting booking with customer details
# -------------------------

# -------------------------
# Main Dispatcher Agent with Handoff
# -------------------------

main_dispatcher_agent = Agent(
    name="Main Dispatcher Agent",
    instructions="""You are a top-level dispatcher agent. 
Your role is to understand the user's request and hand off to the correct specialized agent.

Use the `handoff` function to pass control to:
- **'Meeting Booking Assistant'**: If the user wants to book a meeting (collect details if missing).
- **'Calendar and Availability Agent'**: If the user is asking to find available time slots, check for free time, or create a new calendar event.
- **'Business Days Finder'**: If the user is asking about future business days.

If a request doesn't fit any specialized agent (e.g., greetings, general knowledge), respond directly.
""",
    handoffs=[ calendar_and_availability_agent, business_days_finder, meeting_booking_assistant],
    model=model
)



# ==========================================================# ==========================================================
# ✅ Chainlit Integration (Replaces old main() tests)
# ==========================================================
import chainlit as cl

# Lower noisy logs from agent library if needed
openai_logger = logging.getLogger("openai.agents")
openai_logger.setLevel(logging.CRITICAL)

@cl.on_chat_start
async def start():
    # Store the main dispatcher agent in the user session to preserve state
    cl.user_session.set("agent", main_dispatcher_agent)
    await cl.Message(
        content=(
            "👋 Hi! I'm your **Meeting Assistant**.\n\n"
            "I can help you with:\n"
            "• 📅 Booking meetings (give full details or I will ask follow-ups)\n"
            "• 🕒 Finding free slots (e.g. 'Find slots on 2025-10-20 for 30 minutes')\n"
            "• 📆 Listing upcoming business days (e.g. 'next 5 business days')\n\n"
            "Type your request and I'll handle the rest."
        )
    ).send()

# Replace the on_message function in your code with this version:

@cl.on_message
async def on_message(message: cl.Message):
    user_text = message.content.strip()
    try:
        # Retrieve the agent from the user session
        agent = cl.user_session.get("agent")
        
        # Create a runner
        runner = Runner()
        
        try:
            # Run with streaming
            response_msg = cl.Message(content="")
            await response_msg.send()
            
            final_output = ""  # collect final string
            
            async for chunk in runner.run(agent, user_text, stream=True):
                if isinstance(chunk, str):
                    final_output += chunk
                    await response_msg.stream_token(chunk)
                elif isinstance(chunk, dict) and "output" in chunk:
                    final_output += chunk["output"]
                    await response_msg.stream_token(chunk["output"])
            
            # Update once with the full final output
            await response_msg.update(content=final_output)
            
        except TypeError as e:
            if "unexpected keyword argument 'stream'" in str(e):
                # Run without streaming
                result = await runner.run(agent, user_text)

                # Extract only the final output string
                if isinstance(result, str):
                    content = result
                elif isinstance(result, dict):
                    content = result.get("output", result.get("content", str(result)))
                elif hasattr(result, "final_output"):
                    content = getattr(result, "final_output")
                elif hasattr(result, "output"):
                    content = getattr(result, "output")
                elif hasattr(result, "content"):
                    content = result.content
                else:
                    content = str(result)

                await cl.Message(content=content).send()
            else:
                raise e

    except Exception as e:
        logger.exception("Error handling user message")
        await cl.Message(
            content=f"⚠️ Sorry — an error occurred while processing your request: {str(e)}"
        ).send()



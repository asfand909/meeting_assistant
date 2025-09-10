"""
OpenAI Agent tools for calendar scheduling functionality.
Provides functions for checking availability and booking meetings.
"""

import os
import datetime as dt
from typing import List, Dict
from dateutil import parser as dp
from pydantic import BaseModel, Field
from agents import function_tool
from ms_graph_service import (
    get_schedule, 
    create_calendar_event, 
    create_online_meeting_standalone, 
    DEFAULT_TZ
)

# Configuration
WORKING_START_HOUR = int(os.getenv("WORKING_START_HOUR", "9"))
WORKING_END_HOUR = int(os.getenv("WORKING_END_HOUR", "18"))


def _iso_day_window(date_str: str, tz: str, start_hour: int = WORKING_START_HOUR, 
                    end_hour: int = WORKING_END_HOUR) -> tuple[str, str]:
    """
    Convert a date string to ISO datetime range for a working day.
    
    Args:
        date_str: Date in YYYY-MM-DD format
        tz: Timezone
        start_hour: Working day start hour
        end_hour: Working day end hour
        
    Returns:
        Tuple of (start_iso, end_iso) strings
    """
    date_obj = dp.isoparse(date_str).date()
    start_dt = dt.datetime.combine(date_obj, dt.time(hour=start_hour, minute=0))
    end_dt = dt.datetime.combine(date_obj, dt.time(hour=end_hour, minute=0))
    return start_dt.isoformat(), end_dt.isoformat()


def _free_slots_from_schedule(payload: Dict, start_iso: str, end_iso: str, 
                              required_min: int) -> List[Dict]:
    """
    Extract free time slots from Microsoft Graph schedule response.
    
    Args:
        payload: Graph API schedule response
        start_iso: Day start time in ISO format
        end_iso: Day end time in ISO format
        required_min: Minimum slot duration in minutes
        
    Returns:
        List of available time slots with start/end times
    """
    # Extract busy periods from schedule
    schedule_items = []
    if payload.get("value") and len(payload["value"]) > 0:
        schedule_items = payload["value"][0].get("scheduleItems", [])
    
    busy_periods = []
    for item in schedule_items:
        status = (item.get("status") or "").lower()
        # Treat busy, out-of-office, and tentative as unavailable
        if status in ("busy", "oof", "tentative"):
            busy_start = dp.isoparse(item["start"]["dateTime"])
            busy_end = dp.isoparse(item["end"]["dateTime"])
            busy_periods.append((busy_start, busy_end))
    
    # Sort busy periods by start time
    busy_periods.sort(key=lambda x: x[0])
    
    # Find gaps between busy periods
    current_time = dp.isoparse(start_iso)
    day_end = dp.isoparse(end_iso)
    free_slots = []
    
    for busy_start, busy_end in busy_periods:
        # Check if there's a gap before this busy period
        if busy_start > current_time:
            gap_duration = (busy_start - current_time).total_seconds() / 60
            if gap_duration >= required_min:
                slot_end = min(
                    current_time + dt.timedelta(minutes=required_min),
                    busy_start
                )
                free_slots.append({
                    "start": current_time.isoformat(),
                    "end": slot_end.isoformat(),
                    "maxEnd": busy_start.isoformat(),
                    "duration_min": required_min,
                    "start_time": current_time.strftime("%H:%M"),
                    "end_time": slot_end.strftime("%H:%M")
                })
        
        # Move current time to end of busy period
        current_time = max(current_time, busy_end)
    
    # Check for gap after last busy period
    if current_time < day_end:
        remaining_minutes = (day_end - current_time).total_seconds() / 60
        if remaining_minutes >= required_min:
            slot_end = current_time + dt.timedelta(minutes=required_min)
            free_slots.append({
                "start": current_time.isoformat(),
                "end": slot_end.isoformat(),
                "maxEnd": day_end.isoformat(),
                "duration_min": required_min,
                "start_time": current_time.strftime("%H:%M"),
                "end_time": slot_end.strftime("%H:%M")
            })
    
    return free_slots


@function_tool
async def list_available_slots(date: str, duration_min: int = 30, 
                               working_start: int = WORKING_START_HOUR, 
                               working_end: int = WORKING_END_HOUR) -> List[Dict]:
    """
    List available time slots on the organizer's calendar for a given date.
    
    Args:
        date: Date in YYYY-MM-DD format
        duration_min: Required meeting duration in minutes (15-240)
        working_start: Working day start hour (0-23)
        working_end: Working day end hour (0-23)
        
    Returns:
        List of available time slots with start times and durations
    """
    try:
        # Validate inputs
        duration_min = max(15, min(240, duration_min))
        working_start = max(0, min(23, working_start))
        working_end = max(working_start + 1, min(23, working_end))
        
        # Get day boundaries
        start_iso, end_iso = _iso_day_window(date, DEFAULT_TZ, working_start, working_end)
        
        # Query Microsoft Graph for schedule
        schedule_payload = await get_schedule(
            start_iso, end_iso, 
            interval_min=max(15, min(60, duration_min)), 
            tz=DEFAULT_TZ
        )
        
        # Extract free slots
        slots = _free_slots_from_schedule(schedule_payload, start_iso, end_iso, duration_min)
        
        return {
            "date": date,
            "timezone": DEFAULT_TZ,
            "working_hours": f"{working_start:02d}:00 - {working_end:02d}:00",
            "requested_duration": duration_min,
            "available_slots": slots,
            "total_slots": len(slots)
        }
        
    except Exception as e:
        return {
            "error": f"Failed to retrieve schedule: {str(e)}",
            "date": date,
            "available_slots": []
        }


class BookingRequest(BaseModel):
    """Data model for meeting booking requests."""
    customer_name: str = Field(..., description="Customer's full name")
    customer_email: str = Field(..., description="Customer's email address")
    meeting_title: str = Field(..., description="Meeting subject/title")
    date: str = Field(..., description="Meeting date in YYYY-MM-DD format")
    start_time: str = Field(..., description="Meeting start time in HH:MM 24-hour format")
    duration_min: int = Field(30, ge=15, le=240, description="Meeting duration in minutes")
    notes: str = Field("", description="Optional meeting notes or agenda")


@function_tool
async def book_meeting_slot(booking: BookingRequest) -> Dict:
    """
    Book a Teams meeting in the organizer's calendar.
    
    Args:
        booking: BookingRequest object with all meeting details
        
    Returns:
        Dictionary with booking confirmation and Teams join URL
    """
    try:
        # Parse start and end times
        start_dt = dp.isoparse(f"{booking.date}T{booking.start_time}")
        end_dt = start_dt + dt.timedelta(minutes=booking.duration_min)
        
        # Create meeting title with customer name
        meeting_subject = f"{booking.meeting_title} - {booking.customer_name}"
        
        # Create calendar event with Teams meeting
        event = await create_calendar_event(
            subject=meeting_subject,
            start_iso=start_dt.isoformat(),
            end_iso=end_dt.isoformat(),
            attendees=[booking.customer_email],
            tz=DEFAULT_TZ
        )
        
        # Extract Teams join URL
        join_url = None
        online_meeting = event.get("onlineMeeting")
        if online_meeting:
            join_url = online_meeting.get("joinUrl")
        
        # Fallback: create standalone meeting if joinUrl missing
        if not join_url:
            try:
                standalone_meeting = await create_online_meeting_standalone(
                    meeting_subject, 
                    start_dt.isoformat(), 
                    end_dt.isoformat()
                )
                join_info = standalone_meeting.get("joinInformation", {})
                join_url = join_info.get("joinUrl") or standalone_meeting.get("joinWebUrl")
            except Exception as fallback_error:
                print(f"Fallback meeting creation failed: {fallback_error}")
        
        # Format response
        return {
            "success": True,
            "booking_id": event.get("id"),
            "meeting_title": meeting_subject,
            "customer_name": booking.customer_name,
            "customer_email": booking.customer_email,
            "start_time": event.get("start", {}).get("dateTime"),
            "end_time": event.get("end", {}).get("dateTime"),
            "timezone": DEFAULT_TZ,
            "teams_join_url": join_url,
            "calendar_link": event.get("webLink"),
            "organizer_message": f"Meeting '{meeting_subject}' successfully booked for {booking.date} at {booking.start_time}.",
            "attendees": [a["emailAddress"]["address"] for a in event.get("attendees", [])]
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Booking failed: {str(e)}",
            "customer_name": booking.customer_name,
            "customer_email": booking.customer_email,
            "requested_date": booking.date,
            "requested_time": booking.start_time
        }


@function_tool
async def check_specific_time_availability(date: str, start_time: str, duration_min: int = 30) -> Dict:
    """
    Check if a specific time slot is available for booking.
    
    Args:
        date: Date in YYYY-MM-DD format
        start_time: Start time in HH:MM 24-hour format
        duration_min: Required duration in minutes
        
    Returns:
        Dictionary with availability status
    """
    try:
        # Parse the requested time slot
        start_dt = dp.isoparse(f"{date}T{start_time}")
        end_dt = start_dt + dt.timedelta(minutes=duration_min)
        
        # Get schedule for the requested time period
        schedule_payload = await get_schedule(
            start_dt.isoformat(), 
            end_dt.isoformat(), 
            interval_min=15, 
            tz=DEFAULT_TZ
        )
        
        # Check for conflicts
        conflicts = []
        if schedule_payload.get("value") and len(schedule_payload["value"]) > 0:
            schedule_items = schedule_payload["value"][0].get("scheduleItems", [])
            
            for item in schedule_items:
                status = (item.get("status") or "").lower()
                if status in ("busy", "oof", "tentative"):
                    item_start = dp.isoparse(item["start"]["dateTime"])
                    item_end = dp.isoparse(item["end"]["dateTime"])
                    
                    # Check for overlap
                    if not (end_dt <= item_start or start_dt >= item_end):
                        conflicts.append({
                            "start": item["start"]["dateTime"],
                            "end": item["end"]["dateTime"],
                            "status": status
                        })
        
        is_available = len(conflicts) == 0
        
        return {
            "date": date,
            "start_time": start_time,
            "end_time": end_dt.strftime("%H:%M"),
            "duration_min": duration_min,
            "is_available": is_available,
            "conflicts": conflicts,
            "message": "Time slot is available" if is_available else f"Time slot conflicts with {len(conflicts)} existing appointment(s)"
        }
        
    except Exception as e:
        return {
            "error": f"Failed to check availability: {str(e)}",
            "is_available": False
        }
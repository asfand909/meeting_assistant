"""
OpenAI Agents SDK Booking Agent for meeting scheduling functionality.
Modern implementation using OpenAI Agents SDK with built-in conversation management and tool handling.
"""

import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Import OpenAI Agents SDK
from agents import Agent, Runner, Session
from openai import AsyncOpenAI
from agents.models import OpenAIChatCompletionsModel

# Load environment variables
load_dotenv()

# Initialize Gemini client through OpenAI-compatible interface
api = os.getenv('GOOGLE_API_KEY')
client = AsyncOpenAI(
    api_key=api,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

# Create Gemini model instance
model = OpenAIChatCompletionsModel(
    model='gemini-2.5-flash',
    openai_client=client
)


class BookingRequest(BaseModel):
    """Data model for booking requests."""
    customer_name: str = Field(description="Customer's full name")
    customer_email: str = Field(description="Customer's email address")
    meeting_title: str = Field(description="Title or purpose of the meeting")
    date: str = Field(description="Meeting date in YYYY-MM-DD format")
    start_time: str = Field(
        description="Meeting start time in HH:MM format (24-hour)")
    duration_min: int = Field(
        default=30, description="Meeting duration in minutes (15-240)")
    notes: str = Field(default="", description="Optional meeting notes")


class BookingAgent:
    """Modern OpenAI Agents SDK-based booking agent."""

    def __init__(self):
        self.session = Session()

        # System instructions for the agent
        self.instructions = """You are a friendly and professional meeting booking assistant.

**CONVERSATION FLOW:**

**1. GENERAL CONVERSATION MODE:**
- Start with warm greetings and friendly conversation
- Handle general questions, small talk, and introductions naturally
- Be personable and engaging
- Only switch to booking mode when user explicitly mentions wanting to arrange, schedule, or book a meeting

**2. MEETING BOOKING MODE (triggered when user wants to arrange a meeting):**
When a user asks about arranging/scheduling/booking a meeting, present this menu:

"I'd be happy to help you schedule a meeting! Here are your options:

**Meeting Duration Options:**
1️⃣ 15 minutes - Quick check-in or brief discussion
2️⃣ 30 minutes - Standard meeting (most popular)  
3️⃣ 45 minutes - Extended discussion
4️⃣ 60 minutes - Comprehensive meeting
5️⃣ 90 minutes - Workshop or detailed session
6️⃣ 120 minutes - Long meeting or training
7️⃣ Custom duration - Let me know your preferred length

Please select an option (1-7) or tell me your preferred duration, and I'll show you available time slots!"

**3. AFTER MENU SELECTION:**
- Once they choose a duration, ask for their preferred date
- Use tools to check availability and present options
- Collect required information: name, email, meeting title
- Complete the booking with confirmation

**Key Guidelines:**
- **Time Zone**: Always work in Arabian Standard Time
- **Date Format**: Accept dates in YYYY-MM-DD format (e.g., 2025-08-26)  
- **Time Format**: Use 24-hour format for times (e.g., 14:30)
- **Working Hours**: Default 9 AM to 6 PM unless specified otherwise

**Communication Style:**
- Start friendly and conversational
- Become more structured during booking process
- Always confirm details before booking
- Provide Teams meeting link after successful booking
- Format time slots clearly (e.g., "9:00 AM - 9:30 AM")

**Example Flow:**
User: "Hello!"
You: "Hello! Welcome! How are you doing today? How can I help you?"

User: "I'd like to book a meeting"  
You: [Show the 1-7 menu]

User: "Option 2 please"
You: "Perfect! 30 minutes it is. What date would work best for you?"

Remember: Be human-like in general conversation, professional during booking."""

        # Create the agent with tools using Gemini model
        self.agent = Agent(
            name="BookingAssistant",
            instructions=self.instructions,
            tools=[
                self.list_available_slots,
                self.check_specific_time_availability,
                self.book_meeting_slot
            ],
            model=model
        )

    async def list_available_slots(
        self,
        date: str,
        duration_min: int = 30,
        working_start: int = 9,
        working_end: int = 18
    ) -> Dict[str, Any]:
        """
        List available time slots for a specific date.

        Args:
            date: Date in YYYY-MM-DD format
            duration_min: Slot duration in minutes (15-240)
            working_start: Working hours start (0-23)
            working_end: Working hours end (0-23)

        Returns:
            Dictionary with available slots or error message
        """
        try:
            # Import the actual calendar tools
            from tools import list_available_slots
            result = await list_available_slots(date, duration_min, working_start, working_end)
            return result
        except Exception as e:
            return {"error": f"Failed to retrieve available slots: {str(e)}"}

    async def check_specific_time_availability(
        self,
        date: str,
        start_time: str,
        duration_min: int = 30
    ) -> Dict[str, Any]:
        """
        Check if a specific time slot is available.

        Args:
            date: Date in YYYY-MM-DD format
            start_time: Start time in HH:MM format
            duration_min: Duration in minutes (15-240)

        Returns:
            Dictionary with availability status
        """
        try:
            from tools import check_specific_time_availability
            result = await check_specific_time_availability(date, start_time, duration_min)
            return result
        except Exception as e:
            return {"error": f"Failed to check availability: {str(e)}"}

    async def book_meeting_slot(
        self,
        customer_name: str,
        customer_email: str,
        meeting_title: str,
        date: str,
        start_time: str,
        duration_min: int = 30,
        notes: str = ""
    ) -> Dict[str, Any]:
        """
        Book a meeting slot and create Teams meeting.

        Args:
            customer_name: Customer's full name
            customer_email: Customer's email
            meeting_title: Meeting title/purpose
            date: Date in YYYY-MM-DD format
            start_time: Start time in HH:MM format
            duration_min: Duration in minutes (15-240)
            notes: Optional meeting notes

        Returns:
            Dictionary with booking confirmation and Teams link
        """
        try:
            from tools import book_meeting_slot, BookingRequest

            booking = BookingRequest(
                customer_name=customer_name,
                customer_email=customer_email,
                meeting_title=meeting_title,
                date=date,
                start_time=start_time,
                duration_min=duration_min,
                notes=notes
            )

            result = await book_meeting_slot(booking)
            return result
        except Exception as e:
            return {"error": f"Failed to book meeting: {str(e)}"}

    async def process_message(self, message: str) -> str:
        """
        Process a user message using the OpenAI Agents SDK.

        Args:
            message: User's input message

        Returns:
            Agent's response
        """
        try:
            # Use the SDK's Runner to process the message
            result = await Runner.run_async(
                agent=self.agent,
                message=message,
                session=self.session
            )

            return result.final_output

        except Exception as e:
            return f"I apologize, but I encountered an error: {str(e)}. Please try again or contact support if the issue persists."

    def reset_session(self):
        """Reset the conversation session."""
        self.session = Session()

    def get_conversation_history(self) -> List[Dict[str, Any]]:
        """Get the conversation history from the session."""
        return self.session.get_messages()


class MultiAgentBookingSystem:
    """
    Advanced multi-agent system with specialized agents for different booking tasks.
    Demonstrates handoffs between agents using the Agents SDK.
    """

    def __init__(self):
        self.session = Session()

        # Availability checking agent using Gemini
        self.availability_agent = Agent(
            name="AvailabilityChecker",
            instructions="""You are specialized in checking calendar availability. 
            Your role is to:
            - Check available time slots for requested dates
            - Verify specific time availability
            - Present options clearly to customers
            - Hand off to BookingAgent when customer is ready to book
            
            Use the available tools to check calendar availability and present options professionally.""",
            tools=[self.list_available_slots,
                   self.check_specific_time_availability],
            model=model
        )

        # Booking agent using Gemini
        self.booking_agent = Agent(
            name="BookingAgent",
            instructions="""You are specialized in completing meeting bookings.
            Your role is to:
            - Collect all required customer information
            - Create calendar events and Teams meetings
            - Provide booking confirmations
            - Handle booking-related questions
            
            Always ensure you have all required information before booking:
            - Customer name and email
            - Meeting title/purpose
            - Date and time
            - Duration""",
            tools=[self.book_meeting_slot],
            model=model
        )

        # Main coordinator agent using Gemini
        self.coordinator = Agent(
            name="BookingCoordinator",
            instructions="""You are the main booking coordinator. 
            Route customer requests to the appropriate specialized agent:
            - For availability questions: hand off to AvailabilityChecker
            - For booking confirmations: hand off to BookingAgent
            - For general questions: handle directly
            
            Always maintain a professional, friendly tone.""",
            model=model
        )

    async def list_available_slots(self, date: str, duration_min: int = 30,
                                   working_start: int = 9, working_end: int = 18):
        """Tool wrapper for availability agent."""
        try:
            from tools import list_available_slots
            return await list_available_slots(date, duration_min, working_start, working_end)
        except Exception as e:
            return {"error": f"Failed to retrieve available slots: {str(e)}"}

    async def check_specific_time_availability(self, date: str, start_time: str, duration_min: int = 30):
        """Tool wrapper for availability agent."""
        try:
            from tools import check_specific_time_availability
            return await check_specific_time_availability(date, start_time, duration_min)
        except Exception as e:
            return {"error": f"Failed to check availability: {str(e)}"}

    async def book_meeting_slot(self, customer_name: str, customer_email: str,
                                meeting_title: str, date: str, start_time: str,
                                duration_min: int = 30, notes: str = ""):
        """Tool wrapper for booking agent."""
        try:
            from tools import book_meeting_slot, BookingRequest

            booking = BookingRequest(
                customer_name=customer_name,
                customer_email=customer_email,
                meeting_title=meeting_title,
                date=date,
                start_time=start_time,
                duration_min=duration_min,
                notes=notes
            )

            return await book_meeting_slot(booking)
        except Exception as e:
            return {"error": f"Failed to book meeting: {str(e)}"}

    async def process_message(self, message: str) -> str:
        """Process message through the multi-agent system."""
        try:
            # Start with the coordinator agent
            result = await Runner.run_async(
                agent=self.coordinator,
                message=message,
                session=self.session
            )

            return result.final_output

        except Exception as e:
            return f"I apologize, but I encountered an error: {str(e)}. Please try again."


def create_booking_agent() -> BookingAgent:
    """Create and return a configured booking agent."""
    return BookingAgent()


def create_multi_agent_system() -> MultiAgentBookingSystem:
    """Create and return a multi-agent booking system."""
    return MultiAgentBookingSystem()


# Usage example
if __name__ == "__main__":
    import asyncio

    async def main():
        # Simple single agent
        agent = create_booking_agent()

        # Example conversation
        response = await agent.process_message(
            "Hi, I'd like to book a meeting for tomorrow at 2 PM"
        )
        print("Agent:", response)

        # Multi-agent system example
        multi_system = create_multi_agent_system()
        response = await multi_system.process_message(
            "What availability do you have this week?"
        )
        print("Multi-Agent:", response)

    # Run example
        asyncio.run(main())

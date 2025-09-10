# Teams Meeting Booking Chatbot with Google Gemini

An AI-powered chatbot that allows customers to book meetings directly into your Microsoft 365 calendar with automatic Teams meeting creation. Built with Google Gemini AI, Chainlit, and Microsoft Graph API.

## Features

- 🤖 **Conversational AI**: Natural language booking interface
- 📅 **Real-time Availability**: Checks your actual calendar for conflicts
- 📧 **Automatic Invites**: Creates Teams meetings and sends invitations
- 🌍 **Timezone Support**: Configurable timezone handling
- 🔒 **Secure**: Uses app-only authentication (no customer sign-in required)
- ⚡ **Fast**: Real-time calendar integration with Microsoft Graph

## Prerequisites

- Python 3.8+
- Microsoft 365 account with Teams
- Azure Active Directory app registration
- Google Gemini API key

## Quick Start

### 1. Clone and Setup
```bash
git clone <your-repo-url>
cd teams-booking-chatbot
python -m venv venv

# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Azure App Registration

1. Go to [Azure Portal](https://portal.azure.com) > Azure Active Directory > App registrations
2. Click "New registration"
3. Name: "Teams Booking Bot"
4. Account types: "Accounts in this organizational directory only"
5. Click "Register"

#### Add API Permissions:
1. Go to "API permissions" 
2. Add these Microsoft Graph **Application** permissions:
   - `Calendars.ReadWrite`
   - `Calendars.Read`
   - `OnlineMeetings.ReadWrite.All` (optional fallback)
3. Click "Grant admin consent for [Your Tenant]"

#### Get Credentials:
1. Go to "Certificates & secrets" → "New client secret"
2. Copy the secret value immediately (you won't see it again!)
3. Go to "Overview" and copy:
   - Application (client) ID
   - Directory (tenant) ID

### 3. Environment Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:
```bash
# Google Gemini Configuration
GOOGLE_API_KEY=your-gemini-api-key-here
GEMINI_MODEL=gemini-1.5-flash

# Azure AD App Registration  
AZURE_TENANT_ID=your-tenant-id
AZURE_CLIENT_ID=your-client-id
AZURE_CLIENT_SECRET=your-client-secret

# Calendar Configuration
ORGANIZER_UPN=your-email@company.com
DEFAULT_TZ=Arabian Standard Time

# Optional: Working Hours
WORKING_START_HOUR=9
WORKING_END_HOUR=18
```

### Get Google Gemini API Key:
1. Go to [Google AI Studio](https://aistudio.google.com/)
2. Sign in with your Google account
3. Click "Get API key" → "Create API key"
4. Copy your API key

### 4. Test Connection

```bash
python -c "import asyncio; from ms_graph_service import test_connection; print(asyncio.run(test_connection()))"
```

### 5. Run the Application

```bash
chainlit run app.py -w
```

Visit: http://localhost:8000

## Usage Examples

**Customer:** "I need a 30-minute meeting on 2025-08-26"

**Bot:** Shows available slots, collects details, books meeting with Teams link

**Customer:** "What's available tomorrow at 2 PM?"

**Bot:** Checks specific time availability and guides booking process

## API Endpoints

The chatbot uses these Microsoft Graph endpoints:

- `GET /users/{id}/calendar/getSchedule` - Check availability
- `POST /users/{id}/events` - Create calendar events with Teams meetings
- `POST /users/{id}/onlineMeetings` - Fallback Teams meeting creation

## Troubleshooting

### Common Issues

**"MSAL error: invalid_client"**
- Check your `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET`
- Verify the app registration exists in the correct tenant

**"Insufficient privileges"**  
- Ensure API permissions are granted and admin consent is provided
- Wait 5-10 minutes after granting permissions

**"Teams join URL is null"**
- Ensure the organizer has signed into Teams at least once
- Check that Teams is enabled for the organizer
- Verify `onlineMeetingProvider` is allowed in your tenant

**"Calendar not found"**
- Verify `ORGANIZER_UPN` matches exactly (case-sensitive)
- Try using the user's Object ID instead of UPN

### Debug Mode

Run with verbose logging:
```bash
CHAINLIT_DEBUG=true chainlit run app.py -w
```

### Test Individual Components

```bash
# Test Graph API connection
python -c "import asyncio; from ms_graph_service import test_connection; print(asyncio.run(test_connection()))"

# Test schedule retrieval
python -c "
import asyncio
from ms_graph_service import get_schedule
from datetime import datetime, timedelta
start = datetime.now().isoformat()
end = (datetime.now() + timedelta(hours=8)).isoformat()
print(asyncio.run(get_schedule(start, end)))
"
```

## Security Considerations

- Keep `AZURE_CLIENT_SECRET` secure and out of version control
- Use Azure Key Vault in production
- Consider network restrictions for the app registration
- Monitor API usage and rate limits
- Regularly rotate client secrets

## Customization

### Modify Working Hours
Edit `DEFAULT_TZ`, `WORKING_START_HOUR`, `WORKING_END_HOUR` in `.env`

### Change AI Model
Update `GEMINI_MODEL` in `.env` (supports gemini-1.5-flash, gemini-1.5-pro, etc.)

### Customize Agent Instructions
Edit the `system_instruction` in `gemini_agent.py` to change the bot's behavior

### Add Custom Tools
Create new functions in `tools.py` with the `@function_tool` decorator

## Deployment

### Production Considerations

1. **Environment Variables**: Use secure secret management
2. **SSL/HTTPS**: Required for production webhooks
3. **Rate Limiting**: Monitor Graph API usage
4. **Logging**: Implement proper logging and monitoring
5. **Error Handling**: Add comprehensive error handling
6. **Scalability**: Consider using Azure Functions or similar serverless

### Docker Deployment

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "8000"]
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## Support

For issues:
1. Check the troubleshooting section
2. Review Microsoft Graph documentation
3. Check Azure AD app registration settings
4. Open an issue with detailed error logs

## License

MIT License - see LICENSE file for details.

## Resources

- [Microsoft Graph API Documentation](https://docs.microsoft.com/en-us/graph/)
- [Azure AD App Registration Guide](https://docs.microsoft.com/en-us/azure/active-directory/develop/)
- [OpenAI Agents SDK](https://github.com/openai/openai-agents)
- [Chainlit Documentation](https://docs.chainlit.io/)
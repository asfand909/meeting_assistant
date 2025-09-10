# token_test.py
import asyncio
from ms_graph_service import _get_access_token, test_connection

async def main():
    try:
        token = _get_access_token()
        print("Got token length:", len(token))
        profile = await test_connection()
        print("Connected as:", profile.get("displayName") or profile.get("userPrincipalName"))
    except Exception as e:
        print("Token/profile error:", repr(e))

if __name__ == "__main__":
    asyncio.run(main())

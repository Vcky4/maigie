import asyncio
import httpx
import json
import uuid

BASE_URL = "http://localhost:8000"
API_V1 = f"{BASE_URL}/api/v1"


async def test_oauth_flow():
    print("Testing ChatGPT OAuth 2.1 Flow...")

    async with httpx.AsyncClient() as client:
        # 1. Test Discovery Endpoint
        print("\n--- 1. Discovery Endpoint ---")
        discovery_res = await client.get(f"{BASE_URL}/.well-known/oauth-authorization-server")
        print(f"Status: {discovery_res.status_code}")
        print(json.dumps(discovery_res.json(), indent=2))

        # 2. Test Dynamic Client Registration
        print("\n--- 2. Dynamic Client Registration ---")
        register_payload = {
            "redirect_uris": ["https://chatgpt.com/aip/g-test/oauth/callback"],
            "client_name": "ChatGPT API Tester",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        }

        register_res = await client.post(f"{API_V1}/mcp/oauth/register", json=register_payload)
        print(f"Status: {register_res.status_code}")

        if register_res.status_code != 200:
            print("Failed to register client:", register_res.text)
            return

        client_data = register_res.json()
        client_id = client_data["client_id"]
        redirect_uri = client_data["redirect_uris"][0]
        print(json.dumps(client_data, indent=2))

        print("\n--- Next Steps ---")
        print(f"To test authorization, you would navigate a user to your frontend URL:")
        print(
            f"http://localhost:4200/chatgpt/authorize?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code&state=test-state-123&code_challenge=test-challenge&code_challenge_method=S256"
        )

        # We can't easily script the authorization decision here because it requires a valid user session (JWT)
        print(
            "\nThe test concludes here because step 3 (Authorization Decision) requires an authenticated user token."
        )


if __name__ == "__main__":
    asyncio.run(test_oauth_flow())

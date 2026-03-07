import os
import asyncio
from google import genai


async def verify():
    # Attempt to get the key from the environment
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ Error: GEMINI_API_KEY environment variable not found.")
        return

    client = genai.Client(api_key=api_key)

    print("Testing embedding with text-embedding-004 on API version v1...")
    try:
        response = client.models.embed_content(model="text-embedding-004", contents="Hello world")
        print("✅ Success! Embedding generated.")
        print(f"Vector length: {len(response.embeddings[0].values)}")
    except Exception as e:
        print(f"❌ Failed: {e}")


if __name__ == "__main__":
    asyncio.run(verify())

import os

from dotenv import load_dotenv
from google import genai

# Load your .env file
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌ Error: GEMINI_API_KEY not found in .env")
    exit()

client = genai.Client(api_key=api_key)

print(f"✅ Key found: {api_key[:5]}... checking models...\n")

try:
    print("Available Models for Chat:")
    found = False
    models = client.models.list()
    for m in models:
        # Check if model supports content generation
        if (
            hasattr(m, "supported_generation_methods")
            and "generateContent" in m.supported_generation_methods
        ):
            print(f"- {m.name}")
            found = True
        elif hasattr(m, "name"):
            # If we can't check methods, just list all models
            print(f"- {m.name}")
            found = True

    if not found:
        print("⚠️ No chat models found. Check your API Key permissions.")

except Exception as e:
    print(f"❌ Error listing models: {e}")

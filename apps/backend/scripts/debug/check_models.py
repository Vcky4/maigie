import os

import google.generativeai as genai
from dotenv import load_dotenv

# Load your .env file
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌ Error: GEMINI_API_KEY not found in .env")
    exit()

genai.configure(api_key=api_key)

print(f"✅ Key found: {api_key[:5]}... checking models...\n")

try:
    print("Available Models for Chat:")
    found = False
    for m in genai.list_models():
        if "generateContent" in m.supported_generation_methods:
            print(f"- {m.name}")
            found = True

    if not found:
        print("⚠️ No chat models found. Check your API Key permissions.")

except Exception as e:
    print(f"❌ Error listing models: {e}")

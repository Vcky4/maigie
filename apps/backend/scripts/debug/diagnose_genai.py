import os
import sys

from dotenv import load_dotenv
from google import genai

# Load your .env file
load_dotenv()


def diagnose():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ Error: GEMINI_API_KEY not found in .env")
        return

    client = genai.Client(api_key=api_key)
    print(f"✅ Key found: {api_key[:5]}... checking models with google-genai SDK...\n")

    try:
        print("Available Models:")
        for model in client.models.list():
            methods = model.supported_generate_methods or []
            print(f"- {model.name} (Methods: {methods})")

    except Exception as e:
        print(f"❌ Error listing models: {e}")


if __name__ == "__main__":
    diagnose()

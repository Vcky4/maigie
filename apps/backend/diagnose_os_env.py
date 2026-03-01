import os
from google import genai


def diagnose():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ Error: GEMINI_API_KEY not found in OS environment")
        return

    client = genai.Client(api_key=api_key)
    print(f"✅ Key found (os.getenv): {api_key[:5]}... checking models...")

    try:
        print("Available models:")
        for model in client.models.list():
            print(f"- {model.name}")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    diagnose()

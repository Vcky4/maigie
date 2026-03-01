import asyncio
import os
from google import genai
from google.genai import types


async def test():
    client = genai.Client()

    # Let's try passing history in different formats
    history1 = [{"role": "user", "parts": ["hi"]}]
    history2 = [{"role": "user", "parts": [{"text": "hi"}]}]
    history3 = [types.Content(role="user", parts=[types.Part.from_text("hi")])]

    print("Testing history1 (raw strings in parts list)")
    try:
        chat = client.aio.chats.create(model="gemini-2.5-flash", history=history1)
        r = await chat.send_message("reply back")
        print("Success! history1 works")
    except Exception as e:
        print("Error on history1:", repr(e))

    print("\nTesting history2 (dict parts)")
    try:
        chat = client.aio.chats.create(model="gemini-2.5-flash", history=history2)
        r = await chat.send_message("reply back")
        print("Success! history2 works")
    except Exception as e:
        print("Error on history2:", repr(e))

    print("\nTesting history3 (Pydantic objects)")
    try:
        chat = client.aio.chats.create(model="gemini-2.5-flash", history=history3)
        r = await chat.send_message("reply back")
        print("Success! history3 works")
    except Exception as e:
        print("Error on history3:", repr(e))


if __name__ == "__main__":
    asyncio.run(test())

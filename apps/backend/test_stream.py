import asyncio
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()


async def main():
    try:
        client = genai.Client()
        chat = client.aio.chats.create(model="gemini-2.5-flash")
        stream = chat._send_message_stream("Hello, tell me a short joke.")

        chunk_count = 0
        async for chunk in stream:
            chunk_count += 1
            text = chunk.text if hasattr(chunk, "text") else "NO_TEXT_ATTR"
            parts = getattr(chunk, "parts", "NO_PARTS_ATTR")
            print(f"[{chunk_count}] text={text!r}")
            if parts != "NO_PARTS_ATTR":
                for i, p in enumerate(parts):
                    print(f"  part {i}: {p}")
        print("Done streaming.")
    except Exception as e:
        import traceback

        traceback.print_exc()
        print("Error:", type(e), e)


asyncio.run(main())

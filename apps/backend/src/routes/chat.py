"""
Chat Routes & WebSocket Endpoint.
Handles real-time messaging with Gemini AI and Action Execution.
"""

import json
import re

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from jose import JWTError, jwt

from prisma import Prisma
from src.config import settings
from src.services.action_service import action_service
from src.services.llm_service import llm_service
from src.services.socket_manager import manager
from src.services.voice_service import voice_service

router = APIRouter()
db = Prisma()


async def get_current_user_ws(token: str = Query(...)):
    """
    Authenticate WebSocket connection via query param.
    ws://localhost:8001/api/v1/chat/ws?token=eyJ...
    """
    # Ensure DB is connected (WebSockets can sometimes race the main app startup)
    if not db.is_connected():
        await db.connect()

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            raise HTTPException(status_code=403, detail="Invalid token")

        user = await db.user.find_unique(where={"email": email})
        if not user:
            raise HTTPException(status_code=403, detail="User not found")

        return user
    except JWTError:
        raise HTTPException(status_code=403, detail="Could not validate credentials")


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, user: dict = Depends(get_current_user_ws)):
    """
    Main WebSocket endpoint for AI Chat.
    """
    # 1. Connect
    await manager.connect(websocket, user.id)

    # 2. Find or Create an active Chat Session
    session = await db.chatsession.find_first(
        where={"userId": user.id, "isActive": True}, order={"updatedAt": "desc"}
    )

    if not session:
        session = await db.chatsession.create(data={"userId": user.id, "title": "New Chat"})

    try:
        while True:
            # 3. Receive Message (Text)
            user_text = await websocket.receive_text()

            # 4. Save User Message to DB
            await db.chatmessage.create(
                data={
                    "sessionId": session.id,
                    "userId": user.id,
                    "role": "USER",
                    "content": user_text,
                }
            )

            # 5. Build History for Context (Last 10 messages)
            history_records = await db.chatmessage.find_many(
                where={"sessionId": session.id}, order={"createdAt": "asc"}, take=10
            )

            # Format history for Gemini
            formatted_history = []
            for msg in history_records:
                # Map DB roles to Gemini roles ('user' or 'model')
                role = "user" if msg.role == "USER" else "model"
                formatted_history.append({"role": role, "parts": [msg.content]})

            # 6. Get AI Response
            ai_response_text = await llm_service.get_chat_response(
                history=formatted_history, user_message=user_text
            )

            # --- NEW: Action Detection Logic ---
            # Regex to find content between <<<ACTION_START>>> and <<<ACTION_END>>>
            action_match = re.search(
                r"<<<ACTION_START>>>(.*?)<<<ACTION_END>>>", ai_response_text, re.DOTALL
            )

            clean_response = ai_response_text  # Default to full text
            action_result = None

            if action_match:
                try:
                    print(f"⚙️ Action Detected for User {user.id}")
                    # 1. Extract and Parse JSON
                    json_str = action_match.group(1).strip()
                    action_payload = json.loads(json_str)

                    # 2. Execute Action (Create Course)
                    action_result = await action_service.execute_action(
                        action_type=action_payload.get("type"),
                        action_data=action_payload.get("data"),
                        user_id=user.id,
                    )

                    # 3. Clean the response (remove the hidden JSON tags)
                    clean_response = ai_response_text.replace(action_match.group(0), "").strip()

                    # 4. Append a confirmation message if successful
                    if action_result and action_result.get("status") == "success":
                        clean_response += f"\n\n✅ **System:** {action_result['message']}"

                except Exception as e:
                    print(f"❌ Action Execution Error: {e}")
                    clean_response += (
                        "\n\n(⚠️ I tried to execute the action, but an error occurred.)"
                    )

            # 7. Save AI Message to DB (We save the CLEAN text)
            await db.chatmessage.create(
                data={
                    "sessionId": session.id,
                    "userId": user.id,
                    "role": "ASSISTANT",
                    "content": clean_response,
                }
            )

            # 8. Send text back to Client
            await manager.send_personal_message(clean_response, user.id)

            # 9. (Optional) If an action happened, send a separate event to refresh the Frontend
            if action_result:
                await manager.send_json({"type": "event", "payload": action_result}, user.id)

    except WebSocketDisconnect:
        manager.disconnect(user.id)
    except Exception as e:
        print(f"WS Error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass


@router.post("/voice")
async def handle_voice_upload(file: UploadFile = File(...), token: str = Query(...)):
    """
    Upload an audio file, transcribe it, and return the text.
    """
    # Validate User
    user = await get_current_user_ws(token)

    # Transcribe
    transcript = await voice_service.transcribe_audio(file)

    return {"text": transcript}

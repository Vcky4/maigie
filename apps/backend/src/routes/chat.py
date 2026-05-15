"""
Chat Routes & WebSocket Endpoint.
Handles real-time messaging with Gemini AI and Action Execution.
"""

import logging
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    File,
    Form,  # <--- Added
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from prisma import Prisma
from src.dependencies import CurrentUser
from src.routes.chat_helpers import _guess_image_media_type
from src.routes.chat_sessions import session_router
from src.routes.chat_ws import register_chat_websocket_routes
from src.services.llm_registry import LlmTask, default_model_for
from src.services.llm_service import llm_service
from src.services.storage_service import storage_service  # <--- Added
from src.services.usage_tracking_service import increment_feature_usage
from src.services.voice_service import voice_service
from src.utils.exceptions import SubscriptionLimitError

router = APIRouter()
db = Prisma()
logger = logging.getLogger(__name__)

get_current_user_ws = register_chat_websocket_routes(router, db)

router.include_router(session_router)


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


# 👇 ENDPOINT: Upload image(s) (for eager upload — supports multiple files)
@router.post("/image/upload", summary="Upload one or more images and return URLs")
async def upload_chat_image(
    files: list[UploadFile] = File(None),
    file: UploadFile = File(None),
    token: str = Query(...),
):
    """
    Upload one or more images to storage and return URLs.
    Accepts either `files` (multiple) or `file` (single, backward compat).
    Returns: { urls: [{url, filename}] }
    """
    # Validate user
    user = await get_current_user_ws(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # Collect all files (support both single and multiple)
    all_files: list[UploadFile] = []
    if files:
        all_files.extend(files)
    if file:
        all_files.append(file)

    if not all_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided.",
        )

    # Cap at 5 images per upload
    if len(all_files) > 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 5 images per upload.",
        )

    # Validate all image types
    for f in all_files:
        if f.content_type not in ["image/jpeg", "image/png", "image/webp"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only JPEG, PNG, or WebP images are allowed. Got: {f.content_type}",
            )

    try:
        # Check file upload limit for FREE tier users (count all files)
        from src.core.database import db

        user_obj = await db.user.find_unique(where={"id": user.id})
        if user_obj:
            for _ in all_files:
                await increment_feature_usage(user_obj, "file_uploads", db_client=db)

        # Upload each file to BunnyCDN
        results = []
        for f in all_files:
            upload_result = await storage_service.upload_file(f, path="chat-images")
            results.append({"url": upload_result["url"], "filename": upload_result["filename"]})
            print(f"🔵 Image pre-uploaded: {upload_result['url']}")

        # Backward compat: if single file, also return top-level url/filename
        response = {"urls": results}
        if len(results) == 1:
            response["url"] = results[0]["url"]
            response["filename"] = results[0]["filename"]

        return response

    except SubscriptionLimitError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)
    except Exception as e:
        print(f"❌ Error in /chat/image/upload: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/image/file", summary="Download a chat upload from Bunny storage (Bearer auth)")
async def get_chat_image_file(
    _user: CurrentUser,
    path: str = Query(..., min_length=12, max_length=512),
):
    """
    Serves files under chat-images/ via the storage API so the browser can show uploads
    when the public CDN hostname has certificate or TLS issues.
    """
    normalized = path.strip().lstrip("/")
    if ".." in normalized or not normalized.startswith("chat-images/"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid path")

    fetched = await storage_service.fetch_object_bytes(normalized)
    if not fetched:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    data, raw_ct = fetched
    media_type = _guess_image_media_type(normalized, raw_ct)
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


# 👇 ENDPOINT: Delete uploaded image (if user cancels)
@router.delete("/image/delete", summary="Delete an uploaded image")
async def delete_chat_image(
    url: str = Query(...),
    token: str = Query(...),
):
    """
    Delete a previously uploaded image from storage.
    Used when user removes an image before sending.
    """
    # Validate user
    user = await get_current_user_ws(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        success = await storage_service.delete_file(url)
        if success:
            print(f"🗑️ Image deleted: {url}")
            return {"status": "deleted"}
        else:
            return {"status": "not_found"}

    except Exception as e:
        print(f"❌ Error in /chat/image/delete: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# 👇 LEGACY ENDPOINT: Image Analysis Chat (upload + analyze in one call)
# NOTE: Deprecated - prefer using eager upload (/image/upload) + WebSocket with fileUrls context
@router.post("/image", summary="Upload an image and get AI analysis")
async def handle_image_chat(
    file: UploadFile = File(...),
    text: str = Form(default="Here"),
    token: str = Form(...),
):
    """
    Handles multimodal chat:
    1. Uploads image to Storage (BunnyCDN)
    2. Saves USER message with image URL to DB
    3. Sends Image + Text to Gemini AI via llm_service
    4. Saves ASSISTANT response to DB
    """
    # Validate user from token
    user = await get_current_user_ws(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # 1. Validate Image Type
    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG, or WebP images are allowed.",
        )

    # Ensure DB is connected
    if not db.is_connected():
        await db.connect()

    try:
        # Find or create an active chat session
        session = await db.chatsession.find_first(
            where={"userId": user.id, "isActive": True}, order={"updatedAt": "desc"}
        )
        if not session:
            session = await db.chatsession.create(data={"userId": user.id, "title": "New Chat"})

        # 2. Upload to BunnyCDN
        upload_result = await storage_service.upload_file(file, path="chat-images")
        image_url = upload_result["url"]

        print(f"🔵 Image uploaded: {image_url}")

        # 3. Save User Message (with Image URL)
        user_message = await db.chatmessage.create(
            data={
                "sessionId": session.id,
                "userId": user.id,
                "role": "USER",
                "content": text,
                "imageUrl": image_url,
                "modelName": "user-upload",
            }
        )

        # Bump session updatedAt to move it to the top of history (Interaction based)
        await db.chatsession.update(where={"id": session.id}, data={"updatedAt": datetime.now(UTC)})

        # 4. Get AI Analysis (Gemini Vision)
        print("🔵 Asking Gemini to analyze...")
        ai_response_text = await llm_service.analyze_image(text, image_url)

        # 5. Save AI Response
        ai_message = await db.chatmessage.create(
            data={
                "sessionId": session.id,
                "userId": user.id,
                "role": "ASSISTANT",
                "content": ai_response_text,
                "modelName": default_model_for(LlmTask.CHAT_DEFAULT),
            }
        )

        return {"status": "success", "user_message": user_message, "ai_message": ai_message}

    except Exception as e:
        print(f"❌ Error in /chat/image: {e}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

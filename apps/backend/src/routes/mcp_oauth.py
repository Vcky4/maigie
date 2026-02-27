import logging
import secrets
import base64
import hashlib
from datetime import datetime, timedelta, UTC
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Form, status
from pydantic import BaseModel

from src.core.database import db
from src.dependencies import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MCP OAuth2.1"])

# ==========================================
#  MODELS
# ==========================================


class DynamicClientRegistrationRequest(BaseModel):
    redirect_uris: list[str]
    client_name: str | None = None
    client_uri: str | None = None
    logo_uri: str | None = None
    token_endpoint_auth_method: str | None = "none"
    grant_types: list[str] | None = ["authorization_code", "refresh_token"]
    response_types: list[str] | None = ["code"]


# ==========================================
#  OAUTH DISCOVERY
# ==========================================


@router.get("/.well-known/oauth-authorization-server")
async def oauth_discovery(request: Request):
    """
    OAuth 2.1 Authorization Server Metadata
    Required for ChatGPT Dynamic Client Registration
    """
    # Use request.base_url or configured domain
    base_url = str(request.base_url).rstrip("/")
    if "api/v1" in base_url:
        base_url = base_url.split("/api/v1")[0]

    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/api/v1/mcp/oauth/authorize",
        "token_endpoint": f"{base_url}/api/v1/mcp/oauth/token",
        "registration_endpoint": f"{base_url}/api/v1/mcp/oauth/register",
        "scopes_supported": ["tools"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": [
            "none",
            "client_secret_post",
            "client_secret_basic",
        ],
        "code_challenge_methods_supported": ["S256"],
    }


# ==========================================
#  DYNAMIC CLIENT REGISTRATION
# ==========================================


@router.post("/oauth/register")
async def register_client(request: DynamicClientRegistrationRequest):
    """
    Dynamic Client Registration Endpoint
    Allows ChatGPT to register its redirect URIs and get a client_id.
    """
    logger.info(f"Registering new OAuth Client: {request.client_name}")

    # Generate unique client ID (and secret if needed, though 'none' shouldn't use it)
    client_id = f"maigie-client-{secrets.token_urlsafe(16)}"

    auth_method = request.token_endpoint_auth_method or "none"
    client_secret = secrets.token_urlsafe(32) if auth_method != "none" else None

    try:
        new_client = await db.oauthclient.create(
            data={
                "clientId": client_id,
                "clientSecret": client_secret,
                "clientName": request.client_name or "Unknown Client",
                "redirectUris": request.redirect_uris,
                "clientUri": request.client_uri,
                "logoUri": request.logo_uri,
                "tokenEndpointAuthMethod": auth_method,
                "grantTypes": request.grant_types or ["authorization_code", "refresh_token"],
                "responseTypes": request.response_types or ["code"],
            }
        )

        response = {
            "client_id": new_client.clientId,
            "client_name": new_client.clientName,
            "redirect_uris": new_client.redirectUris,
            "token_endpoint_auth_method": new_client.tokenEndpointAuthMethod,
            "grant_types": new_client.grantTypes,
            "response_types": new_client.responseTypes,
        }
        if client_secret:
            response["client_secret"] = new_client.clientSecret

        return response
    except Exception as e:
        logger.error(f"Error registering client: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to register client")


# ==========================================
#  AUTHORIZATION ENDPOINT
# ==========================================


class AuthorizeDecisionRequest(BaseModel):
    client_id: str
    redirect_uri: str
    state: str
    code_challenge: str
    code_challenge_method: str
    approved: bool


@router.post("/oauth/authorize/decision")
async def authorize_decision(decision: AuthorizeDecisionRequest, current_user: CurrentUser):
    """
    Called by the Frontend Consent Screen after the user approves access.
    Generates the short-lived authorization code.
    """
    if not decision.approved:
        raise HTTPException(status_code=400, detail="Access denied by user")

    # 1. Validate Client
    client = await db.oauthclient.find_unique(where={"clientId": decision.client_id})
    if not client:
        raise HTTPException(status_code=400, detail="Unknown client_id")

    if decision.redirect_uri not in client.redirectUris:
        logger.error(f"Invalid redirect URI: {decision.redirect_uri}")
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")

    # 2. Generate Auth Code (5 minutes expiry)
    auth_code = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(minutes=5)

    try:
        await db.oauthcode.create(
            data={
                "code": auth_code,
                "clientId": client.clientId,
                "userId": current_user.id,
                "redirectUri": decision.redirect_uri,
                "codeChallenge": decision.code_challenge,
                "codeChallengeMethod": decision.code_challenge_method,
                "expiresAt": expires_at,
            }
        )

        # 3. Return the Redirect URL for the frontend to navigate the user back to ChatGPT
        return {"redirect_to": f"{decision.redirect_uri}?code={auth_code}&state={decision.state}"}
    except Exception as e:
        logger.error(f"Error creating auth code: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ==========================================
#  TOKEN ENDPOINT
# ==========================================


@router.post("/oauth/token")
async def token_endpoint(
    request: Request,
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    redirect_uri: Annotated[str | None, Form()] = None,
    code: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    refresh_token: Annotated[str | None, Form()] = None,
):
    """
    Exchanges an authorization code (or refresh token) for an access token.
    Implements PKCE validation.
    """
    client = await db.oauthclient.find_unique(where={"clientId": client_id})
    if not client:
        raise HTTPException(status_code=401, detail="Invalid client")

    if grant_type == "authorization_code":
        if not code or not redirect_uri or not code_verifier:
            raise HTTPException(
                status_code=400, detail="Missing required parameters for authorization_code grant"
            )

        # 1. Look up the code
        auth_code = await db.oauthcode.find_unique(where={"code": code})
        if not auth_code:
            raise HTTPException(status_code=400, detail="Invalid or expired authorization code")

        # 2. Validate Code rules
        if auth_code.isUsed:
            # Code was already used. In a strict OAuth implementation, you might revoke all tokens associated with it.
            raise HTTPException(status_code=400, detail="Authorization code already used")

        if auth_code.clientId != client_id or auth_code.redirectUri != redirect_uri:
            raise HTTPException(status_code=400, detail="Client ID or Redirect URI mismatch")

        if auth_code.expiresAt.replace(tzinfo=UTC) < datetime.now(UTC):
            raise HTTPException(status_code=400, detail="Authorization code expired")

        # 3. Validate PKCE
        if auth_code.codeChallenge and auth_code.codeChallengeMethod == "S256":
            # Hash the provided verifier and compare to stored challenge
            hashed = hashlib.sha256(code_verifier.encode()).digest()
            challenge = base64.urlsafe_b64encode(hashed).decode().rstrip("=")

            if challenge != auth_code.codeChallenge:
                logger.error(
                    f"PKCE Challenge failed. Expected {auth_code.codeChallenge}, got {challenge}"
                )
                raise HTTPException(status_code=400, detail="Invalid PKCE code_verifier")

        # 4. Mark code as used
        await db.oauthcode.update(where={"id": auth_code.id}, data={"isUsed": True})

        # 5. Issue Tokens
        access_token = secrets.token_hex(32)
        new_refresh_token = secrets.token_hex(32)

        # Tokens valid for a long time (e.g. 30 days) or however suitable for GPT Apps
        access_expires = datetime.now(UTC) + timedelta(days=30)
        refresh_expires = datetime.now(UTC) + timedelta(days=90)

        await db.oauthtoken.create(
            data={
                "accessToken": access_token,
                "refreshToken": new_refresh_token,
                "clientId": client_id,
                "userId": auth_code.userId,
                "accessTokenExpiresAt": access_expires,
                "refreshTokenExpiresAt": refresh_expires,
            }
        )

        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 30 * 24 * 60 * 60,  # 30 days in seconds
            "refresh_token": new_refresh_token,
        }

    elif grant_type == "refresh_token":
        if not refresh_token:
            raise HTTPException(status_code=400, detail="Missing refresh_token")

        token_record = await db.oauthtoken.find_unique(where={"refreshToken": refresh_token})
        if not token_record:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        if token_record.clientId != client_id:
            raise HTTPException(status_code=401, detail="Client ID mismatch")

        if token_record.refreshTokenExpiresAt and token_record.refreshTokenExpiresAt.replace(
            tzinfo=UTC
        ) < datetime.now(UTC):
            raise HTTPException(status_code=401, detail="Refresh token expired")

        # Rotate tokens (invalidate old, issue new)
        await db.oauthtoken.delete(where={"id": token_record.id})

        new_access = secrets.token_hex(32)
        new_refresh = secrets.token_hex(32)
        access_expires = datetime.now(UTC) + timedelta(days=30)
        refresh_expires = datetime.now(UTC) + timedelta(days=90)

        await db.oauthtoken.create(
            data={
                "accessToken": new_access,
                "refreshToken": new_refresh,
                "clientId": client_id,
                "userId": token_record.userId,
                "accessTokenExpiresAt": access_expires,
                "refreshTokenExpiresAt": refresh_expires,
            }
        )

        return {
            "access_token": new_access,
            "token_type": "Bearer",
            "expires_in": 30 * 24 * 60 * 60,
            "refresh_token": new_refresh,
        }

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported grant type: {grant_type}")

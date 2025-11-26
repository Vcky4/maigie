"""
Authentication routes (JWT Signup/Login + OAuth Placeholders).

Copyright (C) 2024 Maigie Team
"""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr  # <--- ADDED THIS IMPORT

from src.config import settings
from src.core.database import db
from src.core.security import (
    create_access_token,
    get_password_hash,
    verify_password,
)
from src.dependencies import CurrentUser
from src.models.auth import (
    Token,
    UserLogin,
    UserResponse,
    UserSignup,
)

router = APIRouter()

# ==========================================
#  JWT AUTHENTICATION (Your Task)
# ==========================================

@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(user_data: UserSignup):
    """
    Register a new user account.
    """
    # 1. Check if email already exists
    existing_user = await db.user.find_unique(where={"email": user_data.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # 2. Hash the password
    hashed_password = get_password_hash(user_data.password)

    # 3. Create user and default preferences
    new_user = await db.user.create(
        data={
            "email": user_data.email,
            "passwordHash": hashed_password,
            "name": user_data.name,
            "provider": "email",
            "preferences": {
                "create": {
                    "theme": "light",
                    "language": "en",
                    "notifications": True,
                }
            },
        },
        include={"preferences": True},
    )

    return new_user


@router.post("/login", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
):
    """
    OAuth2 compatible token login (Swagger UI).
    """
    user = await db.user.find_unique(where={"email": form_data.username})

    if not user or not user.passwordHash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(form_data.password, user.passwordHash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )

    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/login/json", response_model=Token)
async def login_json(user_data: UserLogin):
    """
    Standard JSON login endpoint for frontend apps.
    """
    user = await db.user.find_unique(where={"email": user_data.email})

    if not user or not user.passwordHash or not verify_password(user_data.password, user.passwordHash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )

    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def read_users_me(current_user: CurrentUser):
    """
    Get current user information.
    """
    return current_user


# ==========================================
#  OAUTH AUTHENTICATION (Teammate Task)
# ==========================================

@router.get("/oauth/providers")
async def get_oauth_providers():
    """
    TODO: [Teammate Name] Implement listing available providers.
    """
    return {"providers": ["google", "github"]}


@router.get("/oauth/{provider}/authorize")
async def oauth_authorize(provider: str, request: Request):
    """
    TODO: [Teammate Name] Initiate OAuth flow.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="OAuth implementation pending",
    )


@router.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str, code: str, state: str, request: Request):
    """
    TODO: [Teammate Name] Handle OAuth callback.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="OAuth callback pending",
    )


# ==========================================
#  SESSION & PASSWORD MANAGEMENT
# ==========================================

@router.post("/logout")
async def logout():
    """
    End user session.
    """
    return {"message": "Successfully logged out"}

# --- Password & Email Management (Stubs) ---

class PasswordResetRequest(BaseModel):
    email: EmailStr

class EmailConfirmation(BaseModel):
    token: str

@router.post("/reset-password")
async def reset_password(request: PasswordResetRequest):
    """
    Request a password reset email.
    """
    # 1. Check if user exists
    user = await db.user.find_unique(where={"email": request.email})
    if user:
        # TODO: Generate reset token and send email via SendGrid/AWS
        print(f" MOCK EMAIL: Sending password reset link to {request.email}")
    
    # Always return 200 to prevent email enumeration attacks
    return {"message": "If an account exists, a reset email has been sent."}

@router.post("/confirm-email")
async def confirm_email(data: EmailConfirmation):
    """
    Verify email address using a token.
    """
    # TODO: Validate token and update user.isActive = True
    return {"message": "Email successfully confirmed"}
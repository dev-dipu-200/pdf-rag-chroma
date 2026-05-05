from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models import User
from app.schemas import AuthRequest, AuthResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse)
async def register(payload: AuthRequest, db: AsyncSession = Depends(get_db)):
    username = payload.username.strip().lower()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters.")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    result = await db.execute(select(User).filter(User.username == username))
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already exists.")

    count_result = await db.execute(select(func.count(User.id)))
    has_any_user = count_result.scalar() > 0
    role = "user" if has_any_user else "admin"
    user = User(username=username, password_hash=hash_password(payload.password), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return AuthResponse(
        access_token=create_access_token(user),
        user=UserResponse(id=user.id, username=user.username, role=user.role, created_at=user.created_at),
    )


@router.post("/login", response_model=AuthResponse)
async def login(payload: AuthRequest, db: AsyncSession = Depends(get_db)):
    username = payload.username.strip().lower()
    result = await db.execute(select(User).filter(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    return AuthResponse(
        access_token=create_access_token(user),
        user=UserResponse(id=user.id, username=user.username, role=user.role, created_at=user.created_at),
    )


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    del current_user
    return {"status": "logged_out"}


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role,
        created_at=current_user.created_at,
    )

import asyncio

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


async def _username_exists(db: AsyncSession, username: str):
    result = await db.execute(select(User).filter(User.username == username))
    return result.scalar_one_or_none()


async def _user_count(db: AsyncSession) -> int:
    count_result = await db.execute(select(func.count(User.id)))
    return count_result.scalar() or 0


async def _create_user(
    db: AsyncSession, username: str, password: str, role: str
) -> User:
    password_hash = await asyncio.to_thread(hash_password, password)
    user = User(username=username, password_hash=password_hash, role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/register", response_model=AuthResponse)
async def register(payload: AuthRequest, db: AsyncSession = Depends(get_db)):
    username = payload.username.strip().lower()
    if len(username) < 3:
        raise HTTPException(
            status_code=400, detail="Username must be at least 3 characters."
        )
    if len(payload.password) < 6:
        raise HTTPException(
            status_code=400, detail="Password must be at least 6 characters."
        )

    existing = await _username_exists(db, username)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already exists.")

    total_users = await _user_count(db)
    if total_users > 0:
        raise HTTPException(
            status_code=403,
            detail="Direct API registration is restricted to admins after initial setup.",
        )

    user = await _create_user(db, username, payload.password, "admin")

    return AuthResponse(
        access_token=create_access_token(user),
        user=UserResponse(
            id=user.id,
            username=user.username,
            role=user.role,
            created_at=user.created_at,
        ),
    )


@router.post("/register-ui", response_model=AuthResponse)
async def register_ui(payload: AuthRequest, db: AsyncSession = Depends(get_db)):
    username = payload.username.strip().lower()
    if len(username) < 3:
        raise HTTPException(
            status_code=400, detail="Username must be at least 3 characters."
        )
    if len(payload.password) < 6:
        raise HTTPException(
            status_code=400, detail="Password must be at least 6 characters."
        )

    existing = await _username_exists(db, username)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already exists.")

    total_users = await _user_count(db)
    if total_users == 0:
        raise HTTPException(
            status_code=403,
            detail="Create the initial admin account through the backend API first.",
        )

    user = await _create_user(db, username, payload.password, "user")
    return AuthResponse(
        access_token=create_access_token(user),
        user=UserResponse(
            id=user.id,
            username=user.username,
            role=user.role,
            created_at=user.created_at,
        ),
    )


@router.post("/register-admin", response_model=AuthResponse)
async def register_admin(payload: AuthRequest, db: AsyncSession = Depends(get_db)):
    username = payload.username.strip().lower()
    if len(username) < 3:
        raise HTTPException(
            status_code=400, detail="Username must be at least 3 characters."
        )
    if len(payload.password) < 6:
        raise HTTPException(
            status_code=400, detail="Password must be at least 6 characters."
        )

    existing = await _username_exists(db, username)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already exists.")

    user = await _create_user(db, username, payload.password, "admin")
    return AuthResponse(
        access_token=create_access_token(user),
        user=UserResponse(
            id=user.id,
            username=user.username,
            role=user.role,
            created_at=user.created_at,
        ),
    )


@router.post("/login", response_model=AuthResponse)
async def login(payload: AuthRequest, db: AsyncSession = Depends(get_db)):
    username = payload.username.strip().lower()
    result = await db.execute(select(User).filter(User.username == username))
    user = result.scalar_one_or_none()
    password_valid = False
    if user is not None:
        password_valid = await asyncio.to_thread(
            verify_password,
            payload.password,
            user.password_hash,
        )
    if user is None or not password_valid:
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    return AuthResponse(
        access_token=create_access_token(user),
        user=UserResponse(
            id=user.id,
            username=user.username,
            role=user.role,
            created_at=user.created_at,
        ),
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

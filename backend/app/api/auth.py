"""Unified sign-in for every role, passenger self-registration and the forgot-password flow."""
from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import current_user, get_engine
from app.engine import TransitEngine
from app.models import EmailIn, Identity, LoginIn, PasswordResetIn, RegisterIn, SessionInfo
from app.services.auth import HOME_PAGES, SESSION_COOKIE, SESSION_TTL

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _session_info(identity: Identity) -> SessionInfo:
    return SessionInfo(**identity.model_dump(), home=HOME_PAGES[identity.role])


def _set_cookie(response: Response, engine: TransitEngine, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=int(SESSION_TTL.total_seconds()),
        httponly=True, samesite="lax", secure=engine.settings.secure_cookies, path="/",
    )


@router.post("/login", response_model=SessionInfo)
def login(body: LoginIn, response: Response, engine: TransitEngine = Depends(get_engine)):
    """One login for passengers, operators and the master admin. ``home`` tells the
    client which dashboard to open for the user's role."""
    identity, token = engine.auth.login(body.email, body.password)
    _set_cookie(response, engine, token)
    return _session_info(identity)


@router.post("/register", response_model=SessionInfo, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, response: Response, engine: TransitEngine = Depends(get_engine)):
    """Self-registration always creates a passenger; other roles are assigned by the master admin."""
    identity, token = engine.auth.register_passenger(body.email, body.password)
    _set_cookie(response, engine, token)
    return _session_info(identity)


@router.get("/me", response_model=SessionInfo)
def me(identity: Identity = Depends(current_user)):
    return _session_info(identity)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, engine: TransitEngine = Depends(get_engine)):
    engine.auth.logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(body: EmailIn, engine: TransitEngine = Depends(get_engine)):
    """E-mail a 6-digit verification code. The response is identical whether or not
    the account exists, so the endpoint cannot be used to discover accounts."""
    await engine.auth.request_password_reset(body.email)
    return {"detail": "If an account exists for this e-mail, a verification code has been sent."}


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(body: PasswordResetIn, engine: TransitEngine = Depends(get_engine)):
    """Set a new password with the e-mailed code (15 min validity, 5 attempts)."""
    engine.auth.reset_password(body.email, body.code, body.new_password)

"""Master admin panel: registered users and role assignment."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_engine, require_admin
from app.engine import TransitEngine
from app.models import Identity, Role, RoleUpdateIn, UserOut

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/users", response_model=list[UserOut])
def list_users(q: Optional[str] = Query(None, max_length=254), role: Optional[Role] = Query(None),
               limit: int = Query(200, ge=1, le=1000), engine: TransitEngine = Depends(get_engine)):
    return engine.repo.list_users(q, role, limit)


@router.patch("/users/{user_id}", response_model=UserOut)
def set_role(user_id: int, body: RoleUpdateIn, admin: Identity = Depends(require_admin),
             engine: TransitEngine = Depends(get_engine)):
    """Assign the passenger or operator role. The user is signed out so their next
    login opens the dashboard of the new role."""
    return engine.auth.set_role(admin, user_id, body.role)

"""
Customer address management — part of the USERS section.

URL mapping (API_CONTRACT.md → USERS):
  GET    /customers/me/addresses                       → list_addresses
  POST   /customers/me/addresses                       → create_address
  PATCH  /customers/me/addresses/{addressId}           → update_address
  DELETE /customers/me/addresses/{addressId}           → delete_address
  POST   /customers/me/addresses/{addressId}/default   → set_default_address
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_customer, get_db
from app.models.auth.user import UserModel
from app.schemas.customer.address import AddressCreate, AddressResponse, AddressUpdate
from app.services.customer.address_service import AddressService

router = APIRouter(tags=["Users — Customer Addresses"])


# ──────────────────────────────────────────────────────────────────────────────
# GET /customers/me/addresses
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/customers/me/addresses",
    response_model=list[AddressResponse],
    summary="List customer addresses",
    description=(
        "Returns all saved delivery addresses for the authenticated customer. "
        "Default address appears first. Authorization: Customer session."
    ),
)
async def list_addresses(
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = AddressService(db)
    addresses = await service.list_addresses(current_user.id)
    return [AddressResponse.model_validate(a) for a in addresses]


# ──────────────────────────────────────────────────────────────────────────────
# POST /customers/me/addresses
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/customers/me/addresses",
    response_model=AddressResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new delivery address",
    description=(
        "Body: `{ fullName, phone, addressLine, landmark?, city, state, "
        "pincode, type, isDefault? }`.  \n"
        "Setting `isDefault: true` demotes the previous default. "
        "The first address created is automatically marked as default. "
        "Authorization: Customer session."
    ),
)
async def create_address(
    data: AddressCreate,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = AddressService(db)
    address = await service.create_address(current_user.id, data)
    return AddressResponse.model_validate(address)


# ──────────────────────────────────────────────────────────────────────────────
# PATCH /customers/me/addresses/{addressId}
# ──────────────────────────────────────────────────────────────────────────────

@router.patch(
    "/customers/me/addresses/{address_id}",
    response_model=AddressResponse,
    summary="Update a saved address",
    description=(
        "Partial update — only provided fields are changed. "
        "Authorization: Customer session (must own the address)."
    ),
)
async def update_address(
    address_id: str,
    data: AddressUpdate,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = AddressService(db)
    address = await service.update_address(current_user.id, address_id, data)
    return AddressResponse.model_validate(address)


# ──────────────────────────────────────────────────────────────────────────────
# DELETE /customers/me/addresses/{addressId}
# ──────────────────────────────────────────────────────────────────────────────

@router.delete(
    "/customers/me/addresses/{address_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved address",
    description="Authorization: Customer session (must own the address).",
)
async def delete_address(
    address_id: str,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = AddressService(db)
    await service.delete_address(current_user.id, address_id)


# ──────────────────────────────────────────────────────────────────────────────
# POST /customers/me/addresses/{addressId}/default
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/customers/me/addresses/{address_id}/default",
    response_model=AddressResponse,
    summary="Set an address as the default",
    description=(
        "Promotes the specified address to default and demotes the previous default. "
        "Authorization: Customer session (must own the address)."
    ),
)
async def set_default_address(
    address_id: str,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = AddressService(db)
    address = await service.set_default_address(current_user.id, address_id)
    return AddressResponse.model_validate(address)

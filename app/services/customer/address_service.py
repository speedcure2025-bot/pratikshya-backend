from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ForbiddenException, NotFoundException
from app.models.auth.user import UserModel
from app.models.customer.address import AddressModel
from app.models.customer.customer import CustomerProfileModel
from app.schemas.customer.address import AddressCreate, AddressUpdate


class AddressService:
    """CRUD for customer delivery addresses."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_profile(self, user_id: str) -> CustomerProfileModel:
        stmt = (
            select(UserModel)
            .where(UserModel.id == user_id)
            .options(
                selectinload(UserModel.customer_profile)
                .selectinload(CustomerProfileModel.addresses)
            )
        )
        res = await self.db.execute(stmt)
        user = res.scalars().first()
        if not user or not user.customer_profile:
            raise NotFoundException("Customer profile not found.")
        return user.customer_profile

    async def _get_address_owned_by(self, address_id: str, profile: CustomerProfileModel) -> AddressModel:
        for addr in profile.addresses:
            if addr.id == address_id:
                return addr
        # Re-query directly to be safe
        stmt = select(AddressModel).where(
            AddressModel.id == address_id,
            AddressModel.customer_id == profile.id,
        )
        res = await self.db.execute(stmt)
        addr = res.scalars().first()
        if not addr:
            raise NotFoundException("Address not found.")
        return addr

    async def _demote_existing_default(self, profile: CustomerProfileModel) -> None:
        """Clear the current default address before setting a new one."""
        stmt = select(AddressModel).where(
            AddressModel.customer_id == profile.id,
            AddressModel.is_default == True,
        )
        res = await self.db.execute(stmt)
        for addr in res.scalars().all():
            addr.is_default = False

    # ------------------------------------------------------------------
    # GET /customers/me/addresses
    # ------------------------------------------------------------------

    async def list_addresses(self, user_id: str) -> List[AddressModel]:
        profile = await self._get_profile(user_id)
        # Sort: default first, then by creation time
        return sorted(profile.addresses, key=lambda a: (not a.is_default, a.created_at))

    # ------------------------------------------------------------------
    # POST /customers/me/addresses
    # ------------------------------------------------------------------

    async def create_address(self, user_id: str, data: AddressCreate) -> AddressModel:
        profile = await self._get_profile(user_id)

        if data.is_default:
            await self._demote_existing_default(profile)

        # If this is the first address, make it default automatically
        if not profile.addresses:
            is_default = True
        else:
            is_default = data.is_default

        address = AddressModel(
            customer_id=profile.id,
            full_name=data.full_name,
            phone=data.phone,
            address_line=data.address_line,
            landmark=data.landmark,
            city=data.city,
            state=data.state,
            pincode=data.pincode,
            address_type=data.address_type,
            is_default=is_default,
        )
        self.db.add(address)
        await self.db.commit()
        await self.db.refresh(address)
        return address

    # ------------------------------------------------------------------
    # PATCH /customers/me/addresses/{addressId}
    # ------------------------------------------------------------------

    async def update_address(
        self, user_id: str, address_id: str, data: AddressUpdate
    ) -> AddressModel:
        profile = await self._get_profile(user_id)
        address = await self._get_address_owned_by(address_id, profile)

        if data.is_default is True and not address.is_default:
            await self._demote_existing_default(profile)
            address.is_default = True
        elif data.is_default is not None:
            address.is_default = data.is_default

        if data.full_name is not None:
            address.full_name = data.full_name
        if data.phone is not None:
            address.phone = data.phone
        if data.address_line is not None:
            address.address_line = data.address_line
        if data.landmark is not None:
            address.landmark = data.landmark
        if data.city is not None:
            address.city = data.city
        if data.state is not None:
            address.state = data.state
        if data.pincode is not None:
            address.pincode = data.pincode
        if data.address_type is not None:
            address.address_type = data.address_type

        await self.db.commit()
        await self.db.refresh(address)
        return address

    # ------------------------------------------------------------------
    # DELETE /customers/me/addresses/{addressId}
    # ------------------------------------------------------------------

    async def delete_address(self, user_id: str, address_id: str) -> None:
        profile = await self._get_profile(user_id)
        address = await self._get_address_owned_by(address_id, profile)
        await self.db.delete(address)
        await self.db.commit()

    # ------------------------------------------------------------------
    # POST /customers/me/addresses/{addressId}/default
    # ------------------------------------------------------------------

    async def set_default_address(self, user_id: str, address_id: str) -> AddressModel:
        profile = await self._get_profile(user_id)
        address = await self._get_address_owned_by(address_id, profile)

        await self._demote_existing_default(profile)
        address.is_default = True

        await self.db.commit()
        await self.db.refresh(address)
        return address

from app.models.base import Base


class VerificationTokenModel(Base):
    """Database model for VerificationToken."""
    __tablename__ = "auth_verification_token"

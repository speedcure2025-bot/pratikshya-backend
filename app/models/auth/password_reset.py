from app.models.base import Base


class PasswordResetModel(Base):
    """Database model for PasswordReset."""
    __tablename__ = "auth_password_reset"

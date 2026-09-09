from app.models.base import Base


class PaymentModel(Base):
    """Database model for Payment."""
    __tablename__ = "checkout_payment"

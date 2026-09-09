from app.models.base import Base


class PaymentTransactionModel(Base):
    """Database model for PaymentTransaction."""
    __tablename__ = "checkout_payment_transaction"

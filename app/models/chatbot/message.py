from app.models.base import Base


class MessageModel(Base):
    """Database model for Message."""
    __tablename__ = "chatbot_message"

class ChatService:
    """Business logic service for ChatService."""
    def __init__(self, db_session):
        self.db = db_session

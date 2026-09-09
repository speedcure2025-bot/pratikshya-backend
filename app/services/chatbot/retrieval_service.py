class RetrievalService:
    """Business logic service for RetrievalService."""
    def __init__(self, db_session):
        self.db = db_session

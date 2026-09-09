class TransferService:
    """Business logic service for TransferService."""
    def __init__(self, db_session):
        self.db = db_session

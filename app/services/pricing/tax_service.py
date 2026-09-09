class TaxService:
    """Business logic service for TaxService."""
    def __init__(self, db_session):
        self.db = db_session

class ReservationService:
    """Business logic service for ReservationService."""
    def __init__(self, db_session):
        self.db = db_session

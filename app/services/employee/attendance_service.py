class AttendanceService:
    """Business logic service for AttendanceService."""
    def __init__(self, db_session):
        self.db = db_session

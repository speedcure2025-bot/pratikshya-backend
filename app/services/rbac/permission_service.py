class PermissionService:
    """Business logic service for PermissionService."""
    def __init__(self, db_session):
        self.db = db_session

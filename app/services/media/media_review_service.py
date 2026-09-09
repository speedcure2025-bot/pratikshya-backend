class MediaReviewService:
    """Business logic service for MediaReviewService."""
    def __init__(self, db_session):
        self.db = db_session

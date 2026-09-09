from pydantic import BaseModel, Field


class GoogleOAuthRequest(BaseModel):
    """Request body for Google OAuth login.

    The frontend should use the Google Identity SDK (Sign-In button / One Tap) to
    obtain an ID token, then POST it here. The backend verifies the token with Google
    and issues our own JWT pair.
    """

    id_token: str = Field(
        ...,
        description="Google ID token obtained from the frontend SDK (google.accounts.id / gapi.auth2)",
    )


class FacebookOAuthRequest(BaseModel):
    """Request body for Facebook OAuth login.

    The frontend should use the Facebook Login SDK to obtain a user access token,
    then POST it here. The backend calls the Graph API to verify and retrieve user info.
    """

    access_token: str = Field(
        ...,
        description="Facebook user access token obtained from FB.login() in the frontend SDK",
    )

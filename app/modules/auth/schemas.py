from pydantic import BaseModel, EmailStr, Field


class AuthUser(BaseModel):
    id: str
    email: str
    name: str | None = None
    company: str | None = None
    role: str | None = None
    user_type: str = "free"
    credits_remaining: int = 0
    plan: str = "free"


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str | None = None
    company: str | None = None
    role: str | None = None


class SignInRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AuthUser


class ResetPasswordRequest(BaseModel):
    email: EmailStr


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class UpdatePasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)


class ConfirmResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class RefreshTokenRequest(BaseModel):
    # Optional: cookie-based clients send no body and supply the refresh token via
    # the httpOnly refresh cookie instead.
    refresh_token: str | None = None


class GoogleAuthRequest(BaseModel):
    id_token: str


class VerifyEmailRequest(BaseModel):
    token: str


class SignUpResponse(BaseModel):
    """Returned when Supabase requires email confirmation before issuing a session."""
    verification_required: bool = True
    email: str

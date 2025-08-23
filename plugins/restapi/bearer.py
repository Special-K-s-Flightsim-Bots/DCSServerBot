from fastapi import HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials


class APIKeyBearer(HTTPBearer):
    def __init__(self, api_key: str, auto_error: bool = True):
        super().__init__(auto_error=auto_error)
        self.api_key = api_key

    async def __call__(self, request: Request):
        credentials: HTTPAuthorizationCredentials = await super().__call__(request)
        if not credentials:
            self.raise_http_exception("Invalid authorization code.")
        self.check_credentials_scheme(credentials.scheme)
        if credentials.credentials != str(self.api_key):
            self.raise_http_exception("Invalid API key or expired token.")
        return credentials.credentials

    def check_credentials_scheme(self, scheme):
        if scheme != "Bearer":
            self.raise_http_exception("Invalid authentication scheme.")

    def raise_http_exception(self, detail: str, status_code: int = 403):
        raise HTTPException(status_code=status_code, detail=detail)

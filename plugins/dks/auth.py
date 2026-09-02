import asyncio
import jwt

from core import get_translation
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import DKS

_ = get_translation(__name__.split('.')[1])


class TokenBearer(HTTPBearer):
    def __init__(self, *, plugin: "DKS", jwks_url: str, auto_error: bool = True):
        super().__init__(auto_error=auto_error)
        self.plugin = plugin
        self.jwt_client = jwt.PyJWKClient(jwks_url)

    async def __call__(self, request: Request, allow_ip_check: bool = True):
        try:
            credentials: HTTPAuthorizationCredentials | None = await super().__call__(request)
        except HTTPException:
            # No Authorization header – fall back
            credentials = None

        if not credentials or credentials.scheme != 'Bearer':
            asyncio.create_task(self.plugin.update_embed(_("Registration failed. DKS passed no bearer token.")))
            raise HTTPException(status_code=403, detail="Invalid or expired token")
        payload = await self.verify_token(credentials.credentials)
        if not payload:
            asyncio.create_task(self.plugin.update_embed(
                _("Registration failed. DKS passed an invalid/outdated token."))
            )
            raise HTTPException(status_code=403, detail="Invalid or expired token")

        request.state.jwt_token = credentials.credentials
        request.state.jwt_payload = payload

        return credentials.credentials

    async def verify_token(self, token: str) -> str | None:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")

            if not kid:
                return None

            signing_key = await asyncio.to_thread(self.jwt_client.get_signing_key, kid)

            if not signing_key:
                return None

            data = jwt.decode(
                token,
                signing_key.key,
                algorithms=[signing_key.algorithm_name],
                audience='dcssb',
                leeway=10
            )

            otp = data.pop("otp", None)
            if otp and otp != self.plugin.otp:
                return None

            return data

        except Exception as ex:
            return None

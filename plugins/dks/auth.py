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
    def __init__(self, *, plugin: "DKS", jwks_url: str, auto_error: bool = True, linked: bool = False):
        super().__init__(auto_error=auto_error)
        self.plugin = plugin
        self.jwt_client = jwt.PyJWKClient(jwks_url)
        # linked: the request comes from a registered DKS (its key, as for the RestAPI), not a registration (OTP)
        self.linked = linked

    async def __call__(self, request: Request):
        try:
            credentials: HTTPAuthorizationCredentials | None = await super().__call__(request)
        except HTTPException:
            # No Authorization header – fall back
            credentials = None

        if not credentials or credentials.scheme != 'Bearer':
            self.plugin.log.warning("DKS: called without a bearer token.")
            raise HTTPException(status_code=403, detail="Invalid or expired token")
        payload = await self.verify_token(credentials.credentials)
        if not payload:
            self.plugin.log.warning("DKS: invalid or expired token.")
            raise HTTPException(status_code=403, detail="Invalid or expired token")

        request.state.jwt_token = credentials.credentials
        request.state.jwt_payload = payload
        # the link the key names (linked), or the registration the one-time key was issued for
        request.state.dks_link = payload.pop('_link', None)
        request.state.dks_registration = payload.pop('_registration', None)

        return credentials.credentials

    async def verify_token(self, token: str) -> str | None:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")

            if not kid:
                self.plugin.log.warning("verify_token(): No kid present.")
                return None

            signing_key = await asyncio.to_thread(self.jwt_client.get_signing_key, kid)

            if not signing_key:
                self.plugin.log.warning("verify_token(): No signing key present.")
                return None

            data = jwt.decode(
                token,
                signing_key.key,
                algorithms=[signing_key.algorithm_name],
                audience='dcssb',
                leeway=10
            )

            if self.linked:
                data['_link'] = self.plugin.match_link(data.get('key'))
                if not data['_link']:
                    self.plugin.log.warning("verify_token(): Key does not match.")
                    return None
                return data

            data['_registration'] = self.plugin.claim_otp(data.pop("otp", None))
            if not data['_registration']:
                self.plugin.log.warning("verify_token(): OTP does not match.")
                return None

            return data

        except Exception as ex:
            self.plugin.log.warning(f"verify_token(): Exception: {ex}")
            return None

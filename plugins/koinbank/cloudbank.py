from typing import Optional, Union

from core import DataObjectFactory
from plugins.koinbank.bank import Bank


@DataObjectFactory.register(Bank)
class CloudBank(Bank):
    async def check_balance(self, account_id: Union[int, str]) -> float:
        ...

    async def transaction(self, from_account: str, to_account: str, amount: float, comment: Optional[str] = None):
        ...

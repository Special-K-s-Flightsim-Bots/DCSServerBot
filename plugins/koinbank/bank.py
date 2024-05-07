from core import DataObject, Node
from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class Bank(DataObject):
    """
    Bank Class
    ==========

    This class represents a bank and provides methods to perform transactions and check account balances.

    Attributes:
    -----------
    - `initial_balance` (float): The initial balance of the bank.

    Methods:
    --------
    - `__post_init__()`:
        This method is called automatically after the class is initialized. It checks the balance of the account
        associated with the guild ID.

    - `transaction(from_account: str, to_account: str, amount: float, comment: Optional[str] = None)`:
        This method performs a transaction from one account to another. It takes the following parameters:
          - `from_account` (str): The account from which the amount is transferred.
          - `to_account` (str): The account to which the amount is transferred.
          - `amount` (float): The amount to be transferred.
          - `comment` (Optional[str]): An optional comment to describe the transaction.

    - `check_balance(account_id: Union[int, str]) -> float`:
        This method checks the balance of the specified account. It takes the following parameter:
          - `account_id` (Union[int, str]): The ID of the account for which the balance is to be checked.

    """
    initial_balance: float = 0

    @classmethod
    async def create(cls, node: Node):
        self = cls(name="Bank", node=node)
        await self.check_balance(self.node.guild_id)
        return self

    async def transaction(self, from_account: str, to_account: str, amount: float, comment: Optional[str] = None):
        ...

    async def check_balance(self, account_id: Union[int, str]) -> float:
        ...


class NotEnoughKoinsException(Exception):
    def __init__(self, from_account: str, to_account: str):
        self.from_account = from_account
        self.to_account = to_account
        super().__init__('Not enough Koins on account.')

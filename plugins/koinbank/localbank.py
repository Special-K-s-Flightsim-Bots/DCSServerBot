import psycopg
import sys

from core import DataObjectFactory
from plugins.koinbank.bank import Bank, NotEnoughKoinsException
from typing import Optional, Union


@DataObjectFactory.register(Bank)
class LocalBank(Bank):

    async def check_balance(self, account_id: Union[int, str]) -> float:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT balance FROM accounts WHERE account_id = %s
            """, (str(account_id), ))
            row = await cursor.fetchone()
            if not row:
                # no account there, we need to create one
                initial_balance = self.initial_balance if isinstance(account_id, str) else sys.maxsize
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO accounts (account_id, balance)
                        VALUES (%s, %s)
                    """, (str(account_id), initial_balance))
                    return initial_balance
            else:
                return row[0]

    async def transaction(self, from_account: str, to_account: str, amount: float, comment: Optional[str] = None):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                try:
                    await conn.execute("""
                        INSERT INTO transactions (from_account, to_account, amount, comment)
                        VALUES (%s, %s, %s, %s)
                    """, (from_account, to_account, amount, comment))
                except psycopg.Error:
                    raise NotEnoughKoinsException(from_account, to_account)

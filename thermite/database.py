import asyncpg
from typing import Optional, Sequence, Tuple


class Database(object):
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @staticmethod
    async def connect(
        username: str, password: Optional[str], hostname: Optional[str], db_name: str
    ):
        pool = await asyncpg.create_pool(
            user=username, password=password, host=hostname, database=db_name
        )
        return Database(pool)

    async def add_pipe(self, source: str, target: str, reason: str):
        query = """
            INSERT INTO pipe (source, target, reason, ts)
            VALUES ($1, $2, $3, NOW()::TIMESTAMP)
        """
        async with self._pool.acquire() as conn:
            await conn.execute(query, source, target, reason)

    async def remove_pipe(self, source: str):
        query = """
            DELETE FROM pipe
            WHERE source = $1
        """
        async with self._pool.acquire() as conn:
            await conn.execute(query, source)

    async def get_pipes(self) -> Sequence[Tuple[str, str]]:
        query = "SELECT source, target FROM pipe"
        async with self._pool.acquire() as conn:
            return await conn.fetch(query)

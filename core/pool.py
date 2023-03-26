from psycopg2.pool import ThreadedConnectionPool as _ThreadedConnectionPool
from threading import Semaphore


class ThreadedConnectionPool(_ThreadedConnectionPool):
    def __init__(self, minconn: int, maxconn: int, *args, **kwargs):
        self._semaphore = Semaphore(maxconn)
        super().__init__(minconn, maxconn, *args, **kwargs)

    def getconn(self, key=None):
        self._semaphore.acquire()
        try:
            return super().getconn(key)
        except:
            self._semaphore.release()
            raise

    def putconn(self, conn=None, key=None, close=False):
        try:
            super().putconn(conn, key, close)
        finally:
            self._semaphore.release()

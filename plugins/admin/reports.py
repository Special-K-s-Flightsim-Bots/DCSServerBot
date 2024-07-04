import pandas as pd

from core import report
from psycopg.rows import dict_row


class NodeStats(report.MultiGraphElement):

    async def render(self, node: str, period: str):
        sql = """
            SELECT date_trunc('minute', time) AS time, pool_available, requests_queued, requests_wait_ms, 
                   dcs_queue, asyncio_queue
            FROM nodestats 
            WHERE time > ((NOW() AT TIME ZONE 'UTC') - ('1 ' || %s)::interval)
            AND node = %s 
            ORDER BY 1
        """
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, (period, node))
                if cursor.rowcount > 0:
                    series = pd.DataFrame.from_dict(await cursor.fetchall())
                    series.columns = [
                        'time', 'Available', 'Queued Requests', 'Wait-time (ms)', 'DCS-Queue', 'asyncio-Queue'
                    ]
                    series.plot(ax=self.axes[0], x='time', y=['Available'], title='Pool Size', xticks=[],
                                xlabel='')
                    self.axes[0].legend(loc='upper left')
                    self.axes[0].set_ylim(0, self.apool.max_size)
                    series.plot(ax=self.axes[1], x='time', y=['Queued Requests'], title='Pool Performance', xticks=[],
                                xlabel='')
                    self.axes[1].legend(loc='upper left')
                    ax3 = self.axes[1].twinx()
                    series.plot(ax=ax3, x='time', y=['Wait-time (ms)'], xticks=[], xlabel='', color='red')
                    ax3.legend(['Wait-time (ms)'], loc='upper right')
                    series.plot(ax=self.axes[2], x='time', y=['DCS-Queue'], title='Queues', xlabel='',
                                ylabel='Threads')
                    self.axes[2].legend(loc='upper left')
                    ax4 = self.axes[2].twinx()
                    series.plot(ax=ax4, x='time', y=['asyncio-Queue'], xlabel='', color='red')
                    ax4.legend(['asyncio-Queue'], loc='upper right')
                else:
                    for i in range(0, 2):
                        self.axes[i].bar([], [])
                        self.axes[i].set_xticks([])
                        self.axes[i].set_yticks([])
                        self.axes[i].text(0, 0, 'No data available.', ha='center', va='center', size=20)

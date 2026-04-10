import asyncio
import os
import pickle

from core import Extension, MizFile
from datetime import timedelta


class Persistence(Extension):

    CONFIG_DICT = {
        "path": {
            "type": str,
            "label": "Path",
            "default": "Saves",
            "required": False
        }
    }

    def get_pickle_file(self, filename: str | None = None) -> str | None:
        if not filename and not self.server.current_mission:
            return None
        path = os.path.join(self.server.instance.missions_dir,
                            self.locals.get('persistence', {}).get('path', 'Saves'))
        if not filename:
            filename = self.server.current_mission.filename
        return os.path.join(path, os.path.basename(filename[:-3] + 'pkl'))

    async def startup(self, *, quiet: bool = False) -> bool:
        return await super().startup(quiet=True)

    def shutdown(self, *, quiet: bool = False) -> bool:
        # last persist before a regular shutdown
        self._persist()
        return super().shutdown(quiet=True)

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        pickle_file = self.get_pickle_file(filename)
        if not pickle_file or not os.path.exists(pickle_file):
            return filename, False

        with open(pickle_file, mode='rb') as f:
            payload = pickle.load(f)
        miz = await asyncio.to_thread(MizFile, filename)
        miz.date = payload['date']
        miz.start_time = payload['start_time']
        await asyncio.to_thread(miz.save, filename)
        return filename, True

    def _persist(self):
        pickle_file = self.get_pickle_file()
        if not pickle_file:
            return
        os.makedirs(os.path.dirname(pickle_file), exist_ok=True)
        mission_time = self.server.current_mission.mission_time
        mission_date = self.server.current_mission.date
        start_time = self.server.current_mission.start_time

        payload = {
            "start_time": int(start_time + mission_time) % 86400,
            "date": mission_date + timedelta(days=int((start_time + mission_time) // 86400))
        }
        with open(pickle_file, mode='wb') as f:
            pickle.dump(payload, f)

    def is_running(self) -> bool:
        if not self.running:
            return False
        # we get here every minute
        self._persist()
        return True

    async def reset(self, filename: str):
        pickle_file = self.get_pickle_file(filename)
        if pickle_file and os.path.exists(pickle_file):
            os.remove(pickle_file)

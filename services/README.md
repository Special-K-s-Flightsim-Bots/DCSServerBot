# Services
Similar to [plugins](../plugins/README.md), and [extensions](../extensions/README.md), services are part of the 
modular architecture of DCSServerBot.

A service provides specific functionalities that can be used by any other component of the bot, like plugins or 
extensions. For example, if you want to use the `BotService`, you can ask the ServiceRegistry for it and use it like so:
```python
from core import ServiceRegistry
from services.bot import BotService
from typing import cast

bot_service = cast(BotService, ServiceRegistry.get(BotService))
await bot_service.alert(title="Critical Alert!", message="This is an alert message!")
```

### Examples
#### MusicService
The MusicServices needs access to your SRS servers that run on any node. And it needs access to your music library
which "might" be on every node (recommendation would be to have it in the cloud).
The music plugin uses the MusicService to play music on the SRS servers.
It connects to the respective node and uses the MusicService to play music.

#### BackupService
The BackupService needs access to the file system of each node due to its function of backing up data stored there. 
Although the NodeProxy (read_file(), write_file(), list_directory()) could be employed for this purpose, 
I opted to categorize it as a service rather than having read_file() transfer files across the database to the master 
node. This approach would not only be inefficient but also impractical given its resource consumption and lack of speed.

#### ModManagerService
This service has to unpack mod-packs onto your DCS servers that are installed on the specific nodes.

I think you get the idea – whenever you need to do something on a specific node, a service needs to be implemented.

## How to implement a Service?
You need to implement your own class that derives from the class [Service](../core/services/base.py). 
It has to register with the ServiceRegistry to be able to be used anywhere in the code.

A sample might look like this:
```python
from core import ServiceRegistry, Service, Server, proxy
from services.bot import BotService

# Register the service with the ServiceRegistry.
# You can define an optional plugin that determines if this service should be loaded or not.
# If you use "master_only=True", the service will only be loaded on the master node.
# If you use "agent_only=True", the service will only be loaded on agents.
# You can also define dependencies for this service with "depends_on" (see below).
@ServiceRegistry.register(plugin="myplugin", master_only=True, depends_on=[BotService])
class MyService(Service):

    async def start(self):
        await super().start()
        # do something on service start
        self.log.info(f"MyService runs on node {self.node.name}")
        config = self.get_config()  # access config/services/myservice.yaml

    async def stop(self, *args, **kwargs):
        await super().stop()
        # do something on service stop

    async def switch(self, master: bool):
        await super().switch(master)
        # called when a master/agent switch happened

    def get_config(self, server: Server | None = None, **kwargs) -> dict:
        # you can overwrite the get_config() method to implement your own config-reader.
        ...

    # Use "proxy" to access methods on other nodes.
    # In this case, the server might be remote. 
    # DCSServerBot will automatically call the correct method on the correct node.
    @proxy
    async def my_method(self, server: Server):
        # you can define your own methods here.
```
You have access to any other core functionality of the bot in here. 
You can access other services (be aware – you might not be on the master node!), the database, the local servers 
(unless you are on the master, then you can access all servers), etc.

> [!IMPORTANT]
> You should always check if you are the master (self.node.master) and if any of the servers you access 
> are remote or not (`server.is_remote`).

> [!NOTE]
> If you depend on a master_only service, your service will automatically be a master_only service.
> In the above example, the BotService is a master_only service, so the master_only flag was not needed 
> and was added for reference only.

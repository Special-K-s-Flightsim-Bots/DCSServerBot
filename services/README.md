# Services
Similar to [plugins](../plugins/README.md), services are part of the modular system of DCSServerBot.<br>
The reason to have plugins and services lies in the nature of the cluser [architecture](../ARCHITECTURE.md). If there
would only be one bot in your whole installation, there usually would not be the need for any service. But as 
DCSServerBot supports large installations, the system itself has to be capable of doing so. That's why I had to 
distinguish between plugins and services and that's why the development of enhancements might look a bit more complex
that it needs to be.

But lets see, it does not necessarily need to be like that.

## When Do I implement a Service, when a Plugin?
For any common usecase, like if you want to add some Discord command or some in-game chat-command, a plugin will usually
do the job. The plugin only runs on the master node and has access to events from all DCS servers, the database and the
majority of the configuration.<br>
In case you need to run something on every node of the cluster, you will need to implement a service.

### Examples
#### MusicService
The MusicServices needs access to your SRS servers, that run on any node. And it needs access to your music library,
that "might" be on every node (recommendation would be to have it in the cloud).

#### BackupService
The BackupService needs access to the file system of any node, as it should backup the data in there. It could be
implemented by using the NodeProxy (read_file(), write_file(), list_directory()) also, but I decided to have it as a 
service, as read_file() would transfer any file over the database to the master node, which does not only not sound
very efficient, it indeed isn't.

#### OvGMEService
This service has to unpack modpacks onto your DCS servers that are installed on the specific nodes.

I think you get the idea - whenever you need to do something on a specific node, a service needs to be implemented.

## How to implement a Service?
You need to implement your own class that derives from the class [Service](../core/services/base.py). It has to register
with the ServiceRegistry, to be able to be used anywhere in the code.

A sample might look like this:
```python
from core import ServiceRegistry, Service

# Register the service with the ServiceRegistry.
# You can define an optional plugin, that determines if this services should be loaded or not.
@ServiceRegistry.register("MyService", plugin="myplugin")
class MyService(Service):

        async def start(self):
            await super().start()
            # do something on service start
            self.log.info(f"MyService runs on node {self.node.name}")
            config = self.get_config()  # access config/services/myservice.yaml

        async def stop(self, *args, **kwargs):
            await super().stop()
            # do something on service stop
```
That's more or less it. You have access to any other core functionality of the bot in here. You can access other services
(be aware - you might not be on the master node!), the database, the local servers (unless you are on the master, then
you can access all servers), etc.
You should always check if you are the master (self.node.master) and if any of the servers you access are remote or not
(server.is_remote).
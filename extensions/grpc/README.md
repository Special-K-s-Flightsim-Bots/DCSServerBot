# Extension "DCS-gRPC"
[DCS-gRPC](https://github.com/DCS-gRPC) is a communication library, that is somehow similar to what DCSServerBot does 
already. It has some differences though and comes with some other tools. This said, you can use it alongside DCSServerBot
without issues.

## Configuration
The extension itself allows you to configure your DCS-gRPC server from your instance configurations like with any other
extension:
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        gRPC:
          enabled: true
          port: 50051     # you can set any configuration parameter here, that will be replaced in your dcs-grpc.lua file.
```

> [!TIP]
> You can rename the gRPC extension in your server status embed by setting a "name" in the configuration like so:
> ```yaml
> extension:
>   gRPC:
>     name: MyFancyName  # Optional: default is "DCS-gRPC"
> ```

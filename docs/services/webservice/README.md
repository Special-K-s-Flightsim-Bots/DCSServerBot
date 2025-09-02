---
layout: default
title: README
nav_section: services/webservice
---

# Service WebService
This service provides a simple web service. It is WIP, and it will be enhanced in the future.
Currently you can use it with the [RestAPI plugin](https://github.com/frankkopp/DCSServerBot3/tree/master/services/restapi).

## Configuration
The webservice can be configured in its config/services/webservice.yaml file:
```yaml
DEFAULT:
  listen: 0.0.0.0   # the interface to bind the internal webserver to
  port: 9876        # the port the webservice is listening on
  debug: false      # Enable /openapi.json, /docs and /redoc endpoints to test the API (default: false)
```

> [!NOTE]
> To access the API documentation, you can enable debug and access the documentation with these links: 
> http://localhost:9876/docs
> http://localhost:9876/redoc
> Please refer to the [OpenAPI specification](https://swagger.io/specification/) for more information.

> [!WARNING]
> Do NOT enable debug for normal operations, especially if you expose the port to the outside world.

> [!IMPORTANT]
> It is advisable to use a reverse proxy like nginx and maybe SSL encryption to secure the webservice. 

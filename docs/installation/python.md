---
title: Python
parent: Installation
nav_order: 3
---

You need to have [Python] 3.9 (or higher) installed to run DCSServerBot.
Just download and install [Python] from the website and install it with default settings.
Or you can use a package manager of your choice like [Winget] or [Chocolaty]:

- `choco install python`
- `winget install python`

After installation run this command for a quick test:

```python --version```

On the first start all needed Python modules will be installed in an [virtual environment] in the sub-folder `venv`.
If you want to know the needed modules take a look at `requirements.txt`.

[Python]: https://www.python.org/
[Chocolaty]: https://chocolatey.org/
[Winget]: https://learn.microsoft.com/en-us/windows/package-manager/winget/
[virtual environment]: https://docs.python.org/3/library/venv.html

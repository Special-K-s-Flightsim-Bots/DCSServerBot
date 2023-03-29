---
title: Git
parent: Installation
nav_order: 1
---

{: .note }
> The installation of Git is optional.

If you install Git and clone the DCSServerBot repository from GitHub onto your hard-disk, the autoupdate will work out of the box.
You must ensure, that the `git.exe` is in your `PATH` environment variable.

Just download it from [git-scm.com] and install it with default settings.
Or you can use a package manager of your choice [Winget] / [Chocolaty]:
- `choco install git`
- `winget install git`

After installation run this command for a quick test:

```git --version```

If `git.exe` is in your `PATH` environment variable follow this [guide] how to add it.

{: .note }
> You don't need to know a lot about git and how to work with it.
> All you need to use is the initial clone command, which will be discussed later.
> No need to learn a lot about git, because the bot handles all auto-update steps by himself.

[guide]: https://linuxhint.com/add-git-to-path-windows/
[git-scm.com]: https://git-scm.com/downloads
[Chocolaty]: https://chocolatey.org/
[Winget]: https://learn.microsoft.com/en-us/windows/package-manager/winget/

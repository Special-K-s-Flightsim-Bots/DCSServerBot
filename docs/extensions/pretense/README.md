---
layout: default
title: README
nav_section: extensions/pretense
---

# Extension "Pretense"
[Pretense](https://github.com/Dzsek/pretense) is a dynamic campaign system built by Dzsek. Unfortunately he dropped
the development of it lately, but it is still in use by many and great missions to run on your servers. That is why
I decided to not drop the support for now in DCSServerBot.<br>
The main part happens in the [Pretense](../../plugins/pretense/README.md) plugin, where you can run commands and configure
specific statistic displays. But you can also use this small extension to either display your users which version of
Pretense you are using and to have a very basic configuration of it. 

## Configuration
Just add some lines to your nodes.yaml like so:
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Pretense:
          randomize: true # puts a randomize.lua in your Missions\Saves directory. See the Pretense documentation for more.
```

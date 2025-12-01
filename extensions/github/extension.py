import asyncio
import git
import os
import subprocess

from core import Extension, utils, Server
from typing_extensions import override
from urllib.parse import urlparse


class GitHub(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.repo = self.config['repo']
        self.target = os.path.join(utils.format_string(self.config['target'], server=self.server,
                                                       instance=self.server.instance, node=self.node),
                                   self.get_repo_name(self.repo))

    @staticmethod
    def get_repo_name(repo: str) -> str:
        parsed_url = urlparse(repo)
        repo_name = os.path.basename(parsed_url.path)
        if repo_name.endswith('.git'):
            repo_name = repo_name[:-4]
        return repo_name

    @staticmethod
    def get_default_branch(target: str) -> str:
        result = subprocess.run(
            ['git', 'remote', 'show', 'origin'],
            cwd=target,
            capture_output=True,
            text=True,
            check=True
        )
        for line in result.stdout.splitlines():
            if "HEAD branch" in line:
                _, branch = line.split(": ")
                return branch.strip()
        return "master"

    def clone_with_filter(self, pattern: str):
        subprocess.run(['git', 'init'], cwd=self.target, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        subprocess.run(['git', 'remote', 'add', '-f', 'origin', self.repo], cwd=self.target, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['git', 'config', 'core.sparseCheckout', 'true'], cwd=self.target, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        sparse_checkout_path = os.path.join(self.target, '.git', 'info', 'sparse-checkout')
        with open(sparse_checkout_path, 'w') as f:
            f.write(pattern + '\n')

        branch = self.config.get('branch', self.get_default_branch(self.target))
        subprocess.run(['git', 'pull', '--set-upstream', 'origin', branch],
                       cwd=self.target, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def update(self):
        self.log.debug(f"{self.name}: Updating repository {self.repo} into {self.target}")
        repo = git.Repo(self.target)
        repo.git.pull()

    async def clone(self):
        self.log.debug(f"{self.name}: Cloning repository {self.repo} into {self.target}")
        if not os.path.exists(self.target):
            os.makedirs(self.target)

        pattern = self.config.get('filter')
        if filter:
            await asyncio.to_thread(self.clone_with_filter, pattern)
        else:
            git.Repo.clone_from(self.repo, self.target)

    @override
    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        try:
            await self.update()
        except (git.NoSuchPathError, git.InvalidGitRepositoryError):
            await self.clone()
        return filename, False

    @override
    async def startup(self, *, quiet: bool = False) -> bool:
        return await super().startup(quiet=True)

    @override
    def shutdown(self, *, quiet: bool = False) -> bool:
        return super().shutdown(quiet=True)

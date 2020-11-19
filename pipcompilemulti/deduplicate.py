"""Remove packages included in referenced environments."""

import itertools
import logging

from pipcompilemulti.utils import recursive_relations, merged_packages


logger = logging.getLogger("pip-compile-multi")


class PackageDeduplicator:
    """Remove packages included in referenced environments."""

    def __init__(self):
        self.env_packages = {}
        self.env_confs = None
        self._recompiled_envs = set()

    def on_discover(self, env_confs):
        """Save environment references."""
        self.env_confs = env_confs

    def register_packages_for_env(self, env_name, packages):
        """Save environment packages."""
        self.env_packages[env_name] = packages

    def ignored_packages(self, env_name):
        """Get package mapping from name to version for referenced environments."""
        if self.env_confs is None:
            return {}
        return merged_packages(
            self.env_packages,
            recursive_relations(self.env_confs, env_name)['refs'],
        )

    def mark_recompiled(self, env_name):
        self._recompiled_envs.add(env_name)

    def need_recompile(self, env_name):
        if self.env_confs is None:
            return True
        relations = recursive_relations(self.env_confs, env_name)
        return any(
            rel in self._recompiled_envs
            for rels in relations.values()
            for rel in rels
        )

    def recursive_relations(self, env_name):
        """Return recursive list of environment names referenced by or constraining env_name."""
        if self.env_confs is None:
            return {'refs': (), 'cons': ()}
        return recursive_relations(self.env_confs, env_name)

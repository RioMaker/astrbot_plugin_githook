"""Populate AstrBot's checkbox configuration without enabling discovered repos."""

from .config import repository_switches


class RepositoryRegistry:
    def __init__(self, config):
        self.config = config

    def enabled(self, repo):
        return repo.casefold() in self.enabled_repos()

    def enabled_repos(self):
        return {
            row["repo"].casefold()
            for row in repository_switches(self.config.get("repository_switches", []))
            if row["enabled"]
        }

    def sync(self, plugins):
        # No await between reading current choices and saving additions. AstrBot's
        # dashboard updates this same config object before reloading the plugin.
        previous = self.config.get("repository_switches", [])
        rows = repository_switches(previous)
        known = {row["repo"].casefold() for row in rows}
        added = []
        for plugin in sorted(plugins, key=lambda p: p.repo.casefold()):
            if plugin.repo.casefold() in known:
                continue
            rows.append({"__template_key": "repository", "repo": plugin.repo, "enabled": False})
            known.add(plugin.repo.casefold())
            added.append(plugin.repo)
        if rows != previous:
            self.config["repository_switches"] = rows
            save = getattr(self.config, "save_config", None)
            try:
                if callable(save):
                    save()
            except Exception:
                self.config["repository_switches"] = previous
                raise
        return added

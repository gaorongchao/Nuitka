#     Copyright 2022, Kay Hayen, mailto:kay.hayen@gmail.com
#
#     Part of "Nuitka", an optimizing Python compiler that is compatible and
#     integrates with CPython, but also works on its own.
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.
#
""" Standard plug-in to resolve pkg_resource actions at compile time rather than runtime.

Nuitka can detect some things that "pkg_resources" may not even be able to during
runtime, e.g. right now checking pip installed versions, is not a thing, while
some packages in their code, e.g. derive their __version__ value from that.
"""


import re

from nuitka.plugins.PluginBase import NuitkaPluginBase
from nuitka.utils.Utils import withNoDeprecationWarning


class NuitkaPluginResources(NuitkaPluginBase):
    plugin_name = "pkg-resources"
    plugin_desc = "Resolve version numbers at compile time."

    def __init__(self):
        with withNoDeprecationWarning():
            try:
                import pkg_resources
            except (ImportError, RuntimeError):
                self.pkg_resources = None
            else:
                self.pkg_resources = pkg_resources

        try:
            import importlib_metadata
        except (ImportError, SyntaxError, RuntimeError):
            self.metadata = None
        else:
            self.metadata = importlib_metadata

        # Note: This one is overriding above import, but doesn't need to initialize
        # the value, since it will already be set in case of a problem.
        try:
            from importlib import metadata

            self.metadata = metadata
        except ImportError:
            pass

    @staticmethod
    def isAlwaysEnabled():
        return True

    def _handleEasyInstallEntryScript(self, dist, group, name):
        module_name = None
        main_name = None

        # First try metadata, which is what the runner also does first.
        if self.metadata:
            dist = self.metadata.distribution(dist.partition("==")[0])

            for entry_point in dist.entry_points:
                if entry_point.group == group and entry_point.name == name:
                    module_name = entry_point.module
                    main_name = entry_point.attr

                    break

        if module_name is None and self.pkg_resources:
            with withNoDeprecationWarning():
                entry_point = self.pkg_resources.get_entry_info(dist, group, name)

            module_name = entry_point.module_name
            main_name = entry_point.name

        if module_name is None:
            self.sysexit(
                "Error, failed to resolve easy install entry script, is the installation broken?"
            )

        return r"""
import sys, re
sys.argv[0] = re.sub(r'(-script\.pyw?|\.exe)?$', '', sys.argv[0])
import %(module_name)s
sys.exit(%(module_name)s.%(main_name)s)
""" % {
            "module_name": module_name,
            "main_name": main_name,
        }

    def onModuleSourceCode(self, module_name, source_code):
        if module_name == "__main__":
            match = re.search(
                "\n# EASY-INSTALL-ENTRY-SCRIPT: '(.*?)','(.*?)','(.*?)'", source_code
            )

            if match is not None:
                self.info(
                    "Detected easy install entry script, compile time detecting entry point."
                )

                return self._handleEasyInstallEntryScript(*match.groups())

        # This one has strings with false matches, don't attempt those.
        if module_name == "setuptools.command.easy_install":
            return source_code

        if self.metadata:
            for total, quote1, name, quote2 in re.findall(
                r"""\b((?:importlib[_.])?metadata\.version\(\s*(['"]?)(.*?)(['"]?)\s*\))""",
                source_code,
            ):
                if name == "__name__":
                    name = module_name.asString()
                    quote1 = quote2 = "'"

                if quote1 == quote2:
                    if quote1:
                        try:
                            value = self.metadata.version(name)
                        except self.metadata.PackageNotFoundError:
                            self.warning(
                                "Cannot find requirement '%s' for '%s', expect potential run time problem."
                                % (name, module_name)
                            )

                            continue
                        except Exception:  # catch all, pylint: disable=broad-except
                            self.sysexit(
                                "Error, failed to resolve '%s', probably a plugin parsing bug for '%s' code."
                                % (name, module_name)
                            )
                        else:
                            value = repr(value)
                            source_code = source_code.replace(total, value)

        return source_code

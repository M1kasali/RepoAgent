"""Include the optional, self-contained terminal bundle in built wheels."""

from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        source = Path(__file__).parent / "ui-tui" / "dist"
        if (source / "entry.js").is_file():
            destination = Path(self.build_lib) / "repoagent/harness/ui-tui/dist"
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            self.warn("TUI bundle missing; run npm --prefix ui-tui ci and npm --prefix ui-tui run build before release builds")


setup(cmdclass={"build_py": BuildPy})

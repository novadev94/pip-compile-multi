"""Environment class"""

import os
import re
import logging
import subprocess

from .dependency import Dependency
from .features import FEATURES
from .deduplicate import PackageDeduplicator
from .utils import relation_keys


logger = logging.getLogger("pip-compile-multi")


class Environment(object):
    """requirements file"""

    RE_REF = re.compile(r'^(?:-r|--requirement)\s*(?P<path>\S+).*$')
    RE_CON = re.compile(r'^(?:-c|--constraint)\s*(?P<path>\S+).*$')

    def __init__(self, name, deduplicator=None, recompiled_envs=None):
        """
        name - name of the environment, e.g. base, test
        """
        self.name = name
        self._dedup = deduplicator or PackageDeduplicator()
        self.ignore = self._dedup.ignored_packages(name)
        self.packages = {}
        self._outfile_pkg_names = None
        if recompiled_envs is None:
            recompiled_envs = set()
        self._recompiled_envs = recompiled_envs

    def maybe_create_lockfile(self):
        """
        Write recursive dependencies list to outfile unless the goal is
        to upgrade specific package(s) which don't already appear.
        Populate package ignore set in either case and return
        boolean indicating whether outfile was written.
        """
        relations = self._dedup.recursive_relations(self.name)
        logger.info(
            "Locking %s to %s. References: %r; Constraints: %r",
            self.infile, self.outfile,
            sorted(relations['refs']),
            sorted(relations['cons']),
        )
        recompile = (
            FEATURES.affected(self.name)
            or self._dedup.need_recompile(self.name)
            or self.is_outdated()
        )
        if recompile:
            logger.debug('Compiling %s', self.outfile)
            self.create_lockfile()
        else:
            logger.debug('Fixing %s', self.outfile)
            self.fix_lockfile()  # populate ignore set
        return recompile

    def is_outdated(self):
        from .verify import generate_hash_comment, parse_hash_comment
        current_comment = generate_hash_comment(self.infile)
        existing_comment = parse_hash_comment(self.outfile)
        return current_comment != existing_comment

    def create_lockfile(self):
        """
        Write recursive dependencies list to outfile
        with hard-pinned versions.
        Then fix it.
        """
        process = subprocess.Popen(
            self.pin_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.workingdir,
        )
        stdout, stderr = process.communicate()
        if process.returncode == 0:
            self.fix_lockfile()
        else:
            logger.critical("ERROR executing %s", ' '.join(self.pin_command))
            logger.critical("Exit code: %s", process.returncode)
            logger.critical(stdout.decode('utf-8'))
            logger.critical(stderr.decode('utf-8'))
            raise RuntimeError("Failed to pip-compile {0}".format(self.infile))

    @classmethod
    def parse_relations(cls, filename):
        """
        Read filename line by line searching for pattern:

        -r file.in
        or
        --requirement file.in
        or
        -c file.in
        or
        --constraint file.in

        return a dict of key => set of matched file names without extension.
        E.g. {'refs': ['file1'], 'cons': ['file2']}
        """
        patterns = {'refs': cls.RE_REF, 'cons': cls.RE_CON}
        dependencies = {name: set() for name in patterns.keys()}
        for line in open(filename):
            for name, pattern in patterns.items():
                matched = pattern.match(line)
                if matched:
                    reference = matched.group('path')
                    reference_base = os.path.splitext(reference)[0]
                    dependencies[name].add(reference_base)
        return dependencies

    @property
    def infile(self):
        """Path of the input file"""
        return FEATURES.compose_input_file_path(self.name)

    @property
    def outfile(self):
        """Path of the output file"""
        return FEATURES.compose_output_file_path(self.name)

    @property
    def workingdir(self):
        """Working directory for pip-compile command"""
        return FEATURES.base_dir.path

    @property
    def pin_command(self):
        """Compose pip-compile shell command"""
        parts = [
            'pip-compile',
            '--no-header',
            '--verbose',
        ]
        parts.extend(FEATURES.pin_options(self.name))
        parts.extend(['--output-file',
                      os.path.relpath(self.outfile, self.workingdir),
                      os.path.relpath(self.infile, self.workingdir)])
        return parts

    def fix_lockfile(self):
        """Run each line of outfile through fix_pin"""
        with open(self.outfile, 'rt') as fp:
            lines = [
                self.fix_pin(line)
                for line in self.concatenated(fp)
            ]
        with open(self.outfile, 'wt') as fp:
            fp.writelines([
                line + '\n'
                for line in lines
                if line is not None
            ])
        self._dedup.register_packages_for_env(self.name, self.packages)

    @staticmethod
    def concatenated(fp):
        """Read lines from fp concatenating on backslash (\\)"""
        line_parts = []
        for line in fp:
            line = line.strip()
            if line.endswith('\\'):
                line_parts.append(line[:-1].rstrip())
            else:
                line_parts.append(line)
                yield ' '.join(line_parts)
                line_parts[:] = []
        if line_parts:
            # Impossible:
            raise RuntimeError("Compiled file ends with backslash \\")

    def fix_pin(self, line):
        """
        Fix dependency by removing post-releases from versions
        and loosing constraints on internal packages.
        Drop packages from ignore set

        Also populate packages set
        """
        dep = Dependency(line)
        if dep.valid:
            if dep.package in self.ignore:
                ignored_version = self.ignore[dep.package]
                if ignored_version is not None:
                    # ignored_version can be None to disable conflict detection
                    if dep.version and dep.version != ignored_version:
                        logger.error(
                            "Package %s was resolved to different "
                            "versions in different environments: %s and %s",
                            dep.package, dep.version, ignored_version,
                        )
                        raise RuntimeError(
                            "Please add constraints for the package "
                            "version listed above"
                        )
                return None
            self.packages[dep.package] = dep.version
            dep.drop_post(self.name)
            return dep.serialize()
        return line.strip()

    def add_relations(self, relations):
        """Add relations to other_names in outfile"""
        # We don't add constraints to those files because pip-sync are dumb...
        keys = ['refs']
        all_names = [(key, relations[key]) for key in keys if relations[key]]
        if not all_names:
            # Skip on empty list
            return
        with open(self.outfile, 'rt') as fp:
            header, body = self.split_header(fp)
        with open(self.outfile, 'wt') as fp:
            fp.writelines(header)
            for key, rels in all_names:
                if key == 'cons':
                    prefix = '-c'
                elif key == 'refs':
                    prefix = '-r'
                fp.writelines(
                    '{0} {1}\n'.format(
                        prefix, FEATURES.compose_output_file_name(name)
                    )
                    for name in sorted(rels)
                )
            fp.writelines(body)

    @staticmethod
    def split_header(fp):
        """
        Read file pointer and return pair of lines lists:
        first - header, second - the rest.
        """
        body_start, header_ended = 0, False
        lines = []
        for line in fp:
            if line.startswith('#') and not header_ended:
                # Header text
                body_start += 1
            else:
                header_ended = True
            lines.append(line)
        return lines[:body_start], lines[body_start:]

    def replace_header(self, header_text):
        """Replace pip-compile header with custom text"""
        with open(self.outfile, 'rt') as fp:
            _, body = self.split_header(fp)
        with open(self.outfile, 'wt') as fp:
            fp.write(header_text)
            fp.writelines(body)

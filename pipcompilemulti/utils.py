"""Functional utilities for lists and dicts manipulation."""

import logging
import itertools
import collections


logger = logging.getLogger("pip-compile-multi")
relation_keys = ('cons', 'refs')


def _recursive_visit(rels_by_name, startings, keys=relation_keys):
    visited = set(startings)
    queue = collections.deque(startings)
    while queue:
        name = queue.popleft()
        relations = rels_by_name[name]
        for key in keys:
            for related in relations[key]:
                if related not in visited:
                    visited.add(related)
                    queue.append(related)
    return visited


def recursive_relations(envs, starting):
    """
    Return dict key => set of recursive relations for given env name

    >>> local_rels = recursive_relations([
    ...     {'name': 'base', 'refs': []},
    ...     {'name': 'test', 'refs': ['base']},
    ...     {'name': 'local', 'refs': ['test']},
    ... ], 'local')
    >>> local_refs == {'refs': ['base', 'test'], 'cons': []}
    True
    """
    rels_by_name = {env['name']: env for env in envs}
    refs = _recursive_visit(rels_by_name, [starting], ['refs'])
    cons = _recursive_visit(rels_by_name, refs)
    cons -= refs
    for rels in (refs, cons):
        rels.discard(starting)
    return {'refs': refs, 'cons': cons}


def merged_packages(env_packages, names):
    """Return union set of environment packages with given names.

    >>> sorted(merged_packages(
    ...     {
    ...         'a': {'x': 1, 'y': 2},
    ...         'b': {'y': 2, 'z': 3},
    ...         'c': {'z': 3, 'w': 4}
    ...     },
    ...     ['a', 'b']
    ... ).items())
    [('x', 1), ('y', 2), ('z', 3)]
    """
    combined_packages = sorted(itertools.chain.from_iterable(
        env_packages[name].items()
        for name in names
    ))
    result = {}
    errors = set()
    for name, version in combined_packages:
        if name in result:
            if result[name] != version:
                errors.add((name, version, result[name]))
        else:
            result[name] = version
    if errors:
        for error in sorted(errors):
            logger.error(
                "Package %s was resolved to different "
                "versions in different environments: %s and %s",
                error[0], error[1], error[2],
            )
        raise RuntimeError(
            "Please add constraints for the package version listed above"
        )
    return result


def relation_cluster(envs, name):
    """
    Return set of all env names referencing or
    referenced or constraining or constraint by given name.

    >>> cluster = sorted(relation_cluster([
    ...     {'name': 'base', 'refs': []},
    ...     {'name': 'test', 'refs': ['base']},
    ...     {'name': 'local', 'refs': ['test']},
    ... ], 'test'))
    >>> cluster == ['base', 'local', 'test']
    True
    """
    edges = [
        set([env['name'], rel])
        for env in envs
        for key in relation_keys
        for rel in env[key]
    ]
    prev, cluster = set(), set([name])
    while prev != cluster:
        # While cluster grows
        prev = set(cluster)
        to_visit = []
        for edge in edges:
            if cluster & edge:
                # Add adjacent nodes:
                cluster |= edge
            else:
                # Leave only edges that are out
                # of cluster for the next round:
                to_visit.append(edge)
        edges = to_visit
    return cluster

#!/usr/bin/env python3
"""Generates the per-site /[DOMAIN].json files served on map.snarfed.org.

Usage: make_web.py sites.json[.gz] links.json[.gz] extra.json[.gz] ...

Writes site.json.gz ... output files, one per site.

sites.json[.gz] is generated by sites_to_bigquery.py.

links.json[.gz] is a JSON file with per-site link data, one record per line,
generated by this BigQuery view:
https://bigquery.cloud.google.com/table/indie-map:indiemap.social_graph_links

extra.json[.gz] ... are more JSON files, one record per line, with per-site data
to be merged in. Each record must have at least a 'domain' property.

See ../crawl/notes for details.
"""
from collections import defaultdict, OrderedDict
import copy
from datetime import datetime
import decimal
from decimal import Decimal
import gzip
from itertools import chain
import math
import operator
import os
# simplejson supports encoding Decimal, but json doesn't
import simplejson as json
import sys
import time

MF2_WEIGHTS = {
    'in-reply-to': 5,
    'invitee': 5,
    'quotation-of': 3,
    'repost-of': 3,
    'like-of': 2,
    'favorite-of': 2,
    'bookmark-of': 2,
    'other': 1,
}
DIRECTION_WEIGHTS = {
    'out': 2,
    'in': 1,
}
MAX_BASE_LINKS = 500  # cap on number of link domains in base files

def read_lines(filename):
    with open(os.path.join(os.path.dirname(__file__), '../crawl', filename),
              'rt', encoding='utf-8') as f:
        return [line.strip() for line in f]

TAGS = {tag: read_lines(filename) for tag, filename in (
    ('bridgy', 'domains_bridgy_sent.txt'),
    ('community', 'domains_community.txt'),
    ('elder', 'domains_elders.txt'),
    ('founder', 'domains_founders.txt'),
    ('IRC', 'domains_irc_people.txt'),
    ('tool', 'domains_tools.txt'),
    ('webmention.io', 'domains_webmention.io.txt'),
)}
TAGS_NO_SUBDOMAINS = {
    'micro.blog',
    'withknown.com',
}
SERVERS = {
    'Known http://withknown.com': 'Known',
    'Known https://withknown.com': 'Known',
}
SERVER_TAGS = {
    'Known',
    'WordPress',
}

decimal.getcontext().prec = 3  # calculate/output scores at limited precision


def load_links(links_in):
    """Loads and processes a social graph links JSON file.

    Args:
      links_in: sequence of social graph links objects. See file docstring.

    Returns: (links, domains, out_counts, in_counts)

    links:
      {'[DOMAIN]': {
          'links_out': [INTEGER],
          'links_in':  [INTEGER],
          'links': {
            'TO_DOMAIN': {
              'out': {
                'in-reply-to': [INTEGER],  # mf2 classes
                'like-of': [INTEGER],
                ...
                'other': [INTEGER],
              },
              'in': {
                [SAME]
              },
              'score': [FLOAT],
            },
            ...
          },
        },
        ...,
      }
    domains: set of from domains
    out_counts, in_counts: {'[DOMAIN]': [INTEGER]}
    """
    print('\nProcessing links', end='', flush=True)

    links = defaultdict(lambda: defaultdict(lambda: defaultdict(
        lambda: defaultdict(int))))
    out_counts = defaultdict(int)
    in_counts = defaultdict(int)
    from_domains = set()

    for i, link in enumerate(links_in):
        if i and i % 10000 == 0:
            print('.', end='', flush=True)

        from_domain = link['from_domain']
        from_domains.add(from_domain)
        to_domain = link['to_domain']
        num = int(link['num'])
        mf2 = link.get('mf2_class', 'other')
        if mf2.startswith('u-'):
            mf2 = mf2[2:]

        links[from_domain][to_domain]['out'][mf2] += num
        links[to_domain][from_domain]['in'][mf2] += num
        out_counts[from_domain] += num
        in_counts[to_domain] += num

    return links, from_domains, out_counts, in_counts


def make_full(sites, single_links, *extras):
    """Generates and returns output site objects with all link domains.

    Args:
      sites: sequence of input site objects
      links_file: sequence of link collections returned by load_links()
      extras: sequences of additional site data objects

    Returns:
      generator of output JSON site objects
    """
    links, from_domains, out_counts, in_counts = load_links(single_links)

    # calculate scores
    print('\nScoring', end='', flush=True)
    for i, domains in enumerate(links.values()):
        if i and i % 10000 == 0:
            print('.', end='', flush=True)

        max_score = 0
        for stats in domains.values():
            score = 0
            for direction, counts in stats.items():
                for mf2, count in counts.items():
                    score += Decimal(count * MF2_WEIGHTS[mf2] *
                                     DIRECTION_WEIGHTS[direction])
            stats['score'] = score
            if score > max_score:
                max_score = score

        # normalize scores to (0, 1] per domain
        max_score_ln = max_score.ln()
        for stats in domains.values():
            score = stats['score']
            stats['score'] = (0 if score <= 1
                              else 1 if max_score_ln == 0
                              else score.ln() / max_score_ln)

    # collect extra data
    all_extra = defaultdict(dict)
    for extra in extras:
        for site in extra:
            all_extra[site['domain']].update(site)

    # emit each site
    print('\nGenerating full...', end='', flush=True)
    more_sites = [{'domain': domain} for domain in
                   sorted(from_domains - set(site['domain'] for site in sites))]

    for i, site in enumerate(sites + more_sites):
        if i and i % 100 == 0:
            print('.', end='', flush=True)
        site = OrderedDict(site)
        domain = site['domain']

        site.update(all_extra[domain])
        site.pop('mf2', None)
        site.pop('html', None)

        num_pages = site.get('num_pages')
        if num_pages:
            site['num_pages'] = int(num_pages)

        # tags
        tags = site.setdefault('tags', [])
        for tag, domains in TAGS.items():
            for tag_domain in domains:
                if (domain == tag_domain or (domain.endswith('.' + tag_domain) and
                                             tag_domain not in TAGS_NO_SUBDOMAINS)):
                    tags.append(tag)
                    break

        if site.get('webmention_endpoints'):
            tags.append('webmention')
        if site.get('micropub_endpoints'):
            tags.append('micropub')

        # servers
        for gen in site.pop('rel_generators', []) + site.pop('meta_generators', []):
            gen = SERVERS.get(gen, gen)
            servers = site.setdefault('servers', [])
            if gen not in servers:
                servers.append(gen)

        for server in site.get('servers', []):
            if server in SERVER_TAGS:
                tags.append(server)

        # crawl times
        fetch = site.pop('fetch_time', None)
        start = site.get('crawl_start')
        end = site.get('crawl_end')
        if start and end:
            convert = lambda ts: datetime(*time.gmtime(int(ts))[:6]).isoformat('T')
            site['crawl_start'] = convert(start)
            site['crawl_end'] = convert(end)
        elif fetch:
            site['crawl_start'] = site['crawl_end'] = fetch

        # links
        domain_links = links.get(domain, {})
        site.update({
            'hcard': json.loads(site.get('hcard', '{}')) or {},
            'links_out': out_counts.get(domain) or 0,
            'links_in': in_counts.get(domain) or 0,
            'links': OrderedDict(sorted(domain_links.items(),
                                        key=lambda item: item[1]['score'],
                                        reverse=True)),
        })

        yield site


def make_base(full):
    """Generates and returns output sites with capped number of link domains.

    Number of link domains per site is capped at MAX_BASE_LINKS.

    Args:
      full: sequence of full site objects created by make_full()

    Returns:
      sequence of output JSON site objects
    """
    print('\nGenerating base...', end='', flush=True)

    base = copy.deepcopy(full)
    for i, site in enumerate(base):
        if i and i % 100 == 0:
            print('.', end='', flush=True)
        if len(site['links']) > MAX_BASE_LINKS:
            site['links'] = OrderedDict(list(site['links'].items())[:MAX_BASE_LINKS])
            site['links_truncated'] = True

    return base


def make_internal(full, domains):
    """Generates and returns output JSON objects with links to internal domains.

    Internal domains are just the sites in the dataset itself.

    Args:
      full: sequence of full site objects created by make_full()
      domains: set of internal domains to limit links to

    Returns:
      sequence of output JSON site objects
    """
    print('\nGenerating internal..', end='', flush=True)

    internal = [copy.deepcopy(site) for site in full if site['domain'] in domains]

    for i, site in enumerate(internal):
        if i and i % 100 == 0:
            print('.', end='', flush=True)
        site['links'] = OrderedDict(
            (domain, val) for domain, val in site.get('links', {}).items()
            if domain in domains)

    return internal


def read_json(path):
    print('Loading %s...' % path)
    fn = gzip.open if path.endswith('.gz') else open
    with fn(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f]


def json_dump(dir, objs):
    os.makedirs(dir, exist_ok=True)
    for obj in objs:
        with open('%s/%s.json' % (dir, obj['domain']), 'wt', encoding='utf-8') as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)


if __name__ == '__main__':
    inputs = [read_json(path) for path in sys.argv[1:]]
    sites = inputs[0]

    print('Loading site files...', end='', flush=True)
    full = list(make_full(*inputs))
    json_dump('full', full)
    json_dump('base', make_base(full))

    domains = set(s['domain'] for s in sites)
    json_dump('internal', make_internal(full, domains))

    print()

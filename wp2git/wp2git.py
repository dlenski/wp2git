#!/usr/bin/env python3
from sys import stderr, stdout, version_info
from itertools import chain, count
from pathlib import Path
import argparse
import subprocess as sp
import urllib.parse as urlparse
import os, locale, time
import re
from datetime import datetime

import mwclient

from .version import __version__

lang, enc = locale.getlocale()
if lang == 'C':
    lang = None
elif lang is not None:
    lang = lang.split('_')[0]

def sanitize(s):
    forbidden = r'?*<>|:\/"'
    for c in forbidden:
        s = s.replace(c, '_')
    return s

def timestamp_num_or_iso(s):
    if s == 'now':
        return s
    try:
        return int(s, 10)
    except ValueError:
        try:
             return float(s)
        except ValueError:
            try:
                if version_info < (3, 11) and s.endswith('Z'):
                    return datetime.fromisoformat(s[:-1] + '+00:00')
                else:
                    return datetime.fromisoformat(s)
            except ValueError:
                return argparse.ArgumentError(f'Could not parse {s!r} as Unix epoch seconds, ISO8601 timestamp, or "now"')


def shortgit(git):
    return next(git[:ii] for ii in range(6, len(git)) if not git[:ii].isdigit())

def parse_args():
    p = argparse.ArgumentParser(description='Create a git repository with the history of one or more specified Wikipedia articles.')
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('article_name', nargs='+')
    g = p.add_argument_group('Git output options')
    g.add_argument('-n', '--no-import', dest='doimport', default=True, action='store_false',
                   help="Don't invoke git fast-import; only generate fast-import data stream")
    g.add_argument('-b', '--bare', action='store_true', help="Import to a bare repository (no working tree)")
    g.add_argument('-o', '--out', type=Path, help='Output directory (default is "wp2git") or fast-import stream file (defaults is stdout)')
    g = p.add_argument_group('Output cleanup')
    g.add_argument('-g', '--git-refs', action='store_true', help="Replace references to earlier revisions with their Git hashes")
    g.add_argument('-D', '--denoise', action='store_true', help='Simplify common "noisy" wikitext in comments')
    g = p.add_argument_group('MediaWiki site selection')
    x=g.add_mutually_exclusive_group()
    x.add_argument('--lang', default=lang, help='Wikipedia language code (default %(default)s)')
    x.add_argument('--site', help='Alternate MediaWiki site (e.g. https://commons.wikimedia.org[/w/])')
    g = p.add_argument_group('Time range restriction (accepted formats are Unix epoch seconds, ISO8601 timestamps, or "now")')
    g.add_argument('--not-before', '-B', type=timestamp_num_or_iso)
    g.add_argument('--not-after', '-A', type=timestamp_num_or_iso)

    args = p.parse_args()
    if args.doimport:
        if args.out is None:
            args.out = next(pp for n in chain(('',), count(2)) if not (pp := Path(f'wp2git{n}')).exists())
        if args.out.exists():
            p.error(f'path {args.out} exists')
        args.out.mkdir(parents=True)
    else:
        if args.bare or args.git_refs:
            p.error('--no-import cannot be combined with --bare or --git-refs')

        if args.out is None:
            args.out = stdout.buffer
        else:
            try:
                args.out = args.out.open('xb')
            except OSError as e:
                p.error(e.args[0])

    return p, args

def main():
    p, args = parse_args()

    # Connect to site with mwclient
    if args.site is not None:
        scheme, host, path = urlparse.urlparse(args.site, scheme='https')[:3]
        if path=='':
            path = '/w/'
        elif not path.endswith('/'):
            path += '/'
    elif args.lang is not None:
        scheme, host, path = 'https', f'{args.lang}.wikipedia.org', '/w/'
    else:
        scheme, host, path = 'https', 'wikipedia.org', '/w/'
    site = mwclient.Site(host, path=path, scheme=scheme)
    print(f'Connected to {scheme}://{host}{path}', file=stderr)

    # Find the page(s)
    fns = []
    rev_iters = []
    next_revs = []
    for an in args.article_name:
        page = site.pages[an]
        if not page.exists:
            p.error(f'Page {an} does not exist')
        fns.append(sanitize(an))

        revit = iter(page.revisions(dir='newer', prop='ids|timestamp|flags|comment|user|userid|content|tags',
                                    start=args.not_before, end=args.not_after))
        rev_iters.append(revit)
        next_revs.append(next(revit, None))

    if args.doimport:
        # Pipe to git fast-import
        sp.check_call(['git', 'init'] + (['--bare'] if args.bare else []), cwd=args.out)
        with open(args.out / ('.' if args.bare else '.git') / 'HEAD', 'rb') as f:
            head = f.read().removeprefix(b'ref: ').strip()
        pipe = sp.Popen(['git', 'fast-import', '--quiet', '--done'], stdin=sp.PIPE, stdout=sp.PIPE, cwd=args.out)
        fid = pipe.stdin
    else:
        fid = args.out
        head = b'refs/heads/master'

    # Output fast-import data stream to file or git pipe
    with fid:
        fid.write(b'reset %s\n' % head)
        id2git = {}

        # Round robin through all the pages' revisions, ordering by timestamp
        while any(next_revs):
            # Pick which of the pages' revisions has the lowest timestamp
            min_ts = (1<<63)
            ii = -1
            for ii, rev in enumerate(next_revs):
                if rev and time.mktime(rev['timestamp']) < min_ts:
                    min_ii, min_ts = ii, time.mktime(rev['timestamp'])
            else:
                rev = next_revs[min_ii]
                fn = fns[min_ii]

            id = rev['revid']
            id2git[id] = None
            text = rev.get('*','')
            user = rev.get('user','')
            user_ = user.replace(' ','_')
            comment = rev.get('comment','')
            userid = rev['userid'] or None  # this is zero for anon/IP users
            tags = (['minor'] if 'minor' in rev else []) + rev['tags']
            ts = time.mktime(rev['timestamp'])

            userlink = f'{scheme}://{host}{path}index.php?title=' + (f'Special:Redirect/user/{userid}' if userid else f"User:{urlparse.quote(user_)}")
            committer = f"{user.replace('<',' ').replace('>',' ')} <>"   # I don't think Wikipedia allows this, but other Mediawiki sites do

            print(f"{time.ctime(ts)} >> {'Minor ' if 'minor' in rev else '      '}Revision {id}"
                  f"{' of ' + args.article_name[min_ii] if len(args.article_name) > 1 else ''} by {user}: {comment}", file=stderr)

            # TODO: get and use 'parsedcomment' which HTML-ifies the comment?
            # May make identification of links to revisions, users, etc. much easier
            refs = set()
            if args.doimport:
                for num in map(lambda n: int(n, 10), re.findall(r'\b\d+\b', comment)):
                    if num in id2git:
                        if id2git[num] is None:
                            fid.write(b'get-mark :%d\n' % num)
                            fid.flush()
                            id2git[num] = pipe.stdout.readline().strip().decode()
                        refs.add(num)

                if args.git_refs:
                    for num in refs:
                        comment = re.sub(r'\[\[(?::?%s:)?Special\:Diff/%d\s*(?:\|[^]]*)?\]\]' % (args.lang, num), shortgit(id2git[num]), comment, flags=re.IGNORECASE)
                        comment = re.sub(r'\b%d\b' % num, shortgit(id2git[num]), comment)

            section_frag = ''
            if m := re.search(r'^\s*/\*\s*(.*?)\s*\*/\s*', comment):
                section = m.group(1)
                section_frag = f'#{urlparse.quote(section.replace(" ", "_"))}'
                if args.denoise:
                    if m.group(0) == comment:
                        comment = f'Edited section "{section}"'
                    else:
                        comment = comment.replace(m.group(0), '', 1)

            if args.denoise:
                comment = re.sub(r'\[\[(?::?%s:)?Special\:Contrib(?:ution)?s/([^]|]+)\s*(?:\|[^]]*)?\]\](?:\s* \(\[\[User talk\:[^]]+\]\]\))?' % args.lang, r'\1', comment, flags=re.IGNORECASE)
                comment = re.sub(r'^\[\[WP:UNDO\|Undid\]\] ', 'Undid ', comment)

            if not comment:
                comment = '<blank>'

            summary = f'{comment}\n\nURL: {scheme}://{host}{path}index.php?oldid={id:d}{section_frag}\nEditor: {userlink}'

            if tags:
                summary += '\nTags: ' + ', '.join(tags)
            if refs and not args.git_refs:
                summary += '\nReferences: ' + ', '.join(f'{r} ({id2git[r]})' for r in refs)

            summary = summary.encode()
            text = text.encode()
            fid.write(b'commit %s\n' % head)
            fid.write(b'mark :%d\n' % id)
            fid.write(b'committer %s %d +0000\n' % (committer.encode(), ts))
            fid.write(b'data %d\n%s\n' % (len(summary), summary))
            fid.write(b'M 644 inline %s.mw\n' % fn.encode())
            fid.write(b'data %d\n%s\n' % (len(text), text))

            # Get the next revision for the page we just output
            next_revs[min_ii] = next(rev_iters[min_ii], None)
        else:
            fid.write(b'done\n')

    if args.doimport:
        retcode = pipe.wait()
        if retcode != 0:
            p.error(f'git fast-import returned {retcode}')
        if not args.bare:
            sp.check_call(['git', 'checkout', '-q', head.decode().removeprefix('refs/heads/')], cwd=args.out)
        print(f'Created git repository in {args.out}', file=stderr)

if __name__=='__main__':
    main()

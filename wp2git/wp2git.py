#!/usr/bin/env python3
from sys import stderr, stdout
from itertools import chain, count
from pathlib import Path
import argparse
import subprocess as sp
import urllib.parse as urlparse
import os, locale, time
import re

import mwclient

from .version import __version__

import logging
logging.basicConfig(level=logging.DEBUG)

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

    args = p.parse_args()
    if args.doimport:
        if args.out is None:
            args.out = next(pp for n in chain(('',), count(2)) if not (pp := Path(f'wp2git{n}')).exists())
        if args.out.exists():
            # # Try to incrementally build
            # args.bare = sp.check_output(['git', 'rev-parse', '--is-bare-repository'], cwd=args.out)
            # with open(args.out / ('.' if args.bare else '.git') / 'HEAD', 'rb') as f:
            #     head = f.read().removeprefix(b'ref: ').strip()
            # githash, *cmesg = sp.check_output(['git', 'show', '-s', '--format=%H%n%B', head.decode()], cwd=args.out, text=True).split('\n', 1)
            # lastrev = None
            # if cmesg:
            #     _, *trailers = cmesg[0].rsplit('\n\n', 1)
            #     if trailers:
            #         trailers = dict(l.split(': ', 1) for l in trailers[0].splitlines())
            #         if trailers.get['URL']
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
    lasttext = [None] * len(args.article_name)
    for an in args.article_name:
        page = site.pages[an]
        if not page.exists:
            p.error(f'Page {an} does not exist')
        fns.append(sanitize(an))

        revit = iter(page.revisions(dir='newer', prop='ids|timestamp|flags|comment|user|userid|tags', max_items=10))
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
            #text = rev.get('*','')
            user = rev.get('user','')
            user_ = user.replace(' ','_')
            comment = rev.get('comment','')
            userid = rev['userid']
            tags = (['minor'] if 'minor' in rev else []) + rev['tags']
            ts = time.mktime(rev['timestamp'])

            prevtext: bytes = lasttext[min_ii]
            if prevtext is None:
                text = next(site.pages[args.article_name[min_ii]].revisions(prop='content', startid=id, endid=id)).get('*', '')
                lasttext[min_ii] = text.encode()
            else:
                res = site.connection.get(f'{scheme}://{host}{path}rest.php/v1/revision/{rev["parentid"]}/compare/{id}')  # hacky
                res.raise_for_status()

                output: bytes = b''

                from_pos = 0
                for diff in res.json()['diff']:
                    import pprint
                    pprint.pprint(diff)

                    # https://www.mediawiki.org/wiki/API:REST_API/Reference#Response_schema_3
                    # The positions in the diff are byte positions, not utf8 character positions, eesh 😒
                    text = diff['text'].encode()
                    lt = len(text)

                    ty = diff['type']
                    if ty in (1, 2, 3):
                        print(f"****Before type={ty}: len(output)={len(output)}, from_pos={from_pos}")

                    if ty == 0:  # context
                        # Just check input matches
                        assert diff['offset']['from'] >= from_pos
                        assert diff['offset']['from'] + lt <= len(prevtext)
                        assert diff['offset']['to'] >= len(output)
                        assert prevtext[diff['offset']['from']:].startswith(text)
                    elif ty == 1:  # add
                        # Copy everything from input until output reaches diff['offset']['to'] in length
                        nn = from_pos + (diff['offset']['to'] - len(output))
                        assert len(prevtext) >= nn >= from_pos, \
                            f"{len(prevtext)} >= {nn} >= {from_pos}"
                        output += prevtext[from_pos: nn]
                        from_pos = nn
                        # ... then add new text
                        output += text + b'\n'
                    elif ty == 2:  # delete
                        # Copy everything from input up to diff['offset']['from']
                        nn = diff['offset']['from']
                        assert len(prevtext) >= nn >= from_pos, \
                            f"{len(prevtext)} >= {nn} >= {from_pos}"
                        output += prevtext[from_pos: nn]
                        # ... then skip text and following '\n'
                        assert prevtext[from_pos:].startswith(text), \
                            f"{prevtext[from_pos:]}.startswith({text!r})"
                        from_pos += lt
                        if from_pos != len(prevtext):  # trailing '\n' inconsistency
                            assert prevtext[from_pos] == 10
                            from_pos += 1
                    elif ty == 3:  # change
                        # Copy everything from input up to diff['offset']['from']
                        nn = diff['offset']['from']
                        assert len(prevtext) >= nn >= from_pos, \
                            f"{len(prevtext)} >= {nn} >= {from_pos}"
                        output += prevtext[from_pos: nn]
                        # ... and check that output reaches diff['offset']['to'] in length
                        if diff['offset']['to'] is not None:
                            assert len(output) == diff['offset']['to'], \
                                f"{len(output)} == {diff['offset']['to']}\n{output}"
                        # ... and add new text, while keeping unchanged text
                        pos = drop = 0
                        for r in diff['highlightRanges']:
                            start = r['start']
                            end = r['start'] + r['length']
                            # add "between-range" unchanged text
                            output += text[pos: start]
                            pos = end
                            if r['type'] == 0:         # addition
                                assert start <= end <= lt
                                output += text[start: end]
                                drop += end - start
                            else:
                                assert r['type'] == 1  # deletion
                        else:
                            output += text[pos:] + b'\n'
                        # ... and skip unchanged and deleted text in the input
                        from_pos = nn + lt - drop
                        if from_pos != len(prevtext):  # trailing '\n' inconsistency
                            assert prevtext[from_pos] == 10
                            from_pos += 1
                    elif ty in (4,5): # moved paragraph
                        pass
                    else:
                        assert False, f"Can't handle diff {diff!r}"

                    if ty in (1, 2, 3):
                        print(f"****After type={ty}: len(output)={len(output)}, from_pos={from_pos},\noutput={output.decode()!r},\nprevtext[from_pos:]={prevtext[from_pos:].decode()!r}")
                else:
                    # Add remaining text
                    output += prevtext[from_pos:]

                lasttext[min_ii] = output
                text = next(site.pages[args.article_name[min_ii]].revisions(prop='content', startid=id, endid=id)).get('*', '')
                if output.decode() not in (text, text+'\n'):
                    open('/tmp/text.mw', 'w').write(text)
                    open('/tmp/output.mw', 'wb').write(output)
                    sp.call(['git', '--no-pager', 'diff', '--no-index', '/tmp/text.mw', '/tmp/output.mw'])
                    p.error('mismatch')
                else:
                    print(f'**************************************************')
                    print(f'****** {rev["parentid"]} -> {id} diff fixup successful ******')
                    print(f'**************************************************')

            userlink = f'{scheme}://{host}{path}index.php?title=' + (f'Special:Redirect/user/{userid}' if userid else f"User:{urlparse.quote(user_)}")
            committer = f"{user.replace('<',' ').replace('>',' ')} <>"

            print(f"{time.ctime(ts)} >> {'Minor ' if 'minor' in rev else '      '}Revision {id}"
                  f"{' of ' + args.article_name[min_ii] if len(args.article_name) > 1 else ''} by {user} {rev.get('userid')}: {comment}", file=stderr)

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

#!/usr/bin/env python3
from sys import stderr, stdout, platform
import argparse
import mwclient
import subprocess as sp
import urllib.parse as urlparse
import os, locale, time
import re
from .version import __version__

locale_encoding = locale.getpreferredencoding()
lang = locale.getdefaultlocale()[0].split('_')[0] or ''

def sanitize(s):
    forbidden = r'?*<>|:\/"'
    for c in forbidden:
        s = s.replace(c, '_')
    return s

def shortgit(git):
    return next(git[:ii] for ii in range(6, len(git)) if not git[:ii].isdigit())

def parse_args():
    p = argparse.ArgumentParser(description='Create a git repository with the history of the specified Wikipedia article.')
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('article_name')
    g = p.add_argument_group('Output options')
    g.add_argument('-n', '--no-import', dest='doimport', default=True, action='store_false',
                   help="Don't invoke git fast-import; only generate fast-import data stream")
    g.add_argument('-b', '--bare', action='store_true', help="Import to a bare repository (no working tree)")
    g.add_argument('-o', '--out', help='Output directory or fast-import stream file')
    g.add_argument('-g', '--git-refs', action='store_true', help="Replace references to earlier revisions with their Git hashes")
    g.add_argument('-D', '--denoise', action='store_true', help='Simplify common noisy wikitext in comments')
    g = p.add_argument_group('MediaWiki site selection')
    x=g.add_mutually_exclusive_group()
    x.add_argument('--lang', default=lang, help='Wikipedia language code (default %(default)s)')
    x.add_argument('--site', help='Alternate MediaWiki site (e.g. https://commons.wikimedia.org[/w/])')

    args = p.parse_args()
    if not args.doimport:
        if args.bare or args.git_refs:
            p.error('--no-import cannot be combined with --bare or --git-refs')

        if args.out is None:
            # http://stackoverflow.com/a/2374507/20789
            if platform == "win32":
                import os, msvcrt
                msvcrt.setmode(stdout.fileno(), os.O_BINARY)
            args.out = stdout
        else:
            try:
                args.out = argparse.FileType('wb')(args.out)
            except argparse.ArgumentTypeError as e:
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
        scheme, host, path = 'https', '%s.wikipedia.org' % args.lang, '/w/'
    else:
        scheme, host, path = 'https', 'wikipedia.org', '/w/'
    site = mwclient.Site(host, path=path, scheme=scheme)
    print('Connected to %s://%s%s' % (scheme, host, path), file=stderr)

    # Find the page
    page = site.pages[args.article_name]
    if not page.exists:
        p.error('Page %s does not exist' % args.article_name)
    fn = sanitize(args.article_name)

    if args.doimport:
        # Create output directory and pipe to git
        if args.out is not None:
            out = args.out
        else:
            out = fn

        if os.path.exists(out):
            p.error('path %s exists' % out)
        else:
            os.mkdir(out)
            sp.check_call(['git','init'] + (['--bare'] if args.bare else []), cwd=out)
            pipe = sp.Popen(['git', 'fast-import','--quiet','--done'], stdin=sp.PIPE, stdout=sp.PIPE, cwd=out)
            fid = pipe.stdin
    else:
        fid = args.out

    # Output fast-import data stream to file or git pipe
    with fid:
        fid.write(b'reset refs/heads/master\n')
        id2git = {}
        for rev in page.revisions(dir='newer', prop='ids|timestamp|flags|comment|user|userid|content|tags'):
            id = rev['revid']
            id2git[id] = None
            text = rev.get('*','')
            user = rev.get('user','')
            user_ = user.replace(' ','_')
            comment = rev.get('comment','')
            tags = (['minor'] if 'minor' in rev else []) + rev['tags']
            ts = time.mktime(rev['timestamp'])

            userlink = f'{scheme}://{host}{path}index.php?title=User:{urlparse.quote(user_)}'
            committer = f'{user} <>'

            print((" >> %sRevision %d by %s at %s: %s" % (('Minor ' if 'minor' in rev else ''), id, user,
                time.ctime(ts), comment)), file=stderr)

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

            if args.denoise:
                comment = re.sub(r'\[\[(?::?%s:)?Special\:Contrib(?:ution)?s/([^]|]+)\s*(?:\|[^]]*)?\]\](?:\s* \(\[\[User talk\:[^]]+\]\]\))?' % args.lang, r'\1', comment, flags=re.IGNORECASE)
                comment = re.sub(r'^\s*/\*\s*([^*]*?)\s*\*/\s*', lambda m: f'Edited section "{m.group(1)}"' if m.group(0)==comment else '', comment)
                comment = re.sub(r'^\[\[WP:UNDO\|Undid\]\] ', 'Undid ', comment)

            if not comment:
                comment = '<blank>'

            summary = f'{comment}\n\nURL: {scheme}://{host}{path}index.php?oldid={id:d}\nEditor: {userlink}'

            if tags:
                summary += '\nTags: ' + ', '.join(tags)
            if refs and not args.git_refs:
                summary += '\nReferences: ' + ', '.join('%d (%s)' % (r, id2git[r]) for r in refs)

            summary = summary.encode()
            text = text.encode()
            fid.write(b'commit refs/heads/master\n')
            fid.write(b'mark :%d\n' % id)
            fid.write(b'committer %s %d +0000\n' % (committer.encode(), ts))
            fid.write(b'data %d\n%s\n' % (len(summary), summary))
            fid.write(b'M 644 inline %s.mw\n' % fn.encode())
            fid.write(b'data %d\n%s\n' % (len(text), text))
        fid.write(b'done\n')

    if args.doimport:
        retcode = pipe.wait()
        if retcode != 0:
            p.error('git fast-import returned %d' % retcode)
        if not args.bare:
            sp.check_call(['git','checkout'], cwd=out)

if __name__=='__main__':
    main()

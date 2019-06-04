#!/usr/bin/env python2
from __future__ import print_function

from sys import stderr, stdout, platform
import argparse
import mwclient
import subprocess as sp
import urlparse
import os, locale, time
from .version import __version__

locale_encoding = locale.getpreferredencoding()
lang = locale.getdefaultlocale()[0].split('_')[0] or ''

def sanitize(s):
    forbidden = r'?*<>|:\/"'
    for c in forbidden:
        s = s.replace(c, '_')
    return s

def parse_args():
    p = argparse.ArgumentParser(description='Create a git repository with the history of the specified Wikipedia article.')
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('article_name')
    g2 = p.add_argument_group('Output options')
    g=g2.add_mutually_exclusive_group()
    g.add_argument('-n', '--no-import', dest='doimport', default=True, action='store_false',
                   help="Don't invoke git fast-import; only generate fast-import data stream")
    g.add_argument('-b', '--bare', action='store_true', help="Import to a bare repository (no working tree)")
    g2.add_argument('-o', '--out', help='Output directory or fast-import stream file')
    g2 = p.add_argument_group('MediaWiki site selection')
    g=g2.add_mutually_exclusive_group()
    g.add_argument('--lang', default=lang, help='Wikipedia language code (default %(default)s)')
    g.add_argument('--site', help='Alternate MediaWiki site (e.g. https://commons.wikimedia.org[/w/])')

    args = p.parse_args()
    if not args.doimport:
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
    site = mwclient.Site((scheme, host), path=path)
    print('Connected to %s://%s%s' % (scheme, host, path), file=stderr)

    # Find the page
    page = site.pages[args.article_name.decode(locale_encoding)]
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
            pipe = sp.Popen(['git', 'fast-import','--quiet','--done'], stdin=sp.PIPE, cwd=out)
            fid = pipe.stdin
    else:
        fid = args.out

    # Output fast-import data stream to file or git pipe
    with fid:
        fid.write('reset refs/heads/master\n')
        for rev in page.revisions(dir='newer', prop='ids|timestamp|flags|comment|user|userid|content|tags'):
            id = rev['revid']
            text = rev.get('*','').encode('utf8')
            user = rev.get('user','').encode('utf8')
            user_ = user.replace(' ','_')
            comment = rev.get('comment','').encode('utf8') or '<blank>'
            tags = (['minor'] if 'minor' in rev else []) + [tag.encode('utf8') for tag in rev['tags']]
            ts = time.mktime(rev['timestamp'])

            if rev.get('userid'):
                committer = '%s <%s@%s>' % (user,user_,host)
            else:
                committer = '%s <>' % user

            print((" >> %sRevision %d by %s at %s: %s" % (('Minor ' if 'minor' in rev else ''), id, rev.get('user',''),
                time.ctime(ts), rev.get('comment',''))).encode('ascii','xmlcharrefreplace'), file=stderr)

            summary = '{comment}\n\nURL: {scheme}://{host}{path}index.php?oldid={id:d}\nEditor: {scheme}://{host}{path}index.php?title=User:{user_}'.format(
                comment=comment, scheme=scheme, host=host, path=path, id=id, user_=user_)

            if tags:
                summary += '\nTags: ' + ', '.join(tags)

            fid.write('commit refs/heads/master\n')
            fid.write('committer %s %d +0000\n' % (committer, ts))
            fid.write('data %d\n%s\n' % (len(summary), summary))
            fid.write('M 644 inline %s.mw\n' % fn)
            fid.write('data %d\n%s\n' % (len(text), text))
        fid.write('done\n')

    if args.doimport:
        pipe.communicate()
        if not args.bare:
            sp.check_call(['git','checkout'], cwd=out)

if __name__=='__main__':
    main()

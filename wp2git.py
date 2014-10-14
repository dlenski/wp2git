#!/usr/bin/env python2
from __future__ import print_function

from sys import stderr, stdout
import argparse
import mwclient
import subprocess as sp
import os, locale, time

lang = locale.getdefaultlocale()[0].split('_')[0] or ''

def sanitize(s):
    forbidden = r'?*<>|:\/"'
    for c in forbidden:
        s = s.replace(c, '_')
    return s

def parse_args():
    p = argparse.ArgumentParser(description='Create a git repository with the history of the specified Wikipedia article.')
    p.add_argument('article_name')
    p.add_argument('--no-import', dest='doimport', default=True, action='store_false',
                        help="Don't invoke git fast-import and only generate the fast-import data")
    p.add_argument('-o','--outdir', help='Output directory')
    g=p.add_mutually_exclusive_group()
    g.add_argument('--lang', default=lang, help='Wikipedia language code (default %(default)s)')
    g.add_argument('--site', help='Alternate site (e.g. commons.wikimedia.org)')
    return p, p.parse_args()

def main():
    p, args = parse_args()

    # Connect to site with mwclient
    if args.site is not None:
        s = args.site
    elif args.lang is not None:
        s = '%s.wikipedia.org' % args.lang
    else:
        s = 'wikipedia.org'

    site = mwclient.Site(s)
    print('Connected to site %s.' % s, file=stderr)

    # Find the page
    page = site.pages[args.article_name]
    if not page.exists:
        p.error('Page %s does not exist' % s)

    # Create output directory
    fn = sanitize(args.article_name)
    if args.outdir is not None:
        path = args.outdir
    else:
        path = fn

    if os.path.exists(path):
        p.error('Path %s exists' % path)
    os.mkdir(path)
    os.chdir(path)

    # Create fast-import data stream
    with open('fast-import-data', 'w+b') as fid:
        fid.write('reset refs/heads/master\n')
        for rev in page.revisions(dir='newer', prop='ids|timestamp|flags|comment|user|content'):
            id = rev['revid']
            text = rev.get('*','').encode('utf8')
            committer = '%s@%s' % (rev['user'].encode('utf8'), site.host)
            ts = time.mktime(rev['timestamp'])
            print(" >> Revision %d by %s at %s: %s" % (id, rev['user'], rev['comment'], time.ctime(ts)), file=stderr)

            summary = '%s\n\nURL: http://%s%sindex.php?oldid=%d' % (rev['comment'].encode('utf8') or '<blank>', site.host, site.path, id)

            fid.write('commit refs/heads/master\n')
            fid.write('committer <%s> %d +0000\n' % (committer, ts))
            fid.write('data %d\n%s\n' % (len(summary), summary))
            fid.write('M 644 inline %s.mw\n' % fn)
            fid.write('data %d\n%s\n' % (len(text), text))
        fid.write('done\n')

        if args.doimport:
            sp.check_call(['git','init','--bare'])
            fid.seek(0, 0)
            sp.check_call(['git', 'fast-import','--quiet'], stdin=fid)

    if args.doimport:
       os.unlink('fast-import-data')

if __name__=='__main__':
    main()

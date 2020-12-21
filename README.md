wp2git
======

This program allows you to download and convert any Wikipedia article's history to a `git` repository, for easy browsing, [annotation](https://git-scm.com/docs/git-annotate),
and [bisecting](https://git-scm.com/docs/git-annotate) (etc.) of older revisions.

### Requirements

Requires Python 3.x and `git` accessible in your `PATH`, and the [`mwclient` package](https://github.com/mwclient/mwclient)
(which will be auto-installed by `pip`).

### Quick installation

For the latest release, install with:

```
pip3 install https://github.com/dlenski/wp2git/archive/v2.0.zip
```

For the latest development build, install with

```
pip3 install https://github.com/dlenski/wp2git/archive/master.zip
```

### Usage

```
$ wp2git [--lang XY] article_name
```

`wp2git` will create a directory, in which a new `git` repository will be created.
The repository will contain a single file named `article_name.mw`, along with the entire edit history
of that article on `XY.wikipedia.org`. (If unspecified, the default language is guessed according to
your locale.)

Use `wp2git --help` to show more options.

### Entirely based on

[CyberShadow's version](https://github.com/CyberShadow/wp2git) written in the D language.

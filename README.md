wp2git
======

This program allows you to download and convert any Wikipedia article's history to a `git` repository, for easy browsing and blaming.

### Quick installation

```
pip install https://github.com/dlenski/wp2git/archive/v1.0.zip
```

### Usage

    $ wp2git [--bare] article_name

`wp2git` will create a directory, in which a new `git` repository will be created.
The repository will contain a single file named `article_name.mw`, along with its entire edit history.

Run `wp2git --help` for more options.

### Requirements

`git` should be accessible from `PATH`. The [`mwclient` package](http://github.com/mwclient/mwclient)
is required.

### Entirely based on

[CyberShadow's version](http://github.com/CyberShadow/wp2git) written in the D language.

# sebsync

sebsync is a simple script to synchronize the Standard Ebooks catalog with your local EPUB
collection.

## How it works

The script reads the Standard Ebooks OPDS catalog and reconciles their ebooks against the
EPUBs in your local filesystem, downloading new ones or updating existing ones as necessary.

If a new ebook is detected in the catalog, then it will be downloaded into your downloads
directory. If an updated version of a book is detected in the catalog that is already in your
local books or download directories, then the updated version will be downloaded and stored
in the existing local file.

The script can also detect extraneous local ebooks (local ebooks not found in Standard Ebooks
catalog). This can can occur when Standard Ebooks changes the identifier of a previously
published ebook. It's a rare occurance, and it's generally safe to delete such files.

## Requirements

This script uses Standard Ebooks' [OPDS feed](https://standardebooks.org/feeds) to get metadata
on all books their catalog. In order to do so, you need to be a member of the Standard Ebooks
[Patrons Circle](https://standardebooks.org/donate#patrons-circle) (or have previously produced
an ebook for Standard Ebooks). If you're not already a Patron, please consider becoming one;
it's a tremendously valuable project, deserving of your support.

## Installation

```
pipx install sebsync
```

## Example usage

```
sebsync --email addr@example.com --books /home/user/MyBooks --downloads /home/user/MyBooks/Downloads
```

## Questions and answers

**Q1.** Why use a separate downloads directory for new ebooks?

**A1.** This is a feature for those who want to easily recognize new ebooks, and to manually
rename and/or categorize them within their library. If this feature is not useful for you,
simply set `--downloads` to be the same directory as `--books`.

**Q2.** What is pipx?

**A2.** [pipx](https://pipx.pypa.io/) is a utility that allows Python packages like sebsync to
be installed in an isolated environment. This makes it really easy to install and run such
scripts without interfering with other Python package installations.

# sebsync

sebsync is a simple script to synchronize all EPUBs from Standard Ebooks to your local
filesystem. It detects any new and updated titles and downloads them automatically.

## How it works

The `sebsync` script reads the Standard Ebooks OPDS catalog and reconciles their titles against
EPUBs in your local filesystem, downloading new ones or updating existing ones as necessary.

If you're member of the Standard Ebooks
[Patrons Circle](https://standardebooks.org/donate#patrons-circle), or have produced an ebook
for them in the past, you may access their [OPDS feed](https://standardebooks.org/feeds),
a catalog of all published titles. This allows new and updated titles to be easily recognized.

If a title is detected in the catalog that is not present in your local books or downloads
directory, then it will be downloaded into your downloads directory. If a new version of a book
is detected in the catalog that is already in your local books or download directory, then the
new version will be downloaded and stored in the same place it was found.

## Installation

```
pipx install sebsync
```

### Example usage

```
sebsync --email address@example.com --books /home/user/MyBooks --downloads /home/user/MyBooks/Downloads
```

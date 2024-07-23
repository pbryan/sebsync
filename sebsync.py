"""Synchronize Standard Ebooks catalog with local ebooks."""

import click
import requests
import xml.etree.ElementTree as ElementTree
import zipfile

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from requests.auth import HTTPBasicAuth


_dry_run: bool = False
_quiet: bool = False
_verbose: bool = False


_epilog = """
    All options can be provided through environment variables in all-caps with SEBSYNC_ prefix.
"""


@dataclass
class StandardEbook:
    """Metadata for ebook in Standard Ebooks OPDS catalog."""

    id: str
    title: str
    author: str
    href: str
    updated: datetime


@dataclass
class LocalEbook:
    """Metadata for ebook in local directory."""

    id: str
    title: str
    path: Path
    modified: datetime


def _if_exists(path: Path) -> Path | None:
    return path if path.exists() else None


def _fromisoformat(text: str) -> datetime:
    """Convert RFC 3339 string into datetime; compatible with Python 3.10."""
    if not text.endswith("Z"):
        raise ValueError("expecting RFC 3339 formatted string")
    d = datetime.fromisoformat(text.rstrip("Z"))
    return datetime(
        d.year, d.month, d.day, d.hour, d.minute, d.second, d.microsecond, timezone.utc
    )


def get_standard_ebooks(opds: str, email: str) -> dict[str, StandardEbook]:
    """Return Standard Ebooks metadata for EPUBs from the OPDS catalog."""
    ns = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/terms/"}
    ebooks = {}
    response = requests.get(opds, stream=True, auth=HTTPBasicAuth(email, ""))
    response.raw.decode_content = True
    root = ElementTree.parse(response.raw).getroot()
    for entry in root.iterfind(".//atom:entry", ns):
        ebook = StandardEbook(
            id=entry.find("dc:identifier", ns).text,
            title=entry.find("atom:title", ns).text,
            author=entry.find("atom:author", ns).find("atom:name", ns).text,
            href=entry.find(".//atom:link[@title='Recommended compatible epub']", ns).attrib[
                "href"
            ],
            updated=_fromisoformat(entry.find("atom:updated", ns).text),
        )
        ebooks[ebook.id] = ebook
    return ebooks


def get_local_ebooks(dir: Path) -> dict[str, LocalEbook]:
    """Return metadata of Standard EPUBs in the specified directory and subdirectories."""
    ebooks = {}
    for path in dir.glob("**/*.epub"):
        with zipfile.ZipFile(path) as zip:
            with zip.open("META-INF/container.xml") as file:
                root = ElementTree.parse(file)
                ns = {"container": "urn:oasis:names:tc:opendocument:xmlns:container"}
                rootfile = root.find(".//container:rootfile", ns).attrib["full-path"]
            with zip.open(rootfile) as file:
                root = ElementTree.parse(file)
                ns = {
                    "opf": "http://www.idpf.org/2007/opf",
                    "dc": "http://purl.org/dc/elements/1.1/",
                }
                metadata = root.find("opf:metadata", ns)
                id = metadata.find("dc:identifier", ns)
                if id is None or "standardebooks.org" not in id.text:
                    continue
                modified = metadata.find(".//opf:meta[@property='dcterms:modified']", ns)
                ebook = LocalEbook(
                    id=id.text,
                    title=metadata.find(".//dc:title", ns).text,
                    path=path,
                    modified=_fromisoformat(modified.text),
                )
                ebooks[ebook.id] = ebook
    return ebooks


def download_ebook(url: str, path: Path) -> None:
    """Download the ebook at the specified URL into the specified path."""
    if not _quiet:
        click.echo(f"Download: {path}")
    if not _dry_run:
        response = requests.get(url, stream=True)
        with path.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1 * 1024 * 1024):
                file.write(chunk)


def ebook_filename(ebook: StandardEbook) -> str:
    """Return an appropriate EPUB file name for the given ebook author and title."""
    replace = {"/": "-", "‘": "'", "’": "'", '"': "'", "“": "'", "”": "'"}
    author = ebook.author
    title = ebook.title
    names = author.split()
    if len(names) > 1:
        author = f"{names[-1]}, {' '.join(names[:-1])}"
    result = f"{author} - {title}.epub"
    for k, v in replace.items():
        result = result.replace(k, v)
    return result


@click.command(help=__doc__, epilog=_epilog)
@click.option(
    "--books",
    help="directory where local books are stored",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=_if_exists(Path.home() / "Books"),
)
@click.option(
    "--downloads",
    help="directory where ebooks are downloaded",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, writable=True, path_type=Path),
    default=_if_exists(Path.home() / "Downloads"),
)
@click.option(
    "--dry-run",
    help="perform a trial run with no changes made",
    is_flag=True,
)
@click.option(
    "--email",
    help="email to authenticate with Standard Ebooks",
    required=True,
)
@click.option(
    "--opds",
    help="URL of Standard Ebooks OPDS catalog",
    default="https://standardebooks.org/feeds/opds/all",
)
@click.option(
    "--quiet",
    help="suppress non-error messages",
    is_flag=True,
)
@click.option(
    "--verbose",
    help="increase verbosity",
    is_flag=True,
)
def sebsync(
    books: str, downloads: str, dry_run: bool, email: str, opds: str, quiet: bool, verbose: bool
):
    global _dry_run
    global _verbose
    global _quiet

    _dry_run = dry_run
    _verbose = verbose
    _quiet = quiet

    remote_ebooks = get_standard_ebooks(opds, email)
    if not remote_ebooks:
        raise click.ClickException("Email address rejected by Standard Ebooks.")
    if _verbose:
        click.echo(f"Found {len(remote_ebooks)} remote Standard Ebooks titles.")

    local_ebooks = get_local_ebooks(downloads) | get_local_ebooks(books)
    if _verbose:
        click.echo(f"Found {len(local_ebooks)} local Standard Ebooks titles.")

    for remote_ebook in remote_ebooks.values():
        if local_ebook := local_ebooks.get(remote_ebook.id):
            if remote_ebook.updated != local_ebook.modified:
                download_ebook(remote_ebook.href, local_ebook.path)
        else:
            path = downloads / ebook_filename(remote_ebook)
            download_ebook(remote_ebook.href, path)

    if not _quiet:
        for local_ebook in local_ebooks.values():
            if local_ebook.id not in remote_ebooks:
                click.secho(f"Extra: {local_ebook.path}", fg="yellow")


def main():
    sebsync(auto_envvar_prefix="SEBSYNC", show_default=True)


if __name__ == "__main__":
    main()

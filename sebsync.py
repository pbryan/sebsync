"""Synchronize Standard Ebooks catalog with local EPUB collection."""

import click
import requests
import xml.etree.ElementTree as ElementTree
import zipfile

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse


_dry_run: bool = False
_quiet: bool = False
_verbose: bool = False


class Status:
    NEW = click.style("N", fg="green")
    UPDATE = click.style("U", fg="blue")
    EXTRA = click.style("X", fg="yellow")
    UNKNOWN = click.style("?", fg="yellow")


_epilog = f"""
    \b
    Download naming conventions:
    • standard: Standard Ebooks' naming (e.g. "edwin-a-abbott_flatland.epub")
    • sortable: sortable author/title (e.g. "Abbott, Edwin A. - Flatland.epub")

    \b
    Reported file statuses:
    • {Status.NEW}: new (downloads the new ebook to downloads directory)
    • {Status.UPDATE}: update (overwrites the existing local ebook with new version)
    • {Status.EXTRA}: extraneous (local ebook not found in Standard Ebooks catalog)
    • {Status.UNKNOWN}: unknown (local ebook could not be processed)

    See https://github.com/pbryan/sebsync/ for updates, bug reports and answers.
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


# map type selection to link title in OPDS catalog
type_selector = {
    "compatible": "Recommended compatible epub",
    "kobo": "Kobo Kepub epub",
    "advanced": "Advanced epub",
}


def echo_status(path: Path, status: str) -> None:
    if not _quiet:
        click.echo(f"{status} {path}")


def if_exists(path: Path) -> Path | None:
    return path if path.exists() else None


def fromisoformat(text: str) -> datetime:
    """Convert RFC 3339 string into datetime; compatible with Python 3.10."""
    if not text.endswith("Z"):
        raise ValueError("expecting RFC 3339 formatted string")
    d = datetime.fromisoformat(text.rstrip("Z"))
    return datetime(
        d.year, d.month, d.day, d.hour, d.minute, d.second, d.microsecond, timezone.utc
    )


def get_remote_ebooks(opds_url: str, email: str, type: str) -> dict[str, StandardEbook]:
    """Return Standard Ebooks metadata for EPUBs from the OPDS catalog."""
    ns = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/terms/"}
    ebooks = {}
    response = requests.get(opds_url, stream=True, auth=HTTPBasicAuth(email, ""))
    response.raw.decode_content = True
    root = ElementTree.parse(response.raw).getroot()
    for entry in root.iterfind(".//atom:entry", ns):
        ebook = StandardEbook(
            id=entry.find("dc:identifier", ns).text,
            title=entry.find("atom:title", ns).text,
            author=entry.find("atom:author", ns).find("atom:name", ns).text,
            href=entry.find(f".//atom:link[@title='{type_selector[type]}']", ns).attrib["href"],
            updated=fromisoformat(entry.find("atom:updated", ns).text),
        )
        ebooks[ebook.id] = ebook
    if not ebooks:
        raise click.ClickException("OPDS catalog download failed. Is email address correct?")
    return ebooks


def get_local_ebooks(dir: Path) -> dict[str, LocalEbook]:
    """Return metadata of Standard EPUBs in the specified directory and subdirectories."""
    ebooks = {}
    for path in dir.glob("**/*.epub"):
        if not path.is_file():
            continue
        try:
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
                        modified=fromisoformat(modified.text),
                    )
                    ebooks[ebook.id] = ebook
        except:
            echo_status(path, Status.UNKNOWN)
    return ebooks


def download_ebook(url: str, path: Path) -> None:
    """Download the ebook at the specified URL into the specified path."""
    if _dry_run:
        return
    download = path.with_suffix(".sebsync")
    response = requests.get(url, stream=True)
    with download.open("wb") as file:
        for chunk in response.iter_content(chunk_size=1 * 1024 * 1024):
            file.write(chunk)
    download.replace(path)


def sortable_author(author: str) -> str:
    """Return the sortable name of the given author."""
    suffixes = {"Jr.", "Sr.", "Esq.", "PhD"}
    split = author.split()
    if len(split) < 2:
        return author
    last = split.pop().rstrip(",")
    suffix = None
    if last in suffixes:
        suffix = last
        last = split.pop()
    result = last
    if split:
        result += f", {' '.join(split)}"
    if suffix:
        result += f", {suffix}"
    return result


def ebook_filename(ebook: StandardEbook) -> str:
    """Return an EPUB file name for Standard ebook."""
    replace = {"/": "-", "‘": "'", "’": "'", '"': "'", "“": "'", "”": "'"}
    match _naming:
        case "standard":
            result = Path(urlparse(ebook.href).path).name
        case "sortable":
            result = f"{sortable_author(ebook.author)} - {ebook.title}.epub"
    for k, v in replace.items():
        result = result.replace(k, v)
    return result


@click.command(help=__doc__, epilog=_epilog)
@click.option(
    "--books",
    help="Directory where local books are stored.",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, writable=True, path_type=Path),
    default=if_exists(Path.home() / "Books"),
)
@click.option(
    "--downloads",
    help="Directory where new ebooks are downloaded.",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, writable=True, path_type=Path),
    default=if_exists(Path.home() / "Downloads"),
)
@click.option(
    "--force-update",
    help="Force update of all local ebooks.",
    is_flag=True,
)
@click.option(
    "--dry-run",
    help="Perform a trial run with no changes made.",
    is_flag=True,
)
@click.option(
    "--email",
    help="Email address to authenticate with Standard Ebooks.",
    required=True,
)
@click.help_option()
@click.option(
    "--naming",
    type=click.Choice(["standard", "sortable"]),
    help="Download file naming convention.",
    default="standard",
)
@click.option(
    "--opds",
    help="URL of Standard Ebooks OPDS catalog.",
    default="https://standardebooks.org/feeds/opds/all",
)
@click.option(
    "--quiet",
    help="Suppress non-error messages.",
    is_flag=True,
)
@click.option(
    "--type",
    type=click.Choice(list(type_selector.keys())),
    help="EPUB type to download.",
    default="compatible",
)
@click.option(
    "--verbose",
    help="Increase verbosity.",
    is_flag=True,
)
@click.version_option(package_name="sebsync")
def sebsync(
    books: Path,
    downloads: Path,
    dry_run: bool,
    email: str,
    force_update: bool,
    naming: str,
    opds: str,
    quiet: bool,
    type: str,
    verbose: bool,
):
    global _dry_run
    global _verbose
    global _quiet
    global _naming

    _dry_run = dry_run
    _naming = naming
    _quiet = quiet
    _verbose = verbose and not quiet  # quiet wins

    remote_ebooks = get_remote_ebooks(opds, email, type)
    if _verbose:
        click.echo(f"Found {len(remote_ebooks)} remote ebooks.")

    local_ebooks = get_local_ebooks(downloads) | get_local_ebooks(books)
    if _verbose:
        click.echo(f"Found {len(local_ebooks)} local ebooks.")

    for remote_ebook in remote_ebooks.values():
        if local_ebook := local_ebooks.get(remote_ebook.id):
            if remote_ebook.updated != local_ebook.modified or force_update:
                echo_status(local_ebook.path, Status.UPDATE)
                download_ebook(remote_ebook.href, local_ebook.path)
        else:
            path = downloads / ebook_filename(remote_ebook)
            echo_status(path, Status.NEW)
            download_ebook(remote_ebook.href, path)

    for local_ebook in local_ebooks.values():
        if local_ebook.id not in remote_ebooks:
            echo_status(local_ebook.path, Status.EXTRA)


def main():
    sebsync(show_default=True)


if __name__ == "__main__":
    main()

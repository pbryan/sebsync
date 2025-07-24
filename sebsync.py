"""Synchronize Standard Ebooks catalog with local EPUB collection."""

import click
import hashlib
import json
import os
import requests
import xml.etree.ElementTree as ElementTree
import zipfile

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from platformdirs import PlatformDirs
from requests.auth import HTTPBasicAuth
from shutil import get_terminal_size
from time import sleep
from urllib.parse import urlparse


class Status:
    CURRENT = click.style("C", fg="white")
    NEW = click.style("N", fg="green")
    UPDATE = click.style("U", fg="blue")
    REMOVE = click.style("R", fg="yellow")
    OUTDATED = click.style("O", fg="yellow")
    EXTRA = click.style("X", fg="red")
    UNKNOWN = click.style("?", fg="red")


_epilog = f"""
    \b
    Download naming conventions:
    • standard: Standard Ebooks naming (e.g. “edwin-a-abbott_flatland.epub”)
    • sortable: sortable author/title (e.g. “Abbott, Edwin A. - Flatland.epub”)

    \b
    Reported file statuses:
    • {Status.NEW}: new (ebook downloaded to downloads directory)
    • {Status.UPDATE}: update (ebook updated with newer version)
    • {Status.OUTDATED}: outdated (ebook has newer version or was deprecated)
    • {Status.REMOVE}: remove (outdated or deprecated ebook removed)
    • {Status.EXTRA}: extraneous (ebook not found in Standard Ebooks catalog)
    • {Status.UNKNOWN}: unknown (ebook could not be processed)
    • {Status.CURRENT}: current (ebook is up-to-date; displayed in verbose mode)

    A local ebook file is “deprecated” if its identifier has been replaced by a new identifier
    in the Standard Ebooks catalog. This occurs when a book is renamed or substantially
    revised. Its replacement is downloaded as a new ebook.

    See https://github.com/pbryan/sebsync/ for updates, bug reports and answers.
"""


@dataclass
class RemoteEbook:
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
    "kindle": "Amazon Kindle azw3",
}


local_ebooks: list[LocalEbook] = []

remote_ebooks: dict[str, RemoteEbook] = {}


def echo_status(path: Path, status: str) -> None:
    if not options.quiet:
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


def toisoformat(d: datetime) -> str:
    """Convert Python datetime object to string."""
    if isinstance(d, (datetime)):
        return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def request(**kwargs):
    """Send an HTTP request."""
    kwargs["auth"] = HTTPBasicAuth(options.email, "")
    if options.debug:
        click.echo(f"{kwargs['method']} {kwargs['url']}", nl=False)
    retry_count = 10
    for retries in range(1, retry_count + 1):
        response = requests.request(**kwargs)
        if response.status_code != 429:
            break
        if not options.quiet:
            click.echo(f"{response.status_code} {response.reason}. Retrying...")
        sleep(5)
    if retries == retry_count:
        raise click.ClickException("Too many retries. Aborting.")
    if options.debug:
        click.echo(f" → {response.status_code} {response.reason}")
    return response


def strip_id_prefix(id: str) -> str:
    return id[4:] if id.startswith("url:") else id


def get_remote_ebooks() -> None:
    """Retrieve Standard Ebooks metadata for EPUBs from the OPDS catalog."""
    ns = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/terms/"}
    response = request(method="GET", url=options.opds, stream=True)
    if response.status_code != 200:
        raise click.ClickException("OPDS catalog download failed. Is email address correct?")
    response.raw.decode_content = True
    root = ElementTree.parse(response.raw).getroot()
    for entry in root.iterfind(".//atom:entry", ns):
        remote_ebook = RemoteEbook(
            id=strip_id_prefix(entry.find("dc:identifier", ns).text),
            title=entry.find("atom:title", ns).text,
            author=entry.find("atom:author", ns).find("atom:name", ns).text,
            href=entry.find(f".//atom:link[@title='{type_selector[options.type]}']", ns).attrib[
                "href"
            ],
            updated=fromisoformat(entry.find("atom:updated", ns).text),
        )
        remote_ebooks[remote_ebook.id] = remote_ebook
    if not remote_ebooks:
        raise click.ClickException("OPDS catalog download failed. Is email address correct?")
    if options.verbose:
        click.echo(f"Found {len(remote_ebooks)} remote ebooks.")


def get_local_ebook_metadata(path) -> LocalEbook | None:
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
            if id is not None and "standardebooks.org" in id.text:
                modified = metadata.find(".//opf:meta[@property='dcterms:modified']", ns)
                return LocalEbook(
                    id=strip_id_prefix(id.text),
                    title=metadata.find(".//dc:title", ns).text,
                    path=path,
                    modified=fromisoformat(modified.text),
                )


def get_local_ebooks(index: dict) -> None:
    """Retrieve metadata of Standard EPUBs in the specified directory and subdirectories."""
    if options.type == "kindle":
        for path in options.books.glob("**/*.azw3"):
            if not path.is_file():
                continue
            try:
                hexdigest = calculate_hash(path)
                if hexdigest in index:
                    local_ebook = LocalEbook(
                        id=index[hexdigest].get("id", None),
                        title=index[hexdigest].get("title", None),
                        path=path,
                        modified=fromisoformat(index[hexdigest].get("modified", None)),
                    )
                    local_ebooks.append(local_ebook)
            except Exception:
                echo_status(path, Status.UNKNOWN)
    else:
        try:
            if local_ebook := get_local_ebook_metadata(path):
                local_ebooks.append(local_ebook)
        except:
            echo_status(path, Status.UNKNOWN)
    if options.verbose:
        click.echo(f"Found {len(local_ebooks)} local ebooks.")


def download_ebook(url: str, path: Path, status: str) -> None:
    """Download the ebook at the specified URL into the specified path."""
    echo_status(path, status)
    if options.dry_run:
        return
    response = request(method="GET", url=url, stream=True)
    if response.status_code != 200:
        raise click.ClickException(f"Download failed (error {response.status_code})")
    download = path.with_suffix(".sebsync")
    with download.open("wb") as file:
        for chunk in response.iter_content(chunk_size=1 * 1024 * 1024):
            file.write(chunk)
    try:
        if not get_local_ebook_metadata(download):
            raise RuntimeError
    except:
        raise click.ClickException("Ebook download failed (corrupt file)")
    download.replace(path)


def calculate_hash(path: Path) -> str:
    """Calculate a SHA-256 hash of a given file."""
    with open(path, "rb") as file:
        hexdigest = hashlib.file_digest(file, "sha256")
    if options.debug:
        click.echo(f"{path.name} -> {hexdigest.hexdigest()}")
    return hexdigest.hexdigest()


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


def books_are_different(local_ebook: LocalEbook, remote_ebook: RemoteEbook) -> bool:
    """Return if differences are detected between local and remote ebooks."""

    # if metadata has exact modification times, then local is considered current
    if remote_ebook.updated == local_ebook.modified:
        if options.debug:
            click.echo(
                f"'{local_ebook.path.name}' has the same modification date as remote file '{ebook_filename(remote_ebook)}'"
            )
        return False

    stat = local_ebook.path.stat()

    file_modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    if remote_ebook.updated > file_modified:
        if options.debug:
            click.echo(
                f"'{local_ebook.path.name}' is older than remote file '{ebook_filename(remote_ebook)}'"
            )
        return True

    response = request(method="HEAD", url=remote_ebook.href)
    content_length = int(response.headers["Content-Length"])
    if content_length != stat.st_size:
        if options.debug:
            click.echo(
                f"'{local_ebook.path.name}' differs in size from remote file '{ebook_filename(remote_ebook)}'"
            )
        return True

    return False


def ebook_filename(ebook: RemoteEbook) -> str:
    """Return an EPUB file name for remote ebook."""
    replace = {"/": "-", "‘": "'", "’": "'", '"': "'", "“": "'", "”": "'"}
    match options.naming:
        case "standard":
            result = Path(urlparse(ebook.href).path).name
        case "sortable":
            author = sortable_author(ebook.author)
            title = ebook.title.rstrip(".")
            result = f"{author} - {title}.epub"
    for k, v in replace.items():
        result = result.replace(k, v)
    return result


def is_deprecated(local_ebook: LocalEbook) -> bool:
    """Return if the specified book identifier is deprecated."""
    response = request(method="HEAD", url=local_ebook.id, allow_redirects=False)
    return (
        response.status_code == 301 and f"url:{response.headers['Location']}" in remote_ebooks
    )


def remove(local_ebook: LocalEbook) -> None:
    """Remove the local ebook from the filesystem."""
    echo_status(local_ebook.path, Status.REMOVE)
    if not options.dry_run:
        local_ebook.path.unlink()


@dataclass
class Options:
    """Command line options."""

    books: Path
    debug: bool
    downloads: Path
    dry_run: bool
    email: str
    force_update: bool
    naming: str
    opds: str
    quiet: bool
    remove: bool
    type: str
    update: bool
    verbose: bool


options: Options = None


context_settings = {
    "max_content_width": get_terminal_size().columns - 2,
}


@click.command(context_settings=context_settings, help=__doc__, epilog=_epilog)
@click.option(
    "--books",
    help="Directory where local books are stored.",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, writable=True, path_type=Path),
    default=if_exists(Path.home() / "Books"),
)
@click.option(
    "--debug",
    is_flag=True,
    hidden=True,
)
@click.option(
    "--downloads",
    help="Directory where new ebooks are downloaded.",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, writable=True, path_type=Path),
    default=if_exists(Path.home() / "Downloads"),
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
@click.option(
    "--force-update",
    help="Force update of all local ebooks (implies --update).",
    is_flag=True,
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
    "--remove/--no-remove",
    help="Remove outdated or deprecated local ebook files.",
    is_flag=True,
    default=False,
)
@click.option(
    "--type",
    type=click.Choice(list(type_selector.keys())),
    help="EPUB type to download.",
    default="compatible",
)
@click.option(
    "--update/--no-update",
    help="Update existing local ebook files.",
    default=True,
)
@click.option(
    "--verbose",
    help="Increase verbosity.",
    is_flag=True,
)
@click.version_option(package_name="sebsync")
def sebsync(**kwargs):
    global options
    options = Options(**kwargs)

    # --force-update implies --update
    if options.force_update:
        options.update = True

    # --quiet wins over --verbose
    options.verbose = options.verbose and not options.quiet

    index = {}
    if options.type == "kindle":
        dirs = PlatformDirs(appname="sebsync")
        indexfile = Path(dirs.user_cache_dir, "sebsync_index")
        if indexfile.exists() and indexfile.is_file():
            if options.debug:
                click.echo(f"Index found at {indexfile}")
            with open(indexfile, "r") as f:
                index = json.load(f)
        if options.verbose:
            click.echo(f"Found {len(index)} books in the index.")

    get_remote_ebooks()
    get_local_ebooks(index)

    for remote_ebook in remote_ebooks.values():
        matching_local_ebooks = [b for b in local_ebooks if b.id == remote_ebook.id]
        download_new = True
        if matching_local_ebooks:
            for local_ebook in matching_local_ebooks:
                if options.update:
                    download_new = False
                    if options.force_update or books_are_different(local_ebook, remote_ebook):
                        old_hexdigest = calculate_hash(local_ebook.path)
                        download_ebook(remote_ebook.href, local_ebook.path, Status.UPDATE)
                        if options.type == "kindle" and not options.dry_run:
                            new_hexdigest = calculate_hash(local_ebook.path)
                            index[new_hexdigest] = {
                                "id": remote_ebook.id,
                                "title": remote_ebook.title,
                                "modified": remote_ebook.updated,
                            }
                            # When forcing an update, the old file and new download may have the same hexdigest;
                            # only remove the old entry from the index when hexdigests are different
                            if old_hexdigest != new_hexdigest:
                                index.pop(old_hexdigest, None)
                    elif options.verbose:
                        echo_status(local_ebook.path, Status.CURRENT)
                else:
                    if books_are_different(local_ebook, remote_ebook):
                        hexdigest = calculate_hash(local_ebook.path)
                        if options.remove:
                            remove(local_ebook)
                        else:
                            echo_status(local_ebook.path, Status.OUTDATED)
                        if options.type == "kindle" and not options.dry_run:
                            index.pop(hexdigest, None)
                    else:
                        download_new = False  # at least one local ebook already matches
                        if options.verbose:
                            echo_status(local_ebook.path, Status.CURRENT)
        if download_new:
            path = options.downloads / ebook_filename(remote_ebook)
            download_ebook(remote_ebook.href, path, Status.NEW)
            if options.type == "kindle" and not options.dry_run:
                downloaded_hexdigest = calculate_hash(path)
                index[downloaded_hexdigest] = {
                    "id": remote_ebook.id,
                    "title": remote_ebook.title,
                    "modified": remote_ebook.updated,
                }

    for local_ebook in local_ebooks:
        if local_ebook.id not in remote_ebooks:
            if is_deprecated(local_ebook):
                hexdigest = calculate_hash(local_ebook.path)
                if options.remove:
                    remove(local_ebook)
                else:
                    echo_status(local_ebook.path, Status.OUTDATED)
                if options.type == "kindle" and not options.dry_run:
                    index.pop(hexdigest, None)
            else:
                echo_status(local_ebook.path, Status.EXTRA)

    if options.type == "kindle" and not options.dry_run:
        # Generate an inventory of local files from the library and downloads directories
        local_files = set()
        for path in options.books.glob("**/*.azw3"):
            if not path.is_file():
                continue
            local_files.add(calculate_hash(path))
        if options.downloads != options.books:
            for path in options.downloads.glob("**/*.azw3"):
                if not path.is_file():
                    continue
                local_files.add(calculate_hash(path))

        # Ensure the index reflects the current library by pruning entries that don't exist locally
        index_orphans = set(index) - local_files
        for hexdigest in index_orphans:
            index.pop(hexdigest, None)
            if options.debug:
                click.echo(
                    f"'{hexdigest}' doesn't match any local files; removing from the index."
                )

        # Save the index
        os.makedirs(dirs.user_cache_dir, exist_ok=True)
        with open(indexfile, "w") as f:
            # JSON can't serialize datetime objects, so they have to be converted to ISO formatted strings
            json.dump(index, f, default=toisoformat)
            if options.debug:
                click.echo(f"Saved index to {indexfile}.")


def main():
    sebsync(show_default=True)


if __name__ == "__main__":
    main()

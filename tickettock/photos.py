"""Pictures on tickets: check an upload really is a picture, then keep two small WebP copies.

A phone screenshot can be 5 MB; a 1600-pixel WebP is about 150 KB, and the
small copy shown in the conversation about 15 KB. Saving a new file also drops
everything a camera writes into a photo (EXIF: the place, the phone), so a
customer's location never ends up on the help desk.
"""
import io

from PIL import Image, ImageOps, UnidentifiedImageError

from .models import Attachment

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_FILES = 3  # per ticket or reply
BIG_SIDE = 1600
SMALL_SIDE = 360
# A tiny file can claim to be a 100,000 x 100,000 picture and fill the memory
# when opened. Pillow refuses anything bigger than this.
Image.MAX_IMAGE_PIXELS = 40_000_000


class PhotoProblem(ValueError):
    pass


def _webp(image, side):
    copy = image.copy()
    copy.thumbnail((side, side))
    out = io.BytesIO()
    copy.save(out, "WEBP", quality=80, method=4)
    return out.getvalue(), copy.size


def clean_name(name):
    """Keep a short, plain file name for the page (never used as a path)."""
    name = (name or "picture").replace("\\", "/").rsplit("/", 1)[-1].strip() or "picture"
    stem = name.rsplit(".", 1)[0][:100]
    return f"{stem}.webp"


def make(ticket, uploader, upload, now, message=None, internal=False):
    """An Attachment (not yet added) from an uploaded file, or PhotoProblem with a sentence for the page."""
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise PhotoProblem("That picture is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoProblem("That picture is too big. Pictures can be up to 5 MB.")
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            if opened.format not in ("JPEG", "PNG", "WEBP", "GIF", "MPO"):
                raise PhotoProblem("Use a JPG, PNG or WebP picture.")
            image = ImageOps.exif_transpose(opened)  # phones save "turn me" as a note; do it now
            image = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise PhotoProblem("That file isn't a picture we can read. Use a JPG, PNG or WebP picture.") from None
    big, (width, height) = _webp(image, BIG_SIDE)
    small, _ = _webp(image, SMALL_SIDE)
    return Attachment(ticket_id=ticket.id, message_id=message.id if message is not None else None,
                      uploader_id=uploader.id, filename=clean_name(upload.filename), data=big, thumb=small,
                      width=width, height=height, is_internal=internal, created_at=now)


def uploads(files):
    """The chosen files from a form (browsers send an empty one when nothing was chosen)."""
    chosen = [upload for upload in files.getlist("pictures") if upload and upload.filename]
    if len(chosen) > MAX_FILES:
        raise PhotoProblem(f"Add at most {MAX_FILES} pictures at a time.")
    return chosen

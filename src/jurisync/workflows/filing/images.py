"""Photos and scans (JPG, PNG, HEIC) to a one-page PDF PJe accepts, with Pillow."""

import io
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif"})
HEIC_SUFFIXES = frozenset({".heic", ".heif"})


def _open_heic():
    """HEIC needs the optional pillow-heif plugin; without it the file goes to review instead of being guessed at."""
    try:
        import pillow_heif
    except ImportError:
        raise ValueError(
            "HEIC images need the optional pillow-heif package; export the photo as JPG or PDF and prepare again"
        ) from None
    pillow_heif.register_heif_opener()


def image_to_pdf(source, destination, limit):
    """Write `source` as a one-page PDF at `destination`, upright (EXIF) and on white, at most `limit` bytes.

    A photo too large for the limit is scaled down step by step rather than split: an image is one page.
    """
    source, destination = Path(source), Path(destination)
    if source.suffix.lower() in HEIC_SUFFIXES:
        _open_heic()
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
        elif image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        for _ in range(6):
            buffer = io.BytesIO()
            image.save(buffer, "PDF", resolution=150.0, quality=85)
            if buffer.tell() <= limit:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(buffer.getvalue())
                return destination
            image = image.resize((max(1, int(image.width * 0.7)), max(1, int(image.height * 0.7))))
    raise ValueError(f"{source.name} stays above PJe's 10 MB limit even scaled down")

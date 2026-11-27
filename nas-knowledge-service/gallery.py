"""Read-only, authenticated-by-caller image gallery. No network or writes."""
import hashlib
import os
import stat
from pathlib import Path

EXTENSIONS = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}
MAX_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 20000


def identifier(kind, relative):
    return hashlib.sha256((kind + ':' + relative).encode()).hexdigest()


def scan():
    configured = os.getenv('FMO_GALLERY_DIR', '')
    if not configured:
        raise ValueError('gallery disabled')
    root = Path(configured)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError('gallery root unavailable')
    root = root.resolve()
    directories = {}
    images = {}
    visited = 0
    def walk_error(error):
        raise ValueError("gallery directory unreadable") from None
    for folder, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        here = Path(folder)
        dirs[:] = sorted(d for d in dirs if not d.startswith(('.', '@')) and d != '#recycle' and not (here/d).is_symlink())
        relative = here.relative_to(root).as_posix()
        relative = '' if relative == '.' else relative
        did = identifier('directory', relative)
        parent = None if not relative else identifier('directory', '' if Path(relative).parent == Path('.') else Path(relative).parent.as_posix())
        directories[did] = {'id': did, 'name': here.name if relative else '图库', 'path': relative, 'parent': parent, 'count': 0, 'recursive_count': 0}
        visited += 1 + len(files)
        if visited > MAX_ENTRIES:
            raise ValueError('gallery entry limit exceeded')
        for name in sorted(files):
            path = here/name
            if name.startswith('.') or path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
                continue
            rel = path.relative_to(root).as_posix()
            iid = identifier('image', rel)
            images[iid] = {'id': iid, 'name': name, 'size': info.st_size, '_relative': rel, '_directory': did}
            directories[did]['count'] += 1
    for item in images.values():
        did = item['_directory']
        while did is not None:
            directories[did]['recursive_count'] += 1
            did = directories[did]['parent']
    return root, directories, images


def directory_list():
    _, directories, _ = scan()
    return {'directories': list(directories.values())}


def image_list(directory, recursive=False):
    _, directories, images = scan()
    if directory not in directories:
        raise ValueError('gallery directory not found')
    prefix = directories[directory]['path']
    selected = []
    for item in images.values():
        if item['_directory'] == directory or (recursive and (not prefix or item['_relative'].startswith(prefix + '/'))):
            selected.append({k: item[k] for k in ('id', 'name', 'size')})
    return {'directory': directory, 'recursive': recursive, 'images': selected}


def read_image(image_id):
    root, _, images = scan()
    if image_id not in images:
        raise ValueError('gallery image not found')
    # openat/O_NOFOLLOW on each component rejects replacements with symlinks,
    # including directory swaps between indexing and opening.
    relative = Path(images[image_id]['_relative'])
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        image_fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        with os.fdopen(image_fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
                raise ValueError('invalid gallery image size')
            data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError('gallery image too large')
        return data, EXTENSIONS[relative.suffix.lower()]
    finally:
        os.close(fd)

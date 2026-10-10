import argparse
import hashlib
import os
import struct
from pathlib import Path


def pe_sections(path: Path):
    with path.open("rb") as f:
        data = f.read()
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[e_lfanew : e_lfanew + 4] == b"PE\0\0", "not PE"
    coff = e_lfanew + 4
    num_sections = struct.unpack_from("<H", data, coff + 2)[0]
    size_opt = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    image_base = struct.unpack_from("<Q" if magic == 0x20B else "<I", data, opt + 28)[0]
    section_table = opt + size_opt
    sections = []
    for i in range(num_sections):
        off = section_table + i * 40
        name = data[off : off + 8].rstrip(b"\0").decode("ascii", "replace")
        virtual_size = struct.unpack_from("<I", data, off + 8)[0]
        virtual_address = struct.unpack_from("<I", data, off + 12)[0]
        raw_size = struct.unpack_from("<I", data, off + 16)[0]
        raw_ptr = struct.unpack_from("<I", data, off + 20)[0]
        raw = data[raw_ptr : raw_ptr + raw_size] if raw_ptr and raw_size else b""
        sections.append(
            (
                name,
                virtual_size,
                virtual_address,
                len(raw),
                hashlib.sha256(raw).hexdigest(),
            )
        )
    return image_base, sections


def dump(path: Path):
    base, sections = pe_sections(path)
    print(f"== {path}")
    print(f"image_base=0x{base:x}")
    for name, vsize, vaddr, rawlen, digest in sections:
        print(
            f"{name:<10} vsize={vsize:>9} vaddr=0x{vaddr:08x} "
            f"raw={rawlen:>9} sha256={digest}"
        )


def default_paths() -> tuple[Path, Path]:
    project_root = Path(__file__).resolve().parents[1]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise SystemExit("LOCALAPPDATA is required when --installed is omitted")
    return (
        project_root
        / "desktop"
        / "src-tauri"
        / "target"
        / "release"
        / "personalmemory-desktop.exe",
        Path(local_app_data) / "PersonalMemory" / "personalmemory-desktop.exe",
    )


def main() -> int:
    default_built, default_installed = default_paths()
    parser = argparse.ArgumentParser(
        description="Compare PE section payloads in built and installed desktop executables."
    )
    parser.add_argument("--built", type=Path, default=default_built)
    parser.add_argument("--installed", type=Path, default=default_installed)
    args = parser.parse_args()
    built = args.built.resolve(strict=True)
    installed = args.installed.resolve(strict=True)

    dump(built)
    dump(installed)

    _, sections_b = pe_sections(built)
    _, sections_i = pe_sections(installed)
    by_name = {
        name: (vsize, vaddr, rawlen, digest)
        for name, vsize, vaddr, rawlen, digest in sections_i
    }
    print("\n-- per-section comparison (built vs installed) --")
    for name, vsize, _vaddr, rawlen, digest in sections_b:
        other = by_name.get(name)
        if other is None:
            print(f"{name:<10} MISSING in installed")
            continue
        if (vsize, rawlen, digest) == (other[0], other[2], other[3]):
            print(f"{name:<10} identical (raw={rawlen}, sha256={digest[:16]}...)")
        else:
            print(
                f"{name:<10} DIFFERS  built sha={digest[:16]}... "
                f"installed sha={other[3][:16]}..."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

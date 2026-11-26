# ISO utility of KVM

Manage read-only installation media (`.iso`) that a VM mounts as a cdrom.
An ISO is never used as a disk or as a backing file — that is what `kvm/image` is for.

```bash
./src/runpy.sh src/kvm/iso/kvm_iso.py --path <iso数据文件.json>
```

### schema

```jsonc
{
    "isoPath": "../ubuntu-24.04.iso",   // .iso file path, relative to this JSON file or absolute
    "isoSource": "{remote/upload}",     // remote: download from downloadLink
                                        // upload: file is placed by the REST upload endpoint
    "downloadLink": "",                 // required when isoSource=remote
    "sha256": ""                        // optional, verified after download
}
```

`Create()` is idempotent: it does nothing if `isoPath` already exists.

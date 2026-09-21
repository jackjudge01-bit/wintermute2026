# mbedtls/md5.h fix for ESP-IDF v6.1 — notes

`03-fix-mbedtls-md5-idf61.patch` is a **one-file fix**, layered on top of
`01-static-partition.patch` and `02-dynamic-sizing.patch` in that order. It
is not optional if you're building against ESP-IDF v6.1 — without it,
`02-dynamic-sizing.patch`'s new file fails to compile.

## What was verified, and how

This is the first of the three patches in this series that has actually
been run through a real compiler and real hardware, not just reviewed and
`git apply --check`ed. The full sequence, done in one session:

1. Cloned `GhostESP-Revival/GhostESP` branch `Development-deki`
   (`0ed9c8d3`) fresh.
2. Applied `01-static-partition.patch`, then `02-dynamic-sizing.patch` —
   both applied cleanly, as already established.
3. Installed ESP-IDF **v6.1** (the exact tag `.github/workflows/compile_all.yml`
   pins) via `git clone --branch v6.1 --recursive` +
   `install.sh esp32s3`. This system had no `cmake`/`ninja` installed —
   ESP-IDF's Linux installer does not auto-install those (only Windows
   does); installed via `apt-get install cmake ninja-build` before the
   build would run at all.
4. `idf.py set-target esp32s3`, then `idf.py build` on
   `configs/sdkconfig.generic_esp32s3_16mb` — **failed** on the first
   attempt:

   ```
   /main/managers/sd_vstorage_manager.c:29:10: fatal error: mbedtls/md5.h: No such file or directory
   ```

## Root cause (confirmed against the actual ESP-IDF v6.1 source tree, not assumed)

ESP-IDF v6.1's `mbedtls` component is **mbedtls 4.1**, built on the new
TF-PSA-Crypto backend. That version no longer ships a public
`mbedtls/md5.h` header. The only `md5.h` left anywhere in the component's
tree is:

```
components/mbedtls/mbedtls/tf-psa-crypto/drivers/builtin/include/mbedtls/private/md5.h
```

— under a `private/` subdirectory, not on any public include path, and not
reachable via `#include "mbedtls/md5.h"` regardless. `main`'s
`CMakeLists.txt` does correctly list `mbedtls` in `REQUIRES`; this isn't a
missing-dependency bug in `02-dynamic-sizing.patch`, it's the header itself
having been removed/relocated between whatever mbedtls version the patch
was originally written against and the one ESP-IDF v6.1 actually ships.

## The fix

Swapped the single `mbedtls_md5(...)` one-shot call for ESP-IDF's own ROM
MD5 API (`esp_rom_md5.h`: `esp_rom_md5_init` / `_update` / `_final`). This
isn't a workaround reaching for whatever compiles — it's the same API
`components/bootloader_support/src/flash_partitions.c` uses to verify this
exact MD5 partition-table-checksum entry type
(`esp_partition_table_verify()`), so the digest algorithm this code writes
is guaranteed to match what the bootloader and `esp_partition`'s loader
will check on boot. `esp_rom/include` (where `esp_rom_md5.h` lives) was
already on `main`'s compile include path with no CMakeLists change needed.

## Result after the fix

- `idf.py build` on `configs/sdkconfig.generic_esp32s3_16mb`: **exit code
  0**, "Project build complete." Only pre-existing warnings elsewhere in
  the tree (unused static functions/variables in `options_screen.c`,
  unrelated to these three patches) — no errors.
- Artifacts produced: `bootloader.bin` (20224 B), `partition-table.bin`
  (3072 B), `Ghost_ESP_IDF.bin` (3,463,840 B; app partition 87% used, 13%
  free).
- `gen_esp32part.py` dump of the built partition table confirms patch 01's
  static partition is present exactly as specified:
  `storage,data,fat,0x400000,4M`.

Hardware flash and serial verification results are in
`04-hardware-verification.md`.

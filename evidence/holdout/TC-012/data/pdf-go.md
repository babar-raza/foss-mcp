# v0.8.1 — Encrypted-stream data loss

A single fix, recommended for everyone reading or writing encrypted PDFs.

## Fixed: an encrypted stream could be silently destroyed on read

Roughly **one encrypted stream in 500** was lost while the document was being parsed.

The parser decodes a stream's declared `/Filter` as it reads it, on the assumption that ciphertext never survives its filter. That assumption is false: a zlib header is only *"CM == 8 and (CMF<<8|FLG) % 31 == 0"*, which random ciphertext satisfies about that often, and the Flate decoder's deliberate tolerance of a truncated tail then returns a byte or two of garbage instead of an error. The stream was marked decoded, the decryption pass skipped it as already-clean, and its real content was gone.

The damage was quiet and content-dependent. In the case that exposed it, a font's `/ToUnicode` CMap collapsed to a single byte, so one space in the extracted text came back as U+FFFD — visible only as an intermittent test failure that never reproduced on a rerun.

Streams are now always decrypted from the bytes exactly as they appear in the file, with the streams a conforming producer leaves in the clear correctly exempt: cross-reference streams (never encrypted, ISO 32000-1 §7.5.8.2) and the metadata stream when `/EncryptMetadata` is false.

**Verification**: reproduced first (2 failures in 500 encrypt/reopen cycles), then a corpus sweep of **1,009 documents** encrypting, reopening and extracting byte-identical text under RC4-128, AES-128 and AES-256, plus regression tests that fail against the old behaviour.

**Full changelog**: https://github.com/aspose-pdf-foss/aspose-pdf-foss-for-go/blob/main/CHANGELOG.md


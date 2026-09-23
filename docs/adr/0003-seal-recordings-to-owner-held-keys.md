# Seal recordings to owner-held public keys in the age format

Status: accepted 2026-09-22; implemented on the controller and in the app.

Recordings must be encrypted at rest against theft of the SSD or the whole controller, recording must resume unattended after power returns, and playback requires the customer's authorization (product notes). The Pi 4 has no TPM or secure element, and there is nobody present to type a passphrase after a power cut. Any key the controller can use to decrypt would therefore sit on the controller, where a thief also has it. So the controller holds only public keys: each recording is sealed to the owner's keys as it is written, and nothing on the controller or its SSD can decrypt it. The owner's phone downloads the sealed file and decrypts it locally.

We use the published age v1 file format (age-encryption.org/v1: X25519 recipients, ChaCha20-Poly1305 in 64 KiB chunks, HKDF-SHA256) rather than a format of our own. Any age implementation, including the open-source `age` command-line tool, opens a Solstead recording with the owner's key, so footage stays recoverable if the vendor disappears (ADR 0001), and the format has independent implementations and published test vectors we test against. ChaCha20 also suits the Pi 4, which lacks AES instructions: about 320 MB/s against 59 MB/s for AES-GCM, measured on the bench Pi, against a camera stream near 1 MB/s.

Keys, all generated on the owner's phone:

- **Device key**: one per phone, kept in that phone's Keychain as device-only. Its public half is enrolled on the controller.
- **Recovery key**: generated at setup and never stored by the phone or controller; only its public half is enrolled. It is an ordinary age identity (`AGE-SECRET-KEY-1…`), so it works with the `age` tool directly. The owner receives it in both forms at setup (accepted 2026-09-22): a printable code, and a file to save outside the phone and controller. The file is a standard age identity file, usable as-is with `age -d -i`.

Encryption is the owner's choice (decided 2026-09-22), set by a switch in the app's Settings. Turning it on runs the recovery-key setup and enrols the keys; turning it off makes the controller forget them, so new recordings are stored unencrypted while recordings already sealed stay sealed and still open with the owner's keys. The app confirms before turning it off and says what is lost.

Every recording is sealed to all enrolled device keys and the one recovery key. The same sealed file can later be uploaded as the off-site backup unchanged, so ADR 0002's customer-held recovery key is this recovery key and the backup needs no second key scheme.

Consequences:

- Plaintext video exists only in RAM: the pre-event buffer and clip assembly moved from the SSD to /dev/shm. A power cut loses the pre-roll and any event still open, as it did before.
- The controller cannot read its own recordings. Anything that needs plaintext after the fact (time-lapse video assembly, thumbnails, trimming) moves to the phone or happens before sealing.
- Enrolling a new phone or replacing keys affects only recordings made afterwards. Giving a later device access to older footage needs a device that already holds a key to re-wrap each file's header, which is small. Revoking a key cannot retract footage already downloaded.
- Losing every device key and the recovery key makes the footage unrecoverable, and support cannot help. That is the point, and the setup flow must say so plainly.
- Event times, durations and sizes stay in plaintext on the SSD so the controller can list, prune and schedule without keys. A thief learns when there was activity, not what it was.
- Clips recorded before enrolment are sealed afterwards, but the SSD may keep their old plaintext blocks. Production setup enrols keys before the first recording.
- Encryption does not protect against someone who takes over a running controller: they see live video and new footage before it is sealed. It protects stored footage.

Setup confirmation (2026-09-22): after printing or saving, the owner types back two randomly chosen five-character groups of the key before enrolment. The printout carries the grouped code, a QR code of the same key, the controller name and date, and the `age` command.

Open: whether the owner can view footage from a new phone by scanning the recovery key; how time-lapse frames are sealed and assembled on the phone; how viewers' keys are added once viewers exist.

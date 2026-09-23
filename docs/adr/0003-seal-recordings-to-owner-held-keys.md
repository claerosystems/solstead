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

Restoring access (2026-09-22): a new phone takes the recovery key (typed, from the saved file, or scanned from the printout's QR code), checks it against the enrolled recovery public key, enrols its own device key, and re-wraps every older sealed clip's header to add itself. Only headers move; the controller accepts a replacement header only if it keeps every existing stanza and leaves the payload untouched. The phone does not keep the recovery key. Clips sealed under an earlier recovery key stay locked to that key.

Time-lapse and manual clips (2026-09-23): with keys enrolled, each time-lapse frame is sealed as it is captured into its own age file (`HHMMSS.jpg.age`), rather than one file per day, because an age file cannot be appended to: a per-day file would keep the day's frames in plaintext in RAM until the window closed, losing them on a power cut. The cost is a header of a few hundred bytes per frame (about 0.2% of a 130 KB frame), and pruning, retention and the schedule are unchanged. The controller cannot open sealed frames, so a day with any sealed frame is downloaded frame by frame, decrypted and assembled into a video on the phone at the same frame rate; days of plain frames are still assembled by the controller, so behaviour without keys is unchanged. The phone keeps the assembled video only while it plays, as with clips; no plaintext cache. Restoring access re-wraps frame headers a day at a time in batches rather than one request per frame. Manual test clips are assembled in RAM and sealed like event clips. Frames and manual clips stored before enrolment are sealed in the background, and enrolment deletes cached plaintext time-lapse exports.

Open: how viewers' keys are added once viewers exist.

/**
 * Atomic file-write helper with `fsync`.
 *
 * Writes data to a sibling temp file, calls `fs.fsyncSync` to flush
 * kernel buffers to disk, then `fs.renameSync` for an atomic
 * replace-on-POSIX (and atomic-ish replace on Windows NTFS, which
 * uses `MOVEFILE_REPLACE_EXISTING` via Node's `fs.renameSync` on
 * Node 10+).
 *
 * The non-atomic `fs.writeFileSync(filePath, ...)` alternative
 * truncates the destination first, so a crash, power loss, or
 * disk-full mid-write leaves a partial file that parsers reject.
 * The temp-then-rename pattern guarantees the destination is either
 * the prior content or the new content, never a truncated half.
 *
 * Mirrors the Rust `atomic_write_bytes` canonical helper: write tmp →
 * fsync → rename. The existing `atomicWriteFileSync` in
 * `ipc/export-handlers.ts` does NOT `fsync` (it's optimized for the
 * large-export hot path where durability is less critical than
 * atomicity); this helper adds the `fsync` for small
 * config-critical files (e.g. `restart_history.json`) where losing a
 * few bytes to a power-loss window would corrupt the crash-loop
 * breaker state.
 *
 * @param filePath  Destination path.
 * @param data      String content to write.
 * @param options   Optional `mode` (perms, default 0o600) and
 *                  `encoding` (default "utf-8").
 */
import fs from "node:fs";

export function atomicWriteFile(
	filePath: string,
	data: string,
	options?: { mode?: number; encoding?: BufferEncoding },
): void {
	const mode = options?.mode ?? 0o600;
	const encoding = options?.encoding ?? "utf-8";
	// Sibling temp file (same directory) so `rename(2)` stays within
	// the same filesystem, cross-device renames fall back to
	// copy+delete, which is non-atomic.
	const tmpPath = `${filePath}.tmp`;
	// Write to the temp file.
	fs.writeFileSync(tmpPath, data, { encoding, flag: "w", mode });
	// fsync to flush kernel buffers to disk. Without this, a power
	// loss after `writeFileSync` but before the kernel flushes the
	// page cache to disk could leave the temp file empty or partial
	//, and the subsequent `renameSync` would then atomically replace
	// the destination with that partial content.
	//
	// The fsync handle is opened "r+" (O_RDWR), NOT "r" (O_RDONLY):
	// on Windows, libuv implements `fs.fsyncSync` as Win32
	// `FlushFileBuffers`, which REQUIRES a handle opened with
	// GENERIC_WRITE. A read-only "r" handle (GENERIC_READ only) made
	// `fsyncSync` throw EACCES/EPERM BEFORE the rename, the sole
	// caller's catch-block logged a warning and swallowed it, so
	// `restart_history.json` (the Electron crash-loop breaker state)
	// never persisted on Windows and the 3-restarts-in-60s cap
	// silently never fired on the primary platform. "r+" satisfies
	// GENERIC_WRITE and is POSIX-equivalent (fsync on O_RDWR is legal
	// on every platform); it does not truncate the just-written temp
	// file. Mirrors the Rust `atomic_write_bytes` fsync fix (the same
	// class of Windows-only durability bug).
	let fd: number | undefined;
	try {
		fd = fs.openSync(tmpPath, "r+");
		fs.fsyncSync(fd);
	} finally {
		if (fd !== undefined) {
			fs.closeSync(fd);
		}
	}
	// Atomic rename (POSIX `rename(2)`; Windows NTFS uses
	// `MoveFileEx` with `MOVEFILE_REPLACE_EXISTING` on Node 10+).
	fs.renameSync(tmpPath, filePath);
}
